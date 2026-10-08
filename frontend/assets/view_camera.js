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
    root.Plotly.relayout(gd, {"scene.camera": camera});
    return true;
  }
  root.MatterVisViewCamera = { graph: graph, apply: apply };
})(window);
