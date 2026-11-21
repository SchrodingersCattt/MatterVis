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
      return Promise.resolve(relayoutAsQueueOwner(gd, clone(latest.patch)));
    }, {isCurrent: current, group: "camera"}).then(finish, finish).catch(function () {
      if (current()) reportStatus("error", "The camera update could not be restored.");
    });
  }
  function relayoutAsQueueOwner(gd, patch) {
    var state = gd && gd.__mvRenderQueueState;
    var previous = state && state.reentrantToken;
    var token = {};
    if (state && state.running) state.reentrantToken = token;
    var pending;
    try {
      pending = root.Plotly.relayout(gd, patch);
    } catch (err) {
      if (state && state.reentrantToken === token) state.reentrantToken = previous;
      throw err;
    }
    function finish() {
      if (state && state.reentrantToken === token) state.reentrantToken = previous;
    }
    if (pending && typeof pending.then === "function") {
      return pending.then(function (value) { finish(); return value; }, function (err) { finish(); throw err; });
    }
    finish();
    return pending;
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
    var internal = plotlyPrivateStateAllowed() ? scene._scene : null;
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
  function hasCoordinateValues(value) {
    if (!value) return false;
    var view = typeof ArrayBuffer !== "undefined" && ArrayBuffer.isView && ArrayBuffer.isView(value);
    if (Array.isArray(value) || view) return value.length > 0;
    return typeof value.bdata === "string" && value.bdata.length > 0 && !!value.dtype;
  }
  function geometryTraceKind(trace) {
    if (!trace || trace.visible === false || trace.visible === "legendonly") return "";
    var type = String(trace.type || "").toLowerCase();
    if (type === "mesh3d" || type === "scatter3d" || type === "cone" || type === "streamtube" || type === "surface" || type === "volume" || type === "isosurface") return type;
    return "";
  }
  function traceHasGeometry(trace) {
    var type = geometryTraceKind(trace);
    if (!type) return false;
    if (type === "mesh3d") return hasCoordinateValues(trace.x) && hasCoordinateValues(trace.i);
    return hasCoordinateValues(trace.x) || hasCoordinateValues(trace.y) || hasCoordinateValues(trace.z);
  }
  function dataHasVisibleGeometry(gd) {
    var data = gd && Array.isArray(gd.data) ? gd.data : [];
    return data.some(traceHasGeometry);
  }
  /* Geometry is missing when the frame claims 3D traces and none of them
   * have coordinates. One empty companion, a hidden trace, a non-3D trace,
   * or a layout image does not erase a trace that does have coordinates. */
  function missingClaimedGeometry(gd) {
    var data = gd && Array.isArray(gd.data) ? gd.data : [];
    var claimed = false;
    var found = false;
    data.forEach(function (trace) {
      if (!geometryTraceKind(trace)) return;
      claimed = true;
      if (traceHasGeometry(trace)) found = true;
    });
    return claimed && !found;
  }
  /* A wiped gl3d scene keeps the input traces and loses scene.traces plus
   * glplot.objects. Hosts that never expose those fields stay on the data
   * check so a camera-only test double does not look blank. */
  function liveRendererEmpty(gd) {
    if (!plotlyPrivateStateAllowed()) return false;
    var scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    var plot = scene && scene.glplot;
    if (!plot || !scene.traces || typeof scene.traces !== "object" || Array.isArray(scene.traces) || !Array.isArray(plot.objects)) return false;
    var gl = plot.canvas && plot.canvas.__mvGL;
    if (plot.contextLost) return true;
    if (gl && typeof gl.isContextLost === "function" && gl.isContextLost()) return true;
    return Object.keys(scene.traces).length === 0 || plot.objects.length === 0;
  }
  function visibleGeometry(gd) {
    if (!dataHasVisibleGeometry(gd)) return false;
    return !liveRendererEmpty(gd);
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
    if (!plotlyPrivateStateAllowed()) return null;
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
    if (plotlyPrivateStateAllowed()) {
      var scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
      var controller = scene && (scene.camera || (scene.glplot && scene.glplot.camera));
      if (controller && controller.eye) return controller.eye;
    }
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
  /* Plotly 4.1.1 reads. A published version that is not this one must not
   * drive projection from controller._ortho or glplot. Test doubles omit
   * Plotly.version and still use the fields they installed. */
  var PLOTLY_GL3D_PRIVATE_VERSION = "4.1.1";
  function plotlyPrivateStateAllowed() {
    var version = root.Plotly && root.Plotly.version;
    if (version == null || version === "") return true;
    return String(version) === PLOTLY_GL3D_PRIVATE_VERSION;
  }
  function rememberFittedFrame(sceneId, ranges, aspectratio, revision, identity) {
    if (!sceneId || !ranges) return;
    if (!root.__mvFittedFrames) root.__mvFittedFrames = {};
    var revisionNumber = Number(revision);
    var previous = root.__mvFittedFrames[String(sceneId)];
    var generation = identity && identity.generation != null && identity.generation !== ""
      ? identity.generation
      : (previous && previous.generation != null ? previous.generation : null);
    var epoch = identity && identity.epoch != null && identity.epoch !== ""
      ? identity.epoch
      : (previous && previous.epoch != null ? previous.epoch : null);
    root.__mvFittedFrames[String(sceneId)] = {
      ranges: clone(ranges),
      aspectratio: aspectratio ? clone(aspectratio) : null,
      revision: Number.isFinite(revisionNumber) ? revisionNumber : null,
      generation: generation,
      epoch: epoch,
    };
  }
  function fittedIdentityMatches(frame, render) {
    if (!frame || !render) return false;
    var generation = render.scene_generation;
    var epoch = render.server_started_at;
    var framedGeneration = frame.generation != null && frame.generation !== "";
    var renderGeneration = generation != null && generation !== "";
    if (framedGeneration !== renderGeneration) return false;
    if (framedGeneration && String(frame.generation) !== String(generation)) return false;
    var framedEpoch = frame.epoch != null && frame.epoch !== "";
    var renderEpoch = epoch != null && epoch !== "";
    if (framedEpoch !== renderEpoch) return false;
    if (framedEpoch && String(frame.epoch) !== String(epoch)) return false;
    return true;
  }
  function noteFigureFrame(render, layout) {
    /* A figure built for a newer Fit already carries that ratio. Stamping
     * the previous frame over it is how an orthographic scale was lost
     * when the camera message had not updated the frame yet. */
    var sceneId = render && render.scene_id;
    if (!sceneId || !root.__mvFittedFrames) return false;
    var frame = root.__mvFittedFrames[String(sceneId)];
    var incoming = Number(render && render.camera_revision);
    var frameRevision = frame && Number(frame.revision);
    var scene = layout && layout.scene;
    var figureAspect = scene && scene.aspectratio;
    if (!frame || !Number.isFinite(frameRevision) || !Number.isFinite(incoming) || !(incoming > frameRevision)) return false;
    if (!figureAspect || !Number.isFinite(Number(figureAspect.x))) return false;
    var ranges = frame.ranges;
    if (scene) {
      var next = {};
      var complete = ["x", "y", "z"].every(function (axis) {
        var pair = scene[axis + "axis"] && scene[axis + "axis"].range;
        if (!pair || pair.length < 2 || !Number.isFinite(Number(pair[0])) || !Number.isFinite(Number(pair[1]))) return false;
        next[axis] = [Number(pair[0]), Number(pair[1])];
        return true;
      });
      if (complete) ranges = next;
    }
    rememberFittedFrame(sceneId, ranges, figureAspect, incoming, {
      generation: render && render.scene_generation,
      epoch: render && render.server_started_at,
    });
    return true;
  }
  function clearFittedFrame(sceneId) {
    if (sceneId && root.__mvFittedFrames) delete root.__mvFittedFrames[String(sceneId)];
  }
  function restoreFittedRanges(gd) {
    if (!gd || gd.__mvFittedRestoring) return Promise.resolve();
    var render = gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    var sceneId = render && render.scene_id;
    var stored = sceneId && root.__mvFittedFrames && root.__mvFittedFrames[String(sceneId)];
    if (stored && !fittedIdentityMatches(stored, render || {})) return Promise.resolve();
    var fitted = stored;
    if (!fitted || !fitted.ranges || !root.Plotly || typeof root.Plotly.relayout !== "function") return Promise.resolve();
    var scene = gd._fullLayout && gd._fullLayout.scene;
    if (!scene) return Promise.resolve();
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
    /* _fullLayout can already show the fitted ratio while glplot still
     * draws the previous one. That is the ratio a scene plot writes back. */
    if (!aspectMismatch && fitted.aspectratio && plotlyPrivateStateAllowed()) {
      var plot = scene._scene && scene._scene.glplot;
      var painted = plot && typeof plot.getAspectratio === "function" ? plot.getAspectratio() : null;
      if (painted) {
        aspectMismatch = ["x", "y", "z"].some(function (axis, index) {
          var want = Number(fitted.aspectratio[axis]);
          var have = Number(painted[axis] !== undefined ? painted[axis] : painted[index]);
          return Number.isFinite(want) && Number.isFinite(have) && Math.abs(have - want) / Math.max(Math.abs(want), 1e-6) > 0.01;
        });
      }
    }
    /* Layout ranges can already name the fit while glplot.bounds still
     * clips with the previous dataScale. A ranges relayout with the
     * projection left alone rebuilds that clip box. */
    if (!mismatch && fitted.ranges && plotlyPrivateStateAllowed()) {
      var plotForBounds = scene._scene && scene._scene.glplot;
      var scale = scene._scene && scene._scene.dataScale;
      var bounds = plotForBounds && plotForBounds.bounds;
      if (bounds && bounds[0] && bounds[1] && scale) {
        mismatch = ["x", "y", "z"].some(function (axis, index) {
          var pair = fitted.ranges[axis];
          var factor = Number(scale[index]);
          if (!pair || !Number.isFinite(factor) || factor === 0) return false;
          var wantMin = Number(pair[0]) * factor;
          var wantMax = Number(pair[1]) * factor;
          var span = Math.max(Math.abs(wantMax - wantMin), 1e-6);
          var haveMin = Number(bounds[0][index]);
          var haveMax = Number(bounds[1][index]);
          if (!Number.isFinite(haveMin) || !Number.isFinite(haveMax)) return false;
          return Math.abs(haveMin - wantMin) / span > 0.02 || Math.abs(haveMax - wantMax) / span > 0.02;
        });
      }
    }
    if (!mismatch && !aspectMismatch) return Promise.resolve();
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
    gd.__mvOwnedRelayoutDepth = Number(gd.__mvOwnedRelayoutDepth || 0) + 1;
    var pending;
    var clear = function () {
      gd.__mvFittedRestoring = false;
      gd.__mvOwnedRelayoutDepth = Math.max(0, Number(gd.__mvOwnedRelayoutDepth || 1) - 1);
    };
    try { pending = root.Plotly.relayout(gd, patch); }
    catch (err) {
      clear();
      return Promise.reject(err);
    }
    return Promise.resolve(pending).then(function (value) {
      clear();
      return value;
    }, function (err) {
      clear();
      return Promise.reject(err);
    });
  }
  function installFittedRangeGuard() {
    /* Fitted ranges are restored inside the frame task. Wrapping
     * Plotly.react here would relayout again after that task moved on. */
    root.__mvFittedRangeGuard = true;
  }
  function eyeVector(value) {
    if (!value) return null;
    var x = Number(value.x !== undefined && value.x !== null ? value.x : value[0]);
    var y = Number(value.y !== undefined && value.y !== null ? value.y : value[1]);
    var z = Number(value.z !== undefined && value.z !== null ? value.z : value[2]);
    return [x, y, z].every(Number.isFinite) ? [x, y, z] : null;
  }
  function eyeMatches(left, right) {
    var a = eyeVector(left);
    var b = eyeVector(right);
    if (!a || !b) return false;
    return Math.abs(a[0] - b[0]) <= 1e-3 && Math.abs(a[1] - b[1]) <= 1e-3 && Math.abs(a[2] - b[2]) <= 1e-3;
  }
  function commitControllerPose(controller, camera) {
    /* Plotly's orbit camera stores distance as a log radius and
     * rebuilds .eye only when the view matrix is recalculated.
     * lookAt recalculates the previous pose first. A second key at
     * the same timestamp is invisible when the sampler keeps the
     * earlier one, and the render tick samples one delay behind
     * lastT. Stamp the pose at lastT and one millisecond later, then
     * recalculate that later sample so .eye is this distance. */
    if (!controller || typeof controller.lookAt !== "function" || !camera) return;
    var eye = eyeVector(camera.eye);
    var center = eyeVector(camera.center);
    var up = eyeVector(camera.up);
    if (!eye || !center || !up) return;
    var view = controller.view;
    var when = 0;
    if (view && typeof view.lastT === "function") {
      when = Number(view.lastT());
      if (!Number.isFinite(when)) when = 0;
    }
    var posed = false;
    if (view && typeof view.lookAt === "function") {
      try {
        view.lookAt(when, eye, center, up);
        view.lookAt(when + 1, eye, center, up);
        posed = true;
      } catch (_) {}
    }
    if (!posed) {
      try { controller.lookAt(eye, center, up); }
      catch (_) { return; }
    }
    if (view && typeof view.recalcMatrix === "function") {
      try { view.recalcMatrix(when + 1); }
      catch (_) {
        try { view.recalcMatrix(when); } catch (_) {}
      }
    }
    if (!eyeMatches(controller.eye, eye) && view && typeof view.recalcMatrix === "function") {
      try { view.recalcMatrix(when); } catch (_) {}
    }
  }
  function noteUnadoptedWheel(graph, pose) {
    /* The camera book can still hold the previous distance when this
     * relayout is acknowledged. A same-scene apply would then paint that
     * stale eye over the zoom. Record the pose, and the book, before the
     * relayout returns. */
    var meta = graph && graph.layout && graph.layout.meta && graph.layout.meta.mattervis_render;
    var sceneId = meta && meta.scene_id ? String(meta.scene_id) : "";
    var revision = meta && meta.camera_revision !== undefined && meta.camera_revision !== null
      ? Number(meta.camera_revision) : null;
    root.__mvUnadoptedWheel = {
      sceneId: sceneId,
      camera: pose,
      camera_revision: Number.isFinite(revision) ? revision : null,
    };
    if (!sceneId) return;
    var book = root.__mvServerCameraByScene || (root.__mvServerCameraByScene = {});
    var previous = book[sceneId] || {};
    book[sceneId] = {
      camera: pose,
      camera_revision: Number.isFinite(revision) ? revision : previous.camera_revision,
      scene_generation: previous.scene_generation !== undefined && previous.scene_generation !== null
        ? previous.scene_generation
        : (meta ? meta.scene_generation : null),
      server_started_at: previous.server_started_at !== undefined && previous.server_started_at !== null
        ? previous.server_started_at
        : (meta ? meta.server_started_at : null),
    };
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
      var projection = "perspective";
      if (plotlyPrivateStateAllowed() && typeof controller._ortho === "boolean") {
        projection = controller._ortho ? "orthographic" : "perspective";
      } else {
        var named = projectionType(actualCamera(graph));
        if (named) projection = named;
      }
      var zoomPose = {
        eye: {x: next[0], y: next[1], z: next[2]},
        center: {x: axisComponent(center, 0, "x"), y: axisComponent(center, 1, "y"), z: axisComponent(center, 2, "z")},
        up: {x: axisComponent(up, 0, "x"), y: axisComponent(up, 1, "y"), z: axisComponent(up, 2, "z")},
      };
      noteUnadoptedWheel(graph, zoomPose);
      Promise.resolve(root.Plotly.relayout(graph, {
        "scene.camera.eye": zoomPose.eye,
        "scene.camera.center": zoomPose.center,
        "scene.camera.up": zoomPose.up,
        "scene.camera.projection.type": projection,
      })).then(function () {
        /* Relayout can replace the controller. Commit the one that is
         * mounted now. The book already holds this pose, so a stale
         * apply that finishes later must not put the previous eye back. */
        var sceneNow = graph._fullLayout && graph._fullLayout.scene && graph._fullLayout.scene._scene;
        var current = sceneNow && (sceneNow.camera || (sceneNow.glplot && sceneNow.glplot.camera));
        commitControllerPose(current || controller, zoomPose);
      }, function () {});
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
    if (gd.__mvContextLossCanvas && gd.__mvContextLossCanvas !== canvas) {
      var ring = root.__mvRendererDiag || (root.__mvRendererDiag = []);
      ring.push({at: Date.now(), kind: "canvas-replaced"});
      if (ring.length > 20) ring.splice(0, ring.length - 20);
    }
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
    var frame = root.__mvFittedFrames[String(sceneId)] || null;
    if (!fittedIdentityMatches(frame, render)) return null;
    return frame;
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
  function wrapGlplot(gd, gl) {
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
  function installAspectLock(gd) {
    /* Plotly's scene plot writes the aspect, then calls setAspectratio.
     * A projection change does that on a gl scene that does not exist
     * yet: setViewport disposes the old one and initializeGLPlot assigns
     * a new one before the plot's last aspect write. Wrapping only the
     * current function misses that assignment. The property hook wraps
     * the replacement before the last write. */
    var holder = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    if (!holder) return;
    if (!holder.__mvGlplotHook) {
      var current = holder.glplot;
      try {
        Object.defineProperty(holder, "glplot", {
          configurable: true,
          enumerable: true,
          get: function () { return current; },
          set: function (gl) {
            current = gl;
            wrapGlplot(gd, gl);
          },
        });
        holder.__mvGlplotHook = true;
      } catch (_) {}
      if (current) wrapGlplot(gd, current);
      return;
    }
    wrapGlplot(gd, holder.glplot);
  }
  function clearFittedAspectListener(gd) {
    if (!gd || !gd.__mvFittedAspectListener) return;
    if (gd.removeListener) gd.removeListener("plotly_afterplot", gd.__mvFittedAspectListener);
    gd.__mvFittedAspectListener = null;
  }
  function watchFittedAspect(gd, token) {
    /* A figure or confirmed-state plot can finish after Fit's relayout
     * promise. The perspective Fit already owns this data box, so that
     * late plot writes the previous ratio and leaves the new eye in
     * place. plotly_afterplot is the end of that plot. Stay subscribed
     * until a newer camera revision replaces this Fit. A timer would
     * miss the plot that arrives after it. */
    if (!gd || typeof gd.on !== "function") return;
    clearFittedAspectListener(gd);
    var attempts = 0;
    function recommitFittedAspect() {
      if (gd.__mvFittedAspectRelayout) return;
      if (Number.isFinite(token) && Number(gd.__mvCameraRevision) !== token) {
        clearFittedAspectListener(gd);
        return;
      }
      var frame = fittedFrame(gd);
      var aspect = frame && frame.aspectratio;
      if (!aspect || !Number.isFinite(Number(aspect.x))) return;
      var scene = gd._fullLayout && gd._fullLayout.scene;
      var live = scene && scene.aspectratio;
      var painted = glAspect(gd);
      var drifted = !live || aspectOffContract(live, aspect, 0.01);
      if (painted && aspectOffContract(painted, aspect, 0.01)) drifted = true;
      if (!drifted) return;
      /* The fit relayout is still inside this plot. A nested relayout is
       * rejected and the rejection used to restore the camera and toast.
       * Retry after that command releases the graph. */
      if (gd.__mvFittedRestoring) {
        setTimeout(recommitFittedAspect, 0);
        return;
      }
      /* A direct write is discarded by the next scene plot, which stores
       * the previous ratio as the user value. A new relayout is a user
       * edit, so that plot keeps this ratio. */
      attempts += 1;
      if (attempts > 8 || !root.Plotly || typeof root.Plotly.relayout !== "function") return;
      var patch = {
        "scene.aspectmode": "manual",
        "scene.aspectratio.x": Number(aspect.x),
        "scene.aspectratio.y": Number(aspect.y),
        "scene.aspectratio.z": Number(aspect.z),
      };
      root.__mv_camera_diag = root.__mv_camera_diag || {};
      root.__mv_camera_diag.repairs = (root.__mv_camera_diag.repairs || 0) + 1;
      gd.__mvFittedAspectRelayout = true;
      Promise.resolve(root.Plotly.relayout(gd, patch)).finally(function () {
        gd.__mvFittedAspectRelayout = false;
        recommitFittedAspect();
      });
    }
    gd.__mvFittedAspectListener = recommitFittedAspect;
    try { gd.on("plotly_afterplot", recommitFittedAspect); } catch (_) { gd.__mvFittedAspectListener = null; }
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
    /* Coordinates have to be on a trace that claims to draw them. A layout
     * image is not geometry. Hidden traces are not a missing model. A wiped
     * gl scene with coordinates still present is not drawable. */
    if (missingClaimedGeometry(gd)) return "geometry";
    if (dataHasVisibleGeometry(gd) && liveRendererEmpty(gd)) return "geometry";
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
    if (/^reset$/i.test(String(options.command || ""))) {
      clearFittedFrame(options.scene_id);
      clearFittedAspectListener(gd);
    }
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
    var sceneKey = String(options.scene_id || "");
    var incomingGeneration = options.scene_generation;
    var generationPresent = incomingGeneration !== undefined && incomingGeneration !== null && String(incomingGeneration) !== "";
    var storedScope = String(gd.__mvCameraRevisionScope || "");
    /* A delivery with no generation must not look like a new clock and
     * wipe a newer Fit. A real generation or epoch change still restarts. */
    var bareSceneScope = !generationPresent && !!sceneKey && storedScope.indexOf(sceneKey + ":") === 0 && storedScope !== revisionScope;
    var sceneChanged = !!(sceneKey && gd.__mvCameraRevisionScene && gd.__mvCameraRevisionScene !== sceneKey);
    var epochChanged = !!(revisionEpoch && gd.__mvCameraRevisionEpoch && revisionEpoch !== String(gd.__mvCameraRevisionEpoch));
    var realScopeChange = storedScope !== "" && revisionScope !== storedScope && !bareSceneScope;
    if (storedScope === "") {
      if (sceneKey) gd.__mvCameraRevisionScene = sceneKey;
      gd.__mvCameraRevisionScope = revisionScope;
      gd.__mvCameraRevisionEpoch = revisionEpoch;
    } else if (sceneChanged || epochChanged || realScopeChange) {
      if (sceneKey) gd.__mvCameraRevisionScene = sceneKey;
      gd.__mvCameraRevisionScope = revisionScope;
      gd.__mvCameraRevisionEpoch = revisionEpoch;
      gd.__mvCameraRevision = NaN;
    } else if (sceneKey) {
      gd.__mvCameraRevisionScene = sceneKey;
    }
    var liveRevision = Number(gd.__mvCameraRevision);
    if (!realScopeChange && !epochChanged && !sceneChanged && sceneKey && Number.isFinite(liveRevision) && Number.isFinite(requestedRevision) && requestedRevision < liveRevision) {
      return Promise.reject(new Error("stale camera command"));
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
    if (!/^reset$/i.test(String(options.command || "")) && options.ranges) {
      rememberFittedFrame(options.scene_id, options.ranges, options.aspectratio, requestedRevision, {
        generation: options.scene_generation,
        epoch: options.server_started_at,
      });
    }
    if (options.aspectratio && Number.isFinite(Number(options.aspectratio.x))) watchFittedAspect(gd, requestedRevision);
    var appliedEpoch;
    var run = function () {
      if (!current()) { diag("staleDrops"); throw new Error("stale camera command"); }
      appliedEpoch = queueEpoch(gd);
      gd.__mvFittedRestoring = true;
      if (!/^reset$/i.test(String(options.command || "")) && options.ranges) {
        rememberFittedFrame(options.scene_id, options.ranges, options.aspectratio, requestedRevision, {
        generation: options.scene_generation,
        epoch: options.server_started_at,
      });
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
      var attempt = withTimeout(Promise.resolve(relayoutAsQueueOwner(gd, clone(patch))), timeoutMs(), function () {
        if (gd.__mvRenderQueueState === queueToken) resetQueue(gd, "camera relayout timeout");
      });
      return attempt.then(function () {
        if (Number.isFinite(requestedRevision) && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render) {
          gd.layout.meta.mattervis_render.camera_revision = requestedRevision;
        }
        commitFittedAspect(gd, options.aspectratio);
        installContextLoss(gd);
        /* A drag relayout can resolve after Reset already replaced the queue.
         * Re-apply the newest command so the late Plotly write cannot stick. */
        if (Number.isFinite(requestedRevision) && Number(gd.__mvCameraRevision) !== requestedRevision) {
          var latest = gd.__mvLatestCamera;
          var restore = latest && latest.patch ? Promise.resolve(relayoutAsQueueOwner(gd, clone(latest.patch))) : Promise.resolve();
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
      if (error && /timeout|stale camera command/.test(String(error && error.message || error))) throw error;
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
      return restoreFittedRanges(gd);
    },
    commitControllerPose: commitControllerPose,
    fittedIdentityMatches: fittedIdentityMatches,
    noteFigureFrame: noteFigureFrame,
  };
})(window);
