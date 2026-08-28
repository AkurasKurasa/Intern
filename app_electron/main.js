const { app, BrowserWindow, ipcMain, shell } = require("electron");
const { spawn, execFile } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const readline = require("readline");

const REPO_ROOT = path.join(__dirname, "..");
const BRIDGE_SCRIPT = path.join(REPO_ROOT, "app", "recorder_bridge.py");
const DEMOS_ROOT = path.join(REPO_ROOT, "data", "demos");
const REGISTRY_PATH = path.join(REPO_ROOT, "tasks", "registry.json");
// Matches recorder_bridge.py's own _CAPSULE_LOG_PATH exactly -- a full,
// persisted transcript of everything the Play panel's Activity log
// receives, truncated fresh at the start of each capsule run. A strict
// superset of run_task.py's own logs/latest.log (that only captures
// logger.*() calls, not the raw print()-based countdown/emergency-stop
// lines) -- everything run_task.py logs also flows through this process's
// merged stdout, so one button covers both.
const CAPSULE_LOG_PATH = path.join(REPO_ROOT, "logs", "capsule_activity.log");
// Written only by components/inbox_router/router.py (single writer);
// main.js only ever reads it, same "no bridge round-trip for a plain disk
// read" precedent as readRegistry()/listWorkflows() below.
const INBOX_HISTORY_PATH = path.join(REPO_ROOT, "components", "inbox_router", "data", "routed_history.json");
const INBOX_LOG_PATH = path.join(REPO_ROOT, "logs", "inbox_activity.log");

function resolvePython() {
  const candidates = [
    path.join(os.homedir(), "AppData", "Local", "Programs", "Python", "Python312", "python.exe"),
    "python.exe",
    "python",
  ];
  for (const c of candidates) {
    if (c.includes(path.sep) && !fs.existsSync(c)) continue;
    return c;
  }
  return "python";
}

// Same shape as resolvePython() -- LM Studio's own CLI (bundled with the
// desktop app, not on PATH by default) lives at ~/.lmstudio/bin/lms.exe.
// Falls back to "lms" in case it's ever added to PATH directly.
function resolveLmsCli() {
  const candidates = [
    path.join(os.homedir(), ".lmstudio", "bin", "lms.exe"),
    "lms.exe",
    "lms",
  ];
  for (const c of candidates) {
    if (c.includes(path.sep) && !fs.existsSync(c)) continue;
    return c;
  }
  return "lms";
}

// One-shot lms CLI call -- distinct from startBridge()'s long-lived
// recorder_bridge.py process. execFile (not spawn) because every command
// used here (server status/start, ls --json, ps --json, load) is a
// bounded, one-shot call with output that fits comfortably in memory --
// no need for streaming line-by-line the way capsule runs do.
function runLmsCli(args, timeoutMs = 15000) {
  return new Promise((resolve) => {
    execFile(resolveLmsCli(), args, { timeout: timeoutMs, windowsHide: true },
      (error, stdout, stderr) => {
        resolve({ ok: !error, stdout: (stdout || "").trim(), stderr: (stderr || "").trim(), error });
      });
  });
}

let mainWindow = null;
let miniWindow = null;
let miniWorkflowWindow = null;
let bridge = null;
let bridgeReady = false;
const pendingCommands = [];

// Shared sizing for both mini widgets -- both anchor to the exact same
// screen corner (sw-MINI_MARGIN, sh-MINI_MARGIN), each just at its own
// height. Centralized here after two real bugs (both widgets' info-pills
// getting silently squeezed by flexbox, twice) came from the window
// height and its matching setPosition() math being hand-duplicated in
// separate places and drifting out of sync -- one set of numbers used
// everywhere (createMiniWindow, createMiniWorkflowWindow,
// switchMiniWidget) instead.
const MINI_WIDTH = 92;
const MINI_MARGIN = 20;
const MINI_RECORD_HEIGHT = 320;     // 4 circles + two-line frames/sessions pill
// Was 300 (one-line pill); bumped to 320 when the pill became two lines
// (task name + model/step, 2026-08-19) -- same real content math as
// MINI_RECORD_HEIGHT now that both pills are the same shape.
const MINI_WORKFLOW_HEIGHT = 320;   // 4 circles + two-line task/model pill

// Which main-window section was last active ("home" = Recorder,
// "workflows" = Workflows) -- decides which mini overlay minimizing
// shows. Updated by the renderer via setActiveSection() every time the
// user switches tabs, so this stays correct even if they never minimize
// right after switching.
let activeSection = "home";

// The model_path currently loaded in the main window's Play panel, and
// whether a capsule run is currently in flight -- mirrored here so the
// mini Play/Stop widget (a separate, tiny window with no capsule-picker
// UI of its own) can act on "whatever's loaded" and show correct
// enabled/running state immediately on open, without round-tripping
// through the main renderer for every click.
let currentCapsuleName = null;
let capsuleIsRunning = false;
// Mirrors the recorder's own "started"/"saved"/"error" events, same
// purpose as capsuleIsRunning above but for the Recorder-tab mini widget:
// lets a freshly (re)opened mini.html know immediately whether a
// recording is already in progress, instead of only finding out on the
// next live event.
let recorderIsRecording = false;

