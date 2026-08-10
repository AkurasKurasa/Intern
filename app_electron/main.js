const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const readline = require("readline");

const REPO_ROOT = path.join(__dirname, "..");
const BRIDGE_SCRIPT = path.join(REPO_ROOT, "app", "recorder_bridge.py");
const DEMOS_ROOT = path.join(REPO_ROOT, "data", "demos");
const REGISTRY_PATH = path.join(REPO_ROOT, "tasks", "registry.json");

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
let bridge = null;
let bridgeReady = false;
const pendingCommands = [];

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
    backgroundColor: "#FFFFFF",
    // Native min/max/close buttons stay native, but the strip they sit in
    // (and its full width) paints orange instead of the default OS chrome.
    titleBarStyle: "hidden",
    titleBarOverlay: {
      color: "#F97316",
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

  // Minimizing the main window doesn't stop a recording — swap in a small
  // always-on-top widget so Start/Stop/status stay reachable without the
  // full window open.
  mainWindow.on("minimize", () => {
    if (!miniWindow || miniWindow.isDestroyed()) createMiniWindow();
    miniWindow.show();
  });
  mainWindow.on("restore", () => {
    if (miniWindow && !miniWindow.isDestroyed()) miniWindow.hide();
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
    backgroundColor: "#F6F6F4",
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
ipcMain.handle("capsule-run", (_evt, modelPath) => {
  queueOrSend({ cmd: "run_capsule", model_path: modelPath });
});
ipcMain.handle("capsule-stop", () => {
  queueOrSend({ cmd: "stop_capsule" });
});
ipcMain.handle("restore-main", () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.restore();
    mainWindow.focus();
  }
  if (miniWindow && !miniWindow.isDestroyed()) miniWindow.hide();
});
