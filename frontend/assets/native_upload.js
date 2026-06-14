/* Native CIF upload for MatterVis.
 *
 * Dash's dcc.Upload reads each file into a base64 data URL on the browser
 * main thread, then ships that giant string through Dash's callback channel.
 * Large CIFs make the page look frozen before the server ever sees a byte.
 *
 * This script keeps the UI responsive by posting the raw File object directly
 * to /api/v2/upload as multipart/form-data. When the server finishes parsing
 * and registering the scene, we poke a hidden dcc.Store so the existing Dash
 * state-sync callback refreshes tabs and controls immediately.
 */
(function () {
  if (window.__mattervisNativeUploadInstalled) {
    return;
  }
  window.__mattervisNativeUploadInstalled = true;

  function byId(id) {
    return document.getElementById(id);
  }

  function setStatus(message, level) {
    const status = byId("upload-status");
    if (!status) {
      return;
    }
    status.textContent = message || "";
    if (level === "error") {
      status.style.color = "#B91C1C";
    } else if (level === "success") {
      status.style.color = "#047857";
    } else {
      status.style.color = "#444444";
    }
  }

  function setDropzoneBusy(dropzone, busy) {
    if (!dropzone) {
      return;
    }
    dropzone.dataset.uploadBusy = busy ? "1" : "0";
    dropzone.style.opacity = busy ? "0.65" : "";
    dropzone.style.pointerEvents = busy ? "none" : "";
  }

  function triggerDashSync(payload) {
    const data = {
      seq: Date.now(),
      status: payload && payload.status,
      names: (payload && payload.names) || [],
    };
    if (window.dash_clientside && typeof window.dash_clientside.set_props === "function") {
      window.dash_clientside.set_props("native-upload-sync", { data: data });
      return;
    }
    const store = byId("native-upload-sync");
    if (!store) {
      return;
    }
    store.value = JSON.stringify(data);
    store.dispatchEvent(new Event("input", { bubbles: true }));
    store.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function forceActiveSceneFromServer(payload) {
    try {
      const response = await fetch("/api/v2/scenes", { cache: "no-store" });
      if (!response.ok) {
        return;
      }
      const scenesPayload = await response.json();
      const activeId = scenesPayload && scenesPayload.active_id;
      if (!activeId || !window.dash_clientside || typeof window.dash_clientside.set_props !== "function") {
        return;
      }
      // The upload handler has already made the new scene active server-side.
      // Make the browser tab selection explicit too; relying only on the
      // pending_state/native-upload-sync callback can lose a race with the
      // 5s poll or a concurrent store update on large uploads.
      window.dash_clientside.set_props("scene-tabs", { value: activeId });
      triggerDashSync(payload || { status: "success", names: [] });
    } catch (_err) {
      // Best effort; the normal Dash sync path still runs.
    }
  }

  /* Active wait for the server to register the new scene tab.
   *
   * Without this, the "Updating scene..." status (set in
   * uploadFiles below) sits there forever even after the upload
   * succeeded: setStatus has no Dash callback that would clear it,
   * and the 5 s ``agent-state-poll`` may race against the
   * ``native-upload-sync`` set_props in some Dash builds (the latter
   * is a no-op when ``dash_clientside.set_props`` is missing or when
   * the listening callback already drained ``pending_state`` from
   * a different tab).
   *
   * Strategy: poll ``GET /api/v2/scenes`` until the new structure
   * name appears in the scene list (the server-side upload handler
   * always calls ``create_scene`` so the new tab is guaranteed to
   * exist). Then poke ``native-upload-sync`` again and let Dash's
   * single scene-tab writer switch to the backend's active scene.
   * Hard timeout at 30 s so the status never sticks forever even if
   * the server changes shape.
   */
  let activeSceneWait = null;

  function waitForSceneAndSwitch(uploadedNames) {
    if (!uploadedNames || !uploadedNames.length) return;
    if (activeSceneWait && typeof activeSceneWait.close === "function") {
      activeSceneWait.close();
    }
    const wanted = new Set(uploadedNames);
    const deadline = Date.now() + 30000;
    if (!window.WebSocket) {
      triggerDashSync({ status: "success", names: uploadedNames });
      setStatus("Loaded: " + uploadedNames.join(", "), "success");
      return;
    }
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(proto + "//" + window.location.host + "/api/v2/ws");
    activeSceneWait = ws;
    const timeout = window.setTimeout(function () {
      if (activeSceneWait === ws) activeSceneWait = null;
      try { ws.close(); } catch (_err) {}
      setStatus("Uploaded: " + uploadedNames.join(", "), "success");
      triggerDashSync({ status: "success", names: uploadedNames });
    }, Math.max(1000, deadline - Date.now()));
    ws.addEventListener("message", function (event) {
      let payload = null;
      try {
        payload = JSON.parse(event.data || "{}");
      } catch (_err) {
        return;
      }
      const state = payload && payload.state;
      if (state && wanted.has(state.structure)) {
        window.clearTimeout(timeout);
        if (activeSceneWait === ws) activeSceneWait = null;
        try { ws.close(); } catch (_err) {}
        const payload = { status: "success", names: uploadedNames };
        triggerDashSync(payload);
        forceActiveSceneFromServer(payload);
        setStatus("Loaded: " + uploadedNames.join(", "), "success");
      }
    });
  }

  function uploadOne(file, index, total) {
    return new Promise(function (resolve, reject) {
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file, file.name || "upload.cif");

      xhr.open("POST", "/api/v2/upload", true);
      xhr.upload.onprogress = function (event) {
        if (event.lengthComputable) {
          const pct = Math.round((event.loaded / event.total) * 100);
          setStatus(
            "Uploading " + file.name + " (" + index + "/" + total + "): " + pct + "%...",
            "info"
          );
        } else {
          setStatus("Uploading " + file.name + " (" + index + "/" + total + ")...", "info");
        }
      };
      xhr.onload = function () {
        let payload = null;
        try {
          payload = JSON.parse(xhr.responseText || "{}");
        } catch (_err) {
          payload = {};
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(payload);
          return;
        }
        reject(new Error(payload.error || ("Upload failed with HTTP " + xhr.status)));
      };
      xhr.onerror = function () {
        reject(new Error("Network error while uploading " + file.name));
      };
      xhr.onabort = function () {
        reject(new Error("Upload aborted for " + file.name));
      };

      setStatus("Uploading " + file.name + " (" + index + "/" + total + ")...", "info");
      xhr.send(form);
    });
  }

  async function uploadFiles(files, dropzone, input) {
    const list = Array.prototype.slice.call(files || []).filter(Boolean);
    if (!list.length) {
      return;
    }
    setDropzoneBusy(dropzone, true);
    const names = [];
    try {
      for (let i = 0; i < list.length; i += 1) {
        const file = list[i];
        setStatus(
          "Uploading " + file.name + " (" + (i + 1) + "/" + list.length + ")...",
          "info"
        );
        const meta = await uploadOne(file, i + 1, list.length);
        names.push(meta.name || file.name);
        setStatus("Processing complete: " + names.join(", "), "success");
      }
      setStatus("Uploaded CIF(s): " + names.join(", ") + ". Updating scene...", "success");
      const payload = { status: "success", names: names };
      triggerDashSync(payload);
      forceActiveSceneFromServer(payload);
      // Belt-and-braces: actively wait for the scene tab to appear
      // and switch to it. Without this the status text never clears.
      waitForSceneAndSwitch(names);
    } catch (err) {
      setStatus("Upload failed: " + (err && err.message ? err.message : String(err)), "error");
      triggerDashSync({ status: "error", names: names });
    } finally {
      setDropzoneBusy(dropzone, false);
      if (input) {
        input.value = "";
      }
    }
  }

  function install() {
    const dropzone = byId("scene-cif-upload");
    const input = byId("scene-cif-upload-input");
    if (!dropzone || !input) {
      window.setTimeout(install, 250);
      return;
    }
    if (dropzone.dataset.nativeUploadInstalled === "1") {
      return;
    }
    dropzone.dataset.nativeUploadInstalled = "1";

    dropzone.addEventListener("click", function () {
      if (dropzone.dataset.uploadBusy === "1") {
        return;
      }
      input.click();
    });
    dropzone.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });
    input.addEventListener("change", function () {
      uploadFiles(input.files, dropzone, input);
    });

    ["dragenter", "dragover"].forEach(function (type) {
      dropzone.addEventListener(type, function (event) {
        event.preventDefault();
        dropzone.style.background = "#EEF2FF";
      });
    });
    ["dragleave", "drop"].forEach(function (type) {
      dropzone.addEventListener(type, function (event) {
        event.preventDefault();
        dropzone.style.background = "";
      });
    });
    dropzone.addEventListener("drop", function (event) {
      uploadFiles(event.dataTransfer && event.dataTransfer.files, dropzone, input);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install);
  } else {
    install();
  }
})();
