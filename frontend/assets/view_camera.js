/* Camera-only delivery.  Plotly is touched only through this explicit path. */
(function (root) {
  "use strict";
  function graph() {
    var host = document.getElementById("crystal-graph");
    return host && host.querySelector(".js-plotly-plot");
  }
  function apply(camera) {
    var gd = graph();
    if (!gd || !root.Plotly || typeof root.Plotly.relayout !== "function" || !camera) return false;
    /* Plotly's gl3d relayout is asynchronous.  Projection changes are
     * especially sensitive because switching the camera controller while a
     * previous relayout is still drawing can leave the scene with a stale
     * projection and an invalid view matrix.  Reset/align commands then race
     * that stale promise and appear not to recover the scene.  Share the
     * figure queue used by the full-frame gate so camera commands are applied
     * in delivery order and a rejected relayout cannot poison later updates.
     */
    var previous = gd.__mvRenderQueue || Promise.resolve();
    var next = Promise.resolve(previous).catch(function () {}).then(function () {
      return root.Plotly.relayout(gd, {"scene.camera": camera});
    });
    gd.__mvRenderQueue = next.catch(function () {});
    return true;
  }
  root.MatterVisViewCamera = { graph: graph, apply: apply };
})(window);
