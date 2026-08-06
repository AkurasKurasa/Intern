const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const readline = require("readline");

const REPO_ROOT = path.join(__dirname, "..");
const BRIDGE_SCRIPT = path.join(REPO_ROOT, "app", "recorder_bridge.py");
const DEMOS_ROOT = path.join(REPO_ROOT, "data", "demos");

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
// no need to round-trip through the Python bridge for this.
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

    if (sessions.length) {
      groups.push({
        name: groupName,
        totalSteps: sessions.reduce((a, s) => a + s.steps, 0),
        sessionCount: sessions.length,
        sessions,
      });
    }
  }
  groups.sort((a, b) => b.sessions[0].mtime - a.sessions[0].mtime);
  return groups;
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
ipcMain.handle("restore-main", () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.restore();
    mainWindow.focus();
  }
  if (miniWindow && !miniWindow.isDestroyed()) miniWindow.hide();
});
