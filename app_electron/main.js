const { app, BrowserWindow, ipcMain, shell } = require("electron");
const { spawn } = require("child_process");
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

let mainWindow = null;
let miniWindow = null;
let miniWorkflowWindow = null;
let bridge = null;
let bridgeReady = false;
const pendingCommands = [];

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
    if (activeSection === "workflows") {
      if (!miniWorkflowWindow || miniWorkflowWindow.isDestroyed()) createMiniWorkflowWindow();
      miniWorkflowWindow.show();
    } else {
      if (!miniWindow || miniWindow.isDestroyed()) createMiniWindow();
      miniWindow.show();
    }
  });
  mainWindow.on("restore", () => {
    if (miniWindow && !miniWindow.isDestroyed()) miniWindow.hide();
    if (miniWorkflowWindow && !miniWorkflowWindow.isDestroyed()) miniWorkflowWindow.hide();
  });
}

function createMiniWindow() {
  miniWindow = new BrowserWindow({
    title: "Intern — mini",
    width: 240,
    height: 168,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    frame: false,
    backgroundColor: "#F7F5F0",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  miniWindow.loadFile(path.join(__dirname, "renderer", "mini.html"));

  const { width: sw, height: sh } = require("electron").screen.getPrimaryDisplay().workAreaSize;
  miniWindow.setPosition(sw - 260, sh - 188);

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
    width: 92,
    height: 216,
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
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  miniWorkflowWindow.loadFile(path.join(__dirname, "renderer", "mini-workflow.html"));

  const { width: sw, height: sh } = require("electron").screen.getPrimaryDisplay().workAreaSize;
  miniWorkflowWindow.setPosition(sw - 112, sh - 236);

  // Push whatever state already exists (a capsule may already be loaded
  // and/or running from before this widget was ever created) so it opens
  // showing correct enabled/running state immediately, not "nothing
  // loaded" until the next live event happens to fire.
  miniWorkflowWindow.webContents.once("did-finish-load", pushMiniWorkflowState);

  miniWorkflowWindow.on("closed", () => { miniWorkflowWindow = null; });
}

// Re-sends the widget's non-running state (which capsule is loaded) --
// called on creation AND every time capsule-set-current changes, since
// the widget can stay open (minimized, just hidden) across the user
// loading a different workflow without ever being recreated. Running
// state doesn't need this: it's already kept live via the same
// recorder-event broadcast the widget listens to directly.
function pushMiniWorkflowState() {
  if (!miniWorkflowWindow || miniWorkflowWindow.isDestroyed()) return;
  miniWorkflowWindow.webContents.send("mini-workflow-state", {
    hasCapsule: !!currentCapsuleName,
    isRunning: capsuleIsRunning,
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
  queueOrSend({ cmd: "start", output_dir: outputDir || null });
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
ipcMain.handle("capsule-run", (_evt, capsuleName) => {
  queueOrSend({ cmd: "run_capsule", capsule_name: capsuleName });
});
ipcMain.handle("capsule-stop", () => {
  queueOrSend({ cmd: "stop_capsule" });
});
ipcMain.handle("capsule-set-current", (_evt, capsuleName) => {
  currentCapsuleName = capsuleName || null;
  pushMiniWorkflowState();
});
// The mini Play/Stop widget has no capsule-picker UI of its own -- Play
// always means "run whatever's currently loaded in the main window's
// Play panel," tracked via capsule-set-current above.
ipcMain.handle("capsule-run-current", () => {
  if (currentCapsuleName) queueOrSend({ cmd: "run_capsule", capsule_name: currentCapsuleName });
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
ipcMain.handle("restore-main", () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.restore();
    mainWindow.focus();
  }
  if (miniWindow && !miniWindow.isDestroyed()) miniWindow.hide();
  if (miniWorkflowWindow && !miniWorkflowWindow.isDestroyed()) miniWorkflowWindow.hide();
});
