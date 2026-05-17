/* Optional WebSocket figure fast lane.
 *
 * Disabled by default: set window.MATTERVIS_WS_FIGURE = true before Dash
 * hydrates if you want the browser to subscribe to full-figure snapshots over
 * /api/v2/ws. The HTTP Dash figure output remains the fallback.
 */
(function () {
  if (window.MATTERVIS_WS_FIGURE !== true) return;

  function graphDiv() {
    const root = document.getElementById("crystal-graph");
    return root ? root.querySelector(".js-plotly-plot") : null;
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
      const gd = graphDiv();
      if (!gd) return;
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
