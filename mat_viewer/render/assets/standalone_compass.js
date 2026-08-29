/* Keep the corner lattice compass aligned with a standalone Plotly scene.
 *
 * Static exports intentionally bake a paper-coordinate compass. Interactive
 * HTML uses this sibling SVG instead: it can redraw from the live WebGL
 * camera without calling Plotly.relayout during a drag.
 */
(function () {
  "use strict";

  const gd = document.getElementById("{plot_id}");
  if (!gd || gd.dataset.mvStandaloneCompass === "1") return;

  function compassContext() {
    let meta = gd.layout && gd.layout.meta;
    if (typeof meta === "string") {
      try { meta = JSON.parse(meta); } catch (_) { return null; }
    }
    return meta && meta.compass ? meta.compass : null;
  }

  const ctx = compassContext();
  if (!ctx || !Array.isArray(ctx.M) || ctx.M.length !== 3) return;
  gd.dataset.mvStandaloneCompass = "1";

  const diagnostics = {
    redraws: 0,
    drag_frames: 0,
    relayouts: 0,
  };
  gd.__mvStandaloneCompass = diagnostics;

  function xyz(value, fallback) {
    if (!value) return fallback.slice();
    if (Array.isArray(value)) {
      return [Number(value[0]), Number(value[1]), Number(value[2])];
    }
    function component(raw, defaultValue) {
      if (raw === undefined || raw === null) return defaultValue;
      const parsed = Number(raw);
      return Number.isFinite(parsed) ? parsed : defaultValue;
    }
    return [
      component(value.x, fallback[0]),
      component(value.y, fallback[1]),
      component(value.z, fallback[2]),
    ];
  }

  function cross(a, b) {
    return [
      a[1] * b[2] - a[2] * b[1],
      a[2] * b[0] - a[0] * b[2],
      a[0] * b[1] - a[1] * b[0],
    ];
  }

  function dot(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  }

  function normalise(v) {
    const length = Math.hypot(v[0], v[1], v[2]);
    if (!Number.isFinite(length) || length < 1e-12) return null;
    return [v[0] / length, v[1] / length, v[2] / length];
  }

  function screenBasis(camera) {
    if (!camera || !camera.eye || !camera.up) return null;
    const eye = xyz(camera.eye, [0, 0, 1]);
    const center = xyz(camera.center, [0, 0, 0]);
    const up = xyz(camera.up, [0, 1, 0]);
    const view = normalise([
      center[0] - eye[0],
      center[1] - eye[1],
      center[2] - eye[2],
    ]);
    if (!view) return null;
    const right = normalise(cross(view, up));
    if (!right) return null;
    const screenUp = normalise(cross(right, view));
    return screenUp ? {right: right, screenUp: screenUp} : null;
  }

  function projectLattice(camera) {
    const basis = screenBasis(camera);
    if (!basis) return null;
    return ctx.M.map(function (row, index) {
      let vector = [Number(row[0]), Number(row[1]), Number(row[2])];
      if (Array.isArray(ctx.cube_scale) && ctx.cube_scale.length === 3) {
        vector = vector.map(function (value, axis) {
          return value / (Number(ctx.cube_scale[axis]) || 1);
        });
      }
      vector = normalise(vector);
      if (!vector) throw new Error("invalid lattice vector " + index);
      return [dot(vector, basis.right), dot(vector, basis.screenUp)];
    });
  }

  function liveCamera() {
    const fullScene = gd._fullLayout && gd._fullLayout.scene;
    const internal = fullScene && fullScene._scene;
    if (internal && typeof internal.getCamera === "function") {
      try {
        const camera = internal.getCamera();
        if (camera && camera.eye && camera.center && camera.up) return camera;
      } catch (_) {}
    }
    const layoutScene = gd.layout && gd.layout.scene;
    return (
      (layoutScene && layoutScene.camera) ||
      (fullScene && fullScene.camera) ||
      null
    );
  }

  const parent = gd.parentElement || document.body;
  const position = window.getComputedStyle(parent).position;
  if (!position || position === "static") parent.style.position = "relative";

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("data-mattervis-compass", "standalone");
  svg.style.position = "absolute";
  svg.style.pointerEvents = "none";
  svg.style.zIndex = "5";
  parent.appendChild(svg);

  function element(name, attributes) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.keys(attributes).forEach(function (key) {
      node.setAttribute(key, String(attributes[key]));
    });
    return node;
  }

  function fitSvg() {
    svg.style.left = gd.offsetLeft + "px";
    svg.style.top = gd.offsetTop + "px";
    svg.style.width = gd.clientWidth + "px";
    svg.style.height = gd.clientHeight + "px";
    svg.setAttribute("viewBox", "0 0 " + gd.clientWidth + " " + gd.clientHeight);
  }

  function draw(camera) {
    if (!camera || gd.clientWidth < 1 || gd.clientHeight < 1) return;
    let projected;
    try { projected = projectLattice(camera); } catch (_) { return; }
    if (!projected) return;

    fitSvg();
    svg.replaceChildren();
    diagnostics.redraws += 1;

    const labels = ctx.labels || ["a", "b", "c"];
    const colors = ctx.colors || ["#2F2F2F", "#2F2F2F", "#2F2F2F"];
    const anchor = ctx.anchor || [0.10, 0.18];
    const originX = Number(anchor[0]) * gd.clientWidth;
    const originY = (1 - Number(anchor[1])) * gd.clientHeight;
    const pixelLength = Number(ctx.pixel_length || 50);
    const lineWidth = Number(ctx.line_width || 2);
    const labelOffset = Number(ctx.label_pixel_offset || 10);
    const fontSize = Number(ctx.font_size || 14);
    const dotThreshold = Number(ctx.dot_threshold || 0.05);
    const dotRadius = Number(ctx.dot_radius_px || 4);
    const norms = projected.map(function (p) { return Math.hypot(p[0], p[1]); });
    const maxNorm = Math.max.apply(null, norms);
    if (!Number.isFinite(maxNorm) || maxNorm < 1e-8) return;

    svg.appendChild(element("circle", {
      cx: originX, cy: originY, r: 1.4,
      fill: "#2F2F2F", "fill-opacity": 0.75,
    }));

    projected.forEach(function (projection, index) {
      const relative = norms[index] / maxNorm;
      const color = colors[index] || colors[0] || "#2F2F2F";
      const label = labels[index] || String(index + 1);
      if (relative < dotThreshold) {
        svg.appendChild(element("circle", {
          cx: originX, cy: originY, r: dotRadius, fill: color,
        }));
        return;
      }

      const dx = projection[0] * pixelLength / maxNorm;
      const dy = -projection[1] * pixelLength / maxNorm;
      const tipX = originX + dx;
      const tipY = originY + dy;
      const length = Math.hypot(dx, dy);
      const ux = dx / length;
      const uy = dy / length;
      const arrowLength = 7;
      const arrowWidth = 4;
      const baseX = tipX - ux * arrowLength;
      const baseY = tipY - uy * arrowLength;
      const px = -uy;
      const py = ux;

      svg.appendChild(element("line", {
        x1: originX, y1: originY, x2: tipX, y2: tipY,
        stroke: color, "stroke-width": lineWidth, "stroke-linecap": "round",
      }));
      svg.appendChild(element("polygon", {
        points: [
          tipX + "," + tipY,
          (baseX + px * arrowWidth) + "," + (baseY + py * arrowWidth),
          (baseX - px * arrowWidth) + "," + (baseY - py * arrowWidth),
        ].join(" "),
        fill: color,
      }));
      const text = element("text", {
        x: tipX + ux * labelOffset,
        y: tipY + uy * labelOffset,
        fill: color,
        "font-size": fontSize,
        "font-family": "sans-serif",
        "font-style": ctx.italic ? "italic" : "normal",
        "text-anchor": "middle",
        "dominant-baseline": "central",
      });
      text.textContent = label;
      svg.appendChild(text);
    });
  }

  let dragging = false;
  let frame = null;

  function dragFrame() {
    if (!dragging) return;
    diagnostics.drag_frames += 1;
    draw(liveCamera());
    frame = window.requestAnimationFrame(dragFrame);
  }

  gd.addEventListener("mousedown", function (event) {
    if (event.button !== 0 || dragging) return;
    dragging = true;
    dragFrame();
  });

  window.addEventListener("mouseup", function () {
    if (!dragging) return;
    dragging = false;
    if (frame !== null) window.cancelAnimationFrame(frame);
    frame = null;
    draw(liveCamera());
  });

  gd.addEventListener("wheel", function () {
    window.requestAnimationFrame(function () { draw(liveCamera()); });
  }, {passive: true});

  if (typeof gd.on === "function") {
    gd.on("plotly_relayout", function () {
      diagnostics.relayouts += 1;
      draw(liveCamera());
    });
    gd.on("plotly_afterplot", function () { draw(liveCamera()); });
  }
  window.addEventListener("resize", function () { draw(liveCamera()); });
  draw(liveCamera());
})();
