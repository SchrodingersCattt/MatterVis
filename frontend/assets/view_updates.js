/* The only browser-side view submitter.  It applies typed local patches or a
 * complete geometry frame after checking scene and version applicability. */
(function (root) {
  "use strict";
  var delivered = Object.create(null);
  var pending = Object.create(null);
  var latestCameraRevision = Object.create(null);
  var latestVersions = Object.create(null);
  root.__mv_view_diag = root.__mv_view_diag || {submissions: 0, fullFrames: 0, localPatches: 0, cameraUpdates: 0, staleDrops: 0, cameraStale: 0, cameraDeduped: 0, graphUnavailable: 0};
  function graph() { return root.MatterVisViewCamera && root.MatterVisViewCamera.graph(); }
  function clone(value) {
    if (value === undefined || value === null) return value;
    try {
      if (typeof root.structuredClone === "function") return root.structuredClone(value);
    } catch (_) {}
    return JSON.parse(JSON.stringify(value));
  }
  function meta(update) { return (update && (update.versions || update)) || {}; }
  function currentMeta(gd) {
    var m = gd && gd.layout && gd.layout.meta;
    m = m && m.mattervis_render;
    return m || {};
  }
  var MAX_DELIVERED = 256;
  function noteEpoch(update) {
    var epoch = update && update.server_started_at ? String(update.server_started_at) : "";
    if (!epoch) return true;
    if (root.__mvServerEpoch && epoch < root.__mvServerEpoch) return false;
    if (root.__mvServerEpoch && epoch !== root.__mvServerEpoch) {
      delivered = Object.create(null);
      latestCameraRevision = Object.create(null);
      latestVersions = Object.create(null);
      root.__mv_view_diag.epochResets = (root.__mv_view_diag.epochResets || 0) + 1;
    }
    root.__mvServerEpoch = epoch;
    return true;
  }
  function noteGraph(gd) {
    if (gd && root.__mvDeliveryGraph && root.__mvDeliveryGraph !== gd) {
      delivered = Object.create(null);
      latestCameraRevision = Object.create(null);
      latestVersions = Object.create(null);
      root.__mv_view_diag.remountResets = (root.__mv_view_diag.remountResets || 0) + 1;
    }
    if (gd) root.__mvDeliveryGraph = gd;
  }
  function deliveryKey(update, gd) {
    var scene = update.scene_id;
    var render = currentMeta(gd);
    var v = meta(update);
    var epoch = String(update.server_started_at || render.server_started_at || root.__mvServerEpoch || "");
    var operation = String(update.operation_id || "");
    return [epoch, scene || render.scene_id || "", String(v.scene_generation || render.scene_generation || ""), Number(v.geometry_version || v.geometry || 0), Number(v.display_version || 0), Number(v.camera_version || v.camera_revision || 0), operation].join(":");
  }
  function cameraScope(update, gd) {
    var render = currentMeta(gd);
    var generation = meta(update).scene_generation;
    if (generation === undefined || generation === null || generation === "") generation = render.scene_generation || "";
    return String(update.scene_id || render.scene_id || "") + ":" + String(generation);
  }
  function cameraRevision(update) {
    var value = Number(meta(update).camera_version || meta(update).camera_revision || 0);
    return Number.isFinite(value) ? value : 0;
  }
  function versionScope(update, gd) {
    var render = currentMeta(gd);
    var scene = String((update && update.scene_id) || render.scene_id || "");
    var value = meta(update).scene_generation;
    if (value === undefined || value === null || value === "") value = render.scene_generation || "";
    /* Geometry/display/camera versions restart when a scene is remounted.
     * Keeping the generation in this scope prevents a prior graph's high
     * revision from making valid commands for the new graph look stale. */
    return scene + ":" + String(value);
  }
  function remembered(update, gd) {
    return latestVersions[versionScope(update, gd)] || {geometry: 0, display: 0, camera: 0};
  }
  function rememberVersions(update, gd) {
    var scope = versionScope(update, gd);
    var prev = latestVersions[scope] || {geometry: 0, display: 0, camera: 0};
    var v = meta(update);
    latestVersions[scope] = {
      geometry: Math.max(prev.geometry, Number(v.geometry_version || v.geometry || 0) || 0),
      display: Math.max(prev.display, Number(v.display_version || 0) || 0),
      camera: Math.max(prev.camera, Number(v.camera_version || v.camera_revision || 0) || 0),
    };
  }
  function cameraIsCurrent(update, gd) {
    var scope = cameraScope(update, gd);
    var revision = cameraRevision(update);
    var latest = latestCameraRevision[scope];
    return latest === undefined || revision >= latest;
  }
  function updateKind(update) {
    return String((update && (update.update_kind || update.kind)) || "");
  }
  function isCompleteFrame(update) {
    var kind = updateKind(update);
    return !!(update && update.figure) || kind === "full" || kind === "geometry";
  }
  function selectedScene() {
    if (typeof root.MatterVisSelectedScene === "function") {
      var selected = root.MatterVisSelectedScene();
      if (selected) return String(selected);
    }
    return "";
  }
  function applicable(update, gd) {
    if (!update || !gd) return false;
    noteGraph(gd);
    if (!noteEpoch(update)) return false;
    var scene = update.scene_id;
    var render = currentMeta(gd);
    var complete = isCompleteFrame(update);
    /* A complete frame for the newly selected scene must paint while the
     * canvas still shows the previous scene. Patch updates still require
     * an exact rendered-scene match. */
    if (complete) {
      var selected = selectedScene();
      if (selected && scene && String(scene) !== selected) return false;
      if (!selected && scene && render.scene_id && String(scene) !== String(render.scene_id)) return false;
    } else if (scene && render.scene_id && String(scene) !== String(render.scene_id)) {
      return false;
    }
    var sameScene = !scene || !render.scene_id || String(scene) === String(render.scene_id);
    var seen = remembered(update, gd);
    var v = meta(update), current = Math.max(Number(render.geometry_version || render.render_revision || 0), seen.geometry);
    var geometry = Number(v.geometry_version || v.geometry || 0);
    if (sameScene && geometry && current && geometry < current) return false;
    var generation = v.scene_generation !== undefined ? String(v.scene_generation) : "";
    if (sameScene && generation && render.scene_generation !== undefined && generation !== String(render.scene_generation)) return false;
    var display = Number(v.display_version || 0), currentDisplay = Math.max(Number(render.display_version || 0), seen.display);
    var camera = Number(v.camera_version || v.camera_revision || 0), currentCamera = Math.max(Number(render.camera_version || render.camera_revision || 0), seen.camera);
    if (sameScene && display && currentDisplay && display < currentDisplay) return false;
    if (sameScene && camera && currentCamera && camera < currentCamera) return false;
    if (updateKind(update) === "camera" && !cameraIsCurrent(update, gd)) return false;
    var key = deliveryKey(update, gd);
    if (!delivered[key]) return true;
    /* Switching away and back reuses the cached frame. The delivery key
     * is already known, but the canvas is still the other scene. */
    return !!(complete && scene && render.scene_id && String(scene) !== String(render.scene_id));
  }
  function markDelivered(update, gd, key) {
    delivered[key || deliveryKey(update, gd)] = true;
    var keys = Object.keys(delivered);
    while (keys.length > MAX_DELIVERED) {
      delete delivered[keys.shift()];
    }
  }
  function role(trace) {
    var meta = trace && trace.meta;
    if (!meta) return "";
    if (meta.mv_role || meta.role) return meta.mv_role || meta.role;
    if (meta.kind === "labels" || meta.kind === "label") return "labels";
    if (meta.kind === "polyhedron") return "polyhedron";
    return "";
  }
  function localDisplay(update, gd) {
    var payload = update.payload || update;
    var updateKind = String(update.update_kind || update.kind || "display");
    var option = payload.option;
    var hasOptions = Array.isArray(payload.display_options);
    var options = hasOptions ? payload.display_options : [];
    var labels = option === "labels" ? payload.enabled : options.indexOf("labels") >= 0;
    var axes = option === "axes" ? payload.enabled : options.indexOf("axes") >= 0;
    var touchesLabels = option === "labels" || hasOptions;
    var visibilityIndices = [], visibilityValues = [], opacityIndices = [], opacityValues = [];
    (gd.data || []).forEach(function (trace, index) {
      var r = role(trace);
      if (r === "labels" && updateKind !== "overlay" && touchesLabels) {
        visibilityIndices.push(index);
        visibilityValues.push(!!labels);
      }
      if ((r === "polyhedron" || (trace.meta && trace.meta.kind === "polyhedron")) && trace.meta && trace.meta.spec_id && Array.isArray(payload.polyhedron_specs)) {
        payload.polyhedron_specs.forEach(function (spec) {
          if (String(spec.id) === String(trace.meta.spec_id)) {
            visibilityIndices.push(index);
            visibilityValues.push(spec.enabled !== false);
          }
        });
      }
      if ((r === "atom" || r === "bond") && payload.opacity !== undefined) {
        opacityIndices.push(index);
        opacityValues.push(Number(payload.opacity));
      }
      if ((r === "atom" || r === "bond") && payload.minor_opacity !== undefined && trace.meta && trace.meta.mv_minor) {
        opacityIndices.push(index);
        opacityValues.push(Number(payload.minor_opacity));
      }
    });
    var compassChanged = option === "axes" || (hasOptions && (updateKind === "overlay" || updateKind === "display"));
    if (payload.axis_scale !== undefined && gd.layout && gd.layout.meta) {
      var meta = gd.layout.meta;
      var compass = meta.compass || meta.compass_context;
      var nextScale = Number(payload.axis_scale);
      if (compass && Number.isFinite(nextScale)) {
        compass.pixel_length = Math.max(20, nextScale * 360);
        compass.axis_scale = nextScale;
        compassChanged = true;
        if (root.MatterVisViewCompass && typeof root.MatterVisViewCompass.redraw === "function") {
          root.MatterVisViewCompass.redraw();
        }
      }
    }
    if (compassChanged && root.MatterVisViewCompass && typeof root.MatterVisViewCompass.setEnabled === "function") {
      root.MatterVisViewCompass.setEnabled(!!axes);
    }
    var applied = visibilityIndices.length > 0 || opacityIndices.length > 0 || compassChanged;
    if (!applied) return Promise.resolve(false);
    if (root.Plotly && typeof root.Plotly.restyle === "function") {
      var operations = [];
      if (visibilityIndices.length) operations.push(Promise.resolve(root.Plotly.restyle(gd, {visible: visibilityValues}, visibilityIndices)));
      if (opacityIndices.length) operations.push(Promise.resolve(root.Plotly.restyle(gd, {opacity: opacityValues}, opacityIndices)));
      if (operations.length) return Promise.all(operations).then(function () { return true; });
    }
    return Promise.resolve(applied);
  }
  function noteDrop(update, gd, reason) {
    var v = meta(update), current = currentMeta(gd);
    root.__mv_view_diag.lastDrop = {
      reason: String(reason || "stale"),
      kind: updateKind(update),
      scene: update && update.scene_id || null,
      generation: v.scene_generation,
      geometry: Number(v.geometry_version || v.geometry || 0),
      display: Number(v.display_version || 0),
      camera: Number(v.camera_version || v.camera_revision || 0),
      currentScene: current.scene_id || null,
      currentGeneration: current.scene_generation,
      currentGeometry: Number(current.geometry_version || current.geometry || 0),
      currentDisplay: Number(current.display_version || 0),
      currentCamera: Number(current.camera_version || current.camera_revision || 0),
    };
  }
  function submit(update) {
    if (!update || typeof update !== "object") return "";
    var gd = graph();
    if (!gd) return "";
    /* Take one immutable snapshot before any queued Plotly work. */
    var snapshot = clone(update);
    var kind = String(snapshot.update_kind || snapshot.kind || "full");
    if (snapshot.type === "render_error") {
      var renderMessage = snapshot.error || "Rendering failed. The last good frame is unchanged.";
      if (root.MatterVisViewCamera && typeof root.MatterVisViewCamera.reportStatus === "function") {
        root.MatterVisViewCamera.reportStatus("error", renderMessage);
      } else {
        root.__mvRenderStatus = {level: "error", message: renderMessage, at: Date.now()};
      }
      if (root.console && root.console.error) root.console.error("MatterVis render failed", renderMessage);
      return "error";
    }
    root.__mv_view_diag.submissions += 1;
    if (!applicable(snapshot, gd)) {
      root.__mv_view_diag.staleDrops += 1;
      if (kind === "camera") root.__mv_view_diag.cameraStale += 1;
      noteDrop(snapshot, gd, "inapplicable");
      return "stale";
    }
    rememberVersions(snapshot, gd);
    if (kind === "camera") {
      root.__mv_view_diag.cameraUpdates += 1;
      var scope = cameraScope(snapshot, gd);
      var revision = cameraRevision(snapshot);
      if (latestCameraRevision[scope] === undefined || revision > latestCameraRevision[scope]) latestCameraRevision[scope] = revision;
      if (!cameraIsCurrent(snapshot, gd)) {
        root.__mv_view_diag.staleDrops += 1;
        root.__mv_view_diag.cameraStale += 1;
        noteDrop(snapshot, gd, "camera-order");
        return "stale";
      }
      var cameraKey = deliveryKey(snapshot, gd);
      if (pending[cameraKey]) {
        root.__mv_view_diag.cameraDeduped += 1;
        root.__mv_view_diag.lastCameraDeduped = {key: cameraKey, revision: revision};
        return pending[cameraKey];
      }
      var camera = snapshot.payload || snapshot;
      var cameraOptions = {
        scene_id: snapshot.scene_id,
        scene_generation: meta(snapshot).scene_generation,
        camera_version: revision,
        command: String(camera.command || snapshot.reason || ""),
        graph_generation: Number(gd.__mvGraphGeneration || 0),
        isCurrent: function () {
          return cameraIsCurrent(snapshot, gd) && applicable(snapshot, gd);
        },
      };
      var hasAsyncCamera = root.MatterVisViewCamera && typeof root.MatterVisViewCamera.applyAsync === "function";
      if (!hasAsyncCamera) root.__mv_view_diag.graphUnavailable += 1;
      var cameraWork = hasAsyncCamera
        ? root.MatterVisViewCamera.applyAsync(clone(camera.camera), cameraOptions)
        : Promise.reject(new Error("camera graph unavailable"));
      pending[cameraKey] = Promise.resolve(cameraWork).catch(function (error) {
        if (/graph unavailable|not ready/i.test(String(error && error.message || error))) {
          root.__mv_view_diag.graphUnavailable += 1;
          root.__mv_view_diag.lastGraphUnavailable = {key: cameraKey, revision: revision, scene: snapshot.scene_id};
        }
        throw error;
      }).then(function () {
        markDelivered(snapshot, gd, cameraKey);
        return "camera";
      }).finally(function () { delete pending[cameraKey]; });
      return pending[cameraKey];
    }
    if (kind === "overlay" || kind === "display" || kind === "analysis") {
      root.__mv_view_diag.localPatches += 1;
      var patchKey = deliveryKey(snapshot, gd);
      if (pending[patchKey]) return pending[patchKey];
      pending[patchKey] = Promise.resolve(localDisplay(snapshot, gd)).then(function (applied) {
        if (!applied) {
          root.__mv_view_diag.unsupported = (root.__mv_view_diag.unsupported || 0) + 1;
          return "unsupported";
        }
        markDelivered(snapshot, gd, patchKey);
        return kind;
      }, function (error) {
        if (root.MatterVisViewCamera && typeof root.MatterVisViewCamera.reportStatus === "function") {
          root.MatterVisViewCamera.reportStatus("error", "A display patch failed. The last good frame is unchanged.");
        }
        throw error;
      }).finally(function () { delete pending[patchKey]; });
      return pending[patchKey];
    }
    if (snapshot.figure && root.Plotly && typeof root.Plotly.react === "function") {
      root.__mv_view_diag.fullFrames += 1;
      var figureKey = deliveryKey(snapshot, gd);
      if (pending[figureKey]) return pending[figureKey];
      var layout = clone(snapshot.figure.layout || {});
      /* Live-camera preservation belongs to the frame gate, which keeps it
       * only for the same scene and interaction generation. Copying it here
       * would paint scene A's camera onto scene B before that gate runs. */
      pending[figureKey] = Promise.resolve(root.Plotly.react(gd, clone(snapshot.figure.data || []), layout)).then(function () {
        gd.__mvGraphGeneration = Number(gd.__mvGraphGeneration || 0) + 1;
        markDelivered(snapshot, gd, figureKey);
        return "figure";
      }, function (error) {
        if (root.MatterVisViewCamera && typeof root.MatterVisViewCamera.reportStatus === "function") {
          root.MatterVisViewCamera.reportStatus("error", "The latest frame did not render. The last good frame is unchanged.");
        }
        throw error;
      }).finally(function () { delete pending[figureKey]; });
      return pending[figureKey];
    }
    // A geometry notification without a figure only confirms that a
    // background build is pending. Do not consume its version key: the
    // completed figure uses the same versions and must still pass through
    // Plotly.react when it arrives over WS or the HTTP fallback.
    return "";
  }
  function pickLatest(updates) {
    var latest = null;
    (updates || []).forEach(function (item) {
      if (item && (item.figure || item.type === "view_update" || item.type === "render_error")) latest = item;
    });
    return latest;
  }
  root.MatterVisViewUpdates = { submit: submit, apply: submit, commit: submit, applicable: applicable, pickLatest: pickLatest, deliveredCount: function () { return Object.keys(delivered).length; }, reset: function () { delivered = Object.create(null); latestCameraRevision = Object.create(null); latestVersions = Object.create(null); } };
  (root.__mv_pending_view_updates || []).splice(0).forEach(function (update) { submit(update); });
})(window);
