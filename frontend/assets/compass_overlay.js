/* Live compass overlay rendered in a *separate* SVG layered over the
 * graph div. Avoids ALL Plotly.relayout calls for compass updates,
 * which is what was previously freezing the gl3d render mid-drag.
 *
 * Why a separate SVG, not Plotly annotations:
 *
 *   The previous version updated compass arrows by calling
 *   ``Plotly.relayout(gd, { annotations, shapes })`` on every drag
 *   frame. Plotly's relayout machinery, on a 3D scene, ends up calling
 *   ``scene.draw()`` -> ``setCameraPosition(layout.scene.camera)``
 *   *during* the user's rotation drag. That overwrites the live
 *   gl-plot-3d ``view._matrix`` accumulated by the orbit controller
 *   with the LAST COMMITTED camera (the default eye, since Plotly
 *   does not commit to ``layout.scene.camera`` until mouseup -- see
 *   plotly/plotly.js#6359). The visible symptom was "the molecule
 *   doesn't rotate during drag, only snaps after release"; verified
 *   by Playwright (8 mid-drag screenshots all byte-identical).
 *
 *   Doing the compass in a sibling SVG means we never call Plotly
 *   while the user is dragging. The GL canvas is free to render every
 *   frame; we just paint our arrows on top in a layer Plotly never
 *   touches.
 *
 * The Python ``axis_key_overlay`` still bakes a Plotly-annotation
 * compass into the figure layout for *static* export (kaleido /
 * orbital panels / scripts/). On the live page we strip those
 * Plotly compass entries ONCE at attach time so they don't visually
 * collide with the SVG overlay; subsequent updates touch only the
 * SVG.
 */
