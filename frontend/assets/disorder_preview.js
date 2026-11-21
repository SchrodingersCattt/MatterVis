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

  function liveScene(gd) {
    return gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene || null;
  }

  function orthoFlag(scene) {
    var camera = scene && scene.camera;
    if (!camera || typeof camera._ortho !== "boolean") return null;
    return camera._ortho;
  }

  function component(value, index, name) {
    if (!value) return NaN;
    if (value[name] !== undefined && value[name] !== null) return Number(value[name]);
    return Number(value[index]);
  }

  function vector(value) {
    var point = {
      x: component(value, 0, "x"),
      y: component(value, 1, "y"),
      z: component(value, 2, "z"),
    };
    return [point.x, point.y, point.z].every(function (item) { return Number.isFinite(item); }) ? point : null;
  }

  /* Eye, center, and up only. A whole scene.camera object, or a private
   * Scene.setViewport call, disposes the glplot when the projection differs
   * and does not run Scene.plot, which leaves the new scene undrawn. */
  function sameProjectionPatch(camera) {
    var eye = vector(camera && camera.eye);
    var center = vector(camera && camera.center);
    var up = vector(camera && camera.up);
    if (!eye || !center || !up) return null;
    return {
      "scene.camera.eye": eye,
      "scene.camera.center": center,
      "scene.camera.up": up,
    };
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
    if (!oldIndices.length && !traces.length) return nope();
    var sceneAtStart = sceneId(gd);
    var generationAtStart = Number(gd.__mvGraphGeneration || 0);
    var sceneObject = liveScene(gd);
    var orthoAtStart = orthoFlag(sceneObject);
    var saved = null;
    try {
      saved = sceneObject && typeof sceneObject.getCamera === "function"
        ? JSON.parse(JSON.stringify(sceneObject.getCamera()))
        : null;
    } catch (_) {
      saved = null;
    }
    function current() {
      var shown = sceneId(gd);
      if (sceneAtStart && shown && shown !== sceneAtStart) return false;
      if (Number(gd.__mvGraphGeneration || 0) !== generationAtStart) return false;
      var scene = liveScene(gd);
      if (sceneObject && scene && scene !== sceneObject) return false;
      var ortho = orthoFlag(scene);
      if (orthoAtStart !== null && ortho !== null && ortho !== orthoAtStart) return false;
      return true;
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
    var patch = sameProjectionPatch(saved);
    if (patch && typeof root.Plotly.relayout === "function") {
      chain = chain.then(function () {
        return enqueue(gd, function () {
          if (!current()) return null;
          return root.Plotly.relayout(gd, patch);
        });
      });
    }
    chain.catch(function () {});
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
