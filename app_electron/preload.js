const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("recorderAPI", {
  start: (outputDir) => ipcRenderer.invoke("recorder-start", outputDir),
  stop: () => ipcRenderer.invoke("recorder-stop"),
  replay: (n) => ipcRenderer.invoke("recorder-replay", n),
  onEvent: (callback) => {
    ipcRenderer.on("recorder-event", (_evt, event) => callback(event));
  },
});
