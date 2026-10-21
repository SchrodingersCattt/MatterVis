/* The distance histogram is mounted inside a hidden Analysis tab. Plotly
   sizes it at 0×0 and leaves a blank chart until something resizes that
   graph. The scene resizer only watches the structure canvas. */
(function () {
  function histogramPlot() {
    var graph = document.getElementById("topology-histogram");
    if (!graph || !window.Plotly || !window.Plotly.Plots) return null;
    var plot = graph.classList.contains("js-plotly-plot")
      ? graph
      : graph.querySelector(".js-plotly-plot");
    return plot || null;
  }

  function resizeHistogram() {
    var plot = histogramPlot();
    if (!plot || typeof window.Plotly.Plots.resize !== "function") return;
    var rect = plot.getBoundingClientRect();
    if (rect.width < 40 || rect.height < 40) return;
    try {
      window.Plotly.Plots.resize(plot);
    } catch (error) {
      /* A hidden or half-built graph can refuse a resize; the next tab
         open tries again. */
    }
  }

  function scheduleResize() {
    var run = function () { resizeHistogram(); };
    if (window.requestAnimationFrame) {
      window.requestAnimationFrame(function () { window.requestAnimationFrame(run); });
    } else {
      window.setTimeout(run, 32);
    }
  }

  var rootObserver = null;

  function watch() {
    var pane = document.getElementById("analysis-panel-content");
    if (!pane || pane.dataset.histogramBound === "1") return;
    pane.dataset.histogramBound = "1";
    if (!pane.classList.contains("analysis-tab-content--hidden")) scheduleResize();
    if (typeof MutationObserver === "function") {
      new MutationObserver(function () {
        if (!pane.classList.contains("analysis-tab-content--hidden")) scheduleResize();
      }).observe(pane, { attributes: true, attributeFilter: ["class"] });
    }
    if (rootObserver) {
      rootObserver.disconnect();
      rootObserver = null;
    }
  }

  if (typeof MutationObserver === "function") {
    rootObserver = new MutationObserver(function () { watch(); });
    rootObserver.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watch);
  } else {
    watch();
  }
})();
