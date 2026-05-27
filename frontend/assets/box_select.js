/* Shift-drag box selection for the 3D crystal viewer. */
(function () {
  function pushSelection(rect, viewport, additive) {
    if (!window.dash_clientside || typeof window.dash_clientside.set_props !== "function") {
      return false;
    }
    window.dash_clientside.set_props("rightclick-target", {
      data: {
        kind: "_global",
        action: "select_box",
        rect_pixels: rect,
        viewport_size: viewport,
        additive: !!additive,
        ts: Date.now(),
      },
    });
    return true;
  }

  function bind() {
    const container = document.getElementById("crystal-graph");
    if (!container || container.dataset.boxSelectBound === "1") return;
    container.dataset.boxSelectBound = "1";
    let start = null;
    let overlay = null;

    function ensureOverlay() {
      if (overlay) return overlay;
      overlay = document.createElement("div");
      overlay.className = "mv-box-select";
      overlay.style.position = "fixed";
      overlay.style.border = "1px solid #FFD24A";
      overlay.style.background = "rgba(255, 210, 74, 0.18)";
      overlay.style.pointerEvents = "none";
      overlay.style.zIndex = "9999";
      document.body.appendChild(overlay);
      return overlay;
    }

    function updateOverlay(event) {
      if (!start) return;
      const el = ensureOverlay();
      const left = Math.min(start.clientX, event.clientX);
      const top = Math.min(start.clientY, event.clientY);
      const width = Math.abs(event.clientX - start.clientX);
      const height = Math.abs(event.clientY - start.clientY);
      el.style.left = `${left}px`;
      el.style.top = `${top}px`;
      el.style.width = `${width}px`;
      el.style.height = `${height}px`;
    }

    container.addEventListener("mousedown", function (event) {
      if (!event.shiftKey || event.button !== 0) return;
      start = { clientX: event.clientX, clientY: event.clientY };
      updateOverlay(event);
      event.preventDefault();
      event.stopPropagation();
    }, true);

    document.addEventListener("mousemove", function (event) {
      if (!start) return;
      updateOverlay(event);
      event.preventDefault();
    }, true);

    document.addEventListener("mouseup", function (event) {
      if (!start) return;
      const graphRect = container.getBoundingClientRect();
      const rect = [
        start.clientX - graphRect.left,
        start.clientY - graphRect.top,
        event.clientX - graphRect.left,
        event.clientY - graphRect.top,
      ];
      const viewport = [graphRect.width, graphRect.height];
      const width = Math.abs(rect[2] - rect[0]);
      const height = Math.abs(rect[3] - rect[1]);
      if (overlay) {
        overlay.remove();
        overlay = null;
      }
      start = null;
      if (width >= 4 && height >= 4) {
        pushSelection(rect, viewport, event.shiftKey);
      }
      event.preventDefault();
      event.stopPropagation();
    }, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
  new MutationObserver(bind).observe(document.documentElement, { childList: true, subtree: true });
})();
