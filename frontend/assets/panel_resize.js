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

  // All native controls stay mounted; selecting a tool only changes visibility.
  // Tab state is local to this page and resets to Display on refresh.
  const toolTabs = ["display", "analysis", "operation"];

  function setActiveTab(panel, tab) {
    toolTabs.forEach(function (name) {
      const button = document.getElementById(name + "-panel-toggle");
      const content = document.getElementById(name + "-panel-content");
      const active = name === tab;
      if (button) {
        button.classList.toggle("analysis-panel-toggle--active", active);
        button.setAttribute("aria-selected", String(active));
        button.setAttribute("aria-pressed", String(active));
        button.tabIndex = active ? 0 : -1;
      }
      if (content) {
        content.classList.toggle("analysis-tab-content--hidden", !active);
      }
    });
    panel.dataset.activeTab = tab;
    window.dispatchEvent(new Event("resize"));
  }

  function bindPanelTab(toggleId, tab) {
    const toggle = document.getElementById(toggleId);
    const panel = document.getElementById("left-panel");
    if (!toggle || !panel || toggle.dataset.bound === "1") {
      return;
    }
    toggle.dataset.bound = "1";
    toggle.addEventListener("click", function (event) {
      event.preventDefault();
      setActiveTab(panel, tab);
    });
    toggle.addEventListener("keydown", function (event) {
      let index = toolTabs.indexOf(tab);
      if (event.key === "ArrowRight") index = (index + 1) % toolTabs.length;
      else if (event.key === "ArrowLeft") index = (index + toolTabs.length - 1) % toolTabs.length;
      else if (event.key === "Home") index = 0;
      else if (event.key === "End") index = toolTabs.length - 1;
      else return; // Native buttons handle Enter and Space.
      event.preventDefault();
      setActiveTab(panel, toolTabs[index]);
      document.getElementById(toolTabs[index] + "-panel-toggle").focus();
    });
  }

  function init() {
    bindSplitter("left-splitter", "left-panel", "left");
    bindPanelTab("display-panel-toggle", "display");
    bindPanelTab("analysis-panel-toggle", "analysis");
    bindPanelTab("operation-panel-toggle", "operation");
    const panel = document.getElementById("left-panel");
    if (panel && !panel.dataset.activeTab) {
      setActiveTab(panel, "display");
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
