/* Optional WebSocket figure fast lane.
 *
 * Enabled by default. Set window.MATTERVIS_WS_FIGURE = false before Dash
 * hydrates to force the legacy HTTP Dash figure path.
 */
(function () {
  if (window.MATTERVIS_WS_FIGURE === false) return;
  let lastFigureSeq = 0;

  // ---- Interaction gate -------------------------------------------------
  // Plotly rotation/zoom is entirely client-side. A background figure push
  // (prewarm-ready / topology-ready) applied via Plotly.react tears down and
  // rebuilds the WebGL scene, which interrupts an in-progress mouse drag and
  // makes rotation feel frozen -- most noticeable right after an upload, when
  // several broadcasts arrive in a burst. So we DECOUPLE async figure pushes
  // from live interaction: while the user is dragging/zooming the scene we
  // stash only the latest push and flush it once the gesture settles.
  let interacting = false;
  let interactingTimer = null;
  let pendingPush = null; // { data, layout, seq }

  function endInteractingSoon() {
    if (interactingTimer) window.clearTimeout(interactingTimer);
    interactingTimer = window.setTimeout(function () {
      interacting = false;
      interactingTimer = null;
      flushPendingPush();
    }, 220);
  }

  function markInteracting() {
    interacting = true;
    if (interactingTimer) {
      window.clearTimeout(interactingTimer);
      interactingTimer = null;
    }
  }

  function inGraph(target) {
    return Boolean(target && target.closest && target.closest("#crystal-graph"));
  }

  document.addEventListener(
    "pointerdown",
    function (event) {
      if (inGraph(event.target)) markInteracting();
    },
    true
  );
  document.addEventListener("pointerup", function () {
    if (interacting) endInteractingSoon();
  });
  document.addEventListener("pointercancel", function () {
    if (interacting) endInteractingSoon();
  });
  document.addEventListener(
    "wheel",
    function (event) {
      if (inGraph(event.target)) {
        markInteracting();
        endInteractingSoon();
      }
    },
    { passive: true, capture: true }
  );

  function graphDiv() {
    const root = document.getElementById("crystal-graph");
    return root ? root.querySelector(".js-plotly-plot") : null;
  }

  // Apply a figure push, preserving the user's current on-screen camera.
  // The user's live camera comes from mouse-drag rotation, which is NOT
  // persisted server-side (only axis-button moves are), so the incoming
  // layout carries a stale/default scene.camera. Re-applying the live
  // camera here keeps async pushes from resetting the view.
  function applyPush(data, rawLayout) {
    const gd = graphDiv();
    if (!gd) return;
    const layout = rawLayout || {};
    try {
      const liveCam =
        gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene.camera;
      if (liveCam) {
        layout.scene = Object.assign({}, layout.scene || {}, {
          camera: JSON.parse(JSON.stringify(liveCam)),
        });
      }
    } catch (err) {
      /* best effort: fall back to server-provided camera */
    }
    window.Plotly.react(gd, data || [], layout);
  }

  function flushPendingPush() {
    if (!pendingPush) return;
    const push = pendingPush;
    pendingPush = null;
    applyPush(push.data, push.layout);
  }

  function currentSceneId() {
    const node = document.getElementById("fast-view-metadata");
    const text = node ? (node.textContent || "").trim() : "";
    if (!text) return null;
    try {
      const meta = JSON.parse(text);
      return meta && meta.scene_id ? String(meta.scene_id) : null;
    } catch (err) {
      return null;
    }
  }

  function has3DScene(figure) {
    const layout = figure && figure.layout;
    const data = (figure && Array.isArray(figure.data)) ? figure.data : [];
    if (!layout || typeof layout !== "object" || !layout.scene || typeof layout.scene !== "object") return false;
    return data.some(function (trace) {
      const type = String((trace && trace.type) || "").toLowerCase();
      return type === "mesh3d" || type === "scatter3d" || type === "cone" || Boolean(trace && trace.z);
    });
  }

  function connect() {
    if (!window.WebSocket || !window.Plotly) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(proto + "//" + window.location.host + "/api/v2/ws");
    ws.addEventListener("open", function () {
      ws.send(JSON.stringify({ type: "subscribe_figure", enabled: true }));
    });
    ws.addEventListener("message", function (event) {
      let payload;
      try {
        payload = JSON.parse(event.data || "{}");
      } catch (err) {
        return;
      }
      if (!payload.figure) return;
      if (payload.figure._mattervis_pending || !has3DScene(payload.figure)) return;
      const current = currentSceneId();
      if (current && payload.scene_id && String(payload.scene_id) !== current) return;
      const seq = Number(payload.figure_seq || payload.figure_version || 0);
      if (seq && seq <= lastFigureSeq) return;
      const gd = graphDiv();
      if (!gd) return;
      if (seq) lastFigureSeq = seq;
      const data = payload.figure.data || [];
      const layout = payload.figure.layout || {};
      if (interacting) {
        // The user is mid drag/zoom: stash only the latest push and apply
        // it when the gesture settles, so async re-renders never interrupt
        // live rotation.
        pendingPush = { data: data, layout: layout, seq: seq };
        return;
      }
      applyPush(data, layout);
    });
    ws.addEventListener("close", function () {
      window.setTimeout(connect, 1500);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();
