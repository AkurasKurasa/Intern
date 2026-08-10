const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("recorderAPI", {
  start: (outputDir) => ipcRenderer.invoke("recorder-start", outputDir),
  stop: () => ipcRenderer.invoke("recorder-stop"),
  replay: (n) => ipcRenderer.invoke("recorder-replay", n),
  restoreMain: () => ipcRenderer.invoke("restore-main"),
  onEvent: (callback) => {
    ipcRenderer.on("recorder-event", (_evt, event) => callback(event));
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
  run: (modelPath) => ipcRenderer.invoke("capsule-run", modelPath),
  stop: () => ipcRenderer.invoke("capsule-stop"),
  openLog: () => ipcRenderer.invoke("capsule-open-log"),
  readLog: () => ipcRenderer.invoke("capsule-read-log"),
});
