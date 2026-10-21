/* Canvas status bar: show a spinner for user-driven Dash work that takes
   long enough to notice (figure builds, scope changes, exports). Background
   polls must not flash the indicator. */
(function () {
  var POLLS = ["agent-state-poll.", "perf-log-poll.", "status-dismiss-timer."];

  function busyNode() {
    return document.getElementById("mv-status-busy");
  }

  function setBusy(on, message) {
    var node = busyNode();
    if (!node) return;
    var text = node.querySelector(".mv-status-busy-text");
    if (on) {
      node.classList.remove("is-hidden");
      if (text && message) text.textContent = message;
      return;
    }
    if (node.getAttribute("data-hold") === "render") return;
    node.classList.add("is-hidden");
  }

  function requestBody(init) {
    if (!init || init.body == null) return "";
    var body = init.body;
    if (typeof body === "string") return body;
    return "";
  }

  function isBackgroundPoll(bodyText) {
    if (!bodyText) return false;
    var changed = [];
    try {
      changed = (JSON.parse(bodyText).changedPropIds) || [];
    } catch (error) {
      return false;
    }
    if (!changed.length) return false;
    return changed.every(function (prop) {
      return POLLS.some(function (prefix) { return String(prop).indexOf(prefix) === 0; });
    });
  }

  function syncFromRenderPill() {
    var pill = document.getElementById("mv-render-status");
    var node = busyNode();
    if (!pill || !node) return;
    var rendering = !pill.hidden && pill.getAttribute("data-level") === "busy";
    if (rendering) {
      node.setAttribute("data-hold", "render");
      setBusy(true, pill.textContent || "Rendering structure…");
      return;
    }
    if (node.getAttribute("data-hold") === "render") {
      node.removeAttribute("data-hold");
      node.classList.add("is-hidden");
    }
  }

  var rootObserver = null;

  function watchRenderPill() {
    var pill = document.getElementById("mv-render-status");
    if (!pill || pill.dataset.statusBound === "1" || typeof MutationObserver !== "function") return;
    pill.dataset.statusBound = "1";
    new MutationObserver(syncFromRenderPill).observe(pill, {
      attributes: true,
      attributeFilter: ["hidden", "data-level"],
      childList: true,
      characterData: true,
      subtree: true,
    });
    if (rootObserver) {
      rootObserver.disconnect();
      rootObserver = null;
    }
  }

  function noteUserRequest(bodyText) {
    if (isBackgroundPoll(bodyText)) return null;
    if (bodyText.indexOf("_dash-update-component") === -1 && bodyText.indexOf("changedPropIds") === -1) {
      return null;
    }
    setBusy(true, "Working…");
    return function release() {
      window.setTimeout(syncFromRenderPill, 40);
      window.setTimeout(function () {
        var node = busyNode();
        if (node && node.getAttribute("data-hold") !== "render") node.classList.add("is-hidden");
      }, 450);
    };
  }

  if (typeof window.fetch === "function" && !window.fetch.__mvStatusBar) {
    var originalFetch = window.fetch;
    var wrapped = function (input, init) {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var bodyText = requestBody(init);
      var release = null;
      if (String(url).indexOf("_dash-update-component") !== -1) {
        release = noteUserRequest(bodyText);
      }
      var pending = originalFetch.apply(this, arguments);
      if (!release || !pending || typeof pending.finally !== "function") return pending;
      return pending.finally(release);
    };
    wrapped.__mvStatusBar = true;
    window.fetch = wrapped;
  }

  if (typeof MutationObserver === "function") {
    rootObserver = new MutationObserver(function () { watchRenderPill(); });
    rootObserver.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watchRenderPill);
  } else {
    watchRenderPill();
  }
})();
