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
  let installTimer = null;

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
      if (activeId && window.dash_clientside && typeof window.dash_clientside.set_props === "function") {
        // The upload completion is authoritative for the selected scene. The
        // persistent figure client will queue its frame until this value is
        // painted, so no second figure socket is needed here.
        window.dash_clientside.set_props("scene-tabs", { value: activeId });
      }
      if (activeId && typeof window.MatterVisSelectScene === "function") {
        window.MatterVisSelectScene(activeId);
      }
      if (activeId && typeof window.MatterVisFlushSceneFrame === "function") {
        window.MatterVisFlushSceneFrame(activeId);
      }
      // Always poke Dash, including builds without set_props. The fallback
      // hidden-store path keeps the controls and disorder banner in sync.
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
  function delay(milliseconds) {
    return new Promise(function (resolve) {
      window.setTimeout(resolve, milliseconds);
    });
  }

  /* The multipart POST returns as soon as the placeholder tab is registered.
   * Poll its job id until the real bundle is installed.  A failed MolCrysKit
   * parse must reject the upload here; otherwise the old websocket-only wait
   * reported "Uploaded" forever while the tab contained no atoms. */
  async function waitForUpload(meta, file, index, total) {
    if (!meta || meta.status !== "pending" || !meta.job_id) {
      if (meta && meta.status === "error") {
        throw new Error(meta.error || ("Unable to parse " + file.name));
      }
      return meta;
    }
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      let response;
      let payload;
      try {
        response = await fetch(
          "/api/v2/upload/" + encodeURIComponent(meta.job_id),
          { cache: "no-store" }
        );
        payload = await response.json();
      } catch (err) {
        throw new Error(
          "Could not check upload status for " + file.name + ": " +
          (err && err.message ? err.message : String(err))
        );
      }
      if (!response.ok) {
        throw new Error(payload && payload.error ? payload.error : "Upload status unavailable");
      }
      if (payload.status === "error") {
        throw new Error(payload.error || ("Unable to parse " + file.name));
      }
      if (payload.status === "ready" || payload.status === "complete" || payload.status === "success") {
        return Object.assign({}, meta, payload, { status: "ready" });
      }
      setStatus(
        "Processing " + file.name + " (" + index + "/" + total + ")...",
        "info"
      );
      await delay(350);
    }
    throw new Error("Timed out while processing " + file.name);
  }

  async function waitForSceneAndSwitch(uploadedNames) {
    if (!uploadedNames || !uploadedNames.length) return;
    // Upload completion is already exposed by the HTTP job endpoint.  Do not
    // open a second figure connection here: transient per-upload sockets
    // race the figure subscriber during repeated uploads and can leave the
    // browser reporting an Invalid frame header while the old scene remains
    // selected.  Poll the scene list, then perform one explicit Dash switch.
    const wanted = new Set(uploadedNames);
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      try {
        const response = await fetch("/api/v2/scenes", { cache: "no-store" });
        const payload = response.ok ? await response.json() : null;
        const scenes = (payload && payload.scenes) || [];
        const present = new Set(
          scenes.map(function (scene) { return scene && scene.structure_name; })
        );
        if (uploadedNames.every(function (name) { return present.has(name); })) {
          await forceActiveSceneFromServer({ status: "success", names: uploadedNames });
          setStatus("Loaded: " + uploadedNames.join(", "), "success");
          return;
        }
      } catch (_err) {
        // The next poll retries while the Dash state and scene store settle.
      }
      await delay(250);
    }
    setStatus("Uploaded: " + uploadedNames.join(", "), "success");
    triggerDashSync({ status: "success", names: uploadedNames });
  }

  function uploadOne(file, index, total) {
    return new Promise(function (resolve, reject) {
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file, file.name || "upload.cif");

      xhr.open("POST", "/api/v2/upload", true);
      xhr.upload.onprogress = function (event) {
        if (event.lengthComputable) {
          var pct = Math.round((event.loaded / event.total) * 100);
          if (pct >= 100) {
            setStatus(
              "Processing " + file.name + " (" + index + "/" + total + ")...",
              "info"
            );
          } else {
            setStatus(
              "Uploading " + file.name + " (" + index + "/" + total + "): " + pct + "%...",
              "info"
            );
          }
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
          waitForUpload(payload, file, index, total).then(resolve).catch(reject);
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
      // Wait for the scene store to expose every completed upload, then
      // switch exactly once. Awaiting this sequence prevents a stale tab
      // value from winning a Dash callback race with the upload completion.
      await waitForSceneAndSwitch(names);
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

  function scheduleInstall() {
    if (installTimer !== null) {
      return;
    }
    installTimer = window.setTimeout(function () {
      installTimer = null;
      install();
    }, 250);
  }

  function install() {
    const dropzone = byId("scene-cif-upload");
    const input = byId("scene-cif-upload-input");
    if (!dropzone || !input) {
      scheduleInstall();
      return;
    }
    if (installTimer !== null) {
      window.clearTimeout(installTimer);
      installTimer = null;
    }
    // Dash may replace either node when a scene callback refreshes the left
    // panel. Keep the binding tied to the current input element instead of
    // treating the first mounted dropzone as permanent. This is what makes
    // clicking the upload surface work after switching tabs or uploading a
    // second file.
    if (dropzone.__mattervisUploadInput === input) {
      return;
    }

    if (dropzone.dataset.nativeUploadInstalled !== "1") {
      dropzone.dataset.nativeUploadInstalled = "1";
      dropzone.addEventListener("click", function () {
        if (dropzone.dataset.uploadBusy === "1") {
          return;
        }
        const currentInput = dropzone.__mattervisUploadInput;
        if (currentInput && typeof currentInput.click === "function") {
          currentInput.click();
        }
      });
      dropzone.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          const currentInput = dropzone.__mattervisUploadInput;
          if (currentInput && typeof currentInput.click === "function") {
            currentInput.click();
          }
        }
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
        const currentInput = dropzone.__mattervisUploadInput;
        uploadFiles(event.dataTransfer && event.dataTransfer.files, dropzone, currentInput);
      });
    }

    dropzone.__mattervisUploadInput = input;
    input.addEventListener("change", function () {
      uploadFiles(input.files, dropzone, input);
    });
  }

  function watchLayout() {
    if (window.__mattervisNativeUploadObserver || !window.MutationObserver) {
      return;
    }
    const root = document.body || document.documentElement;
    if (!root) {
      window.setTimeout(watchLayout, 250);
      return;
    }
    const observer = new MutationObserver(function () {
      install();
    });
    observer.observe(root, { childList: true, subtree: true });
    window.__mattervisNativeUploadObserver = observer;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      install();
      watchLayout();
    });
  } else {
    install();
    watchLayout();
  }
})();
