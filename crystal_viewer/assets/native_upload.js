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
   * exist). Then switch ``scene-tabs`` to that new scene id via
   * ``dash_clientside.set_props`` so the user lands on their upload
   * immediately, and clear the status. Hard timeout at 30 s so the
   * status never sticks forever even if the server changes shape.
   */
  function waitForSceneAndSwitch(uploadedNames) {
    if (!uploadedNames || !uploadedNames.length) return;
    const wanted = new Set(uploadedNames);
    const deadline = Date.now() + 30000;
    function tick() {
      fetch("/api/v2/scenes", { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (payload) {
          if (!payload) throw new Error("no scenes payload");
          const scenes = payload.scenes || [];
          let target = null;
          for (let i = scenes.length - 1; i >= 0; i -= 1) {
            const sc = scenes[i];
            const patch = (sc && sc.state_patch) || {};
            const structure = patch.structure || sc.label;
            if (structure && wanted.has(structure)) {
              target = sc;
              break;
            }
          }
          if (target && target.id) {
            if (window.dash_clientside && typeof window.dash_clientside.set_props === "function") {
              try {
                window.dash_clientside.set_props("scene-tabs", { value: target.id });
              } catch (_err) { /* tolerated; the next poll will pick it up */ }
            }
            setStatus("Loaded: " + uploadedNames.join(", "), "success");
            return;
          }
          if (Date.now() > deadline) {
            setStatus(
              "Uploaded: " + uploadedNames.join(", ") +
              ". The new scene is registered but the UI did not auto-switch -- " +
              "click the new tab in the Scenes list to view it.",
              "info"
            );
            return;
          }
          window.setTimeout(tick, 250);
        })
        .catch(function () {
          if (Date.now() > deadline) {
            setStatus("Uploaded: " + uploadedNames.join(", "), "success");
            return;
          }
          window.setTimeout(tick, 500);
        });
    }
    tick();
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
      triggerDashSync({ status: "success", names: names });
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
