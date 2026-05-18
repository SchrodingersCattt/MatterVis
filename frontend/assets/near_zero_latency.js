/* Near-zero-latency view controls.
 *
 * Camera/projection buttons are layout-only operations. Update Plotly
 * directly in the browser, then synchronise backend state with
 * /api/v2/camera/action using broadcast:false so the next poll does not
 * trigger a full Dash figure rebuild.
 */
(function () {
  const AXIS_BY_BUTTON = {
    "view-align-a": "a",
    "view-align-b": "b",
    "view-align-c": "c",
    "view-align-astar": "a*",
    "view-align-bstar": "b*",
    "view-align-cstar": "c*",
  };

  let meta = null;

  function readMeta() {
    const node = document.getElementById("fast-view-metadata");
    if (!node) return meta;
    const text = (node.textContent || "").trim();
    if (!text) return meta;
    try {
      meta = JSON.parse(text);
    } catch (err) {
      return meta;
    }
    return meta;
  }

  function graphDiv() {
    const root = document.getElementById("crystal-graph");
    return root ? root.querySelector(".js-plotly-plot") : null;
  }

  function normalize(v, fallback) {
    const x = Number(v && v[0]);
    const y = Number(v && v[1]);
    const z = Number(v && v[2]);
    let out = [Number.isFinite(x) ? x : fallback[0], Number.isFinite(y) ? y : fallback[1], Number.isFinite(z) ? z : fallback[2]];
    const n = Math.hypot(out[0], out[1], out[2]);
    if (n < 1e-9) out = fallback.slice();
    const m = Math.hypot(out[0], out[1], out[2]) || 1;
    return [out[0] / m, out[1] / m, out[2] / m];
  }

  function cross(a, b) {
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  }

  function dot(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  }

  function sub(a, b) {
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  }

  function add(a, b) {
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  }

  function scale(a, s) {
    return [a[0] * s, a[1] * s, a[2] * s];
  }

  function inv3(m) {
    const a = m[0][0], b = m[0][1], c = m[0][2];
    const d = m[1][0], e = m[1][1], f = m[1][2];
    const g = m[2][0], h = m[2][1], i = m[2][2];
    const A = e * i - f * h;
    const B = c * h - b * i;
    const C = b * f - c * e;
    const D = f * g - d * i;
    const E = a * i - c * g;
    const F = c * d - a * f;
    const G = d * h - e * g;
    const H = b * g - a * h;
    const I = a * e - b * d;
    const det = a * A + b * D + c * G;
    if (Math.abs(det) < 1e-12) return null;
    return [[A / det, B / det, C / det], [D / det, E / det, F / det], [G / det, H / det, I / det]];
  }

  function latticeAxes(m) {
    const real = { a: m[0], b: m[1], c: m[2] };
    const inv = inv3(m);
    const recip = inv ? { "a*": [inv[0][0], inv[1][0], inv[2][0]], "b*": [inv[0][1], inv[1][1], inv[2][1]], "c*": [inv[0][2], inv[1][2], inv[2][2]] } : {};
    const out = {};
    Object.keys(real).forEach((k) => { out[k] = normalize(real[k], [1, 0, 0]); });
    Object.keys(recip).forEach((k) => { out[k] = normalize(recip[k], [1, 0, 0]); });
    return out;
  }

  function orthogonaliseUp(view, upPick) {
    let up = sub(upPick, scale(view, dot(upPick, view)));
    if (Math.hypot(up[0], up[1], up[2]) < 1e-9) {
      [[0, 0, 1], [0, 1, 0], [1, 0, 0]].some((fallback) => {
        up = sub(fallback, scale(view, dot(fallback, view)));
        return Math.hypot(up[0], up[1], up[2]) > 1e-9;
      });
    }
    return normalize(up, [0, 1, 0]);
  }

  function currentCamera(gd, m) {
    const cam = (gd && gd.layout && gd.layout.scene && gd.layout.scene.camera) || (m && m.camera) || {};
    return {
      eye: Object.assign({ x: 0, y: 0, z: 1.8 }, cam.eye || {}),
      center: Object.assign({ x: 0, y: 0, z: 0 }, cam.center || {}),
      up: Object.assign({ x: 0, y: 1, z: 0 }, cam.up || {}),
      projection: cam.projection || { type: (m && m.projection) || "perspective" },
    };
  }

  function cameraForAxis(axis, gd, m) {
    const axes = latticeAxes(m.M || [[1, 0, 0], [0, 1, 0], [0, 0, 1]]);
    const view = axes[axis] || [0, 0, 1];
    const upKey = axis === "c" ? "b" : axis === "c*" ? "b*" : axis.indexOf("*") >= 0 ? "c*" : "c";
    const up = orthogonaliseUp(view, axes[upKey] || [0, 1, 0]);
    const curr = currentCamera(gd, m);
    const eye = [curr.eye.x, curr.eye.y, curr.eye.z];
    const center = [curr.center.x || 0, curr.center.y || 0, curr.center.z || 0];
    const distance = Math.max(1e-6, Math.hypot(eye[0] - center[0], eye[1] - center[1], eye[2] - center[2])) || 1.8;
    const nextEye = add(center, scale(view, distance));
    return {
      eye: { x: nextEye[0], y: nextEye[1], z: nextEye[2] },
      center: { x: center[0], y: center[1], z: center[2] },
      up: { x: up[0], y: up[1], z: up[2] },
      projection: curr.projection,
    };
  }

  function setDashStore(id, data) {
    if (!window.dash_clientside || typeof window.dash_clientside.set_props !== "function") return;
    try {
      window.dash_clientside.set_props(id, { data: data });
    } catch (err) {
      /* best effort */
    }
  }

  function syncCamera(action, payload, camera) {
    const m = readMeta() || {};
    const body = Object.assign({ action: action, scene_id: m.scene_id, broadcast: false }, payload || {});
    fetch("/api/v2/camera/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(function () {});
    meta = Object.assign({}, m, { camera: camera, camera_revision: Number(m.camera_revision || 0) + 1 });
    setDashStore("camera-state-store", { scene_id: m.scene_id, camera: camera });
  }

  function applyCamera(camera, action, payload) {
    const gd = graphDiv();
    if (!gd || !window.Plotly || typeof window.Plotly.relayout !== "function") return;
    window.Plotly.relayout(gd, { "scene.camera": camera });
    syncCamera(action, payload, camera);
  }

  function bindButtons() {
    Object.keys(AXIS_BY_BUTTON).forEach((id) => {
      const btn = document.getElementById(id);
      if (!btn || btn.dataset.nzBound === "1") return;
      btn.dataset.nzBound = "1";
      btn.addEventListener("click", function () {
        const m = readMeta();
        if (!m) return;
        applyCamera(cameraForAxis(AXIS_BY_BUTTON[id], graphDiv(), m), "align", { axis: AXIS_BY_BUTTON[id] });
      }, true);
    });
    const reset = document.getElementById("view-reset");
    if (reset && reset.dataset.nzBound !== "1") {
      reset.dataset.nzBound = "1";
      reset.addEventListener("click", function () {
        const m = readMeta();
        if (m && m.default_camera) applyCamera(m.default_camera, "reset", {});
      }, true);
    }
    const projection = document.getElementById("view-projection");
    if (projection && projection.dataset.nzBound !== "1") {
      projection.dataset.nzBound = "1";
      projection.addEventListener("change", function (event) {
        const m = readMeta();
        const gd = graphDiv();
        if (!m || !gd) return;
        const target = event.target;
        if (!target || target.name !== "view-projection" || !target.checked) return;
        const cam = currentCamera(gd, m);
        cam.projection = { type: target.value || "perspective" };
        applyCamera(cam, "projection", { type: cam.projection.type });
      }, true);
    }
  }

  function init() {
    readMeta();
    bindButtons();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
  new MutationObserver(function () {
    readMeta();
    bindButtons();
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
