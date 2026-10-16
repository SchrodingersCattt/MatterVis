/* Camera-only delivery. Plotly is touched only through this explicit path. */
(function (root) {
  "use strict";
  var MAX_QUEUE = 32;
  var MAX_QUEUE_RESETS = 2;
  var DEFAULT_TIMEOUT_MS = 1500;
  root.__mv_camera_diag = root.__mv_camera_diag || {attempts: 0, failures: 0, timeouts: 0, staleDrops: 0, postconditionFailures: 0};
  function diag(name) { root.__mv_camera_diag[name] = Number(root.__mv_camera_diag[name] || 0) + 1; }
  function clone(value) {
    if (value === undefined || value === null) return value;
    try {
      if (typeof root.structuredClone === "function") return root.structuredClone(value);
    } catch (_) {}
    return JSON.parse(JSON.stringify(value));
  }
  function timeoutMs() {
    var value = Number(root.MATTERVIS_CAMERA_TIMEOUT_MS || DEFAULT_TIMEOUT_MS);
    return Number.isFinite(value) && value > 0 ? value : DEFAULT_TIMEOUT_MS;
  }
  function graphGeneration(gd) {
    return Number(gd && gd.__mvGraphGeneration || 0);
  }
  function queueEpoch(gd) {
    return Number(gd && gd.__mvQueueEpoch || 0);
  }
  function resetQueue(gd, reason) {
    /* A Plotly promise cannot be cancelled. Abandon the in-flight coordinator
     * after a bounded wait so a hung relayout cannot block Reset or a new
     * frame. Commands that have not started move to the fresh queue. The hung
     * call can no longer acknowledge success, and the scene generation is left
     * unchanged so a same-scene Reset stays eligible. */
    var previous = gd.__mvRenderQueueState;
    var parked = [];
    if (previous && Array.isArray(previous.items)) {
      parked = previous.items.splice(0);
      previous.abandoned = true;
    }
    var resets = Number(gd.__mvCameraResets || 0) + 1;
    gd.__mvCameraResets = resets;
    var epoch = queueEpoch(gd) + 1;
    gd.__mvQueueEpoch = epoch;
    gd.__mvCameraQueueRecovery = {epoch: epoch, reason: String(reason || "timeout"), at: Date.now(), resets: resets};
    gd.__mvRenderQueueState = {items: [], running: false, last: Promise.resolve(gd)};
    gd.__mvRenderQueue = gd.__mvRenderQueueState.last;
    gd.__mvCameraQueue = gd.__mvRenderQueueState;
    var bounded = resets <= MAX_QUEUE_RESETS;
    parked.forEach(function (item) {
      if (!bounded || (item.isCurrent && !item.isCurrent())) {
        item.reject(new Error(bounded ? "stale camera command" : "camera relayout timeout"));
        return;
      }
      enqueue(gd, item.task, {isCurrent: item.isCurrent, group: "camera"}).then(item.resolve, item.reject);
    });
    if (bounded && !parked.length && String(reason || "") === "camera relayout timeout") replayLatest(gd, epoch);
    return epoch;
  }
  function replayLatest(gd, epoch) {
    var latest = gd.__mvLatestCamera;
    if (!latest || latest.replayed || !latest.patch) return;
    latest.replayed = true;
    enqueue(gd, function () {
      if (queueEpoch(gd) !== epoch) throw new Error("stale camera command");
      if (postcondition(gd, latest.snapshot)) return gd;
      return Promise.resolve(root.Plotly.relayout(gd, clone(latest.patch))).then(function () {
        if (postcondition(gd, latest.snapshot)) return gd;
        return recover(gd, latest.snapshot).then(function () {
          if (!postcondition(gd, latest.snapshot)) throw new Error("camera postcondition failed");
        });
      }, function () {
        return recover(gd, latest.snapshot).then(function () {
          if (!postcondition(gd, latest.snapshot)) throw new Error("camera postcondition failed");
        });
      });
    }, {group: "camera"});
  }
  function enqueue(gd, task, options) {
    options = options || {};
    var coordinator = root.MatterVisRenderQueue;
    if (coordinator && typeof coordinator.enqueue === "function") {
      return coordinator.enqueue(gd, task, {
        rejectDropped: true,
        isCurrent: options.isCurrent,
        group: options.group,
      });
    }
    var queue = gd.__mvCameraQueue;
    if (!queue || queue !== gd.__mvRenderQueueState) {
      queue = gd.__mvCameraQueue = gd.__mvRenderQueueState = {items: [], running: false, last: Promise.resolve(gd)};
    }
    if (queue.items.length >= MAX_QUEUE) queue.items.splice(0).forEach(function (item) { item.reject(new Error("render queue overflow")); });
    var result = new Promise(function (resolve, reject) { queue.items.push({task: task, resolve: resolve, reject: reject, isCurrent: options.isCurrent}); });
    queue.last = result.catch(function () { return gd; });
    gd.__mvRenderQueue = queue.last;
    function pump() {
      if (queue.running || !queue.items.length) return;
      var item = queue.items.shift();
      if (item.isCurrent && !item.isCurrent()) {
        item.reject(new Error("stale camera command"));
        pump();
        return;
      }
      queue.running = true;
      Promise.resolve().then(item.task).then(item.resolve, item.reject).then(function () { queue.running = false; pump(); }, function () { queue.running = false; pump(); });
    }
    pump();
    return result;
  }
  function withTimeout(promise, ms, onTimeout) {
    return new Promise(function (resolve, reject) {
      var settled = false;
      var timer = setTimeout(function () {
        if (settled) return;
        settled = true;
        try { if (onTimeout) onTimeout(); } catch (_) {}
        diag("timeouts");
        reject(new Error("camera relayout timeout"));
      }, ms);
      Promise.resolve(promise).then(function (value) {
        if (settled) return;
        settled = true; clearTimeout(timer); resolve(value);
      }, function (error) {
        if (settled) return;
        settled = true; clearTimeout(timer); reject(error);
      });
    });
  }
  function graph() {
    var doc = root.document || (typeof document !== "undefined" ? document : null);
    var host = doc && doc.getElementById("crystal-graph");
    return host && host.querySelector(".js-plotly-plot");
  }
  function completeVector(value) {
    if (!value || typeof value !== "object") return false;
    if (Array.isArray(value)) return value.length >= 3 && value.every(function (x) { return Number.isFinite(Number(x)); });
    return ["x", "y", "z"].every(function (key) { return Number.isFinite(Number(value[key])); });
  }
  function projectionType(camera) {
    var value = camera && camera.projection;
    if (typeof value === "string") return value;
    return value && value.type ? String(value.type) : null;
  }
  function requestedPatch(camera) {
    var patch = {};
    var projection = projectionType(camera);
    if (projection) patch["scene.camera.projection.type"] = projection;
    ["eye", "center", "up"].forEach(function (part) {
      if (completeVector(camera && camera[part])) patch["scene.camera." + part] = clone(camera[part]);
    });
    return patch;
  }
  function actualCamera(gd) {
    if (!gd) return null;
    var full = gd._fullLayout || {};
    var scene = full.scene || {};
    var internal = scene._scene;
    if (internal && typeof internal.getCamera === "function") {
      try {
        var camera = internal.getCamera();
        if (camera && camera.eye && camera.center && camera.up) return camera;
      } catch (_) {}
    }
    var controller = internal && (internal.camera || (internal.glplot && internal.glplot.camera));
    if (controller) {
      var eye = controller.eye, center = controller.center, up = controller.up;
      if (eye && center && up) return {
        eye: {x: eye[0], y: eye[1], z: eye[2]},
        center: {x: center[0], y: center[1], z: center[2]},
        up: {x: up[0], y: up[1], z: up[2]},
        projection: {type: controller._ortho ? "orthographic" : "perspective"},
      };
    }
    if (scene.camera) return scene.camera;
    if (gd.layout && gd.layout.scene && gd.layout.scene.camera) return gd.layout.scene.camera;
    return null;
  }
  function visibleGeometry(gd) {
    var data = gd && Array.isArray(gd.data) ? gd.data : [];
    function hasValues(value) {
      return (Array.isArray(value) || (typeof ArrayBuffer !== "undefined" && ArrayBuffer.isView && ArrayBuffer.isView(value))) && value.length > 0;
    }
    return data.some(function (trace) {
      if (!trace || trace.visible === false || trace.visible === "legendonly") return false;
      var type = String(trace.type || "").toLowerCase();
      if (type === "mesh3d") return hasValues(trace.x) && hasValues(trace.i);
      return hasValues(trace.x) || hasValues(trace.y) || hasValues(trace.z);
    });
  }
  function internalProjection(gd) {
    var scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    if (!scene) return null;
    var camera = null;
    if (typeof scene.getCamera === "function") {
      try { camera = scene.getCamera(); } catch (_) {}
    }
    var got = projectionType(camera);
    if (got) return got;
    var controller = scene.camera || (scene.glplot && scene.glplot.camera);
    if (controller && Object.prototype.hasOwnProperty.call(controller, "_ortho")) {
      return controller._ortho ? "orthographic" : "perspective";
    }
    return null;
  }
  function vectorLength(value) {
    if (!value) return null;
    var x = value.x !== undefined ? Number(value.x) : Number(value[0]);
    var y = value.y !== undefined ? Number(value.y) : Number(value[1]);
    var z = value.z !== undefined ? Number(value.z) : Number(value[2]);
    if (![x, y, z].every(function (item) { return Number.isFinite(item); })) return null;
    return Math.hypot(x, y, z);
  }
  function liveEye(gd) {
    var scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    var controller = scene && (scene.camera || (scene.glplot && scene.glplot.camera));
    if (controller && controller.eye) return controller.eye;
    var camera = actualCamera(gd);
    return camera && camera.eye;
  }
  function restoresViewport(options) {
    return /reset|fit/i.test(String(options && options.command || ""));
  }
  function restoreCanonicalViewport(gd) {
    var canonical = gd && gd.__mvCanonicalViewport;
    if (!canonical || !canonical.aspectratio) return;
    var gl = gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene && gd._fullLayout.scene._scene.glplot;
    if (gl && typeof gl.setAspectratio === "function") {
      try { gl.setAspectratio(clone(canonical.aspectratio)); } catch (_) {}
    }
  }
  function axisComponent(value, index, name) {
    if (!value) return 0;
    if (value[name] !== undefined && value[name] !== null) return Number(value[name]);
    return Number(value[index] || 0);
  }
  function installWheelZoom(gd) {
    /* Plotly's orthographic wheel handler multiplies the scene aspect
     * ratio instead of the camera distance. That zoom survives Reset and
     * makes the next perspective wheel jump to a speck. Zoom the eye
     * distance for both projections, and stop the aspect-ratio listener
     * before it runs. The listener lives on document so it runs ahead of
     * Plotly's canvas handler. */
    if (gd) root.__mvWheelGraph = gd;
    if (root.__mvWheelZoomInstalled) return;
    if (typeof document === "undefined" || !document.addEventListener) return;
    root.__mvWheelZoomInstalled = true;
    document.addEventListener("wheel", function (event) {
      var graph = root.__mvWheelGraph;
      if (!graph || !graph.isConnected) return;
      var scene = graph._fullLayout && graph._fullLayout.scene && graph._fullLayout.scene._scene;
      var canvas = scene && scene.glplot && scene.glplot.canvas;
      if (!canvas) return;
      var target = event.target;
      if (target !== canvas && !(target && canvas.contains && canvas.contains(target))) return;
      if (Math.abs(event.deltaX || 0) > Math.abs(event.deltaY || 0)) return;
      var controller = scene.camera || (scene.glplot && scene.glplot.camera);
      if (!controller || !controller.eye) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      var factor = Math.exp(Number(event.deltaY || 0) / 900);
      if (!Number.isFinite(factor)) return;
      factor = Math.max(0.72, Math.min(1.4, factor));
      var eye = controller.eye;
      var next = [
        axisComponent(eye, 0, "x") * factor,
        axisComponent(eye, 1, "y") * factor,
        axisComponent(eye, 2, "z") * factor,
      ];
      var dist = Math.hypot(next[0], next[1], next[2]);
      if (!(dist > 0)) return;
      if (dist < 0.35) { var near = 0.35 / dist; next = next.map(function (v) { return v * near; }); }
      /* Perspective zoom-out used to stop at eye length 8, which clipped a
       * supercell before the whole cell fit. Orthographic size does not
       * follow eye length; the cap only bounds the perspective dolly. */
      var maxEyeDistance = 24;
      if (dist > maxEyeDistance) { var far = maxEyeDistance / dist; next = next.map(function (v) { return v * far; }); }
      var center = controller.center || [0, 0, 0];
      var up = controller.up || [0, 0, 1];
      var projection = controller._ortho ? "orthographic" : "perspective";
      Promise.resolve(root.Plotly.relayout(graph, {
        "scene.camera.eye": {x: next[0], y: next[1], z: next[2]},
        "scene.camera.center": {x: axisComponent(center, 0, "x"), y: axisComponent(center, 1, "y"), z: axisComponent(center, 2, "z")},
        "scene.camera.up": {x: axisComponent(up, 0, "x"), y: axisComponent(up, 1, "y"), z: axisComponent(up, 2, "z")},
        "scene.camera.projection.type": projection,
      })).catch(function () {});
    }, {capture: true, passive: false});
  }
  var resizeTimer = null;
  function installCanvasRefit(gd) {
    var doc = root.document || (typeof document !== "undefined" ? document : null);
    if (!gd || gd.__mvCanvasRefit || !doc) return;
    gd.__mvCanvasRefit = true;
    function refit() {
      if (!gd.isConnected) return;
      if (root.Plotly && root.Plotly.Plots && typeof root.Plotly.Plots.resize === "function") {
        try { root.Plotly.Plots.resize(gd); } catch (_) {}
      }
      restoreCanonicalViewport(gd);
    }
    if (typeof root.ResizeObserver === "function") {
      var observer = new root.ResizeObserver(function () {
        if (resizeTimer) root.clearTimeout(resizeTimer);
        resizeTimer = root.setTimeout(refit, 80);
      });
      observer.observe(gd);
    }
    var viewport = root.visualViewport;
    if (viewport && viewport.addEventListener) {
      viewport.addEventListener("resize", function () {
        if (resizeTimer) root.clearTimeout(resizeTimer);
        resizeTimer = root.setTimeout(refit, 80);
      });
    }
  }
  function reportStatus(level, message) {
    root.__mvRenderStatus = {level: String(level || "info"), message: String(message || ""), at: Date.now()};
    var doc = root.document || (typeof document !== "undefined" ? document : null);
    var node = doc && doc.getElementById && doc.getElementById("mv-render-status");
    if (node) {
      node.hidden = !message;
      node.textContent = message || "";
      node.setAttribute("data-level", root.__mvRenderStatus.level);
    }
  }
  function installContextLoss(gd) {
    if (!gd) return;
    var scene = gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    var canvas = (scene && scene.glplot && scene.glplot.canvas) || (gd.querySelector && gd.querySelector("canvas")) || gd;
    if (!canvas || !canvas.addEventListener) return;
    /* Plotly replaces the gl3d canvas when switching projection or rebuilding
     * a scene.  Keep the listener identity on the canvas itself rather than
     * on the graph div so a replacement receives a fresh handler.  The old
     * handler remains harmless: it checks __mvContextRetired/detached and
     * compares its canvas with the currently live Plotly canvas below. */
    if (gd.__mvContextLossCanvas === canvas) return;
    canvas.addEventListener("webglcontextlost", function (event) {
      if (event && event.preventDefault) event.preventDefault();
      /* Plotly replaces the gl3d canvas during a projection relayout.  The
       * old canvas is deliberately retired by the render gate and its
       * context is released; that is not a real loss of the live scene.
       * Ignore events from a retired/detached canvas so a normal projection
       * change cannot show a false error or start a recovery against a stale
       * scene. */
      if (canvas.__mvContextRetired || (canvas.isConnected === false)) return;
      var liveScene = gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
      var liveCanvas = liveScene && liveScene.glplot && liveScene.glplot.canvas;
      if (liveCanvas && liveCanvas !== canvas) return;
      reportStatus("error", "WebGL context lost. Restoring the current view.");
      var latest = gd.__mvLatestCamera && gd.__mvLatestCamera.snapshot;
      if (latest) recover(gd, latest);
    });
    gd.__mvContextLossCanvas = canvas;
    gd.__mvContextLossInstalled = true;
  }
  function postcondition(gd, camera, options) {
    var expected = projectionType(camera);
    var internal = internalProjection(gd);
    /* When Plotly has created the gl3d scene, the internal projection is the
     * source of truth. A missing or different value is a failed update. */
    if (expected && gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene) {
      if (internal !== expected) return false;
    } else if (expected) {
      var got = projectionType(actualCamera(gd));
      if (got && got !== expected) return false;
    }
    /* If Plotly exposes a scene/data model, require geometry to remain
     * drawable. Isolated hosts with no data model still get the camera check. */
    if (gd && Array.isArray(gd.data) && gd.data.length && !visibleGeometry(gd)) return false;
    if (options && options.requireEye) {
      var expectedDistance = vectorLength(camera && camera.eye);
      var gotDistance = vectorLength(liveEye(gd));
      if (expectedDistance && gotDistance && Math.abs(gotDistance - expectedDistance) / Math.max(expectedDistance, 1e-6) > 0.35) return false;
    }
    var canonical = gd && gd.__mvCanonicalViewport;
    if (options && options.restoreViewport && canonical && canonical.aspectratio) {
      var gl = gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene && gd._fullLayout.scene._scene.glplot;
      var aspect = gl && typeof gl.getAspectratio === "function" ? gl.getAspectratio() : null;
      if (aspect && ["x", "y", "z"].some(function (axis) {
        var want = Number(canonical.aspectratio[axis]);
        var have = Number(aspect[axis]);
        return Number.isFinite(want) && Number.isFinite(have) && Math.abs(have - want) / Math.max(Math.abs(want), 1e-6) > 0.08;
      })) return false;
    }
    return true;
  }
  function recover(gd, camera) {
    if (!root.Plotly || typeof root.Plotly.react !== "function" || !gd || !Array.isArray(gd.data)) return Promise.resolve();
    var layout = clone(gd.layout || {});
    layout.scene = Object.assign({}, layout.scene || {});
    var requested = clone(camera);
    layout.scene.camera = Object.assign({}, layout.scene.camera || {}, requested);
    var canonical = gd.__mvCanonicalViewport;
    if (canonical) {
      if (canonical.aspectmode) layout.scene.aspectmode = canonical.aspectmode;
      if (canonical.aspectratio) layout.scene.aspectratio = clone(canonical.aspectratio);
    }
    return Promise.resolve(root.Plotly.react(gd, clone(gd.data), layout));
  }
  function contextCurrent(gd, options) {
    options = options || {};
    if (typeof options.isCurrent === "function" && !options.isCurrent()) return false;
    var meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    if (meta && options.scene_id && meta.scene_id && String(meta.scene_id) !== String(options.scene_id)) return false;
    if (meta && options.scene_generation !== undefined && meta.scene_generation !== undefined && String(meta.scene_generation) !== String(options.scene_generation)) return false;
    /* A same-scene redraw bumps graph generation. That must not drop the
     * latest camera command. A different scene, or a remount with no scene
     * identity, still invalidates the queued camera. */
    if (options.graph_generation !== undefined && graphGeneration(gd) !== Number(options.graph_generation)) {
      var sameScene = !!(options.scene_id && meta && meta.scene_id && String(meta.scene_id) === String(options.scene_id));
      if (!sameScene) return false;
    }
    if (options.camera_version !== undefined && options.camera_version !== null) {
      var requested = Number(options.camera_version);
      var current = Number(gd && gd.__mvCameraRevision);
      if (Number.isFinite(requested) && Number.isFinite(current) && requested < current) return false;
    }
    return true;
  }
  function settledCurrent(gd, snapshot, epoch, current, options) {
    var check = {requireEye: restoresViewport(options), restoreViewport: restoresViewport(options)};
    if (queueEpoch(gd) !== epoch || !current()) { diag("staleDrops"); throw new Error("stale camera command"); }
    if (!postcondition(gd, snapshot, check)) {
      diag("postconditionFailures");
      reportStatus("error", "The camera update did not match the view. Restoring it.");
      return recover(gd, snapshot).then(function () {
        if (queueEpoch(gd) !== epoch || !current()) throw new Error("stale camera command");
        if (!postcondition(gd, snapshot, check)) { diag("postconditionFailures"); reportStatus("error", "The camera update could not be restored."); throw new Error("camera postcondition failed"); }
        reportStatus("recovered", "Restored the current camera view.");
        gd.__mvCameraResets = 0;
      });
    }
    reportStatus("ok", "");
    gd.__mvCameraResets = 0;
  }
  function applyAsync(camera, options) {
    options = options || {};
    var gd = graph();
    if (!gd || !root.Plotly || typeof root.Plotly.relayout !== "function" || !camera) {
      /* Plotly briefly removes the graph while replacing a gl3d canvas. A
       * camera update arriving in that window must remain retryable rather
       * than poisoning the delivery key and leaving an error toast behind.
       * Bound the wait so a genuinely missing graph still fails visibly. */
      if (camera && root.Plotly && typeof root.Plotly.relayout === "function") {
        var waitUntil = Number(options.__mvGraphWaitUntil || 0);
        if (!waitUntil) {
          waitUntil = Date.now() + 2000;
          options.__mvGraphWaitUntil = waitUntil;
        }
        if (Date.now() < waitUntil && typeof root.setTimeout === "function") {
          return new Promise(function (resolve, reject) {
            root.setTimeout(function () {
              applyAsync(camera, options).then(resolve, reject);
            }, 25);
          });
        }
      }
      reportStatus("error", "The 3D view is not ready. The last good frame was kept.");
      return Promise.reject(new Error("camera graph unavailable"));
    }
    delete options.__mvGraphWaitUntil;
    installWheelZoom(gd);
    installContextLoss(gd);
    var snapshot = clone(camera);
    var patch = requestedPatch(snapshot);
    if (restoresViewport(options)) {
      restoreCanonicalViewport(gd);
      var canonical = gd.__mvCanonicalViewport;
      if (canonical) {
        if (canonical.aspectmode) patch["scene.aspectmode"] = canonical.aspectmode;
        if (canonical.aspectratio) patch["scene.aspectratio"] = clone(canonical.aspectratio);
      }
      var previousUi = gd.layout && gd.layout.scene && gd.layout.scene.uirevision;
      patch["scene.uirevision"] = String(previousUi || "scene") + "#reset" + String(options.camera_version || Date.now());
    }
    if (!Object.keys(patch).length) return Promise.reject(new Error("invalid camera payload"));
    var requestedRevision = Number(options.camera_version);
    var revisionScope = String(options.scene_id || "") + ":" + String(options.scene_generation === undefined ? "" : options.scene_generation);
    if (gd.__mvCameraRevisionScope !== revisionScope) {
      gd.__mvCameraRevisionScope = revisionScope;
      gd.__mvCameraRevision = NaN;
    }
    var previousRevision = Number(gd.__mvCameraRevision);
    if (Number.isFinite(requestedRevision) && (!Number.isFinite(previousRevision) || requestedRevision > previousRevision)) {
      gd.__mvCameraRevision = requestedRevision;
      /* A newer Reset must not wait behind a relayout that never settles.
       * Replace only a currently running coordinator; the old promise is
       * bounded below and can no longer mutate the new queue's state. */
      if (Number.isFinite(previousRevision) && requestedRevision > previousRevision && gd.__mvRenderQueueState && gd.__mvRenderQueueState.running) {
        resetQueue(gd, "superseded camera revision");
        options.graph_generation = graphGeneration(gd);
      }
    }
    var submittedGeneration = graphGeneration(gd);
    if (options.graph_generation === undefined) options.graph_generation = submittedGeneration;
    var current = function () { return contextCurrent(gd, options); };
    if (!current()) return Promise.reject(new Error("stale camera command"));
    gd.__mvLatestCamera = {snapshot: snapshot, patch: patch, replayed: false};
    var run = function () {
      if (!current()) { diag("staleDrops"); throw new Error("stale camera command"); }
      var epoch = queueEpoch(gd);
      var queueToken = gd.__mvRenderQueueState;
      /* Bound only the Plotly call. Time spent waiting behind an earlier
       * command must not expire Reset before it starts, and a timeout from a
       * command that a newer revision already replaced must not reset the new
       * queue. */
      var attempt = withTimeout(Promise.resolve(root.Plotly.relayout(gd, clone(patch))), timeoutMs(), function () {
        if (gd.__mvRenderQueueState === queueToken) resetQueue(gd, "camera relayout timeout");
      });
      var check = {requireEye: restoresViewport(options), restoreViewport: restoresViewport(options)};
      return attempt.then(function () {
        /* A drag relayout can resolve after Reset already replaced the queue.
         * Re-apply the newest command so the late Plotly write cannot stick. */
        if (Number.isFinite(requestedRevision) && Number(gd.__mvCameraRevision) !== requestedRevision) {
          var latest = gd.__mvLatestCamera;
          var restore = latest && latest.patch ? Promise.resolve(root.Plotly.relayout(gd, clone(latest.patch))) : Promise.resolve();
          return restore.then(function () { diag("staleDrops"); throw new Error("stale camera command"); });
        }
        return settledCurrent(gd, snapshot, epoch, current, options);
      }, function (error) {
        if (error && /timeout/.test(String(error && error.message || error))) throw error;
        if (queueEpoch(gd) !== epoch || !current()) { diag("staleDrops"); throw new Error("stale camera command"); }
        diag("postconditionFailures");
        reportStatus("error", "Camera relayout failed. Restoring the current view.");
        return recover(gd, snapshot).then(function () {
          if (queueEpoch(gd) !== epoch || !current()) throw new Error("stale camera command");
          if (!postcondition(gd, snapshot, check)) {
            reportStatus("error", "Camera relayout failed and the view could not be restored.");
            throw error;
          }
          reportStatus("recovered", "Restored the current camera view.");
          gd.__mvCameraResets = 0;
        });
      });
    };
    diag("attempts");
    return enqueue(gd, run, {isCurrent: current, group: "camera"}).then(function () { return true; }).catch(function (error) { diag("failures"); throw error; });
  }
  function apply(camera, options) {
    var gd = graph();
    if (!gd || !root.Plotly || typeof root.Plotly.relayout !== "function" || !camera) {
      reportStatus("error", "The 3D view is not ready. The last good frame was kept.");
      return false;
    }
    applyAsync(camera, options).catch(function () {});
    return true;
  }
  root.MatterVisViewCamera = {
    graph: graph,
    apply: apply,
    applyAsync: applyAsync,
    actualCamera: actualCamera,
    visibleGeometry: visibleGeometry,
    resetQueue: resetQueue,
    installWheelZoom: installWheelZoom,
    installCanvasRefit: installCanvasRefit,
    installContextLoss: installContextLoss,
    reportStatus: reportStatus,
    postcondition: postcondition,
    recover: recover,
  };
})(window);