let localServerProcess = null;

function ensureLocalServerRunning(scriptPath) {
  if (localServerProcess && localServerProcess.exitCode === null) {
    return; // already running
  }
  const pythonExe = resolvePython();
  const fullPath = path.join(REPO_ROOT, scriptPath);
  localServerProcess = spawn(pythonExe, [fullPath], {
    cwd: REPO_ROOT, detached: true, stdio: "ignore", windowsHide: true,
  });
  localServerProcess.unref();
}

function startBridge() {
  const pythonExe = resolvePython();
  bridge = spawn(pythonExe, [BRIDGE_SCRIPT], {
    cwd: REPO_ROOT,
    windowsHide: true,
  });

  const rl = readline.createInterface({ input: bridge.stdout });
  rl.on("line", (line) => {
    line = line.trim();
    if (!line) return;
    let event;
    try {
      event = JSON.parse(line);
    } catch (e) {
      console.error("Bridge sent non-JSON line:", line);
      return;
    }
    if (event.event === "ready") {
      bridgeReady = true;
      for (const cmd of pendingCommands.splice(0)) sendCommand(cmd);
    }
    broadcast("recorder-event", event);
  });

  bridge.stderr.on("data", (data) => {
    console.error("[bridge stderr]", data.toString());
  });

  bridge.on("exit", (code) => {
    console.log("Bridge process exited with code", code);
    bridgeReady = false;
    broadcast("recorder-event", {
      event: "log",
      message: `Backend process exited (code ${code}).`,
      level: "err",
    });
  });
}

// Recorder state lives in the main process (via the bridge) — both the main
// window and the mini widget (when minimized) are just views onto it, so
// every recorder event goes to whichever of them currently exists.
function broadcast(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
  if (miniWindow && !miniWindow.isDestroyed()) miniWindow.webContents.send(channel, payload);
  if (miniWorkflowWindow && !miniWorkflowWindow.isDestroyed()) {
    miniWorkflowWindow.webContents.send(channel, payload);
  }
  if (payload && (payload.event === "capsule_started")) capsuleIsRunning = true;
  if (payload && (payload.event === "capsule_done" || payload.event === "capsule_stopped")) {
    capsuleIsRunning = false;
  }
  if (payload && payload.event === "started") recorderIsRecording = true;
  if (payload && (payload.event === "saved" || payload.event === "error")) {
    recorderIsRecording = false;
  }
}

function sendCommand(cmd) {
  if (!bridge || bridge.killed) return;
  bridge.stdin.write(JSON.stringify(cmd) + "\n");
}

function queueOrSend(cmd) {
  if (bridgeReady) sendCommand(cmd);
  else pendingCommands.push(cmd);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    title: "Intern",
    width: 1040,
    height: 720,
    minWidth: 760,
    minHeight: 560,
    backgroundColor: "#F7F5F0",
    // Native min/max/close buttons stay native, but the strip they sit in
    // (and its full width) paints ink instead of the default OS chrome.
    titleBarStyle: "hidden",
    titleBarOverlay: {
      color: "#16150F",
      symbolColor: "#FFFFFF",
      height: 34,
    },
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));

  // Minimizing the main window doesn't stop a recording (or a capsule
  // run) — swap in a small always-on-top widget so the relevant controls
  // stay reachable without the full window open. Which widget depends on
  // which section was showing: Recorder -> the Start/Stop status card
  // (unchanged); Workflows -> the round Play/Stop widget.
  mainWindow.on("minimize", () => {
    // Direct request: smooth switching between the two mini widgets.
    // Found live: switchMiniWidget() created whichever widget hadn't been
    // needed yet from scratch -- a brand-new BrowserWindow means spinning
    // up a whole Chromium renderer process and loading/parsing the HTML,
    // CSS, and fonts, which measured as a real multi-second gap where the
    // (transparent) window shows nothing at all. Both widgets are cheap
    // and small -- pre-creating both here, hidden, the first time the
    // user ever minimizes means every later switchMiniWidget() call is
    // just hide()/show() on already-loaded windows, no creation cost.
    if (!miniWindow || miniWindow.isDestroyed()) createMiniWindow();
    if (!miniWorkflowWindow || miniWorkflowWindow.isDestroyed()) createMiniWorkflowWindow();
    if (activeSection === "workflows") {
      miniWindow.hide();
      miniWorkflowWindow.show();
    } else {
      miniWorkflowWindow.hide();
      miniWindow.show();
    }
  });
  mainWindow.on("restore", () => {
    if (miniWindow && !miniWindow.isDestroyed()) miniWindow.hide();
    if (miniWorkflowWindow && !miniWorkflowWindow.isDestroyed()) miniWorkflowWindow.hide();
  });
}

