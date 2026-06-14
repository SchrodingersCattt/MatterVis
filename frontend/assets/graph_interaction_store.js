(function () {
  function inGraph(target) {
    return Boolean(target && target.closest && target.closest("#crystal-graph"));
  }

  function setInteraction(active) {
    if (!window.dash_clientside || typeof window.dash_clientside.set_props !== "function") {
      return;
    }
    window.dash_clientside.set_props("graph-interaction-store", {
      active: !!active,
      ts: Date.now(),
    });
  }

  var interactionActive = false;
  var settleTimer = null;

  function markActive() {
    if (settleTimer) {
      window.clearTimeout(settleTimer);
      settleTimer = null;
    }
    if (!interactionActive) {
      interactionActive = true;
      setInteraction(true);
    }
  }

  function markInactiveSoon() {
    if (settleTimer) {
      window.clearTimeout(settleTimer);
    }
    settleTimer = window.setTimeout(function () {
      settleTimer = null;
      if (interactionActive) {
        interactionActive = false;
        setInteraction(false);
      }
    }, 220);
  }

  document.addEventListener(
    "pointerdown",
    function (event) {
      if (inGraph(event.target)) {
        markActive();
      }
    },
    true
  );

  document.addEventListener("pointerup", function () {
    if (interactionActive) {
      markInactiveSoon();
    }
  });

  document.addEventListener("pointercancel", function () {
    if (interactionActive) {
      markInactiveSoon();
    }
  });

  document.addEventListener(
    "wheel",
    function (event) {
      if (!inGraph(event.target)) {
        return;
      }
      markActive();
      markInactiveSoon();
    },
    { passive: true, capture: true }
  );

  window.addEventListener("blur", function () {
    if (interactionActive) {
      interactionActive = false;
      setInteraction(false);
    }
  });
})();
