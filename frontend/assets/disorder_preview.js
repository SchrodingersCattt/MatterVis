/* Disorder preview traces share the graph mutation queue.
 * A preview requested for one scene is dropped if that graph is replaced
 * before the deferred Plotly call runs. */
(function (root) {
  "use strict";
  var PREFIX = "disorder-preview-outline:";

  function graph() {
    var doc = root.document;
    if (!doc || !doc.querySelector) return null;
    return doc.querySelector("#crystal-graph .js-plotly-plot") || doc.getElementById("crystal-graph");
  }

  function nope() {
    return root.dash_clientside && root.dash_clientside.no_update;
  }

  function sceneId(gd) {
    var meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    return meta && meta.scene_id ? String(meta.scene_id) : "";
  }

  function enqueue(gd, task) {
    var queue = root.MatterVisRenderQueue;
    if (queue && typeof queue.enqueue === "function") return queue.enqueue(gd, task, {group: "disorder"});
    return Promise.resolve().then(task);
  }

  function liveViewport(gd) {
    try {
      var scene = gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
      if (!scene || typeof scene.getCamera !== "function") return null;
      return {
        scene: scene,
        camera: JSON.parse(JSON.stringify(scene.getCamera())),
        aspectratio: scene.glplot && scene.glplot.getAspectratio && scene.glplot.getAspectratio(),
      };
    } catch (_) {
      return null;
    }
  }

  function restoreViewport(viewport) {
    if (!viewport || !viewport.scene || !viewport.camera) return;
    try {
      viewport.scene.setViewport({
        camera: viewport.camera,
        aspectratio: viewport.aspectratio || (viewport.scene.glplot && viewport.scene.glplot.getAspectratio && viewport.scene.glplot.getAspectratio()),
      });
    } catch (_) {}
  }

  function prepare(store) {
    var gd = graph();
    if (!gd || !root.Plotly || !gd.data || typeof gd.on !== "function") return nope();
    var oldIndices = [];
    for (var i = 0; i < gd.data.length; i += 1) {
      if (String(gd.data[i].name || "").indexOf(PREFIX) === 0) oldIndices.push(i);
    }
    var traces = [];
    var replicas = store && store.replicas || [];
    replicas.forEach(function (rep) {
      var mesh = (rep && rep.preview_mesh) || {};
      var replicaId = String((rep && rep.id) || "");
      if (!replicaId || !mesh.x || !mesh.x.length) return;
      traces.push({
        type: "mesh3d", name: PREFIX + replicaId,
        x: mesh.x, y: mesh.y || [], z: mesh.z || [], i: mesh.i || [], j: mesh.j || [], k: mesh.k || [],
        color: "#FFD400", opacity: 0, hoverinfo: "skip", showlegend: false, visible: true,
        meta: {kind: "disorder-preview-outline", replica_id: replicaId},
      });
    });
    var viewport = liveViewport(gd);
    var sceneAtStart = sceneId(gd);
    var generationAtStart = Number(gd.__mvGraphGeneration || 0);
    function current() {
      var scene = sceneId(gd);
      if (sceneAtStart && scene && scene !== sceneAtStart) return false;
      return Number(gd.__mvGraphGeneration || 0) === generationAtStart;
    }
    var chain = Promise.resolve();
    if (oldIndices.length) {
      chain = chain.then(function () {
        return enqueue(gd, function () {
          if (!current()) return null;
          return root.Plotly.deleteTraces(gd, oldIndices);
        });
      });
    }
    if (traces.length) {
      chain = chain.then(function () {
        return enqueue(gd, function () {
          if (!current()) return null;
          return root.Plotly.addTraces(gd, traces);
        });
      });
    }
    chain.then(function () {
      if (!current()) return;
      var schedule = root.requestAnimationFrame || function (fn) { fn(); };
      schedule(function () { restoreViewport(viewport); });
    });
    return nope();
  }

  function hover(replicaId) {
    var gd = graph();
    if (!gd || !root.Plotly || !gd.data || typeof gd.on !== "function") return nope();
    var indices = [];
    var opacity = [];
    for (var t = 0; t < gd.data.length; t += 1) {
      var name = String(gd.data[t].name || "");
      if (name.indexOf(PREFIX) !== 0) continue;
      indices.push(t);
      opacity.push(Boolean(replicaId) && name === PREFIX + String(replicaId) ? 0.55 : 0);
    }
    if (!indices.length) return nope();
    enqueue(gd, function () { return root.Plotly.restyle(gd, {opacity: opacity}, indices); });
    return nope();
  }

  root.MatterVisDisorderPreview = {prepare: prepare, hover: hover};
})(typeof window !== "undefined" ? window : globalThis);