(function () {
  // Sentinel value the Python ``axis_key_overlay`` writes into
  // ``annotation.name`` / ``shape.name``. Used both to strip the
  // Plotly-side compass once at attach and to identify our own SVG
  // child nodes for replacement.
  const COMPASS_ITEM_NAME = "mv_compass";
  const SVG_LAYER_ID = "mv-compass-svg";

  // Diagnostic touchpoint. Read in DevTools as ``window.__mv_compass_diag``.
  if (!window.__mv_compass_diag) {
    window.__mv_compass_diag = {
      iife_loaded: 0,
      maybe_attach_called: 0,
      maybe_attach_attached: 0,
      drag_poll_starts: 0,
      drag_poll_ticks: 0,
      drag_poll_redraws: 0,
      svg_redraws: 0,
      svg_redraws_with_camera: 0,
      svg_redraws_with_layout_camera: 0,
      svg_redraws_with_live_camera: 0,
      strip_attempts: 0,
      strip_completed: 0,
      last_attach_skip_reason: null,
    };
  }
  window.__mv_compass_diag.iife_loaded += 1;

  function graphRoot() { return document.getElementById("crystal-graph"); }
  function graphDiv() {
    const root = graphRoot();
    return root ? root.querySelector(".js-plotly-plot") : null;
  }

  function cross(a, b) {
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function scale(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }
  function norm3(v) { return Math.hypot(v[0], v[1], v[2]); }

  function xyzFrom(obj, fallback) {
    if (!obj) return fallback.slice();
    if (Array.isArray(obj)) return [Number(obj[0]) || 0, Number(obj[1]) || 0, Number(obj[2]) || 0];
    function coord(value, fallbackValue) {
      if (value === undefined || value === null) return fallbackValue;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : fallbackValue;
    }
    return [
      coord(obj.x, fallback ? fallback[0] : 0),
      coord(obj.y, fallback ? fallback[1] : 0),
      coord(obj.z, fallback ? fallback[2] : 0),
    ];
  }

  function cameraScreenBasis(camera) {
    if (!camera || !camera.eye || !camera.up) return null;
    const eye = xyzFrom(camera.eye, [0, 0, 1]);
    const center = xyzFrom(camera.center, [0, 0, 0]);
    const up = xyzFrom(camera.up, [0, 1, 0]);
    let view = sub(center, eye);
    let n = norm3(view);
    if (n < 1e-12) return null;
    view = scale(view, 1 / n);
    let right = cross(view, up);
    let rn = norm3(right);
    if (rn < 1e-12) return null;
    right = scale(right, 1 / rn);
    let screenUp = cross(right, view);
    const sn = norm3(screenUp);
    if (sn < 1e-12) return null;
    screenUp = scale(screenUp, 1 / sn);
    return { right: right, screenUp: screenUp };
  }

  function projectLattice(M, basis, cubeScale) {
    if (!M || !basis) return null;
    const out = [];
    for (let i = 0; i < 3; i++) {
      const row = M[i];
      if (!row || row.length < 3) return null;
      let v;
      if (cubeScale && cubeScale.length === 3) {
        const sx = cubeScale[0] || 1;
        const sy = cubeScale[1] || 1;
        const sz = cubeScale[2] || 1;
        v = [row[0] / sx, row[1] / sy, row[2] / sz];
      } else {
        v = row;
      }
      const n = Math.hypot(v[0], v[1], v[2]);
      if (!isFinite(n) || n < 1e-12) return null;
      v = [v[0] / n, v[1] / n, v[2] / n];
      out.push([dot(v, basis.right), dot(v, basis.screenUp)]);
    }
    return out;
  }

  function compassFromMeta(layout) {
    if (!layout || !layout.meta) return null;
    let meta = layout.meta;
    if (typeof meta === "string") {
      try { meta = JSON.parse(meta); } catch (err) { return null; }
    }
    return (meta && meta.compass) ? meta.compass : null;
  }

  function hasCompleteCamera(camera) {
    return !!(camera && camera.eye && camera.center && camera.up);
  }

  function layoutSceneCamera(gd) {
    if (!gd) return null;
    const layoutScene = gd.layout && gd.layout.scene ? gd.layout.scene : null;
    if (layoutScene && hasCompleteCamera(layoutScene.camera)) return layoutScene.camera;
    const fullScene = gd._fullLayout && gd._fullLayout.scene ? gd._fullLayout.scene : null;
    if (fullScene && hasCompleteCamera(fullScene.camera)) return fullScene.camera;
    return null;
  }

  /* SVG layer management.
   *
   * The layer is parented to ``#crystal-graph`` (the wrapper div),
   * positioned absolute so it stretches over the Plotly graph div,
   * and ``pointer-events: none`` so mousedown/wheel still reach the
   * gl3d controller underneath. Z-index is high enough to sit above
   * Plotly's modebar but below the dash floating tooltips.
   */
  function ensureSvgLayer() {
    const root = graphRoot();
    if (!root) return null;
    let svg = root.querySelector("#" + SVG_LAYER_ID);
    if (svg) return svg;
    /* The wrapper must be position:relative so our absolute SVG
       inherits its frame. Plotly leaves ``#crystal-graph`` untyped,
       so set it once. Repeating the assignment is harmless and idempotent. */
    if (!root.style.position || root.style.position === "static") {
      root.style.position = "relative";
    }
    svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("id", SVG_LAYER_ID);
    svg.style.position = "absolute";
    svg.style.left = "0";
    svg.style.top = "0";
    svg.style.width = "100%";
    svg.style.height = "100%";
    svg.style.pointerEvents = "none";
    svg.style.zIndex = "5";
    /* SVG coordinate system stays in pixel space matching the graph
       div's bounding box (set viewBox on resize). Children use plain
       pixel coords. */
    root.appendChild(svg);
    return svg;
  }

  function clearSvg(svg) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
  }

  function svgEl(name, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", name);
    if (attrs) {
      for (const k in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, k)) el.setAttribute(k, attrs[k]);
      }
    }
    return el;
  }

  /* Render compass into the SVG layer.
   *
   * Coordinate convention: SVG coordinates here are pixel coords
   * relative to the GRAPH DIV (which is itself positioned inside the
   * wrapper). We compute the graph div's bounding box vs the wrapper
   * to translate. Y increases downward in pixel space, so the screen-up
   * vector contributes a negative dy.
   */
  function drawCompassSvg(svg, ctx, projections) {
    const root = graphRoot();
    const gd = graphDiv();
    if (!root || !gd) return;
    const rRect = root.getBoundingClientRect();
    const gRect = gd.getBoundingClientRect();
    const offsetX = gRect.left - rRect.left;
    const offsetY = gRect.top - rRect.top;
    const figW = gRect.width || 1024;
    const figH = gRect.height || 720;

    /* Set viewBox to wrapper pixel space so our pixel coords map 1:1. */
    svg.setAttribute("viewBox", "0 0 " + Math.max(1, rRect.width) + " " + Math.max(1, rRect.height));
    svg.setAttribute("width", String(rRect.width));
    svg.setAttribute("height", String(rRect.height));

    const labels = ctx.labels || ["a", "b", "c"];
    const colors = ctx.colors || ["#2F2F2F", "#2F2F2F", "#2F2F2F"];
    const anchor = ctx.anchor || [0.08, 0.12];
    const anchorX = Number(anchor[0]);
    const anchorY = Number(anchor[1]);
    const pixelLength = Number(ctx.pixel_length || 50);
    const lineWidth = Number(ctx.line_width || 2);
    const labelOffset = Number(ctx.label_pixel_offset || 10);
    const fontSize = Number(ctx.font_size || 14);
    const italic = !!ctx.italic;
    const dotThreshold = Number(ctx.dot_threshold || 0.05);
    const dotRadius = Number(ctx.dot_radius_px || 4);
    const arrowhead = Math.max(2, Number(ctx.arrowhead || 3));

    const norms = projections.map((p) => Math.hypot(p[0], p[1]));
    const maxNorm = Math.max.apply(null, norms);
    if (!isFinite(maxNorm) || maxNorm < 1e-8) {
      clearSvg(svg);
      return;
    }

    /* Anchor pixel coords inside the graph div, then translated into
       wrapper pixel coords by adding offsetX/offsetY. Y is in
       SVG-pixel-down convention; Python compass uses paper coords with
       y going up, so we flip when applying the screen-up basis. */
    const anchorPx = offsetX + anchorX * figW;
    const anchorPy = offsetY + (1 - anchorY) * figH;

    /* Same anchor-aware arrow-length cap as the Python overlay so the
       longest arrow + label stay inside the figure. */
    const edgeMarginPx = 8.0;
    const availLeft = Math.max(anchorX * figW - edgeMarginPx, 1.0);
    const availRight = Math.max((1 - anchorX) * figW - edgeMarginPx, 1.0);
    const availDown = Math.max(anchorY * figH - edgeMarginPx, 1.0);
    const availUp = Math.max((1 - anchorY) * figH - edgeMarginPx, 1.0);
    let cap = pixelLength;
    for (let i = 0; i < projections.length; i++) {
      const n = norms[i];
      if (n < 1e-12) continue;
      const ux = projections[i][0] / n;
      const uy = projections[i][1] / n;
      const rel = n / maxNorm;
      const wanted = (pixelLength + labelOffset) * rel;
      if (wanted <= 0) continue;
      let allowed = Infinity;
      if (ux > 0) allowed = Math.min(allowed, availRight / ux);
      else if (ux < 0) allowed = Math.min(allowed, availLeft / -ux);
      if (uy > 0) allowed = Math.min(allowed, availUp / uy);
      else if (uy < 0) allowed = Math.min(allowed, availDown / -uy);
      if (allowed < wanted) cap = Math.min(cap, pixelLength * (allowed / wanted));
    }
    const effectivePixelLength = Math.max(cap, 12.0);
    const scalePx = effectivePixelLength / maxNorm;

    clearSvg(svg);

    /* Shared dot anchor circle so the three arrows look like a triad
       (Python compass does this implicitly via shared anchor). Drawn
       once, regardless of arrow vs dot mode. */
    svg.appendChild(svgEl("circle", {
      cx: String(anchorPx),
      cy: String(anchorPy),
      r: "1.4",
      fill: "#2F2F2F",
      "fill-opacity": "0.75",
    }));

    for (let i = 0; i < labels.length && i < 3; i++) {
      const label = labels[i];
      const color = colors[i] || colors[0] || "#2F2F2F";
      const dxWorld = projections[i][0];
      const dyWorld = projections[i][1];
      const norm = norms[i];
      const rel = maxNorm > 0 ? norm / maxNorm : 0;

      if (rel < dotThreshold) {
        svg.appendChild(svgEl("circle", {
          cx: String(anchorPx),
          cy: String(anchorPy),
          r: String(dotRadius),
          fill: color,
        }));
        const labelPx = anchorPx + (dotRadius + labelOffset);
        const labelPy = anchorPy - (dotRadius + labelOffset);
        const text = svgEl("text", {
          x: String(labelPx),
          y: String(labelPy),
          fill: color,
          "font-size": String(fontSize),
          "font-family": "sans-serif",
          "font-style": italic ? "italic" : "normal",
          "text-anchor": "start",
          "dominant-baseline": "alphabetic",
        });
        text.textContent = label;
        svg.appendChild(text);
        continue;
      }

      /* dyWorld is in screen-up basis (positive = up). SVG y points
         down. Flip sign when laying out arrow tip in pixel space. */
      const dxPx = dxWorld * scalePx;
      const dyPx = dyWorld * scalePx;
      const tipX = anchorPx + dxPx;
      const tipY = anchorPy - dyPx;

      svg.appendChild(svgEl("line", {
        x1: String(anchorPx),
        y1: String(anchorPy),
        x2: String(tipX),
        y2: String(tipY),
        stroke: color,
        "stroke-width": String(lineWidth),
        "stroke-linecap": "round",
      }));

      /* Arrowhead as a small triangle aligned with the line direction.
         Plotly's ``arrowhead=3`` is roughly an open chevron at ~12px;
         we approximate with a filled triangle of the same scale so it
         still reads as a 3D triad's tip. */
      const lenPx = Math.hypot(dxPx, dyPx) || 1;
      const ux = dxPx / lenPx;
      const uy = -dyPx / lenPx; // SVG-down adjusted
      const arrowLen = 5 + arrowhead;
      const arrowWidth = 3 + arrowhead * 0.5;
      const baseX = tipX - ux * arrowLen;
      const baseY = tipY - uy * arrowLen;
      const perpX = -uy;
      const perpY = ux;
      const ax1 = baseX + perpX * arrowWidth;
      const ay1 = baseY + perpY * arrowWidth;
      const ax2 = baseX - perpX * arrowWidth;
      const ay2 = baseY - perpY * arrowWidth;
      svg.appendChild(svgEl("polygon", {
        points: tipX + "," + tipY + " " + ax1 + "," + ay1 + " " + ax2 + "," + ay2,
        fill: color,
      }));

      /* Label sits past the tip along the projection direction. */
      const labelPx = tipX + ux * labelOffset;
      const labelPy = tipY + uy * labelOffset;
      const text = svgEl("text", {
        x: String(labelPx),
        y: String(labelPy),
        fill: color,
        "font-size": String(fontSize),
        "font-family": "sans-serif",
        "font-style": italic ? "italic" : "normal",
        "text-anchor": "middle",
        "dominant-baseline": "central",
      });
      text.textContent = label;
      svg.appendChild(text);
    }
  }

  /* Camera-acquisition helpers ported from the previous version.
   * Documentation of WHY ``intScene.getCamera()`` is the live source
   * of truth lives below the function for searchability. */
  function liveSceneCamera(gd) {
    /* Plotly v3 source (src/plots/gl3d/scene.js):
     *   proto.getCamera = function() {
     *     scene.camera.view.recalcMatrix(scene.camera.view.lastT());
     *     return getLayoutCamera(scene.camera);
     *   };
     * Reading this is the only way to get the matrix-after-recalc
     * camera during a rotation drag; ``layout.scene.camera`` only
     * commits on mouseup.
     */
    if (!gd) return null;
    const full = gd._fullLayout || null;
    const sceneFull = full ? full.scene : null;
    const intScene = sceneFull ? sceneFull._scene : null;
    if (intScene && typeof intScene.getCamera === "function") {
      try {
        const c = intScene.getCamera();
        if (c && c.eye && c.center && c.up) return c;
      } catch (err) { /* fall through */ }
    }
    /* Manual recalcMatrix fallback for hypothetical Plotly versions
       that drop ``getCamera`` from the proto. */
    const ctrl = intScene ? (intScene.camera || (intScene.glplot && intScene.glplot.camera) || null) : null;
    if (ctrl && ctrl.view && typeof ctrl.view.recalcMatrix === "function" && typeof ctrl.view.lastT === "function") {
      try {
        ctrl.view.recalcMatrix(ctrl.view.lastT());
        const e = ctrl.eye, c = ctrl.center, u = ctrl.up;
        if (e && c && u) {
          return {
            eye:    { x: e[0], y: e[1], z: e[2] },
            center: { x: c[0], y: c[1], z: c[2] },
            up:     { x: u[0], y: u[1], z: u[2] },
            projection: { type: ctrl._ortho ? "orthographic" : "perspective" },
          };
        }
      } catch (err) { /* fall through */ }
    }
    if (sceneFull && sceneFull.camera && sceneFull.camera.eye) return sceneFull.camera;
    if (gd.layout && gd.layout.scene && gd.layout.scene.camera) return gd.layout.scene.camera;
    return null;
  }

  function redrawCompass(gd, eventCamera, preferLiveCamera) {
    if (window.__mv_compass_diag) window.__mv_compass_diag.svg_redraws += 1;
    if (!gd || !gd.layout) return;
    const ctx = compassFromMeta(gd.layout);
    if (!ctx || !ctx.M) return;
    let camera = eventCamera || null;
    let cameraSource = camera ? "event" : null;
    if (!camera && preferLiveCamera) {
      camera = liveSceneCamera(gd);
      cameraSource = camera ? "live" : null;
    }
    if (!camera) {
      camera = layoutSceneCamera(gd);
      cameraSource = camera ? "layout" : null;
    }
    if (!camera && !preferLiveCamera) {
      camera = liveSceneCamera(gd);
      cameraSource = camera ? "live" : null;
    }
    if (!camera) return;
    if (window.__mv_compass_diag) window.__mv_compass_diag.svg_redraws_with_camera += 1;
    if (window.__mv_compass_diag && cameraSource === "layout") window.__mv_compass_diag.svg_redraws_with_layout_camera += 1;
    if (window.__mv_compass_diag && cameraSource === "live") window.__mv_compass_diag.svg_redraws_with_live_camera += 1;
    const basis = cameraScreenBasis(camera);
    if (!basis) return;
    const projections = projectLattice(ctx.M, basis, ctx.cube_scale);
    if (!projections) return;
    const svg = ensureSvgLayer();
    if (!svg) return;
    drawCompassSvg(svg, ctx, projections);
  }

  /* rAF coalesce: a high-frequency event burst still does at most
     one redraw per frame. */
  let scheduled = null;
  let scheduledGd = null;
  let scheduledCamera = null;
  function scheduleRedraw(gd, camera) {
    scheduledGd = gd;
    scheduledCamera = camera;
    if (scheduled !== null) return;
    scheduled = (window.requestAnimationFrame || window.setTimeout)(function () {
      const g = scheduledGd;
      const c = scheduledCamera;
      scheduled = null;
      scheduledGd = null;
      scheduledCamera = null;
      redrawCompass(g, c, false);
    });
  }

  /* During-drag camera poll: Plotly does not commit camera to layout
     until mouseup, so we read from gl-plot-3d every frame. */
  let dragPollActive = false;
  let dragPollRaf = null;
  let dragPollLastKey = null;
  let dragArm = null;
  const DRAG_POLL_THRESHOLD_PX = 3;
  function dragCameraKey(camera) {
    if (!camera) return null;
    function v(obj) {
      if (!obj) return ["?", "?", "?"];
      if (Array.isArray(obj)) return [obj[0], obj[1], obj[2]];
      return [obj.x, obj.y, obj.z];
    }
    return v(camera.eye).concat(v(camera.up), v(camera.center)).join(",");
  }

  function cameraFromRelayout(eventData) {
    if (!eventData || typeof eventData !== "object") return null;
    if (hasCompleteCamera(eventData["scene.camera"])) return eventData["scene.camera"];
    if (eventData.scene && hasCompleteCamera(eventData.scene.camera)) return eventData.scene.camera;
    const base = {};
    let changed = false;
    const groups = ["eye", "center", "up"];
    const axes = ["x", "y", "z"];
    for (let gi = 0; gi < groups.length; gi++) {
      const group = groups[gi];
      for (let ai = 0; ai < axes.length; ai++) {
        const axis = axes[ai];
        const key = "scene.camera." + group + "." + axis;
        if (Object.prototype.hasOwnProperty.call(eventData, key)) {
          if (!base[group]) base[group] = {};
          base[group][axis] = Number(eventData[key]);
          changed = true;
        }
      }
    }
    return changed && hasCompleteCamera(base) ? base : null;
  }

  function dragPollTick(gd) {
    if (!dragPollActive) return;
    if (window.__mv_compass_diag) window.__mv_compass_diag.drag_poll_ticks += 1;
    const camera = liveSceneCamera(gd);
    const key = dragCameraKey(camera);
    if (key && key !== dragPollLastKey) {
      dragPollLastKey = key;
      if (window.__mv_compass_diag) window.__mv_compass_diag.drag_poll_redraws += 1;
      redrawCompass(gd, camera, true);
    }
    dragPollRaf = window.requestAnimationFrame
      ? window.requestAnimationFrame(function () { dragPollTick(gd); })
      : window.setTimeout(function () { dragPollTick(gd); }, 16);
  }
  function startDragPoll(gd) {
    if (dragPollActive) return;
    dragPollActive = true;
    dragPollLastKey = null;
    if (window.__mv_compass_diag) window.__mv_compass_diag.drag_poll_starts += 1;
    dragPollTick(gd);
  }
  function pointerXY(event) {
    if (!event) return null;
    const x = Number(event.clientX);
    const y = Number(event.clientY);
    return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
  }
  function armDragPoll(event) {
    if (event && event.button !== undefined && event.button !== 0) return;
    const xy = pointerXY(event);
    if (!xy) return;
    dragArm = { x: xy[0], y: xy[1] };
  }
  function maybeStartDragPollFromMove(gd, event) {
    if (!dragArm || dragPollActive) return;
    const xy = pointerXY(event);
    if (!xy) return;
    const dx = xy[0] - dragArm.x;
    const dy = xy[1] - dragArm.y;
    if (Math.hypot(dx, dy) < DRAG_POLL_THRESHOLD_PX) return;
    startDragPoll(gd);
  }
  function clearDragArm() {
    dragArm = null;
  }
  function stopDragPoll(gd) {
    if (!dragPollActive) return;
    dragPollActive = false;
    if (dragPollRaf !== null) {
      if (window.cancelAnimationFrame) window.cancelAnimationFrame(dragPollRaf);
      else window.clearTimeout(dragPollRaf);
      dragPollRaf = null;
    }
    /* Wheel zoom does not reliably commit layout.scene.camera before this
       debounce fires. Finish from the live WebGL camera so the compass does
       not snap back to the pre-wheel layout camera. */
    const finalCamera = liveSceneCamera(gd);
    redrawCompass(gd, finalCamera, true);
  }
  let wheelStopTimer = null;
  function pulseDragPollOnWheel(gd) {
    startDragPoll(gd);
    if (wheelStopTimer !== null) clearTimeout(wheelStopTimer);
    wheelStopTimer = setTimeout(function () {
      wheelStopTimer = null;
      stopDragPoll(gd);
    }, 250);
  }

  /* Strip the Python-side compass from Plotly annotations / shapes
     ONCE per gd. Doing this on every relayout would break static
     export pipelines that share the figure JSON. */
  const stripped = new WeakSet();
  function stripPlotlyCompassOnce(gd) {
    if (!gd || stripped.has(gd)) return;
    if (!gd.layout || !window.Plotly || typeof window.Plotly.relayout !== "function") return;
    if (window.__mv_compass_diag) window.__mv_compass_diag.strip_attempts += 1;
    const annotations = Array.isArray(gd.layout.annotations) ? gd.layout.annotations : [];
    const shapes = Array.isArray(gd.layout.shapes) ? gd.layout.shapes : [];
    const hasCompassAnn = annotations.some(function (a) { return a && a.name === COMPASS_ITEM_NAME; });
    const hasCompassShape = shapes.some(function (s) { return s && s.name === COMPASS_ITEM_NAME; });
    if (!hasCompassAnn && !hasCompassShape) {
      stripped.add(gd);
      return;
    }
    const filteredAnn = annotations.filter(function (a) { return !(a && a.name === COMPASS_ITEM_NAME); });
    const filteredShapes = shapes.filter(function (s) { return !(s && s.name === COMPASS_ITEM_NAME); });
    try {
      window.Plotly.relayout(gd, { annotations: filteredAnn, shapes: filteredShapes });
      stripped.add(gd);
      if (window.__mv_compass_diag) window.__mv_compass_diag.strip_completed += 1;
    } catch (err) { /* tolerate; SVG overlay still works */ }
  }

  const attached = new WeakSet();

  function maybeAttach(gd) {
    if (window.__mv_compass_diag) window.__mv_compass_diag.maybe_attach_called += 1;
    if (!gd) {
      if (window.__mv_compass_diag) window.__mv_compass_diag.last_attach_skip_reason = "no_gd";
      return;
    }
    if (attached.has(gd)) {
      if (window.__mv_compass_diag) window.__mv_compass_diag.last_attach_skip_reason = "already_attached";
      return;
    }
    if (typeof gd.on !== "function") {
      if (window.__mv_compass_diag) window.__mv_compass_diag.last_attach_skip_reason = "no_gd_on";
      return;
    }
    attached.add(gd);
    if (window.__mv_compass_diag) window.__mv_compass_diag.maybe_attach_attached += 1;

    /* Plotly events: mouseup commit and (when emitted) per-frame
       drag updates. Both go to scheduleRedraw which coalesces. */
    gd.on("plotly_relayout", function (eventData) { scheduleRedraw(gd, cameraFromRelayout(eventData)); });
    gd.on("plotly_relayouting", function (eventData) { scheduleRedraw(gd, cameraFromRelayout(eventData)); });
    gd.on("plotly_afterplot", function () { scheduleRedraw(gd, null); });

    /* DOM-level drag arming. Window-level mouseup so we still
       disarm if the user releases outside the graph div. */
    const onMouseDown = function (event) { armDragPoll(event); };
    const onMouseMove = function (event) { maybeStartDragPollFromMove(gd, event); };
    const onMouseUp = function () { clearDragArm(); stopDragPoll(gd); };
    const onPointerCancel = function () { clearDragArm(); stopDragPoll(gd); };
    const onWheel = function () { pulseDragPollOnWheel(gd); };
    if (gd.addEventListener) {
      gd.addEventListener("mousedown", onMouseDown);
      gd.addEventListener("pointerdown", onMouseDown);
      gd.addEventListener("wheel", onWheel, { passive: true });
    }
    if (window.addEventListener) {
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("pointermove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
      window.addEventListener("pointerup", onMouseUp);
      window.addEventListener("blur", onPointerCancel);
      window.addEventListener("pointercancel", onPointerCancel);
    }
    /* Resize redraw: SVG viewBox tracks the wrapper rect. */
    if (window.addEventListener) {
      window.addEventListener("resize", function () { scheduleRedraw(gd, null); });
    }

    /* First-paint: strip Plotly compass, then draw SVG. The strip
       runs through Plotly.relayout exactly ONCE; that one relayout
       happens before any drag, so it cannot interfere with a live
       rotation. */
    setTimeout(function () {
      stripPlotlyCompassOnce(gd);
      redrawCompass(gd, null, false);
    }, 0);
  }

  function tick() { maybeAttach(graphDiv()); }

  let scopedObserver = null;
  let bodyObserver = null;
  let attachRetryTimer = null;
  let attachRetryDeadline = 0;
  function attachRetryTick() {
    attachRetryTimer = null;
    const before = window.__mv_compass_diag ? window.__mv_compass_diag.maybe_attach_attached : -1;
    tick();
    const after = window.__mv_compass_diag ? window.__mv_compass_diag.maybe_attach_attached : -1;
    if (after > before) {
      /* Schedule one more redraw a tick later so the gl3d scene's
         _scene is fully built before we sample its camera. */
      setTimeout(function () { redrawCompass(graphDiv(), null, false); }, 200);
      return;
    }
    if (Date.now() > attachRetryDeadline) return;
    attachRetryTimer = window.setTimeout(attachRetryTick, 200);
  }
  function startAttachRetry() {
    attachRetryDeadline = Date.now() + 15000;
    if (attachRetryTimer === null) attachRetryTick();
  }
  function bindObservers() {
    const root = graphRoot();
    if (root) {
      if (bodyObserver) { bodyObserver.disconnect(); bodyObserver = null; }
      if (!scopedObserver) {
        scopedObserver = new MutationObserver(function () {
          tick();
          /* On any DOM mutation in the graph wrapper (Dash callback,
             scene swap, ...) we need to re-strip and re-draw because
             Plotly may have reinjected the compass annotations. */
          const gd = graphDiv();
          if (gd && stripped.has(gd)) {
            /* Already stripped; just redraw. */
            scheduleRedraw(gd, null);
          } else {
            stripPlotlyCompassOnce(gd);
            scheduleRedraw(gd, null);
          }
        });
        scopedObserver.observe(root, { childList: true, subtree: true });
      }
      startAttachRetry();
      return;
    }
    if (!bodyObserver) {
      bodyObserver = new MutationObserver(function () {
        if (graphRoot()) bindObservers();
      });
      bodyObserver.observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindObservers);
  } else {
    bindObservers();
  }
})();
