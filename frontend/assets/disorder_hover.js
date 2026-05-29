(function () {
  function setHover(replicaId) {
    if (!window.dash_clientside || typeof window.dash_clientside.set_props !== "function") {
      return;
    }
    window.dash_clientside.set_props("disorder-hover-id", { data: replicaId || null });
  }

  function bindRows() {
    document.querySelectorAll(".disorder-row").forEach(function (row) {
      if (row.dataset.boundDisorderHover === "1") {
        return;
      }
      row.dataset.boundDisorderHover = "1";
      row.addEventListener("mouseenter", function () {
        setHover(row.dataset.replicaId || null);
      });
      row.addEventListener("mouseleave", function () {
        setHover(null);
      });
    });
  }

  function init() {
    bindRows();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  const observer = new MutationObserver(function () {
    bindRows();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
