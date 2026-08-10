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
  play: (sessionPath, count) => ipcRenderer.invoke("workflows-play", sessionPath, count),
});
