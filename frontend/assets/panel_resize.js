(function () {
  const SCENE_MIN = 420;
  const PANEL_MIN = 300;
  const COMPACT_BREAKPOINT = 756;
  // Share of the layout viewport. Browser zoom-out grows CSS pixels; a
  // fixed 380px column would stay a thin strip beside the canvas.
  const DEFAULT_FRACTION = {
    "left-panel": 0.26,
    "mv-extension-panels": 0.22,
  };
  const PANEL_FRACTION_CAP = 0.42;
  const panelFraction = {};

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  let resizingScene = false;
  let lastGraphRect = "";
  let resizeFrame = 0;
  let resizeAgain = false;
  let refitAfterResize = false;

  function graphNode() {
    const graph = document.getElementById("crystal-graph");
    if (!graph) return null;
    return graph.classList.contains("js-plotly-plot")
      ? graph
      : graph.querySelector(".js-plotly-plot");
  }

  function canvasHostSize() {
    const host = document.getElementById("center-panel") || document.getElementById("crystal-graph");
    if (!host) return null;
    const width = Math.round(host.clientWidth);
    const height = Math.round(host.clientHeight);
    if (width < 2 || height < 2) return null;
    return {width: width, height: height};
  }

  function finishSceneResize() {
    resizingScene = false;
    redrawCompass();
    if (refitAfterResize) {
      refitAfterResize = false;
      resizeAgain = false;
      updateCompactLayout();
      fitPanels();
      return;
    }
    if (resizeAgain) {
      resizeAgain = false;
      resizeSceneNow();
    }
  }

  function resizeSceneNow() {
    if (resizingScene) {
      resizeAgain = true;
      return;
    }
    const plot = graphNode();
    const size = canvasHostSize();
    if (!plot || !size || !window.Plotly || typeof window.Plotly.relayout !== "function") {
      return;
    }
    // Plots.resize returns immediately once layout.width and layout.height
    // are both set, which the first autosize does. Measuring the graph
    // itself then keeps that stale box: clipped when the panel shrinks,
    // parked in the top-left when browser zoom enlarges the CSS viewport.
    const dpr = Math.round((window.devicePixelRatio || 1) * 100);
    const key = size.width + "x" + size.height + "@" + dpr;
    if (key === lastGraphRect) return;
    const full = plot._fullLayout || {};
    if (
      Math.abs((full.width || 0) - size.width) < 2 &&
      Math.abs((full.height || 0) - size.height) < 2 &&
      lastGraphRect.endsWith("@" + dpr)
    ) {
      lastGraphRect = key;
      return;
    }
    lastGraphRect = key;
    resizingScene = true;
    resizeAgain = false;
    try {
      plot.style.width = "100%";
      plot.style.height = "100%";
      const pending = window.Plotly.relayout(plot, {
        width: size.width,
        height: size.height,
      });
      if (pending && typeof pending.then === "function") {
        pending.then(finishSceneResize, finishSceneResize);
      } else {
        finishSceneResize();
      }
    } catch (err) {
      finishSceneResize();
    }
  }

  function redrawCompass() {
    if (window.MatterVisCompass && typeof window.MatterVisCompass.redraw === "function") {
      window.MatterVisCompass.redraw();
    }
  }

  function resizeScene() {
    // Resize immediately and once more on the next paint so flex layout and
    // CSS transitions settle before Plotly recalculates the 3-D viewport.
    // A width/height relayout is a full gl3d replot, so a second call waits
    // until that promise settles instead of clearing Plotly's queue.
    if (resizingScene) {
      resizeAgain = true;
      return;
    }
    resizeSceneNow();
    if (resizeFrame || !window.requestAnimationFrame) return;
    resizeFrame = window.requestAnimationFrame(function () {
      resizeFrame = 0;
      resizeSceneNow();
    });
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

  function chosenFraction(panelId) {
    if (panelFraction[panelId] != null) return panelFraction[panelId];
    return DEFAULT_FRACTION[panelId] || 0.22;
  }

  function rememberFraction(panelId, width, rootWidth) {
    if (!(rootWidth > 0)) return;
    panelFraction[panelId] = clamp(width / rootWidth, 0.12, PANEL_FRACTION_CAP);
  }

  function maxPanelWidth(panelId) {
    const root = document.getElementById("viewer-root");
    if (!root) return PANEL_MIN;
    const rootWidth = root.getBoundingClientRect().width;
    const otherId = panelId === "left-panel" ? "mv-extension-panels" : "left-panel";
    const splitters = document.querySelectorAll("#viewer-root > .panel-splitter").length * 8;
    const available = rootWidth - panelWidth(otherId) - SCENE_MIN - splitters;
    const ratioCap = rootWidth * PANEL_FRACTION_CAP;
    if (available < PANEL_MIN) {
      return Math.max(160, available);
    }
    return Math.max(160, Math.min(ratioCap, available));
  }

  function assignPanelWidth(panel, width) {
    panel.style.width = Math.round(width) + "px";
    panel.style.maxWidth = "none";
    panel.style.flex = "0 0 auto";
  }

  // Dash replaces #scene-tabs when scenes change. The scrollable node is
  // that element when it is the tab container (Dash 4); Dash 2 put the id
  // on the outer shell and overflow lives on the inner .tab-container.
  function liveTabScroller() {
    const strip = document.getElementById("scene-tabs");
    if (!strip) return null;
    if (strip.classList.contains("tab-container")) return strip;
    return strip.querySelector(".tab-container") || strip;
  }

  let boundTabScroller = null;

  function syncTabScrollers() {
    const strip = liveTabScroller();
    const prev = document.getElementById("scene-tab-scroll-prev");
    const next = document.getElementById("scene-tab-scroll-next");
    if (!strip || !prev || !next) return;
    const overflow = strip.scrollWidth > strip.clientWidth + 4;
    const maxScroll = strip.scrollWidth - strip.clientWidth;
    prev.hidden = !overflow || strip.scrollLeft <= 1;
    next.hidden = !overflow || strip.scrollLeft >= maxScroll - 1;
  }

  function bindTabScroller(strip) {
    if (!strip || strip === boundTabScroller) return;
    if (boundTabScroller) boundTabScroller.removeEventListener("scroll", syncTabScrollers);
    boundTabScroller = strip;
    strip.addEventListener("scroll", syncTabScrollers);
  }

  function ensureTabScrollers() {
    const card = document.querySelector(".scene-tabs-card");
    const strip = liveTabScroller();
    if (!card || !strip) return;
    function makeButton(id, label, direction) {
      let button = document.getElementById(id);
      if (!button) {
        button = document.createElement("button");
        button.type = "button";
        button.id = id;
        button.className = "scene-tab-scroll scene-tab-scroll--" + direction;
        button.textContent = label;
        button.setAttribute("aria-label", direction === "prev" ? "Scroll scenes left" : "Scroll scenes right");
        button.addEventListener("click", function () {
          const scroller = liveTabScroller();
          if (!scroller) return;
          const amount = Math.max(120, scroller.clientWidth * 0.7);
          scroller.scrollBy({left: direction === "prev" ? -amount : amount});
        });
      }
      if (button.parentElement !== card) card.appendChild(button);
      return button;
    }
    makeButton("scene-tab-scroll-prev", "‹", "prev");
    makeButton("scene-tab-scroll-next", "›", "next");
    bindTabScroller(strip);
    syncTabScrollers();
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
      ensureTabScrollers();
      resizeScene();
      return;
    }
    const rootWidth = root.getBoundingClientRect().width;
    const splitters = document.querySelectorAll("#viewer-root > .panel-splitter").length * 8;
    const budget = rootWidth - SCENE_MIN - splitters;
    let leftWidth = rootWidth * chosenFraction("left-panel");
    let rightWidth = right ? rootWidth * chosenFraction("mv-extension-panels") : 0;
    const used = leftWidth + rightWidth;
    if (used > budget && used > 0) {
      const scale = Math.max(budget, 160) / used;
      leftWidth *= scale;
      rightWidth *= scale;
    } else if (!right && leftWidth < PANEL_MIN && budget >= PANEL_MIN) {
      leftWidth = PANEL_MIN;
    }
    const cap = Math.max(budget, 160);
    assignPanelWidth(left, clamp(leftWidth, 160, cap));
    if (right) assignPanelWidth(right, clamp(rightWidth, 160, cap));
    ensureTabScrollers();
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
        const next = clamp(width, floor, limit);
        assignPanelWidth(panel, next);
        rememberFraction(panelId, next, rootRect.width);
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
      function onViewportResize() {
        if (resizingScene) {
          refitAfterResize = true;
          return;
        }
        updateCompactLayout();
        fitPanels();
      }
      window.addEventListener("resize", onViewportResize);
      if (window.visualViewport && window.visualViewport.addEventListener) {
        window.visualViewport.addEventListener("resize", onViewportResize);
      }
    }
    updateCompactLayout();
    ensureTabScrollers();
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

  let panelObserver = null;
  let panelObserveScheduled = false;
  function schedulePanelInit() {
    if (panelObserveScheduled) return;
    panelObserveScheduled = true;
    const schedule = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 16); };
    schedule(function () {
      panelObserveScheduled = false;
      attachPanelObserver();
      init();
    });
  }
  function mutationElement(node) {
    if (!node) return null;
    return node.nodeType === 1 ? node : node.parentElement;
  }
  function panelMutationIsStatusChrome(mutation) {
    const el = mutationElement(mutation && mutation.target);
    return !!(el && el.closest && el.closest("#mv-status-bar, #mv-render-status, #mv-status-busy"));
  }
  function attachPanelObserver() {
    const root = document.getElementById("viewer-root") || document.body;
    if (!root || (panelObserver && panelObserver.__root === root)) return;
    if (panelObserver) panelObserver.disconnect();
    // Status-bar and render-pill updates are absolute overlays. Refitting
    // the canvas on those child-list changes loops Plotly relayout through
    // the figure settle path.
    panelObserver = new MutationObserver(function (mutations) {
      if (mutations.every(panelMutationIsStatusChrome)) return;
      schedulePanelInit();
    });
    panelObserver.__root = root;
    panelObserver.observe(root, { childList: true, subtree: true });
  }
  attachPanelObserver();

  window.MatterVisPanelResize = {
    fitPanels: fitPanels,
    resizeScene: resizeScene,
  };
})();
