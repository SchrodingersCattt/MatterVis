/* Keyboard shortcuts for the crystal viewer.
 *
 * Maps single-letter keys to common Phase 4 actions, using the most
 * recently hovered atom / bond / polyhedron (cached by
 * right_click_menu.js into a window-level variable) as the seed.
 *
 *   ?      Toggle the keyboard-help overlay
 *   r      Append a 2x2x2 repeat transform (or replace existing repeat)
 *   R      Clear the repeat transform (back to home cell)
 *   g      Grow by 1 bond hop from the hovered atom (or "all" if none)
 *   G      Grow by radius 4 angstrom from the hovered atom
 *   a      Add hovered atom to the current selection
 *   m      Select hovered atom's fragment
 *   e      Select all atoms of hovered atom's element
 *   f      Focus camera on the current selection
 *   h      Hide the hovered atom / bond / polyhedron
 *   c      Open the colour picker for the hovered target
 *   p      Promote the hovered atom to an atom-group rule
 *
 * Actions write into the same `dcc.Store(id="rightclick-target")` that
 * the contextmenu uses, with an extra ``action`` field. The Dash
 * callback dispatches on ``action`` and (optionally) the ``payload``.
 */
(function () {
  // Mirror the ``lastHovered`` cache from right_click_menu.js. Both
  // scripts intentionally re-cache from the same plotly_hover stream
  // so they don't depend on each other's load order.
  let lastHovered = null;

  function pushAction(action, extra) {
    if (!window.dash_clientside || typeof window.dash_clientside.set_props !== "function") {
      return false;
    }
    try {
      window.dash_clientside.set_props("rightclick-target", {
        data: Object.assign(
          {
            kind: lastHovered ? lastHovered.kind : "_global",
            payload: lastHovered,
            action: action,
            ts: Date.now(),
          },
          extra || {}
        ),
      });
      return true;
    } catch (err) {
      return false;
    }
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

  function bindCrystalGraph() {
    const container = document.getElementById("crystal-graph");
    if (!container || container.dataset.kbdBound === "1") return;
    const inner = container.querySelector(".js-plotly-plot");
    if (!inner || typeof inner.on !== "function") {
      window.requestAnimationFrame(bindCrystalGraph);
      return;
    }
    container.dataset.kbdBound = "1";
    inner.on("plotly_hover", function (eventData) {
      if (!eventData || !eventData.points || !eventData.points.length) return;
      lastHovered = decodeCustomdata(eventData.points[0].customdata);
    });
    inner.on("plotly_unhover", function () {
      lastHovered = null;
    });
  }

  function isTypingTarget(el) {
    if (!el) return false;
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    if (el.isContentEditable) return true;
    return false;
  }

  function toggleHelp(force) {
    const help = document.getElementById("kbd-help");
    if (!help) return;
    const hidden = help.classList.contains("kbd-help--hidden");
    if (force === true || (force === undefined && hidden)) {
      help.classList.remove("kbd-help--hidden");
    } else {
      help.classList.add("kbd-help--hidden");
    }
  }

  document.addEventListener("keydown", function (event) {
    if (isTypingTarget(event.target)) return;
    const key = event.key;
    if ((event.ctrlKey || event.metaKey) && !event.altKey) {
      if (key.toLowerCase() === "a") {
        if (pushAction("select_all")) event.preventDefault();
        return;
      }
      if (key.toLowerCase() === "i") {
        if (pushAction("select_invert")) event.preventDefault();
        return;
      }
      return;
    }
    if (event.altKey) return;
    if (key === "?" || (event.shiftKey && key === "/")) {
      event.preventDefault();
      toggleHelp();
      return;
    }
    if (key === "Escape") {
      toggleHelp(false);
      pushAction("select_clear");
      return;
    }
    let dispatched = false;
    switch (key) {
      case "r":
        dispatched = pushAction("supercell_2x");
        break;
      case "R":
        dispatched = pushAction("supercell_clear");
        break;
      case "g":
        dispatched = pushAction("grow_bonds", { hops: 1 });
        break;
      case "G":
        dispatched = pushAction("grow_radius", { radius: 4.0 });
        break;
      case "h":
        dispatched = pushAction("hide");
        break;
      case "c":
        dispatched = pushAction("colour_picker");
        break;
      case "p":
        dispatched = pushAction("promote_to_group");
        break;
      case "a":
        dispatched = pushAction("select_add");
        break;
      case "m":
        dispatched = pushAction("select_fragment");
        break;
      case "e":
        dispatched = pushAction("select_element");
        break;
      case "f":
        dispatched = pushAction("selection_focus_camera");
        break;
      default:
        return;
    }
    if (dispatched) event.preventDefault();
  });

  function init() {
    bindCrystalGraph();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
  const observer = new MutationObserver(function () {
    bindCrystalGraph();
  });
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
})();
