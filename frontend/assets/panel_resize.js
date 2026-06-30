(function () {
  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function bindSplitter(splitterId, panelId, edge) {
    const splitter = document.getElementById(splitterId);
    const panel = document.getElementById(panelId);
    const root = document.getElementById("viewer-root");
    if (!splitter || !panel || !root || splitter.dataset.bound === "1") {
      return;
    }

    splitter.dataset.bound = "1";
    splitter.addEventListener("mousedown", function (event) {
      event.preventDefault();
      panel.classList.remove("analysis-panel--collapsed");
      const rootRect = root.getBoundingClientRect();
      document.body.classList.add("panel-resizing");

      function onMove(moveEvent) {
        let width;
        if (edge === "left") {
          width = clamp(moveEvent.clientX - rootRect.left, 260, 640);
        } else {
          width = clamp(rootRect.right - moveEvent.clientX, 260, 640);
        }
        panel.style.width = width + "px";
        panel.style.flex = "0 0 auto";
      }

      function onUp() {
        document.body.classList.remove("panel-resizing");
        window.removeEventListener("mousemove", onMove);
      }

      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp, { once: true });
    });
  }

  // ``Analysis`` and ``Operation`` are parallel tabs that share the
  // single right-hand collapsible panel. Selecting one shows its own
  // content frame and hides the other; clicking the already-active tab
  // collapses the panel. Tab state is purely visual (CSS classes), so
  // there is no server round trip.
  function setActiveTab(panel, tab) {
    const analysisBtn = document.getElementById("analysis-panel-toggle");
    const operationBtn = document.getElementById("operation-panel-toggle");
    const analysisContent = document.getElementById("analysis-panel-content");
    const operationContent = document.getElementById("operation-panel-content");
    const isOperation = tab === "operation";
    if (analysisBtn) analysisBtn.classList.toggle("analysis-panel-toggle--active", !isOperation);
    if (operationBtn) operationBtn.classList.toggle("analysis-panel-toggle--active", isOperation);
    if (analysisContent) analysisContent.classList.toggle("analysis-tab-content--hidden", isOperation);
    if (operationContent) operationContent.classList.toggle("analysis-tab-content--hidden", !isOperation);
    panel.dataset.activeTab = tab;
  }

  function bindPanelTab(toggleId, tab) {
    const toggle = document.getElementById(toggleId);
    const panel = document.getElementById("right-panel");
    if (!toggle || !panel || toggle.dataset.bound === "1") {
      return;
    }
    toggle.dataset.bound = "1";
    toggle.addEventListener("click", function (event) {
      event.preventDefault();
      const collapsed = panel.classList.contains("analysis-panel--collapsed");
      const alreadyActive = panel.dataset.activeTab === tab;
      if (!collapsed && alreadyActive) {
        // Clicking the active tab again collapses the panel.
        panel.classList.add("analysis-panel--collapsed");
      } else {
        panel.classList.remove("analysis-panel--collapsed");
        setActiveTab(panel, tab);
      }
      window.setTimeout(function () {
        window.dispatchEvent(new Event("resize"));
      }, 180);
    });
  }

  function init() {
    bindSplitter("left-splitter", "left-panel", "left");
    bindSplitter("right-splitter", "right-panel", "right");
    bindPanelTab("analysis-panel-toggle", "analysis");
    bindPanelTab("operation-panel-toggle", "operation");
    const panel = document.getElementById("right-panel");
    if (panel && !panel.dataset.activeTab) {
      setActiveTab(panel, "analysis");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  const observer = new MutationObserver(function () {
    init();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
