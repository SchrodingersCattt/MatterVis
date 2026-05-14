/* Live-camera reprojection of the corner axis compass.
 *
 * The Python ``axis_key_overlay`` writes the compass annotations +
 * shapes into ``fig.layout.annotations`` / ``fig.layout.shapes`` and
 * stashes the inputs needed for reprojection (lattice matrix, anchor,
 * sizing, palette) into ``fig.layout.meta.compass``. Each compass
 * annotation / shape is tagged with ``meta.mv_compass = true`` so this
 * handler can recompute the triad without disturbing other overlays
 * (ORTEP disorder legend, topology badges, ...).
 *
 * The server-side ``capture_camera`` callback intentionally writes
 * camera changes only into ``camera-state-store`` (NOT
 * ``agent-state-store``) so the figure is not rebuilt on every camera
 * tick -- that was deliberate, to avoid the periodic-poll snap-back.
 * Side effect: a server-side rebuild does not fire on bare camera
 * drag, so the compass would otherwise stay frozen at the camera
 * that was current the last time the figure was rebuilt. This
 * handler closes that gap in JS by reprojecting on every
 * ``plotly_relayout`` event whose payload includes a camera change.
 */
(function () {
  // Sentinel value written into ``annotation.name`` / ``shape.name`` by
  // the Python ``axis_key_overlay``. Plotly only accepts strings on
  // per-item ``meta`` slots so we tag with ``name`` instead, which is
  // explicitly free-form and is preserved through the ``Plotly.relayout``
  // round-trip.
  const COMPASS_ITEM_NAME = "mv_compass";

  function graphRoot() {
    return document.getElementById("crystal-graph");
  }

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
    return [
      Number(obj.x) || (fallback ? fallback[0] : 0),
      Number(obj.y) || (fallback ? fallback[1] : 0),
      Number(obj.z) || (fallback ? fallback[2] : 0),
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
    let right = cross(up, view);
    let rn = norm3(right);
    if (rn < 1e-12) return null;
    right = scale(right, 1 / rn);
    let screenUp = cross(view, right);
    const sn = norm3(screenUp);
    if (sn < 1e-12) return null;
    screenUp = scale(screenUp, 1 / sn);
    return { right: right, screenUp: screenUp };
  }

  function projectLattice(M, basis, cubeScale) {
    /* Same aspect-mode correction as the Python side: with
       aspectmode="data" Plotly's camera operates in normalised cube
       coords, so a lattice vector in data space must be divided
       per-axis by the data half-range (cubeScale[i]) before
       projecting onto the camera basis. cubeScale is null/missing
       when the scene was already uniform_viewport-stamped (i.e.
       aspectmode="cube"), in which case data == cube and we project
       M as-is. */
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
      out.push([dot(v, basis.right), dot(v, basis.screenUp)]);
    }
    return out;
  }

  function rebuildCompass(ctx, projections, figW, figH) {
    /* Mirror the Python ``axis_key_overlay`` layout step. Keep the
       logic intentionally simple so it matches line-for-line: a
       single shared anchor, pixel-scaled arrows preserving relative
       projection magnitudes, dot fallback for axes near the view
       direction. */
    const labels = ctx.labels || ["a", "b", "c"];
    const colors = ctx.colors || ["#2F2F2F", "#2F2F2F", "#2F2F2F"];
    const anchor = ctx.anchor || [0.08, 0.12];
    const anchorX = Number(anchor[0]);
    const anchorY = Number(anchor[1]);
    const pixelLength = Number(ctx.pixel_length || 50);
    const lineWidth = Number(ctx.line_width || 2);
    const arrowhead = Number(ctx.arrowhead || 3);
    const labelOffset = Number(ctx.label_pixel_offset || 10);
    const fontSize = Number(ctx.font_size || 14);
    const italic = !!ctx.italic;
    const dotThreshold = Number(ctx.dot_threshold || 0.05);
    const dotRadius = Number(ctx.dot_radius_px || 4);

    const norms = projections.map((p) => Math.hypot(p[0], p[1]));
    const maxNorm = Math.max.apply(null, norms);
    if (!isFinite(maxNorm) || maxNorm < 1e-8) return { annotations: [], shapes: [] };

    /* Same anchor-aware arrow-length clamp as the Python overlay so
       the longest arrow + label stay inside the figure margin. The
       previous fixed-pixel arrow would shoot a label off-screen
       whenever the axis projected close to a figure edge. */
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

    const annotations = [];
    const shapes = [];

    for (let i = 0; i < labels.length && i < 3; i++) {
      const label = labels[i];
      const color = colors[i] || colors[0] || "#2F2F2F";
      const text = italic ? "<i>" + label + "</i>" : String(label);
      const dxWorld = projections[i][0];
      const dyWorld = projections[i][1];
      const norm = norms[i];
      const rel = maxNorm > 0 ? norm / maxNorm : 0;

      if (rel < dotThreshold) {
        const rx = dotRadius / figW;
        const ry = dotRadius / figH;
        shapes.push({
          type: "circle",
          xref: "paper", yref: "paper",
          x0: anchorX - rx, x1: anchorX + rx,
          y0: anchorY - ry, y1: anchorY + ry,
          fillcolor: color,
          line: { color: color, width: 0 },
          layer: "above",
          name: COMPASS_ITEM_NAME,
        });
        const offset = dotRadius + labelOffset;
        annotations.push({
          x: anchorX + offset / figW,
          y: anchorY + offset / figH,
          xref: "paper", yref: "paper",
          text: text,
          showarrow: false,
          xanchor: "left", yanchor: "bottom",
          font: { size: fontSize, color: color },
          name: COMPASS_ITEM_NAME,
        });
        continue;
      }

      const dxPx = dxWorld * scalePx;
      const dyPx = dyWorld * scalePx;
      const tipX = anchorX + dxPx / figW;
      const tipY = anchorY + dyPx / figH;
      annotations.push({
        x: tipX, y: tipY,
        ax: -dxPx, ay: dyPx,
        xref: "paper", yref: "paper",
        axref: "pixel", ayref: "pixel",
        showarrow: true,
        arrowhead: arrowhead,
        arrowsize: 1.0,
        arrowwidth: lineWidth,
        arrowcolor: color,
        text: "",
        standoff: 0,
        startstandoff: 0,
        name: COMPASS_ITEM_NAME,
      });
      const lenPx = Math.hypot(dxPx, dyPx);
      const ux = dxPx / lenPx;
      const uy = dyPx / lenPx;
      annotations.push({
        x: tipX + ux * labelOffset / figW,
        y: tipY + uy * labelOffset / figH,
        xref: "paper", yref: "paper",
        text: text,
        showarrow: false,
        xanchor: "center", yanchor: "middle",
        font: { size: fontSize, color: color },
        name: COMPASS_ITEM_NAME,
      });
    }
    return { annotations: annotations, shapes: shapes };
  }

  function compassFromMeta(layout) {
    if (!layout || !layout.meta) return null;
    // Plotly sometimes stringifies meta on the round-trip; tolerate both.
    let meta = layout.meta;
    if (typeof meta === "string") {
      try { meta = JSON.parse(meta); } catch (err) { return null; }
    }
    return (meta && meta.compass) ? meta.compass : null;
  }

  function isCompassEntry(entry) {
    return !!(entry && entry.name === COMPASS_ITEM_NAME);
  }

  function reprojectCompass(gd, eventData) {
    if (!gd || !gd.layout) return;
    const ctx = compassFromMeta(gd.layout);
    if (!ctx || !ctx.M) return;
    const scene = gd.layout.scene || {};
    let camera = scene.camera || null;
    if (eventData) {
      if (eventData["scene.camera"]) camera = eventData["scene.camera"];
      else if (eventData.scene && eventData.scene.camera) camera = eventData.scene.camera;
    }
    if (!camera) return;
    const basis = cameraScreenBasis(camera);
    if (!basis) return;
    const projections = projectLattice(ctx.M, basis, ctx.cube_scale);
    if (!projections) return;

    /* Pull the actual rendered figure size so paper-coord arrows
       land in the right place on both narrow phones and wide desktops.
       Falls back to the values Python embedded if the graph div has
       not been measured yet (e.g. very first paint). */
    const rect = gd.getBoundingClientRect ? gd.getBoundingClientRect() : null;
    const figW = (rect && rect.width > 0) ? rect.width : 1024;
    const figH = (rect && rect.height > 0) ? rect.height : 720;

    const fresh = rebuildCompass(ctx, projections, figW, figH);

    const existingAnnotations = Array.isArray(gd.layout.annotations) ? gd.layout.annotations : [];
    const existingShapes = Array.isArray(gd.layout.shapes) ? gd.layout.shapes : [];
    const preservedAnnotations = existingAnnotations.filter(function (a) { return !isCompassEntry(a); });
    const preservedShapes = existingShapes.filter(function (s) { return !isCompassEntry(s); });

    const newAnnotations = preservedAnnotations.concat(fresh.annotations);
    const newShapes = preservedShapes.concat(fresh.shapes);

    if (window.Plotly && typeof window.Plotly.relayout === "function") {
      window.Plotly.relayout(gd, {
        annotations: newAnnotations,
        shapes: newShapes,
      });
    }
  }

  const attached = new WeakSet();

  function maybeAttach(gd) {
    if (!gd || attached.has(gd) || typeof gd.on !== "function") return;
    attached.add(gd);
    /* ``plotly_relayout`` fires after the user finishes interacting
       with the camera (mouse-up). ``plotly_relayouting`` fires in the
       middle of a drag; bind both so the compass tracks the camera
       both during AND after rotation. The handlers are cheap (a
       handful of vector ops + Plotly.relayout for two layout keys),
       so the during-drag binding is safe. */
    gd.on("plotly_relayout", function (ev) { reprojectCompass(gd, ev); });
    gd.on("plotly_relayouting", function (ev) { reprojectCompass(gd, ev); });
    /* Also do an initial reprojection once the figure mounts so the
       compass picks up the figure's actual rendered size (the
       Python-embedded 1024x720 default is just a placeholder). */
    setTimeout(function () { reprojectCompass(gd, null); }, 0);
  }

  function tick() {
    maybeAttach(graphDiv());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", tick);
  } else {
    tick();
  }
  /* Dash swaps the graph div in/out on scene tab switches. Re-attach
     each time the DOM mutates so the handlers don't go stale. */
  const observer = new MutationObserver(tick);
  observer.observe(document.body, { childList: true, subtree: true });
})();
