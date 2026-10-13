/* The only browser-side view submitter.  It applies typed local patches or a
 * complete geometry frame after checking scene and version applicability. */
(function (root) {
  "use strict";
  var delivered = Object.create(null);
  var pending = Object.create(null);
  root.__mv_view_diag = root.__mv_view_diag || {submissions: 0, fullFrames: 0, localPatches: 0, cameraUpdates: 0, staleDrops: 0};
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
  function deliveryKey(update, gd) {
    var scene = update.scene_id;
    var render = currentMeta(gd);
    var v = meta(update);
    return [scene || render.scene_id || "", String(v.scene_generation || render.scene_generation || ""), Number(v.geometry_version || v.geometry || 0), Number(v.display_version || 0), Number(v.camera_version || 0)].join(":");
  }
  function applicable(update, gd) {
    if (!update || !gd) return false;
    var scene = update.scene_id;
    var render = currentMeta(gd);
    if (scene && render.scene_id && String(scene) !== String(render.scene_id)) return false;
    var v = meta(update), current = Number(render.geometry_version || render.render_revision || 0);
    var geometry = Number(v.geometry_version || v.geometry || 0);
    if (geometry && current && geometry < current) return false;
    var generation = v.scene_generation !== undefined ? String(v.scene_generation) : "";
    if (generation && render.scene_generation !== undefined && generation !== String(render.scene_generation)) return false;
    var display = Number(v.display_version || 0), currentDisplay = Number(render.display_version || 0);
    var camera = Number(v.camera_version || 0), currentCamera = Number(render.camera_version || render.camera_revision || 0);
    if (display && currentDisplay && display < currentDisplay) return false;
    if (camera && currentCamera && camera < currentCamera) return false;
    return !delivered[deliveryKey(update, gd)];
  }
  function markDelivered(update, gd) {
    delivered[deliveryKey(update, gd)] = true;
  }
  function role(trace) { var m = trace && trace.meta; return m && (m.mv_role || m.role); }
  function localDisplay(update, gd) {
    var payload = update.payload || update;
    var updateKind = String(update.update_kind || update.kind || "display");
    var option = payload.option;
    var options = payload.display_options || [];
    var labels = option === "labels" ? payload.enabled : options.indexOf("labels") >= 0;
    var axes = option === "axes" ? payload.enabled : options.indexOf("axes") >= 0;
    var visibility = [], opacity = [];
    (gd.data || []).forEach(function (trace, index) {
      var r = role(trace);
      if (r === "labels" && updateKind !== "overlay" && (option === "labels" || options.length)) visibility.push([index, !!labels]);
      if ((r === "polyhedron" || (trace.meta && trace.meta.kind === "polyhedron")) && trace.meta && trace.meta.spec_id && Array.isArray(payload.polyhedron_specs)) {
        payload.polyhedron_specs.forEach(function (spec) {
          if (String(spec.id) === String(trace.meta.spec_id)) visibility.push([index, spec.enabled !== false]);
        });
      }
      if ((r === "atom" || r === "bond") && payload.opacity !== undefined) opacity.push([index, Number(payload.opacity)]);
      if ((r === "atom" || r === "bond") && payload.minor_opacity !== undefined && trace.meta && trace.meta.mv_minor) opacity.push([index, Number(payload.minor_opacity)]);
    });
    var compassChanged = option === "axes" || axes !== undefined;
    if (compassChanged && root.MatterVisViewCompass) root.MatterVisViewCompass.setEnabled(!!axes);
    if (root.Plotly && typeof root.Plotly.restyle === "function") {
      var operations = [];
      visibility.forEach(function (item) { operations.push(Promise.resolve(root.Plotly.restyle(gd, {visible: item[1]}, [item[0]]))); });
      opacity.forEach(function (item) { operations.push(Promise.resolve(root.Plotly.restyle(gd, {opacity: item[1]}, [item[0]]))); });
      if (operations.length) return Promise.all(operations);
    }
    return Promise.resolve(visibility.length > 0 || opacity.length > 0 || compassChanged);
  }
  function submit(update) {
    if (!update || typeof update !== "object") return "";
    var gd = graph();
    if (!gd) return "";
    /* Take one immutable snapshot before any queued Plotly work. */
    var snapshot = clone(update);
    var kind = String(snapshot.update_kind || snapshot.kind || "full");
    if (snapshot.type === "render_error") {
      if (root.console && root.console.error) root.console.error("MatterVis render failed", snapshot.error || "unknown error");
      return "error";
    }
    root.__mv_view_diag.submissions += 1;
    if (!applicable(snapshot, gd)) { root.__mv_view_diag.staleDrops += 1; return "stale"; }
    if (kind === "camera") {
      root.__mv_view_diag.cameraUpdates += 1;
      var cameraKey = deliveryKey(snapshot, gd);
      if (pending[cameraKey]) return pending[cameraKey];
      var camera = snapshot.payload || snapshot;
      var cameraWork = root.MatterVisViewCamera && root.MatterVisViewCamera.applyAsync
        ? root.MatterVisViewCamera.applyAsync(clone(camera.camera))
        : Promise.resolve(root.MatterVisViewCamera && root.MatterVisViewCamera.apply(camera.camera));
      pending[cameraKey] = Promise.resolve(cameraWork).then(function () {
        markDelivered(snapshot, gd);
        return "camera";
      }).finally(function () { delete pending[cameraKey]; });
      return pending[cameraKey];
    }
    if (kind === "overlay" || kind === "display" || kind === "analysis") {
      root.__mv_view_diag.localPatches += 1;
      var patchKey = deliveryKey(snapshot, gd);
      if (pending[patchKey]) return pending[patchKey];
      pending[patchKey] = Promise.resolve(localDisplay(snapshot, gd)).then(function () {
        markDelivered(snapshot, gd);
        return kind;
      }).finally(function () { delete pending[patchKey]; });
      return pending[patchKey];
    }
    if (snapshot.figure && root.Plotly && typeof root.Plotly.react === "function") {
      root.__mv_view_diag.fullFrames += 1;
      var figureKey = deliveryKey(snapshot, gd);
      if (pending[figureKey]) return pending[figureKey];
      var layout = clone(snapshot.figure.layout || {});
      var live = root.mattervisCurrentCamera && root.mattervisCurrentCamera(gd);
      if (live && layout.scene) {
        layout = Object.assign({}, layout, {scene: Object.assign({}, layout.scene, {camera: clone(live)})});
      }
      pending[figureKey] = Promise.resolve(root.Plotly.react(gd, clone(snapshot.figure.data || []), layout)).then(function () {
        markDelivered(snapshot, gd);
        return "figure";
      }).finally(function () { delete pending[figureKey]; });
      return pending[figureKey];
    }
    // A geometry notification without a figure only confirms that a
    // background build is pending. Do not consume its version key: the
    // completed figure uses the same versions and must still pass through
    // Plotly.react when it arrives over WS or the HTTP fallback.
    return "";
  }
  root.MatterVisViewUpdates = { submit: submit, apply: submit, commit: submit, applicable: applicable, reset: function () { delivered = Object.create(null); } };
  (root.__mv_pending_view_updates || []).splice(0).forEach(function (update) { submit(update); });
})(window);