// Redesigned 2026-08-18 (uiux_record_overlay_redesign) to match the
// Workflows-tab sibling's transparent floating-circle shape exactly --
// same transparent/hasShadow/frame config, same 92px width. Height
// computed upfront this time (learned live from the sibling's own
// squeeze bug, not rediscovered a third time): padding(24) + 4 circles
// (handle/switch/stop/record, 52px each = 208) + 3 gaps (12px = 36) +
// the two-line frames/sessions pill (~40) = 308, rounded up to 320 for
// headroom.
function createMiniWindow() {
  miniWindow = new BrowserWindow({
    title: "Intern — mini",
    width: MINI_WIDTH,
    height: MINI_RECORD_HEIGHT,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    frame: false,
    transparent: true,
    hasShadow: false,
    backgroundColor: "#00000000",
    // Created eagerly now (see mainWindow's "minimize" handler) even when
    // this isn't the widget the user is about to see -- show:false keeps
    // the default "auto-show once ready-to-show" behavior from flashing
    // it onscreen before the minimize handler's own explicit hide()/
    // show() calls decide which widget is actually visible.
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  miniWindow.loadFile(path.join(__dirname, "renderer", "mini.html"));

  const { width: sw, height: sh } = require("electron").screen.getPrimaryDisplay().workAreaSize;
  miniWindow.setPosition(sw - MINI_WIDTH - MINI_MARGIN, sh - MINI_RECORD_HEIGHT - MINI_MARGIN);

  // A recording may already be in progress from before this widget ever
  // opened -- same real gap the Workflows-tab sibling's own review caught
  // and fixed for its capsule state, applied here too rather than left
  // as a fresh instance of the identical gap. Reuses the existing
  // recorder-event wire format/channel, no new IPC needed.
  miniWindow.webContents.once("did-finish-load", () => {
    if (recorderIsRecording) miniWindow.webContents.send("recorder-event", { event: "started" });
  });

  miniWindow.on("closed", () => { miniWindow = null; });
}

// The Workflows-tab mini widget: no card, no border, no frame -- just two
// round buttons floating over the desktop. transparent:true is what makes
// that possible (frame:false alone still paints an opaque rectangle);
// hasShadow:false keeps Windows/macOS from drawing a drop-shadow rectangle
// behind the transparent area, which would otherwise look like a faint
// ghost square around the circles.
//
// Known, accepted tradeoff: Electron transparent windows still capture
// mouse events across their FULL rectangle by default, not just the
// visibly-drawn circles -- the empty space around them in this small
// 92x216 window is technically click-blocking too, not truly
// click-through the way ghost_overlay.py's ill-fated Python overlay
// tried and failed to be (see DEVELOPERS.md's execution_ghost_cursor_
// disabled_click_swallowing for that whole saga). Not fixed here:
// unlike that overlay, this widget is small, stays in one corner, and is
// never drawn on top of whatever the user is actually trying to click --
// a real but much lower-stakes version of the same category of issue.
// setIgnoreMouseEvents() with per-pixel forwarding would close this gap
// if it ever turns out to matter in practice.
function createMiniWorkflowWindow() {
  miniWorkflowWindow = new BrowserWindow({
    title: "Intern — mini",
    width: MINI_WIDTH,
    height: MINI_WORKFLOW_HEIGHT,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    frame: false,
    transparent: true,
    hasShadow: false,
    backgroundColor: "#00000000",
    // Same reasoning as the Record sibling's show:false above -- this
    // widget is now also created eagerly on first minimize, before we
    // necessarily know it's the one to display.
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  miniWorkflowWindow.loadFile(path.join(__dirname, "renderer", "mini-workflow.html"));

  const { width: sw, height: sh } = require("electron").screen.getPrimaryDisplay().workAreaSize;
  miniWorkflowWindow.setPosition(sw - MINI_WIDTH - MINI_MARGIN, sh - MINI_WORKFLOW_HEIGHT - MINI_MARGIN);

  // Push whatever state already exists (a capsule may already be loaded
  // and/or running from before this widget was ever created) so it opens
  // showing correct enabled/running state immediately, not "nothing
  // loaded" until the next live event happens to fire.
  miniWorkflowWindow.webContents.once("did-finish-load", pushMiniWorkflowState);

  miniWorkflowWindow.on("closed", () => { miniWorkflowWindow = null; });
}

// Direct request: "Find a way to navigate between the two widgets (Record
// and Workflow)". Before this, which widget showed was decided ONCE, by
// activeSection, at the moment you minimized -- no way to see the other
// one without restoring the main window, switching tabs, and minimizing
// again. This lets either mini widget hand off to its sibling directly.
//
// Deliberately does NOT copy one window's live position onto the other --
// the two windows are different heights (270 vs 250) with content packed
// toward the TOP of each (confirmed live while verifying the info-pill
// sizing fix above), so a naive position copy would visually shift the
// handle circle by however much the heights differ. Both windows are
// already independently anchored to the exact same screen corner
// (sw-112,sh-290 / sw-112,sh-270 both resolve to the same sw-20,sh-20
// bottom-right point) by their own creation functions -- reusing each
// one's own real formula here keeps that anchor exact, rather than
// re-deriving it with a fragile offset.
function switchMiniWidget() {
  const { width: sw, height: sh } = require("electron").screen.getPrimaryDisplay().workAreaSize;
  if (miniWindow && !miniWindow.isDestroyed() && miniWindow.isVisible()) {
    miniWindow.hide();
    if (!miniWorkflowWindow || miniWorkflowWindow.isDestroyed()) createMiniWorkflowWindow();
    miniWorkflowWindow.setPosition(sw - MINI_WIDTH - MINI_MARGIN, sh - MINI_WORKFLOW_HEIGHT - MINI_MARGIN);
    miniWorkflowWindow.show();
  } else if (miniWorkflowWindow && !miniWorkflowWindow.isDestroyed() && miniWorkflowWindow.isVisible()) {
    miniWorkflowWindow.hide();
    if (!miniWindow || miniWindow.isDestroyed()) createMiniWindow();
    miniWindow.setPosition(sw - MINI_WIDTH - MINI_MARGIN, sh - MINI_RECORD_HEIGHT - MINI_MARGIN);
    miniWindow.show();
  }
}
ipcMain.handle("switch-mini-widget", () => switchMiniWidget());

// Re-sends the widget's non-running state (which capsule is loaded) --
// called on creation AND every time capsule-set-current changes, since
// the widget can stay open (minimized, just hidden) across the user
// loading a different workflow without ever being recreated. Running
// state doesn't need this: it's already kept live via the same
// recorder-event broadcast the widget listens to directly.
function pushMiniWorkflowState() {
  if (!miniWorkflowWindow || miniWorkflowWindow.isDestroyed()) return;
  // Direct request: "what Workflow were using, and what model." The
  // deployed model_path (what actually runs on Play, not just whatever
  // the main window's Checkpoint dropdown happens to have selected but
  // not yet deployed) already lives in the registry entry -- readRegistry()
  // is a plain, cheap disk read (same precedent as listCapsules()
  // elsewhere), no bridge round-trip needed just to look this up.
  // Script-kind capsules (e.g. Scope #2) carry no model_path at all --
  // modelName stays null for those, and the widget shows nothing for
  // that line rather than a misleading blank/empty string.
  const capsule = listCapsules().find((c) => c.name === currentCapsuleName);
  const modelName = capsule && capsule.model_path
    ? path.basename(capsule.model_path)
    : null;
  miniWorkflowWindow.webContents.send("mini-workflow-state", {
    hasCapsule: !!currentCapsuleName,
    isRunning: capsuleIsRunning,
    capsuleName: currentCapsuleName,
    modelName,
  });
}

// Reads data/demos/<group>/session_*/ directly off disk — a static listing,
// no need to round-trip through the Python bridge for this. Empty groups
// (just created via createWorkflow(), no sessions recorded yet) are still
// included -- they need to show up in the Recorder's Save-to dropdown and
// as a placeholder card in the Workflows list.
function listWorkflows() {
  const groups = [];
  if (!fs.existsSync(DEMOS_ROOT)) return groups;

  for (const groupName of fs.readdirSync(DEMOS_ROOT)) {
    const groupPath = path.join(DEMOS_ROOT, groupName);
    if (!fs.statSync(groupPath).isDirectory()) continue;

    const sessions = [];
    for (const sessionName of fs.readdirSync(groupPath)) {
      if (!sessionName.startsWith("session_")) continue;
      const sessionPath = path.join(groupPath, sessionName);
      if (!fs.statSync(sessionPath).isDirectory()) continue;

      const files = fs.readdirSync(sessionPath)
        .filter((f) => f.endsWith(".json") && f !== "session_manifest.json");
      const stat = fs.statSync(sessionPath);
      sessions.push({
        name: sessionName,
        steps: files.length,
        mtime: stat.mtimeMs,
      });
    }
    sessions.sort((a, b) => b.mtime - a.mtime);

    groups.push({
      name: groupName,
      totalSteps: sessions.reduce((a, s) => a + s.steps, 0),
      sessionCount: sessions.length,
      sessions,
      // Falls back to the folder's own mtime for a fresh, still-empty
      // group -- there's no session to read a timestamp from yet.
      mtime: sessions.length ? sessions[0].mtime : fs.statSync(groupPath).mtimeMs,
    });
  }
  groups.sort((a, b) => b.mtime - a.mtime);
  return groups;
}

// Reserves a new, empty workflow group -- just a folder under data/demos/,
// nothing recorded into it yet. Deliberately does NOT touch registry.json:
// a brand new workflow has no trained checkpoint, so there's nothing real
// to register as a capsule until the user has actually recorded sessions
// and trained/registered a model for it.
function createWorkflow(name) {
  const trimmed = (name || "").trim();
  if (!trimmed) throw new Error("Workflow name can't be empty.");
  if (!/^[A-Za-z0-9 _-]+$/.test(trimmed)) {
    throw new Error("Use only letters, numbers, spaces, - and _.");
  }
  const safe = trimmed.replace(/\s+/g, "_");
  const dir = path.join(DEMOS_ROOT, safe);
  if (fs.existsSync(dir)) throw new Error(`'${safe}' already exists.`);
  fs.mkdirSync(dir, { recursive: true });
  return { name: safe };
}

// ── Capsules -- components/agent/capsule.py's WorkflowCapsule/CapsuleRegistry,
// read/written directly as JSON here (same "no bridge round-trip for plain
// disk reads" precedent as listWorkflows() above). One capsule = one named
// task + the model checkpoint currently deployed for it, e.g. "form_filling".
function readRegistry() {
  if (!fs.existsSync(REGISTRY_PATH)) return { capsules: [] };
  try {
    return JSON.parse(fs.readFileSync(REGISTRY_PATH, "utf8"));
  } catch (e) {
    console.error("Failed to parse registry.json:", e);
    return { capsules: [] };
  }
}

function listCapsules() {
  return readRegistry().capsules || [];
}

// Checkpoints living alongside a capsule's current model_path (same
// directory, e.g. tasks/form_filling/*.pt) -- past training runs this
// project already keeps as manually-made backups, not a formal version
// registry. "Deploy" (below) is what promotes one of these to be the
// capsule's active model_path.
function listCheckpoints(capsuleName) {
  const capsule = listCapsules().find((c) => c.name === capsuleName);
  if (!capsule) return [];
  const dir = path.dirname(path.join(REPO_ROOT, capsule.model_path));
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir)
    .filter((f) => f.endsWith(".pt"))
    .map((f) => {
      const full = path.join(dir, f);
      const stat = fs.statSync(full);
      const rel = path.relative(REPO_ROOT, full).split(path.sep).join("/");
      return { name: f, path: rel, mtime: stat.mtimeMs, size: stat.size };
    })
    .sort((a, b) => b.mtime - a.mtime);
}

function deployCheckpoint(capsuleName, checkpointPath) {
  const registry = readRegistry();
  const capsule = (registry.capsules || []).find((c) => c.name === capsuleName);
  if (!capsule) throw new Error(`Capsule not found: ${capsuleName}`);
  capsule.model_path = checkpointPath;
  fs.writeFileSync(REGISTRY_PATH, JSON.stringify(registry, null, 2));
  return capsule;
}

// ── Task CRUD (Tasks page) -- a "task" IS a capsule registry entry. Create
// also reserves the matching data/demos/<name> recording folder in the
// same step, so the Recorder's Save-to dropdown (which now lists real
// capsules, not a raw folder scan -- see populateOutDirOptions() in the
// renderer) always points at something that actually shows up as a task.
function createTask(name, description) {
  const trimmed = (name || "").trim();
  if (!trimmed) throw new Error("Task name can't be empty.");
  if (!/^[A-Za-z0-9 _-]+$/.test(trimmed)) {
    throw new Error("Use only letters, numbers, spaces, - and _.");
  }
  const safe = trimmed.replace(/\s+/g, "_");
  const registry = readRegistry();
  registry.capsules = registry.capsules || [];
  if (registry.capsules.some((c) => c.name === safe)) {
    throw new Error(`A task named '${safe}' already exists.`);
  }
  const dir = path.join(DEMOS_ROOT, safe);
  if (fs.existsSync(dir)) throw new Error(`'${safe}' already exists.`);
  fs.mkdirSync(dir, { recursive: true });

  const capsule = {
    name: safe,
    description: (description || "").trim(),
    model_path: "",
    trigger_keywords: [],
    trigger_apps: [],
    trace_dir: "",
    created: new Date().toISOString(),
    emoji: "",
  };
  registry.capsules.push(capsule);
  fs.writeFileSync(REGISTRY_PATH, JSON.stringify(registry, null, 2));
  return capsule;
}

// Whitelisted patch -- only fields a person should hand-edit from the
// Tasks page. model_path/kind/entrypoint/args/cwd stay off limits here;
// those come from training/registration, not this edit panel.
function updateCapsule(name, updates) {
  const registry = readRegistry();
  const capsule = (registry.capsules || []).find((c) => c.name === name);
  if (!capsule) throw new Error(`Task not found: ${name}`);
  if (typeof updates.description === "string") capsule.description = updates.description.trim();
  if (typeof updates.emoji === "string") capsule.emoji = updates.emoji.trim();
  if (Array.isArray(updates.trigger_keywords)) capsule.trigger_keywords = updates.trigger_keywords;
  if (Array.isArray(updates.trigger_apps)) capsule.trigger_apps = updates.trigger_apps;
  fs.writeFileSync(REGISTRY_PATH, JSON.stringify(registry, null, 2));
  return capsule;
}

// Registry-entry only, a direct decision: data/demos/<name> recordings
// and any trained .pt checkpoint are left on disk untouched. Deleting a
// task from the list must never be able to destroy recorded work or a
// trained model as a side effect.
function deleteCapsule(name) {
  const registry = readRegistry();
  const before = (registry.capsules || []).length;
  registry.capsules = (registry.capsules || []).filter((c) => c.name !== name);
  if (registry.capsules.length === before) throw new Error(`Task not found: ${name}`);
  fs.writeFileSync(REGISTRY_PATH, JSON.stringify(registry, null, 2));
  return { ok: true };
}

// "" clears back to the placeholder -- capsule.py's WorkflowCapsule.emoji
// field defaults to "" too, so an empty string round-trips cleanly either
// direction (this app's JSON edit vs. a real Python-side registration).
function setCapsuleEmoji(capsuleName, emoji) {
  const registry = readRegistry();
  const capsule = (registry.capsules || []).find((c) => c.name === capsuleName);
  if (!capsule) throw new Error(`Capsule not found: ${capsuleName}`);
  capsule.emoji = (emoji || "").trim();
  fs.writeFileSync(REGISTRY_PATH, JSON.stringify(registry, null, 2));
  return capsule;
}

// ── Inbox Router (Scope #3) -- routed_history.json is owned entirely by
// components/inbox_router/router.py; read directly here exactly like
// readRegistry() above, never written from this process.
function readInboxHistory() {
  if (!fs.existsSync(INBOX_HISTORY_PATH)) return { messages: [] };
  try {
    return JSON.parse(fs.readFileSync(INBOX_HISTORY_PATH, "utf8"));
  } catch (e) {
    console.error("Failed to parse routed_history.json:", e);
    return { messages: [] };
  }
}
function listInboxMessages() {
  return readInboxHistory().messages || [];
}

app.whenReady().then(() => {
  startBridge();
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  queueOrSend({ cmd: "shutdown" });
  if (bridge && !bridge.killed) {
    setTimeout(() => { if (!bridge.killed) bridge.kill(); }, 1500);
  }
  if (process.platform !== "darwin") app.quit();
});

ipcMain.handle("recorder-start", (_evt, outputDir) => {
  const capsule = currentCapsuleName
    ? listCapsules().find((c) => c.name === currentCapsuleName)
    : null;
  const isWebCapsule = !!(capsule && capsule.url);
  queueOrSend({
    cmd: "start", output_dir: outputDir || null,
    trace_type: isWebCapsule ? "web" : "form_filling",
    url: isWebCapsule ? capsule.url : "",
  });
});
ipcMain.handle("recorder-stop", () => {
  queueOrSend({ cmd: "stop" });
});
ipcMain.handle("recorder-replay", (_evt, n) => {
  queueOrSend({ cmd: "replay", n: n || 10 });
});
ipcMain.handle("workflows-list", () => listWorkflows());
ipcMain.handle("workflows-create", (_evt, name) => createWorkflow(name));
ipcMain.handle("capsules-list", () => listCapsules());
ipcMain.handle("capsules-checkpoints", (_evt, capsuleName) => listCheckpoints(capsuleName));
ipcMain.handle("capsules-deploy", (_evt, capsuleName, checkpointPath) =>
  deployCheckpoint(capsuleName, checkpointPath));
ipcMain.handle("capsules-set-emoji", (_evt, capsuleName, emoji) =>
  setCapsuleEmoji(capsuleName, emoji));
ipcMain.handle("capsules-create", (_evt, name, description) => createTask(name, description));
ipcMain.handle("capsules-update", (_evt, name, updates) => updateCapsule(name, updates));
ipcMain.handle("capsules-delete", (_evt, name) => deleteCapsule(name));
ipcMain.handle("capsule-run", (_evt, capsuleName) => {
  // kind="url" isn't a subprocess at all -- there's nothing for
  // recorder_bridge.py/Python to run, so this short-circuits before ever
  // reaching queueOrSend(). Scope #3's Inbox Dispatch page is deliberately
  // built OUTSIDE the Electron app (direct request), so "Play" for it just
  // opens the real browser to that page -- ensuring its local server is
  // running first, when the capsule declares one.
  const capsule = listCapsules().find((c) => c.name === capsuleName);
  if (capsule && capsule.kind === "url") {
    if (capsule.local_server) ensureLocalServerRunning(capsule.local_server);
    if (capsule.url) shell.openExternal(capsule.url);
    return { opened: true };
  }
  queueOrSend({ cmd: "run_capsule", capsule_name: capsuleName });
  return { opened: false };
});
ipcMain.handle("capsule-stop", () => {
  queueOrSend({ cmd: "stop_capsule" });
});
ipcMain.handle("capsule-set-current", (_evt, capsuleName) => {
  currentCapsuleName = capsuleName || null;
  pushMiniWorkflowState();
});
// "Test" section (Play panel, below Checkpoint) -- lets the user open the
// real target apps for a workflow themselves, before pressing Play, e.g.
// to confirm the environment is ready or just to look around. Keyed by
// capsule name, one source of truth here rather than duplicated in the
// renderer. For form_filling (Scope #1) this opens the exact two apps
// run_task.py's own countdown asks the user to already have open --
// SOURCE_WINDOW's "Notepad" match and the wx Car Insurance form
// (registry.json's trigger_apps: "Car Insurance"). For the
// Sheet-to-Portal Matcher (Scope #2) there's no real screen-automation
// target to "get ready" the way Scope #1 has -- automate.py opens its own
// playwright browser itself -- so this opens the source spreadsheet and
// the mock portal's own landing page instead, purely for the user to look
// at, same source+target pairing as Scope #1's just for inspection.
const TEST_MOCKUPS = {
  form_filling: [
    { type: "python", script: path.join(REPO_ROOT, "practice_apps", "car_insurance_entry", "car_insurance_form_wx.py") },
    { type: "notepad", target: path.join(REPO_ROOT, "data_entry_tasks", "data_entry_intake.txt") },
  ],
  "Sheet-to-Portal Matcher": [
    { type: "open", target: path.join(REPO_ROOT, "components", "scope2", "data", "sheets", "grade_sheet.xlsx") },
    { type: "open", target: path.join(REPO_ROOT, "practice_apps", "mocksite", "index.html") },
  ],
};

ipcMain.handle("test-launch-mockups", (_evt, capsuleName) => {
  // Inbox Dispatch's practice target isn't a {type, script/target} pair
  // like form_filling/Sheet-to-Portal Matcher's real, separate apps below
  // -- it's a page on the SAME local server Play's own automate_inbox.py
  // run already starts (see ensureLocalServerRunning), just a different
  // URL path. Checked by `local_server` alone, not `kind === "url"` --
  // Inbox Dispatch's kind is "script" (Play actually clicks through the
  // page now), but it still carries `url`/`local_server` purely for this
  // button.
  const capsule = listCapsules().find((c) => c.name === capsuleName);
  if (capsule && capsule.local_server && capsule.url) {
    ensureLocalServerRunning(capsule.local_server);
    shell.openExternal(`${capsule.url}practice/`);
    return { ok: true, opened: ["practice inbox"] };
  }

  const targets = TEST_MOCKUPS[capsuleName];
  if (!targets) {
    return { ok: false, error: `No test mockups defined for '${capsuleName}'.` };
  }
  const opened = [];
  try {
    for (const t of targets) {
      if (t.type === "python") {
        const pythonExe = resolvePython();
        const child = spawn(pythonExe, [t.script], {
          cwd: REPO_ROOT, detached: true, stdio: "ignore", windowsHide: false,
        });
        child.unref();
        opened.push(path.basename(t.script));
      } else if (t.type === "notepad") {
        // Explicit notepad.exe, not shell.openPath()'s default-app
        // association -- run_task.py's SOURCE_WINDOW match requires the
        // literal "Notepad" window title, which a different default .txt
        // handler wouldn't produce.
        const child = spawn("notepad.exe", [t.target], { detached: true, stdio: "ignore" });
        child.unref();
        opened.push(path.basename(t.target));
      } else {
        shell.openPath(t.target);
        opened.push(path.basename(t.target));
      }
    }
  } catch (e) {
    return { ok: false, error: `Failed to launch mockups: ${e.message}` };
  }
  return { ok: true, opened };
});

// ── Settings tab -- LM Studio control via its own CLI (lms.exe), not a
// new HTTP client of our own -- the exact same local server run_task.py's
// LLMAgent already talks to at http://localhost:1234/v1 once it's running
// with a model loaded. This tab exists so getting that server into a
// working state doesn't require a separate terminal.
ipcMain.handle("settings-lmstudio-refresh", async () => {
  const [status, models, loaded] = await Promise.all([
    runLmsCli(["server", "status"]),
    runLmsCli(["ls", "--llm", "--json"]),
    runLmsCli(["ps", "--json"]),
  ]);
  // "server status" has no --json form -- its stdout is a short, stable
  // sentence ("The server is running on port 1234." / a not-running
  // message), so this checks for the one substring that actually matters
  // rather than parsing free text further.
  const serverRunning = status.ok && /running/i.test(status.stdout);

  let modelList = [];
  try {
    modelList = models.ok ? JSON.parse(models.stdout) : [];
  } catch (e) {
    modelList = [];
  }
  let loadedModels = [];
  try {
    loadedModels = loaded.ok ? JSON.parse(loaded.stdout) : [];
  } catch (e) {
    loadedModels = [];
  }

  return {
    serverRunning,
    serverMessage: status.stdout || status.stderr || "Couldn't reach lms CLI.",
    models: modelList.map((m) => ({ modelKey: m.modelKey, displayName: m.displayName || m.modelKey })),
    loadedModelKeys: loadedModels.map((m) => m.modelKey || m.identifier).filter(Boolean),
  };
});

ipcMain.handle("settings-lmstudio-start-server", async () => {
  const result = await runLmsCli(["server", "start"]);
  if (!result.ok) {
    return { ok: false, error: result.stderr || result.stdout || "Failed to start the LM Studio server." };
  }
  return { ok: true };
});

ipcMain.handle("settings-lmstudio-load-model", async (_evt, modelKey) => {
  if (!modelKey) return { ok: false, error: "No model selected." };
  // -y: non-interactive (the CLI otherwise prompts if the key matches
  // more than one variant) -- loading itself can take a while for a
  // multi-GB model, hence the longer timeout than the other lms calls.
  const result = await runLmsCli(["load", modelKey, "-y"], 120000);
  if (!result.ok) {
    return { ok: false, error: result.stderr || result.stdout || `Failed to load ${modelKey}.` };
  }
  return { ok: true, message: result.stdout };
});

// ── Settings tab -- API keys for the non-local LLM providers, written to
// the repo-root .env file in the exact plain KEY=value shape run_task.py's
// own hand-rolled loader already reads (no python-dotenv anywhere in this
// project, so this writer matches that convention rather than introducing
// a different one). Never echoes a saved key back to the renderer -- only
// whether one is set and a masked last-4-chars preview, so a real secret
// is never round-tripped through IPC/devtools once saved.
const ENV_PATH = path.join(REPO_ROOT, ".env");
const API_KEY_ENV_NAMES = {
  anthropic: "ANTHROPIC_API_KEY",
  groq: "GROQ_API_KEY",
  gemini: "GEMINI_API_KEY",
};

function readEnvFile() {
  if (!fs.existsSync(ENV_PATH)) return {};
  const values = {};
  for (const line of fs.readFileSync(ENV_PATH, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const idx = trimmed.indexOf("=");
    values[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
  }
  return values;
}

// Updates one key in place if the file already sets it, otherwise appends
// it -- every other line (including ones this app knows nothing about) is
// left exactly as it was, never a full-file overwrite from a parsed model.
function writeEnvValue(key, value) {
  let lines = fs.existsSync(ENV_PATH) ? fs.readFileSync(ENV_PATH, "utf8").split(/\r?\n/) : [];
  let found = false;
  lines = lines.map((line) => {
    const trimmed = line.trim();
    if (trimmed && !trimmed.startsWith("#") && trimmed.includes("=")) {
      const idx = trimmed.indexOf("=");
      if (trimmed.slice(0, idx).trim() === key) {
        found = true;
        return `${key}=${value}`;
      }
    }
    return line;
  });
  if (!found) lines.push(`${key}=${value}`);
  while (lines.length && lines[lines.length - 1].trim() === "") lines.pop();
  fs.writeFileSync(ENV_PATH, lines.join("\n") + "\n");
}

ipcMain.handle("settings-get-api-key-status", (_evt, provider) => {
  const envKey = API_KEY_ENV_NAMES[provider];
  if (!envKey) return { ok: false, error: `Unknown provider: ${provider}` };
  // Falls back to the live process env too -- a key set as a real OS/shell
  // environment variable (not through this UI) should still read as "set",
  // not falsely empty.
  const raw = readEnvFile()[envKey] || process.env[envKey] || "";
  return { ok: true, isSet: !!raw, masked: raw ? `••••${raw.slice(-4)}` : "" };
});

ipcMain.handle("settings-save-api-key", (_evt, provider, apiKey) => {
  const envKey = API_KEY_ENV_NAMES[provider];
  if (!envKey) return { ok: false, error: `Unknown provider: ${provider}` };
  const trimmed = (apiKey || "").trim();
  if (!trimmed) return { ok: false, error: "Enter a key first." };
  try {
    writeEnvValue(envKey, trimmed);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: `Couldn't save: ${e.message}` };
  }
});

// The mini Play/Stop widget has no capsule-picker UI of its own -- Play
// always means "run whatever's currently loaded in the main window's
// Play panel," tracked via capsule-set-current above.
ipcMain.handle("capsule-run-current", () => {
  if (!currentCapsuleName) return;
  const capsule = listCapsules().find((c) => c.name === currentCapsuleName);
  if (capsule && capsule.kind === "url") {
    if (capsule.local_server) ensureLocalServerRunning(capsule.local_server);
    if (capsule.url) shell.openExternal(capsule.url);
    return;
  }
  queueOrSend({ cmd: "run_capsule", capsule_name: currentCapsuleName });
});
ipcMain.handle("set-active-section", (_evt, section) => {
  activeSection = section === "workflows" ? "workflows" : "home";
});
// "Open log file" / "Copy log" -- direct user request after a run that
// looked totally silent in the Play panel even though it was genuinely
// running: a way to actually see the full transcript, not just whatever
// still fits in the small scrolling Activity box.
ipcMain.handle("capsule-open-log", () => {
  if (!fs.existsSync(CAPSULE_LOG_PATH)) {
    return { ok: false, error: "No capsule log yet -- run a capsule first." };
  }
  shell.openPath(CAPSULE_LOG_PATH);
  return { ok: true };
});
ipcMain.handle("capsule-read-log", () => {
  if (!fs.existsSync(CAPSULE_LOG_PATH)) return "";
  try {
    return fs.readFileSync(CAPSULE_LOG_PATH, "utf8");
  } catch (e) {
    return "";
  }
});
ipcMain.handle("inbox-start", () => {
  queueOrSend({ cmd: "start_inbox_router" });
});
ipcMain.handle("inbox-stop", () => {
  queueOrSend({ cmd: "stop_inbox_router" });
});
ipcMain.handle("inbox-list", () => listInboxMessages());
ipcMain.handle("inbox-confirm", (_evt, messageId, decision, replyBody) => {
  queueOrSend({
    cmd: "inbox_confirm_suggestion", message_id: messageId, decision,
    reply_body: replyBody || "",
  });
});
ipcMain.handle("inbox-override", (_evt, messageId, newDecision, reason, replyBody) => {
  queueOrSend({
    cmd: "inbox_override_decision", message_id: messageId,
    new_decision: newDecision, reason: reason || "", reply_body: replyBody || "",
  });
});
ipcMain.handle("inbox-open-log", () => {
  if (!fs.existsSync(INBOX_LOG_PATH)) {
    return { ok: false, error: "No Inbox Router log yet -- start it first." };
  }
  shell.openPath(INBOX_LOG_PATH);
  return { ok: true };
});
ipcMain.handle("restore-main", () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.restore();
    mainWindow.focus();
  }
  if (miniWindow && !miniWindow.isDestroyed()) miniWindow.hide();
  if (miniWorkflowWindow && !miniWorkflowWindow.isDestroyed()) miniWorkflowWindow.hide();
});
