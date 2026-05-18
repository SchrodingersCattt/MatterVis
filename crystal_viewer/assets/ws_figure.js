/* Optional WebSocket figure fast lane.
 *
 * Enabled by default. Set window.MATTERVIS_WS_FIGURE = false before Dash
 * hydrates to force the legacy HTTP Dash figure path.
 */
(function () {
  if (window.MATTERVIS_WS_FIGURE === false) return;
  let lastFigureSeq = 0;

  function graphDiv() {
    const root = document.getElementById("crystal-graph");
    return root ? root.querySelector(".js-plotly-plot") : null;
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
      window.Plotly.react(gd, payload.figure.data || [], payload.figure.layout || {});
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
