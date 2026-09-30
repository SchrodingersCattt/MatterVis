/* Shared view transport.  Dash and WebSocket producers publish envelopes;
 * this module only deduplicates and forwards them to the single submitter. */
(function (root) {
  "use strict";
  var lastSeq = 0;
  var listeners = [];
  var socket = null;
  var reconnectTimer = null;
  var stopped = false;
  function on(listener) { if (typeof listener === "function") listeners.push(listener); }
  function deliver(message) {
    if (!message || typeof message !== "object") return false;
    var seq = Number(message.figure_seq || message.seq || 0);
    if (seq && seq <= lastSeq) return false;
    if (seq) lastSeq = seq;
    listeners.slice().forEach(function (listener) { try { listener(message); } catch (_) {} });
    return true;
  }
  function connect() {
    if (stopped || root.MATTERVIS_WS_FIGURE === false || !root.WebSocket) return;
    if (!root.Plotly) { root.requestAnimationFrame(connect); return; }
    var protocol = root.location.protocol === "https:" ? "wss:" : "ws:";
    try { socket = new root.WebSocket(protocol + "//" + root.location.host + "/api/v2/ws"); }
    catch (_) { fallback(); return; }
    socket.addEventListener("open", function () { socket.send(JSON.stringify({type: "subscribe_figure", enabled: true})); });
    socket.addEventListener("message", function (event) {
      try { deliver(JSON.parse(event.data || "{}")); } catch (_) {}
    });
    socket.addEventListener("close", function () {
      socket = null;
      if (!stopped) { clearTimeout(reconnectTimer); reconnectTimer = setTimeout(connect, 1500); }
    });
  }
  function fallback() {
    if (stopped || !root.fetch) return;
    root.fetch("/api/v2/view-updates?since=" + encodeURIComponent(lastSeq), {credentials: "same-origin"})
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (body) {
        (body && body.updates || []).forEach(deliver);
        if (!stopped) reconnectTimer = setTimeout(fallback, 1000);
      }).catch(function () { if (!stopped) reconnectTimer = setTimeout(fallback, 2000); });
  }
  root.MatterVisViewTransport = {
    on: on,
    deliver: deliver,
    lastSequence: function () { return lastSeq; },
    reset: function () { lastSeq = 0; }
    ,connect: connect,
    fallback: fallback,
    stop: function () { stopped = true; if (socket) socket.close(); clearTimeout(reconnectTimer); }
  };
  /* mattervis.js owns the legacy connection while this module is loaded; a
     host may opt into the transport explicitly without creating a second
     socket. */
})(window);
