/* The only browser-side view submitter.  It applies typed local patches or a
 * complete geometry frame after checking scene and version applicability. */
(function (root) {
  "use strict";
  var delivered = Object.create(null);
  root.__mv_view_diag = root.__mv_view_diag || {submissions: 0, fullFrames: 0, localPatches: 0, cameraUpdates: 0, staleDrops: 0};
  function graph() { return root.MatterVisViewCamera && root.MatterVisViewCamera.graph(); }
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
    return [scene || render.scene_id || "", Number(v.geometry_version || v.geometry || 0), Number(v.display_version || 0), Number(v.camera_version || 0)].join(":");
  }
  function applicable(update, gd) {
    if (!update || !gd) return false;
    var scene = update.scene_id;
    var render = currentMeta(gd);
    if (scene && render.scene_id && String(scene) !== String(render.scene_id)) return false;
    var v = meta(update), current = Number(render.geometry_version || render.render_revision || 0);
    var geometry = Number(v.geometry_version || v.geometry || 0);
    if (geometry && current && geometry < current) return false;
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
    if (root.Plotly && typeof root.Plotly.restyle === "function") {
      visibility.forEach(function (item) { root.Plotly.restyle(gd, {visible: item[1]}, [item[0]]); });
      opacity.forEach(function (item) { root.Plotly.restyle(gd, {opacity: item[1]}, [item[0]]); });
    }
    if (option === "axes" || axes !== undefined) {
      if (root.MatterVisViewCompass) root.MatterVisViewCompass.setEnabled(!!axes);
    }
    return visibility.length > 0 || opacity.length > 0 || option === "axes";
  }
  function submit(update) {
    if (!update || typeof update !== "object") return "";
    var gd = graph();
    if (!gd) return "";
    var kind = String(update.update_kind || update.kind || "full");
    if (update.type === "render_error") {
      if (root.console && root.console.error) root.console.error("MatterVis render failed", update.error || "unknown error");
      return "error";
    }
    root.__mv_view_diag.submissions += 1;
    if (!applicable(update, gd)) { root.__mv_view_diag.staleDrops += 1; return "stale"; }
    if (kind === "camera") {
      markDelivered(update, gd);
      root.__mv_view_diag.cameraUpdates += 1;
      if (root.MatterVisViewCamera) root.MatterVisViewCamera.apply((update.payload || update).camera);
      return "camera";
    }
    if (kind === "overlay" || kind === "display" || kind === "analysis") {
      markDelivered(update, gd);
      root.__mv_view_diag.localPatches += 1;
      localDisplay(update, gd);
      return kind;
    }
    if (update.figure && root.Plotly && typeof root.Plotly.react === "function") {
      markDelivered(update, gd);
      root.__mv_view_diag.fullFrames += 1;
      var layout = update.figure.layout || {};
      var live = root.mattervisCurrentCamera && root.mattervisCurrentCamera(gd);
      if (live && layout.scene) {
        layout = Object.assign({}, layout, {scene: Object.assign({}, layout.scene, {camera: live})});
      }
      return Promise.resolve(root.Plotly.react(gd, update.figure.data || [], layout)).then(function () { return "figure"; }).catch(function (error) {
        /* A failed submission must remain retryable. */
        var key = deliveryKey(update, gd);
        delete delivered[key];
        throw error;
      });
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
