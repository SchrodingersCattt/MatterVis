/* Camera-only delivery.  Plotly is touched only through this explicit path. */
(function (root) {
  "use strict";
  var MAX_QUEUE = 32;
  function clone(value) {
    if (value === undefined || value === null) return value;
    try {
      if (typeof root.structuredClone === "function") return root.structuredClone(value);
    } catch (_) {}
    return JSON.parse(JSON.stringify(value));
  }
  function enqueue(gd, task) {
    if (root.MatterVisRenderQueue && typeof root.MatterVisRenderQueue.enqueue === "function") {
      return root.MatterVisRenderQueue.enqueue(gd, task, {rejectDropped: true});
    }
    /* Keep this asset safe when it is loaded in isolation (for example in a
     * small host integration test before mattervis.js). */
    var queue = gd.__mvCameraQueue;
    if (!queue) queue = gd.__mvCameraQueue = {items: [], running: false, last: Promise.resolve(gd)};
    if (queue.items.length >= MAX_QUEUE) queue.items.splice(0).forEach(function (item) { item.reject(new Error("render queue overflow")); });
    var result = new Promise(function (resolve, reject) { queue.items.push({task: task, resolve: resolve, reject: reject}); });
    queue.last = result.catch(function () { return gd; });
    gd.__mvRenderQueue = queue.last;
    function pump() {
      if (queue.running || !queue.items.length) return;
      var item = queue.items.shift(); queue.running = true;
      Promise.resolve().then(item.task).then(item.resolve, item.reject).then(function () { queue.running = false; pump(); }, function () { queue.running = false; pump(); });
    }
    pump();
    return result;
  }
  function graph() {
    var host = document.getElementById("crystal-graph");
    return host && host.querySelector(".js-plotly-plot");
  }
  function applyAsync(camera) {
    var gd = graph();
    if (!gd || !root.Plotly || typeof root.Plotly.relayout !== "function" || !camera) return Promise.reject(new Error("camera graph unavailable"));
    /* Snapshot before enqueueing: Dash may reuse and mutate the update object
     * while Plotly is still drawing the previous camera. */
    var snapshot = clone(camera);
    return enqueue(gd, function () {
      /* Plotly's gl3d projection controller must change in the same ordered
       * relayout as eye/center/up. */
      return Promise.resolve(root.Plotly.relayout(gd, {"scene.camera": clone(snapshot)}));
    }).then(function () { return true; });
  }
  function apply(camera) {
    var gd = graph();
    if (!gd || !root.Plotly || typeof root.Plotly.relayout !== "function" || !camera) return false;
    /* Keep the historic synchronous boolean API. Typed view updates use
     * applyAsync so acknowledgement waits for Plotly completion. */
    applyAsync(camera).catch(function () {});
    return true;
  }
  root.MatterVisViewCamera = { graph: graph, apply: apply, applyAsync: applyAsync };
})(window);
