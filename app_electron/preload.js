const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("recorderAPI", {
  start: (outputDir) => ipcRenderer.invoke("recorder-start", outputDir),
  stop: () => ipcRenderer.invoke("recorder-stop"),
  replay: (n) => ipcRenderer.invoke("recorder-replay", n),
  restoreMain: () => ipcRenderer.invoke("restore-main"),
  // Which main-window section is currently showing ("home" = Recorder,
  // "workflows" = Workflows) -- lets main.js pick the right mini overlay
  // (recorder Start/Stop vs. the round Play/Stop widget) when the window
  // is minimized, without the mini windows needing any UI of their own to
  // ask the user which mode they want.
  setActiveSection: (section) => ipcRenderer.invoke("set-active-section", section),
  onEvent: (callback) => {
    ipcRenderer.on("recorder-event", (_evt, event) => callback(event));
  },
  // Sent once by main.js right after the mini Play/Stop widget finishes
  // loading, carrying whatever capsule/running state already existed
  // before the widget opened (e.g. minimizing mid-run) -- see
  // createMiniWorkflowWindow()'s did-finish-load handler in main.js.
  onMiniWorkflowState: (callback) => {
    ipcRenderer.on("mini-workflow-state", (_evt, state) => callback(state));
  },
});

contextBridge.exposeInMainWorld("workflowsAPI", {
  list: () => ipcRenderer.invoke("workflows-list"),
  create: (name) => ipcRenderer.invoke("workflows-create", name),
});

contextBridge.exposeInMainWorld("capsulesAPI", {
  list: () => ipcRenderer.invoke("capsules-list"),
  checkpoints: (capsuleName) => ipcRenderer.invoke("capsules-checkpoints", capsuleName),
  deploy: (capsuleName, checkpointPath) =>
    ipcRenderer.invoke("capsules-deploy", capsuleName, checkpointPath),
  setEmoji: (capsuleName, emoji) => ipcRenderer.invoke("capsules-set-emoji", capsuleName, emoji),
  run: (capsuleName) => ipcRenderer.invoke("capsule-run", capsuleName),
  stop: () => ipcRenderer.invoke("capsule-stop"),
  openLog: () => ipcRenderer.invoke("capsule-open-log"),
  readLog: () => ipcRenderer.invoke("capsule-read-log"),
  // Mini Play/Stop widget support -- the widget is a tiny, separate
  // window with no capsule-picker UI of its own, so it always acts on
  // "whatever's currently loaded in the main window's Play panel" rather
  // than picking its own target. setCurrent() keeps main.js's copy of
  // that in sync (called from the main renderer whenever the loaded
  // capsule changes); runCurrent() is what the widget's Play circle calls.
  setCurrent: (capsuleName) => ipcRenderer.invoke("capsule-set-current", capsuleName),
  runCurrent: () => ipcRenderer.invoke("capsule-run-current"),
});
