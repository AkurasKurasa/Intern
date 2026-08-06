const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const readline = require("readline");

const REPO_ROOT = path.join(__dirname, "..");
const BRIDGE_SCRIPT = path.join(REPO_ROOT, "app", "recorder_bridge.py");

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
    if (mainWindow) mainWindow.webContents.send("recorder-event", event);
  });

  bridge.stderr.on("data", (data) => {
    console.error("[bridge stderr]", data.toString());
  });

  bridge.on("exit", (code) => {
    console.log("Bridge process exited with code", code);
    bridgeReady = false;
    if (mainWindow) {
      mainWindow.webContents.send("recorder-event", {
        event: "log",
        message: `Backend process exited (code ${code}).`,
        level: "err",
      });
    }
  });
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
    width: 900,
    height: 680,
    minWidth: 700,
    minHeight: 520,
    backgroundColor: "#FFFFFF",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
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
