/* Right-click menu for the Plotly crystal viewer.
 *
 * Wires native `contextmenu` on `#crystal-graph` to a Dash
 * `dcc.Store(id="rightclick-target")`. The Dash side reads the store
 * (`{kind, payload, x, y, ts}`) and renders a `#rightclick-menu`
 * popover with action buttons. Each button has a stable id matching a
 * Dash callback (Set color, Hide, Grow from, Analyze coordination,
 * Promote to group rule).
 *
 * Customdata schema (kept in sync with `crystal_viewer.renderer`):
 *   - atom:        ["atom",        idx, label, elem, is_minor, frag_label]
 *   - polyhedron:  ["polyhedron",  spec_id, fragment_label, is_anchor]
 *   - bond:        ["bond",        label_pair, elem_pair, is_minor]
 *
 * The script intentionally has no hard dependency on Dash internals
 * other than `window.dash_clientside.set_props`, which is available in
 * Dash >= 2.13. If `set_props` is unavailable (older Dash, or before
 * the runtime has booted), we fall back to a hidden input + change
 * event.
 */
(function () {
  // --- helpers ---------------------------------------------------------
  function pushTarget(payload) {
    if (
      window.dash_clientside &&
      typeof window.dash_clientside.set_props === "function"
    ) {
      try {
        window.dash_clientside.set_props("rightclick-target", { data: payload });
        return true;
      } catch (err) {
        // fall through to the hidden-input fallback
      }
    }
    const input = document.getElementById("rightclick-target-fallback");
    if (input) {
      input.value = JSON.stringify(payload);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    }
    return false;
  }

  function decodeCustomdata(cd) {
    if (!Array.isArray(cd) || cd.length === 0) return null;
    const kind = cd[0];
    if (kind === "atom") {
      return {
        kind: "atom",
        index: cd[1],
        label: cd[2],
        element: cd[3],
        is_minor: !!cd[4],
        fragment_label: cd[5] || "",
      };
    }
    if (kind === "polyhedron") {
      return {
        kind: "polyhedron",
        spec_id: cd[1],
        fragment_label: cd[2] || "",
        is_anchor: !!cd[3],
      };
    }
    if (kind === "bond") {
      return {
        kind: "bond",
        label_pair: cd[1],
        element_pair: cd[2],
        is_minor: !!cd[3],
      };
    }
    return null;
  }

  // --- main wiring -----------------------------------------------------
  function bindCrystalGraph() {
    const container = document.getElementById("crystal-graph");
    if (!container || container.dataset.rcmBound === "1") return;
    // Plotly's React wrapper lazily mounts the inner SVG/canvas under
    // the container. Wait for it.
    const inner = container.querySelector(".js-plotly-plot");
    if (!inner || typeof inner.on !== "function") {
      // Try again on the next animation frame -- Plotly will be ready
      // after Dash hydrates the figure.
      window.requestAnimationFrame(bindCrystalGraph);
      return;
    }
    container.dataset.rcmBound = "1";

    // Cache the most recently hovered point so we can identify the
    // right-clicked element. Plotly does not fire a contextmenu event
    // with the picked point, only the underlying DOM event, so we have
    // to remember the last hover.
    let lastHovered = null;
    let lastClient = { x: 0, y: 0 };

    inner.on("plotly_hover", function (eventData) {
      if (!eventData || !eventData.points || !eventData.points.length) return;
      const pt = eventData.points[0];
      lastHovered = decodeCustomdata(pt.customdata);
    });
    inner.on("plotly_unhover", function () {
      lastHovered = null;
    });

    container.addEventListener(
      "mousemove",
      function (event) {
        lastClient = { x: event.clientX, y: event.clientY };
      },
      { passive: true }
    );

    container.addEventListener("contextmenu", function (event) {
      // Only open our menu when the user landed on a known target.
      // Otherwise let the browser show its default menu (the user
      // probably wants "save image as" or developer tools).
      if (!lastHovered) return;
      event.preventDefault();
      pushTarget({
        kind: lastHovered.kind,
        payload: lastHovered,
        x: lastClient.x,
        y: lastClient.y,
        ts: Date.now(),
      });
    });

    // Close the popover on a left click anywhere outside it.
    document.addEventListener(
      "click",
      function (event) {
        const menu = document.getElementById("rightclick-menu");
        if (!menu || menu.classList.contains("rightclick-menu--hidden")) return;
        if (menu.contains(event.target)) return;
        pushTarget({ kind: "_close", ts: Date.now() });
      },
      true
    );

    // Esc key closes the popover too.
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      const menu = document.getElementById("rightclick-menu");
      if (!menu || menu.classList.contains("rightclick-menu--hidden")) return;
      pushTarget({ kind: "_close", ts: Date.now() });
    });
  }

  function init() {
    bindCrystalGraph();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Dash re-renders the figure root from time to time (figure prop
  // updates, scene-tab switches), so re-bind whenever the DOM
  // changes.
  const observer = new MutationObserver(function () {
    bindCrystalGraph();
  });
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
})();
