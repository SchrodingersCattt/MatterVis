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
    var current = function () { return queueEpoch(gd) === epoch && gd.__mvLatestCamera === latest; };
    // Recovery joins the render FIFO through Plotly.react. Run it after the
    // replay task releases the FIFO, and stop if another camera superseded it.
    var finish = function () {
      if (!current() || postcondition(gd, latest.snapshot)) return gd;
      return recover(gd, latest.snapshot).then(function () {
        if (!current()) return gd;
        if (!postcondition(gd, latest.snapshot)) throw new Error("camera postcondition failed");
      });
    };
    enqueue(gd, function () {
      if (!current()) return gd;
      if (postcondition(gd, latest.snapshot)) return gd;
      return Promise.resolve(root.Plotly.relayout(gd, clone(latest.patch)));
    }, {isCurrent: current, group: "camera"}).then(finish, finish).catch(function () {
      if (current()) reportStatus("error", "The camera update could not be restored.");
    });
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
  /* Plotly's perspective near plane is 0.01 in the normalized model box
   * (each axis spans [-aspect/2, aspect/2]). An eye closer than the front
   * face of that box slices atoms. Keep a small face margin so a sphere
   * on the front of the data range stays beyond the near plane.
   * Matches mat_viewer.render.screen_projection.PLOTLY_NEAR. */
  var PLOTLY_NEAR = 0.01;
  var PERSPECTIVE_ZOOM_FACE_MARGIN = 0.08;
  function minimumPerspectiveEyeDistance(eye, center, aspect) {
    var ax = Number(aspect && aspect.x) || 1;
    var ay = Number(aspect && aspect.y) || 1;
    var az = Number(aspect && aspect.z) || 1;
    var dx = axisComponent(eye, 0, "x") - axisComponent(center, 0, "x");
    var dy = axisComponent(eye, 1, "y") - axisComponent(center, 1, "y");
    var dz = axisComponent(eye, 2, "z") - axisComponent(center, 2, "z");
    var dist = Math.hypot(dx, dy, dz);
    var front;
    if (!(dist > 0)) front = 0.5 * Math.max(ax, ay, az);
    else front = 0.5 * (ax * Math.abs(dx) + ay * Math.abs(dy) + az * Math.abs(dz)) / dist;
    return front + PLOTLY_NEAR + PERSPECTIVE_ZOOM_FACE_MARGIN;
  }
  function rememberFittedFrame(sceneId, ranges, aspectratio) {
    if (!sceneId || !ranges) return;
    if (!root.__mvFittedFrames) root.__mvFittedFrames = {};
    root.__mvFittedFrames[String(sceneId)] = {
      ranges: clone(ranges),
      aspectratio: aspectratio ? clone(aspectratio) : null,
    };
  }
  function clearFittedFrame(sceneId) {
    if (sceneId && root.__mvFittedFrames) delete root.__mvFittedFrames[String(sceneId)];
  }
  function restoreFittedRanges(gd) {
    if (!gd || gd.__mvFittedRestoring) return;
    var render = gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    var sceneId = render && render.scene_id;
    var fitted = sceneId && root.__mvFittedFrames && root.__mvFittedFrames[String(sceneId)];
    if (!fitted || !fitted.ranges || !root.Plotly || typeof root.Plotly.relayout !== "function") return;
    var scene = gd._fullLayout && gd._fullLayout.scene;
    if (!scene) return;
    var mismatch = ["x", "y", "z"].some(function (axis) {
      var live = scene[axis + "axis"] && scene[axis + "axis"].range;
      var want = fitted.ranges[axis];
      if (!live || !want) return false;
      var liveSpan = Math.abs(Number(live[1]) - Number(live[0]));
      var wantSpan = Math.abs(Number(want[1]) - Number(want[0]));
      return Math.abs(liveSpan - wantSpan) > 1e-3 * Math.max(wantSpan, 1);
    });
    var aspectMismatch = false;
    if (fitted.aspectratio && Number.isFinite(Number(fitted.aspectratio.x)) && scene.aspectratio) {
      aspectMismatch = ["x", "y", "z"].some(function (axis) {
        var want = Number(fitted.aspectratio[axis]);
        var have = Number(scene.aspectratio[axis]);
        return Number.isFinite(want) && Number.isFinite(have) && Math.abs(have - want) / Math.max(Math.abs(want), 1e-6) > 0.01;
      });
    }
    if (!mismatch && !aspectMismatch) return;
    var patch = {};
    if (mismatch) {
      ["x", "y", "z"].forEach(function (axis) {
        var pair = fitted.ranges[axis];
        if (!pair) return;
        patch["scene." + axis + "axis.range"] = [Number(pair[0]), Number(pair[1])];
      });
    }
    if (aspectMismatch) {
      patch["scene.aspectmode"] = "manual";
      patch["scene.aspectratio"] = clone(fitted.aspectratio);
    }
    gd.__mvFittedRestoring = true;
    Promise.resolve(root.Plotly.relayout(gd, patch)).finally(function () {
      gd.__mvFittedRestoring = false;
    });
  }
  function installFittedRangeGuard() {
    /* A display rebuild can replace the graph with a figure whose axis
     * box is the default cell, under an eye that was solved on the fitted
     * box. Plotly.react is wrapped once so the fitted box is put back
     * without adding a plotly_afterplot listener. */
    if (root.__mvFittedRangeGuard || !root.Plotly || typeof root.Plotly.react !== "function" || root.Plotly.__mvFittedWrapped) {
      if (root.Plotly && root.Plotly.__mvFittedWrapped) root.__mvFittedRangeGuard = true;
      return;
    }
    var react = root.Plotly.react.bind(root.Plotly);
    root.Plotly.react = function (gd) {
      var result = react.apply(root.Plotly, arguments);
      return Promise.resolve(result).then(function (value) {
        restoreFittedRanges(gd);
        return value;
      });
    };
    root.Plotly.__mvFittedWrapped = true;
    root.__mvFittedRangeGuard = true;
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
      var aspect = graph._fullLayout && graph._fullLayout.scene && graph._fullLayout.scene.aspectratio;
      var minDist = minimumPerspectiveEyeDistance(next, controller.center || [0, 0, 0], aspect);
      if (dist < minDist) { var nearScale = minDist / dist; next = next.map(function (v) { return v * nearScale; }); }
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
      // Plots.resize no-ops after the first autosize stores width and height.
      // panel_resize measures the center panel and relayouts to that box.
      if (root.MatterVisPanelResize && typeof root.MatterVisPanelResize.resizeScene === "function") {
        root.MatterVisPanelResize.resizeScene();
      } else if (root.Plotly && typeof root.Plotly.relayout === "function") {
        var host = doc.getElementById("center-panel") || gd.parentElement || gd;
        var rect = host.getBoundingClientRect();
        var width = Math.round(rect.width);
        var height = Math.round(rect.height);
        if (width > 1 && height > 1) {
          try { root.Plotly.relayout(gd, {width: width, height: height}); } catch (_) {}
        }
      }
      restoreCanonicalViewport(gd);
    }
    if (typeof root.ResizeObserver === "function") {
      var observer = new root.ResizeObserver(function () {
        if (resizeTimer) root.clearTimeout(resizeTimer);
        resizeTimer = root.setTimeout(refit, 80);
      });
      var host = doc.getElementById("center-panel") || gd;
      observer.observe(host);
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
  function glAspect(gd) {
    var scene = gd && gd._fullLayout && gd._fullLayout.scene;
    var gl = scene && scene._scene && scene._scene.glplot;
    if (gl && typeof gl.getAspectratio === "function") {
      try { return gl.getAspectratio(); } catch (_) {}
    }
    return null;
  }
  function liveAspect(gd) {
    /* The layout ratio is what the next figure and a relayout read.
     * A gl-only write can paint the new fit while the layout keeps the
     * previous one; the contract is the layout the next frame uses. */
    var scene = gd && gd._fullLayout && gd._fullLayout.scene;
    if (scene && scene.aspectratio) return scene.aspectratio;
    return glAspect(gd);
  }
  function aspectOffContract(have, want, tolerance) {
    if (!want) return false;
    var limit = tolerance === undefined ? 0.08 : tolerance;
    return ["x", "y", "z"].some(function (axis) {
      var target = Number(want[axis]);
      var got = Number(have && have[axis]);
      return Number.isFinite(target) && Number.isFinite(got) && Math.abs(got - target) / Math.max(Math.abs(target), 1e-6) > limit;
    });
  }
  function fittedFrame(gd) {
    var render = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    var sceneId = render && render.scene_id;
    if (!sceneId || !root.__mvFittedFrames) return null;
    return root.__mvFittedFrames[String(sceneId)] || null;
  }
  function writeAspect(holder, aspect) {
    if (!holder) return;
    holder.aspectmode = "manual";
    var current = holder.aspectratio;
    if (current) {
      current.x = aspect.x;
      current.y = aspect.y;
      current.z = aspect.z;
    } else {
      holder.aspectratio = {x: aspect.x, y: aspect.y, z: aspect.z};
    }
  }
  function installAspectLock(gd) {
    /* Plotly's scene plot computes the aspect, writes it onto the layout,
     * then calls setAspectratio. A later fit's ratio is lost there: the
     * plot puts the previous ratio back and the gl copy can diverge.
     * The lock runs at that last write and puts the fitted ratio on both
     * the layout object and the gl scene. A new glplot (projection
     * changes dispose the old one) needs the lock installed again. */
    var scene = gd && gd._fullLayout && gd._fullLayout.scene;
    var gl = scene && scene._scene && scene._scene.glplot;
    if (!gl || typeof gl.setAspectratio !== "function" || gl.__mvAspectLock) return;
    var original = gl.setAspectratio;
    gl.setAspectratio = function (aspect) {
      var frame = fittedFrame(gd);
      var fitted = frame && frame.aspectratio;
      var use = aspect;
      if (fitted && Number.isFinite(Number(fitted.x))) {
        use = {x: Number(fitted.x), y: Number(fitted.y), z: Number(fitted.z)};
        writeAspect(gd._fullLayout && gd._fullLayout.scene, use);
        writeAspect(gd.layout && gd.layout.scene, use);
      }
      return original.call(this, use);
    };
    gl.__mvAspectLock = true;
  }
  function holdFittedAspect(gd, aspect, token) {
    /* A scene plot can replace the ratio after the relayout promise
     * resolves and without another plotly_afterplot. Watch the layout
     * until it has held the commanded ratio, and put that ratio back
     * if a late plot writes the previous one. */
    if (!gd || !aspect || !Number.isFinite(Number(aspect.x))) return;
    var stable = 0;
    var ticks = 0;
    function tick() {
      if (Number.isFinite(token) && Number(gd.__mvCameraRevision) !== token) return;
      ticks += 1;
      var scene = gd._fullLayout && gd._fullLayout.scene;
      var live = scene && scene.aspectratio;
      var painted = glAspect(gd);
      var drifted = !live || aspectOffContract(live, aspect, 0.01);
      if (painted && aspectOffContract(painted, aspect, 0.01)) drifted = true;
      if (drifted) {
        stable = 0;
        installAspectLock(gd);
        commitFittedAspect(gd, aspect);
      } else {
        stable += 1;
      }
      if (stable >= 90 || ticks >= 600) return;
      var schedule = root.requestAnimationFrame || root.setTimeout;
      if (typeof schedule === "function") schedule(tick, 32);
    }
    tick();
  }
  function commitFittedAspect(gd, aspect) {
    /* A scene relayout updates the camera and the axis ranges, and can
     * leave aspectratio at the previous figure. Write the commanded
     * ratio into the existing layout objects (so a plot that already
     * holds those objects sees the new numbers) and the gl scene. */
    installAspectLock(gd);
    var frame = fittedFrame(gd);
    var source = aspect && Number.isFinite(Number(aspect.x)) ? aspect : frame && frame.aspectratio;
    if (!gd || !source || !Number.isFinite(Number(source.x))) return;
    var copy = {x: Number(source.x), y: Number(source.y), z: Number(source.z)};
    writeAspect(gd._fullLayout && gd._fullLayout.scene, copy);
    writeAspect(gd.layout && gd.layout.scene, copy);
    var scene = gd._fullLayout && gd._fullLayout.scene;
    var gl = scene && scene._scene && scene._scene.glplot;
    if (gl && typeof gl.setAspectratio === "function") {
      try { gl.setAspectratio(copy); } catch (_) {}
    }
  }
  function fittedAspect(options) {
    var aspect = options && options.aspectratio;
    return aspect && Number.isFinite(Number(aspect.x)) ? aspect : null;
  }
  function cameraCheck(options) {
    return {
      requireEye: restoresViewport(options),
      restoreViewport: restoresViewport(options),
      aspectratio: fittedAspect(options),
      ranges: options && options.ranges,
      command: options && options.command,
    };
  }
  function liveRanges(gd) {
    var scene = gd && gd._fullLayout && gd._fullLayout.scene;
    if (!scene) return null;
    function pair(axis) {
      var range = scene[axis] && scene[axis].range;
      return range ? [Number(range[0]), Number(range[1])] : null;
    }
    return {x: pair("xaxis"), y: pair("yaxis"), z: pair("zaxis")};
  }
  function postconditionReason(gd, camera, options) {
    var expected = projectionType(camera);
    var internal = internalProjection(gd);
    if (expected && gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene) {
      if (internal !== expected) return "projection";
    } else if (expected) {
      var got = projectionType(actualCamera(gd));
      if (got && got !== expected) return "projection";
    }
    if (gd && Array.isArray(gd.data) && gd.data.length && !visibleGeometry(gd)) return "geometry";
    if (options && options.requireEye) {
      var expectedDistance = vectorLength(camera && camera.eye);
      var gotDistance = vectorLength(liveEye(gd));
      if (expectedDistance && gotDistance && Math.abs(gotDistance - expectedDistance) / Math.max(expectedDistance, 1e-6) > 0.35) return "eye";
    }
    var commanded = fittedAspect(options);
    var canonical = gd && gd.__mvCanonicalViewport;
    if (options && options.restoreViewport) {
      var aspect = liveAspect(gd);
      var painted = glAspect(gd);
      if (commanded) {
        if ((aspect && aspectOffContract(aspect, commanded, 0.01)) ||
            (painted && aspectOffContract(painted, commanded, 0.01))) return "fitted-aspect";
      } else if (canonical && canonical.aspectratio && aspect && aspectOffContract(aspect, canonical.aspectratio)) {
        return "canonical-aspect";
      }
    }
    return "";
  }
  function notePostconditionFailure(gd, camera, options, stage) {
    var live = actualCamera(gd);
    var detail = {
      stage: stage,
      command: options && options.command,
      reason: postconditionReason(gd, camera, options),
      expectedEye: camera && camera.eye,
      liveEye: live && live.eye,
      expectedCenter: camera && camera.center,
      liveCenter: live && live.center,
      ranges: liveRanges(gd),
      liveAspect: liveAspect(gd),
      fittedAspect: fittedAspect(options),
      canonicalAspect: gd && gd.__mvCanonicalViewport && gd.__mvCanonicalViewport.aspectratio,
    };
    root.__mv_camera_diag.lastPostcondition = detail;
    if (root.console && root.console.error) root.console.error("MatterVis camera postcondition " + JSON.stringify(detail));
    return detail;
  }
  function postcondition(gd, camera, options) {
    return !postconditionReason(gd, camera, options);
  }
  function recover(gd, camera, options) {
    if (!root.Plotly || typeof root.Plotly.react !== "function" || !gd || !Array.isArray(gd.data)) return Promise.resolve();
    var layout = clone(gd.layout || {});
    layout.scene = Object.assign({}, layout.scene || {});
    var requested = clone(camera);
    layout.scene.camera = Object.assign({}, layout.scene.camera || {}, requested);
    // Recovery must paint the requested camera rather than reuse the live
    // camera that failed validation under the previous uirevision.
    layout.scene.uirevision = String(layout.scene.uirevision || layout.uirevision || "scene") + "#recover" + String(Date.now());
    var render = layout.meta && layout.meta.mattervis_render;
    var scope = render && String(render.scene_id || "") + ":" + String(render.scene_generation === undefined ? "" : render.scene_generation);
    if (render && scope === gd.__mvCameraRevisionScope &&
        String(render.server_started_at || "") === gd.__mvCameraRevisionEpoch && Number.isFinite(Number(gd.__mvCameraRevision))) {
      render.camera_revision = Number(gd.__mvCameraRevision);
    }
    var commanded = fittedAspect(options);
    if (commanded) {
      layout.scene.aspectmode = "manual";
      layout.scene.aspectratio = clone(commanded);
    } else {
      var canonical = gd.__mvCanonicalViewport;
      if (canonical) {
        if (canonical.aspectmode) layout.scene.aspectmode = canonical.aspectmode;
        if (canonical.aspectratio) layout.scene.aspectratio = clone(canonical.aspectratio);
      }
    }
    /* gd.layout keeps the ranges from the last full figure. A relayout
     * writes the fitted box only onto _fullLayout. Replaying gd.layout
     * would put that first box back under the fitted eye. */
    ["x", "y", "z"].forEach(function (axis) {
      var commandedRange = options && options.ranges && options.ranges[axis];
      var live = gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene[axis + "axis"];
      var pair = commandedRange && commandedRange.length >= 2 ? commandedRange : (live && live.range);
      if (!pair) return;
      layout.scene[axis + "axis"] = Object.assign({}, layout.scene[axis + "axis"] || {}, {
        range: [Number(pair[0]), Number(pair[1])],
        autorange: false,
      });
    });
    var recovery = Promise.resolve(root.Plotly.react(gd, clone(gd.data), layout));
    var queueToken = gd.__mvRenderQueueState;
    return withTimeout(recovery, timeoutMs(), function () {
      if (gd.__mvRenderQueueState === queueToken) resetQueue(gd, "camera recovery timeout");
    });
  }
  function contextCurrent(gd, options) {
    options = options || {};
    if (typeof options.isCurrent === "function" && !options.isCurrent()) return false;
    var meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    if (meta && options.server_started_at && meta.server_started_at && String(meta.server_started_at) !== String(options.server_started_at)) return false;
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
      var scope = String(options.scene_id || "") + ":" + String(options.scene_generation === undefined ? "" : options.scene_generation);
      var epoch = String(options.server_started_at === undefined ? meta && meta.server_started_at || "" : options.server_started_at || "");
      if (gd.__mvCameraRevisionScope === scope && gd.__mvCameraRevisionEpoch === epoch &&
          Number.isFinite(requested) && Number.isFinite(current) && requested < current) return false;
    }
    return true;
  }
  /* Software WebGL can resolve a projection relayout before ``_ortho`` flips.
   * A mismatch that is only the internal projection is worth a few frames.
   * Any other postcondition failure still recovers immediately. */
  var PROJECTION_SETTLE_ATTEMPTS = 3;
  function projectionMismatchOnly(gd, camera, options) {
    var expected = projectionType(camera);
    var internal = internalProjection(gd);
    if (!expected || !internal || internal === expected) return false;
    if (postcondition(gd, camera, options)) return false;
    var matched = clone(camera);
    matched.projection = {type: internal};
    return postcondition(gd, matched, options);
  }
  function whenProjectionSettles(gd, camera, options) {
    function attempt(remaining) {
      if (postcondition(gd, camera, options)) return Promise.resolve(true);
      if (remaining <= 0 || !projectionMismatchOnly(gd, camera, options)) return Promise.resolve(false);
      return new Promise(function (resolve) {
        var settled = false;
        var timer = null;
        function finish() {
          if (settled) return;
          settled = true;
          if (timer) clearTimeout(timer);
          if (gd && gd.removeListener) { try { gd.removeListener("plotly_afterplot", onPlot); } catch (_) {} }
          resolve();
        }
        function onPlot() { finish(); }
        timer = setTimeout(finish, 32);
        if (gd && typeof gd.once === "function") {
          try { gd.once("plotly_afterplot", onPlot); } catch (_) {}
        } else if (gd && typeof gd.on === "function") {
          try { gd.on("plotly_afterplot", onPlot); } catch (_) {}
        }
      }).then(function () { return attempt(remaining - 1); });
    }
    return attempt(PROJECTION_SETTLE_ATTEMPTS);
  }
  function settledCurrent(gd, snapshot, epoch, current, options) {
    var check = cameraCheck(options);
    if (queueEpoch(gd) !== epoch || !current()) { diag("staleDrops"); throw new Error("stale camera command"); }
    function accept() {
      reportStatus("ok", "");
      gd.__mvCameraResets = 0;
    }
    function failOrRecover() {
      diag("postconditionFailures");
      notePostconditionFailure(gd, snapshot, check, "replay");
      reportStatus("error", "The camera update did not match the view. Restoring it.");
      return recover(gd, snapshot, check).then(function () {
        if (queueEpoch(gd) !== epoch || !current()) throw new Error("stale camera command");
        return whenProjectionSettles(gd, snapshot, check).then(function (ok) {
          if (queueEpoch(gd) !== epoch || !current()) throw new Error("stale camera command");
          if (!ok && !postcondition(gd, snapshot, check)) {
            diag("postconditionFailures");
            notePostconditionFailure(gd, snapshot, check, "toast");
            reportStatus("error", "The camera update could not be restored.");
            throw new Error("camera postcondition failed");
          }
          reportStatus("recovered", "Restored the current camera view.");
          gd.__mvCameraResets = 0;
        });
      });
    }
    if (postcondition(gd, snapshot, check)) {
      accept();
      return;
    }
    return whenProjectionSettles(gd, snapshot, check).then(function (ok) {
      if (queueEpoch(gd) !== epoch || !current()) { diag("staleDrops"); throw new Error("stale camera command"); }
      if (ok) {
        accept();
        return;
      }
      return failOrRecover();
    });
  }
  function deferGraphCamera(camera, options) {
    options = options || {};
    var retries = Number(options.__mvGraphRetries || 0);
    if (retries >= 20) return null;
    options.__mvGraphRetries = retries + 1;
    return new Promise(function (resolve, reject) {
      var settled = false;
      var timer = null;
      var target = null;
      var observer = null;
      var host = (root.document && root.document.getElementById) ? root.document.getElementById("crystal-graph") : null;
      function bindTarget() {
        var next = graph();
        if (!next || next === target) return;
        target = next;
        if (target.once) { try { target.once("plotly_afterplot", onAfterplot); } catch (_) {} }
        else if (target.on) { try { target.on("plotly_afterplot", onAfterplot); } catch (_) {} }
      }
      function cleanup() {
        if (timer) { clearTimeout(timer); timer = null; }
        if (observer && observer.disconnect) { try { observer.disconnect(); } catch (_) {} }
        if (target && target.removeListener && onAfterplot) { try { target.removeListener("plotly_afterplot", onAfterplot); } catch (_) {} }
        if (host && host.removeEventListener && onAfterplot) { try { host.removeEventListener("plotly_afterplot", onAfterplot); } catch (_) {} }
      }
      function retry() {
        if (settled) return;
        settled = true;
        cleanup();
        applyAsync(camera, options).then(resolve, reject);
      }
      function onAfterplot() { retry(); }
      bindTarget();
      if (host && root.MutationObserver) {
        try {
          observer = new root.MutationObserver(bindTarget);
          observer.observe(host, {childList: true, subtree: true});
        } catch (_) {}
      }
      if (host && host.addEventListener) {
        try { host.addEventListener("plotly_afterplot", onAfterplot, {once: true}); } catch (_) {}
      }
      timer = setTimeout(retry, 250);
    });
  }
  function carryFittedFrame(gd, options) {
    /* A relayout echo confirms the eye and omits the ratio. The scene
     * plot then stores the previous ratio. Keep the fitted ratio on
     * that delivery until Reset. */
    if (!options || /^reset$/i.test(String(options.command || ""))) return;
    var frame = fittedFrame(gd);
    if (!frame) return;
    if (!(options.aspectratio && Number.isFinite(Number(options.aspectratio.x))) && frame.aspectratio && Number.isFinite(Number(frame.aspectratio.x))) {
      options.aspectratio = frame.aspectratio;
    }
    if (!options.ranges && frame.ranges) options.ranges = frame.ranges;
  }
  function applyAsync(camera, options) {
    options = options || {};
    var gd = graph();
    carryFittedFrame(gd, options);
    if (!camera) {
      diag("missingPayload");
      root.__mv_camera_diag.lastMissingPayload = {camera_version: Number(options.camera_version), scene_id: options.scene_id || null};
      reportStatus("error", "The camera payload is missing. The last good frame was kept.");
      return Promise.reject(new Error("camera payload missing"));
    }
    if (!gd || !root.Plotly || typeof root.Plotly.relayout !== "function") {
      /* Plotly briefly removes the graph while replacing a gl3d canvas. A
       * camera update arriving in that window must remain retryable rather
       * than poisoning the delivery key and leaving an error toast behind.
       * Bound the wait so a genuinely missing graph still fails visibly. */
      if (camera && root.Plotly && typeof root.Plotly.relayout === "function") {
        diag("graphUnavailable");
        root.__mv_camera_diag.lastGraphUnavailable = {
          camera_version: Number(options.camera_version),
          scene_id: options.scene_id || null,
          scene_generation: options.scene_generation,
        };
        var deferred = deferGraphCamera(camera, options);
        if (deferred) return deferred;
      }
      reportStatus("error", "The 3D view is not ready. The last good frame was kept.");
      return Promise.reject(new Error("camera graph unavailable"));
    }
    delete options.__mvGraphWaitUntil;
    delete options.__mvGraphRetries;
    installWheelZoom(gd);
    installContextLoss(gd);
    installFittedRangeGuard();
    if (/^reset$/i.test(String(options.command || ""))) clearFittedFrame(options.scene_id);
    var snapshot = clone(camera);
    var patch = requestedPatch(snapshot);
    var requestedRevision = Number(options.camera_version);
    /* A dotted camera patch still needs a new scene.uirevision whenever it
     * advances the backend camera clock. Without this marker Plotly preserves
     * the previous gl3d camera and can acknowledge orbit/pan/zoom as a no-op
     * even though the eye payload changed. Browser drag persistence omits the
     * revision and therefore keeps the existing uirevision continuity. */
    if (Number.isFinite(requestedRevision)) {
      var currentUi = gd.layout && gd.layout.scene && gd.layout.scene.uirevision;
      patch["scene.uirevision"] = String(currentUi || "scene") + "#camera" + String(requestedRevision);
    }
    if (restoresViewport(options) && !(options.aspectratio && Number.isFinite(Number(options.aspectratio.x)))) {
      restoreCanonicalViewport(gd);
      var canonical = gd.__mvCanonicalViewport;
      if (canonical) {
        if (canonical.aspectmode) patch["scene.aspectmode"] = canonical.aspectmode;
        if (canonical.aspectratio) patch["scene.aspectratio"] = clone(canonical.aspectratio);
      }
      var previousUi = gd.layout && gd.layout.scene && gd.layout.scene.uirevision;
      patch["scene.uirevision"] = String(previousUi || "scene") + "#reset" + String(options.camera_version || Date.now());
    }
    if (options.aspectratio && Number.isFinite(Number(options.aspectratio.x))) {
      patch["scene.aspectmode"] = "manual";
      patch["scene.aspectratio.x"] = Number(options.aspectratio.x);
      patch["scene.aspectratio.y"] = Number(options.aspectratio.y);
      patch["scene.aspectratio.z"] = Number(options.aspectratio.z);
    }
    if (options.ranges) {
      ["x", "y", "z"].forEach(function (axis) {
        var pair = options.ranges[axis];
        if (pair && pair.length >= 2 && [pair[0], pair[1]].every(function (item) { return Number.isFinite(Number(item)); })) {
          patch["scene." + axis + "axis.autorange"] = false;
          patch["scene." + axis + "axis.range"] = [Number(pair[0]), Number(pair[1])];
        }
      });
    }
    if (!Object.keys(patch).length) return Promise.reject(new Error("invalid camera payload"));
    var render = gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    if (options.server_started_at === undefined) options.server_started_at = render && render.server_started_at || "";
    var revisionEpoch = String(options.server_started_at || "");
    var revisionScope = String(options.scene_id || "") + ":" + String(options.scene_generation === undefined ? "" : options.scene_generation);
    if (!contextCurrent(gd, options)) return Promise.reject(new Error("stale camera command"));
    if (gd.__mvCameraRevisionScope !== revisionScope || gd.__mvCameraRevisionEpoch !== revisionEpoch) {
      gd.__mvCameraRevisionScope = revisionScope;
      gd.__mvCameraRevisionEpoch = revisionEpoch;
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
    gd.__mvLatestCamera = {snapshot: snapshot, patch: patch, replayed: false, command: String(options.command || "")};
    if (options.aspectratio && Number.isFinite(Number(options.aspectratio.x)) && gd && typeof gd.on === "function") {
      /* The scene plot can write the previous ratio back after the
       * relayout promise resolves. Recommit on the plots that follow
       * this fit, then drop the listener. A permanent listener would
       * change the settle-wait listener count. */
      var aspectToken = requestedRevision;
      var commandedAspect = options.aspectratio;
      var aspectPlots = 0;
      function recommitFittedAspect() {
        if (Number.isFinite(aspectToken) && Number(gd.__mvCameraRevision) !== aspectToken) {
          if (gd.removeListener) gd.removeListener("plotly_afterplot", recommitFittedAspect);
          return;
        }
        commitFittedAspect(gd, commandedAspect);
        restoreFittedRanges(gd);
        aspectPlots += 1;
        if (aspectPlots >= 4 && gd.removeListener) gd.removeListener("plotly_afterplot", recommitFittedAspect);
      }
      try { gd.on("plotly_afterplot", recommitFittedAspect); } catch (_) {}
      setTimeout(function () {
        if (gd.removeListener) gd.removeListener("plotly_afterplot", recommitFittedAspect);
      }, 2000);
    }
    var appliedEpoch;
    var run = function () {
      if (!current()) { diag("staleDrops"); throw new Error("stale camera command"); }
      appliedEpoch = queueEpoch(gd);
      gd.__mvFittedRestoring = true;
      if (!/^reset$/i.test(String(options.command || "")) && options.ranges) {
        rememberFittedFrame(options.scene_id, options.ranges, options.aspectratio);
      }
      /* Put the ratio on the layout objects the replot reads, and arm
       * the lock, without touching the gl scene yet. setAspectratio marks
       * the scene dirty; a render from that mark can finish after the
       * range edit and write the previous data box back. */
      installAspectLock(gd);
      if (options.aspectratio && Number.isFinite(Number(options.aspectratio.x))) {
        var primed = {x: Number(options.aspectratio.x), y: Number(options.aspectratio.y), z: Number(options.aspectratio.z)};
        writeAspect(gd._fullLayout && gd._fullLayout.scene, primed);
        writeAspect(gd.layout && gd.layout.scene, primed);
      }
      var queueToken = gd.__mvRenderQueueState;
      /* Bound only the Plotly call. Time spent waiting behind an earlier
       * command must not expire Reset before it starts, and a timeout from a
       * command that a newer revision already replaced must not reset the new
       * queue. */
      var attempt = withTimeout(Promise.resolve(root.Plotly.relayout(gd, clone(patch))), timeoutMs(), function () {
        if (gd.__mvRenderQueueState === queueToken) resetQueue(gd, "camera relayout timeout");
      });
      return attempt.then(function () {
        if (Number.isFinite(requestedRevision) && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render) {
          gd.layout.meta.mattervis_render.camera_revision = requestedRevision;
        }
        commitFittedAspect(gd, options.aspectratio);
        holdFittedAspect(gd, options.aspectratio, requestedRevision);
        /* A drag relayout can resolve after Reset already replaced the queue.
         * Re-apply the newest command so the late Plotly write cannot stick. */
        if (Number.isFinite(requestedRevision) && Number(gd.__mvCameraRevision) !== requestedRevision) {
          var latest = gd.__mvLatestCamera;
          var restore = latest && latest.patch ? Promise.resolve(root.Plotly.relayout(gd, clone(latest.patch))) : Promise.resolve();
          return restore.then(function () { diag("staleDrops"); throw new Error("stale camera command"); });
        }
      });
    };
    diag("attempts");
    // Recovery uses Plotly.react, which joins the same render FIFO. Release
    // the relayout task before checking/recovering so it cannot wait for a
    // full-frame task queued behind itself.
    return enqueue(gd, run, {isCurrent: current, group: "camera"}).then(function () {
      return settledCurrent(gd, snapshot, appliedEpoch, current, options);
    }, function (error) {
      if (error && /timeout/.test(String(error && error.message || error))) throw error;
      if (queueEpoch(gd) !== appliedEpoch || !current()) { diag("staleDrops"); throw new Error("stale camera command"); }
      var check = cameraCheck(options);
      diag("postconditionFailures");
      notePostconditionFailure(gd, snapshot, check, "relayout");
      reportStatus("error", "Camera relayout failed. Restoring the current view.");
      return recover(gd, snapshot, check).then(function () {
        if (queueEpoch(gd) !== appliedEpoch || !current()) throw new Error("stale camera command");
        if (!postcondition(gd, snapshot, check)) {
          notePostconditionFailure(gd, snapshot, check, "relayout-toast");
          reportStatus("error", "Camera relayout failed and the view could not be restored.");
          throw error;
        }
        reportStatus("recovered", "Restored the current camera view.");
        gd.__mvCameraResets = 0;
      });
    }).finally(function () { gd.__mvFittedRestoring = false; }).then(function () { return true; }).catch(function (error) { diag("failures"); throw error; });
  }
  function apply(camera, options) {
    options = options || {};
    var gd = graph();
    if (!camera) {
      diag("missingPayload");
      root.__mv_camera_diag.lastMissingPayload = {camera_version: Number(options.camera_version), scene_id: options.scene_id || null};
      reportStatus("error", "The camera payload is missing. The last good frame was kept.");
      return Promise.reject(new Error("camera payload missing"));
    }
    if (!gd || !root.Plotly || typeof root.Plotly.relayout !== "function") {
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
    minimumPerspectiveEyeDistance: minimumPerspectiveEyeDistance,
    installCanvasRefit: installCanvasRefit,
    installContextLoss: installContextLoss,
    reportStatus: reportStatus,
    postcondition: postcondition,
    recover: recover,
    commitFittedAspect: commitFittedAspect,
    reapplyFittedFrame: function (gd) {
      commitFittedAspect(gd);
      restoreFittedRanges(gd);
    },
  };
})(window);
