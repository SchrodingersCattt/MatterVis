(function () {
  const SCENE_MIN = 420;
  const PANEL_MIN = 260;
  const PANEL_MAX = 640;
  const COMPACT_BREAKPOINT = 756;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  let resizingScene = false;

  function resizeScene() {
    if (resizingScene) {
      return;
    }
    resizingScene = true;
    try {
      const graph = document.getElementById("crystal-graph");
      if (window.Plotly && graph) {
        const plot = graph.classList.contains("js-plotly-plot")
          ? graph
          : graph.querySelector(".js-plotly-plot");
        if (plot) {
          window.Plotly.Plots.resize(plot);
        }
      }
      window.dispatchEvent(new Event("resize"));
    } finally {
      resizingScene = false;
    }
  }

  function panelWidth(id) {
    const panel = document.getElementById(id);
    return panel ? panel.getBoundingClientRect().width : 0;
  }

  function isCompact(root) {
    return !!root && root.getBoundingClientRect().width < COMPACT_BREAKPOINT;
  }

  function setCompactPanelOpen(panelId, open) {
    const panel = document.getElementById(panelId);
    const button = document.getElementById("compact-toggle-" + panelId);
    if (!panel || !button) return;
    panel.classList.toggle("compact-panel-open", open);
    button.setAttribute("aria-expanded", String(open));
  }

  function ensureCompactToggles(root) {
    [
      ["left-panel", "Tools", "left"],
      ["mv-extension-panels", "Chat", "right"],
    ].forEach(function (entry) {
      const panelId = entry[0];
      const label = entry[1];
      const side = entry[2];
      if (!document.getElementById(panelId)) return;
      let button = document.getElementById("compact-toggle-" + panelId);
      if (button) return;
      button = document.createElement("button");
      button.type = "button";
      button.id = "compact-toggle-" + panelId;
      button.className = "compact-panel-toggle compact-panel-toggle--" + side;
      button.textContent = label;
      button.setAttribute("aria-controls", panelId);
      button.setAttribute("aria-expanded", "false");
      button.addEventListener("click", function () {
        const panel = document.getElementById(panelId);
        setCompactPanelOpen(panelId, !panel.classList.contains("compact-panel-open"));
      });
      root.appendChild(button);
    });
  }

  function updateCompactLayout() {
    const root = document.getElementById("viewer-root");
    if (!root) return false;
    ensureCompactToggles(root);
    const compact = isCompact(root);
    root.classList.toggle("compact-layout", compact);
    if (!compact) {
      ["left-panel", "mv-extension-panels"].forEach(function (panelId) {
        setCompactPanelOpen(panelId, false);
      });
    }
    return compact;
  }

  function maxPanelWidth(panelId) {
    const root = document.getElementById("viewer-root");
    if (!root) {
      return PANEL_MAX;
    }
    const otherId = panelId === "left-panel" ? "mv-extension-panels" : "left-panel";
    const splitters = document.querySelectorAll("#viewer-root > .panel-splitter").length * 8;
    const available = root.getBoundingClientRect().width - panelWidth(otherId) - SCENE_MIN - splitters;
    if (available < PANEL_MIN) {
      return Math.max(160, available);
    }
    return Math.min(PANEL_MAX, available);
  }

  function fitPanels() {
    const left = document.getElementById("left-panel");
    const right = document.getElementById("mv-extension-panels");
    const root = document.getElementById("viewer-root");
    if (!left || !root) {
      return;
    }
    // At phone/tablet widths, sidebars become overlays controlled by the
    // compact toggle buttons. Keeping their inline desktop widths here would
    // reserve flex space and bring back the clipping this mode avoids.
    if (updateCompactLayout()) {
      resizeScene();
      return;
    }
    const splitters = document.querySelectorAll("#viewer-root > .panel-splitter").length * 8;
    const budget = root.getBoundingClientRect().width - SCENE_MIN - splitters;
    let leftWidth = left.getBoundingClientRect().width;
    let rightWidth = right ? right.getBoundingClientRect().width : 0;
    const used = leftWidth + rightWidth;
    if (used > budget && used > 0) {
      const scale = Math.max(budget, 160) / used;
      leftWidth *= scale;
      rightWidth *= scale;
    }
    left.style.width = clamp(leftWidth, 160, PANEL_MAX) + "px";
    left.style.flex = "0 0 auto";
    if (right) {
      right.style.width = clamp(rightWidth, 160, PANEL_MAX) + "px";
      right.style.flex = "0 0 auto";
    }
    resizeScene();
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
      let frame = 0;

      function onMove(moveEvent) {
        let width;
        if (edge === "left") {
          width = moveEvent.clientX - rootRect.left;
        } else {
          width = rootRect.right - moveEvent.clientX;
        }
        const limit = maxPanelWidth(panelId);
        const floor = Math.min(PANEL_MIN, limit);
        panel.style.width = clamp(width, floor, limit) + "px";
        panel.style.flex = "0 0 auto";
        if (!frame) {
          frame = window.requestAnimationFrame(function () {
            frame = 0;
            resizeScene();
          });
        }
      }

      function onUp() {
        document.body.classList.remove("panel-resizing");
        window.removeEventListener("mousemove", onMove);
        if (frame) {
          window.cancelAnimationFrame(frame);
        }
        resizeScene();
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
    bindSplitter("extension-splitter", "mv-extension-panels", "right");
    if (!document.body.dataset.panelsFitted) {
      document.body.dataset.panelsFitted = "1";
      updateCompactLayout();
      fitPanels();
      window.addEventListener("resize", function () {
        if (!resizingScene) {
          updateCompactLayout();
          fitPanels();
        }
      });
    }
    updateCompactLayout();
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
