/* MatterVis — unified frontend module.
 *
 * Consolidates shared utilities, a single MutationObserver, and
 * per-feature handlers that previously lived in 9 separate IIFE files
 * each with their own observer + duplicate utilities.
 */
(function () {
  // ── Shared utilities (was duplicated in 3+ files) ──────────────
  function graphDiv() {
    const root = document.getElementById("crystal-graph");
    return root ? root.querySelector(".js-plotly-plot") : null;
  }

  function decodeCustomdata(cd) {
    if (!Array.isArray(cd) || cd.length === 0) return null;
    const kind = cd[0];
    if (kind === "atom") return { kind:"atom", index:cd[1], label:cd[2], element:cd[3], is_minor:!!cd[4], fragment_label:cd[5]||"" };
    if (kind === "polyhedron") return { kind:"polyhedron", spec_id:cd[1], fragment_label:cd[2]||"", is_anchor:!!cd[3] };
    if (kind === "bond") return { kind:"bond", label_pair:cd[1], element_pair:cd[2], is_minor:!!cd[3] };
    return null;
  }

  function setDashStore(id, data) {
    if (!window.dash_clientside || typeof window.dash_clientside.set_props !== "function") return false;
    try { window.dash_clientside.set_props(id, {data:data}); return true; }
    catch (_) { return false; }
  }

  // ── Interaction state (graph_interaction_store.js) ─────────────
  let interactionActive = false, settleTimer = null;
  function setInteraction(active) { setDashStore("graph-interaction-store", {active:!!active, ts:Date.now()}); }
  function markActive() {
    if (settleTimer) { clearTimeout(settleTimer); settleTimer = null; }
    if (!interactionActive) { interactionActive = true; setInteraction(true); }
  }
  function markInactiveSoon() {
    if (settleTimer) clearTimeout(settleTimer);
    settleTimer = setTimeout(function () {
      settleTimer = null;
      if (interactionActive) { interactionActive = false; setInteraction(false); }
    }, 220);
  }
  function inGraph(target) { return !!(target && target.closest && target.closest("#crystal-graph")); }

  // ── Right-click menu (right_click_menu.js) ─────────────────────
  let lastHovered = null, lastClient = {x:0,y:0};
  function pushTarget(payload) {
    if (setDashStore("rightclick-target", payload)) return;
    const input = document.getElementById("rightclick-target-fallback");
    if (input) { input.value = JSON.stringify(payload); input.dispatchEvent(new Event("input",{bubbles:true})); }
  }
  function bindRightClick() {
    const container = document.getElementById("crystal-graph");
    if (!container || container.dataset.rcmBound === "1") return;
    const inner = container.querySelector(".js-plotly-plot");
    if (!inner || typeof inner.on !== "function") { requestAnimationFrame(bindRightClick); return; }
    container.dataset.rcmBound = "1";
    inner.on("plotly_hover", function (ed) { if (ed && ed.points && ed.points.length) lastHovered = decodeCustomdata(ed.points[0].customdata); });
    inner.on("plotly_unhover", function () { lastHovered = null; });
    container.addEventListener("mousemove", function (e) { lastClient = {x:e.clientX, y:e.clientY}; }, {passive:true});
    container.addEventListener("contextmenu", function (e) {
      if (!lastHovered) return;
      e.preventDefault();
      pushTarget({kind:lastHovered.kind, payload:lastHovered, x:lastClient.x, y:lastClient.y, ts:Date.now()});
    });
    document.addEventListener("click", function (e) {
      const menu = document.getElementById("rightclick-menu");
      if (!menu || menu.classList.contains("rightclick-menu--hidden")) return;
      if (menu.contains(e.target)) return;
      pushTarget({kind:"_close", ts:Date.now()});
    }, true);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      const menu = document.getElementById("rightclick-menu");
      if (!menu || menu.classList.contains("rightclick-menu--hidden")) return;
      pushTarget({kind:"_close", ts:Date.now()});
    });
  }

  // ── Keyboard shortcuts (keyboard_shortcuts.js) ──────────────────
  function pushAction(action, extra) {
    setDashStore("rightclick-target", Object.assign(
      {kind:lastHovered?lastHovered.kind:"_global", payload:lastHovered, action:action, ts:Date.now()},
      extra||{}
    ));
  }
  function isTypingTarget(el) {
    if (!el) return false;
    const tag = (el.tagName||"").toLowerCase();
    return tag==="input"||tag==="textarea"||tag==="select"||el.isContentEditable;
  }
  function toggleHelp(force) {
    const help = document.getElementById("kbd-help");
    if (!help) return;
    if (force===true||(force===undefined&&help.classList.contains("kbd-help--hidden"))) help.classList.remove("kbd-help--hidden");
    else help.classList.add("kbd-help--hidden");
  }
  document.addEventListener("keydown", function (e) {
    if (isTypingTarget(e.target)) return;
    const key = e.key;
    if ((e.ctrlKey||e.metaKey) && !e.altKey) {
      if (key.toLowerCase()==="a") { pushAction("select_all"); e.preventDefault(); return; }
      if (key.toLowerCase()==="i") { pushAction("select_invert"); e.preventDefault(); return; }
      return;
    }
    if (e.altKey) return;
    if (key==="?"||(e.shiftKey&&key==="/")) { e.preventDefault(); toggleHelp(); return; }
    if (key==="Escape") { toggleHelp(false); pushAction("select_clear"); return; }
    const map = {r:"supercell_2x", R:"supercell_clear", g:"grow_bonds", G:"grow_radius", h:"hide", c:"colour_picker", p:"promote_to_group", a:"select_add", m:"select_fragment", e:"select_element", f:"selection_focus_camera"};
    const extra = {g:{hops:1}, G:{radius:4.0}};
    const action = map[key];
    if (!action) return;
    if (pushAction(action, extra[key]||{})) e.preventDefault();
  });

  // ── Box selection (box_select.js) ───────────────────────────────
  function bindBoxSelect() {
    const container = document.getElementById("crystal-graph");
    if (!container || container.dataset.boxSelectBound==="1") return;
    container.dataset.boxSelectBound = "1";
    let start=null, overlay=null;
    function ensureOverlay() {
      if (overlay) return overlay;
      overlay = document.createElement("div");
      overlay.className = "mv-box-select";
      overlay.style.cssText = "position:fixed;border:1px solid #FFD24A;background:rgba(255,210,74,0.18);pointer-events:none;z-index:9999";
      document.body.appendChild(overlay);
      return overlay;
    }
    function updateOverlay(e) {
      if (!start) return;
      const el = ensureOverlay();
      el.style.left = Math.min(start.clientX,e.clientX)+"px";
      el.style.top = Math.min(start.clientY,e.clientY)+"px";
      el.style.width = Math.abs(e.clientX-start.clientX)+"px";
      el.style.height = Math.abs(e.clientY-start.clientY)+"px";
    }
    container.addEventListener("mousedown", function (e) {
      if (!e.shiftKey||e.button!==0) return;
      start = {clientX:e.clientX, clientY:e.clientY};
      updateOverlay(e);
      e.preventDefault(); e.stopPropagation();
    }, true);
    document.addEventListener("mousemove", function (e) { if (start) { updateOverlay(e); e.preventDefault(); } }, true);
    document.addEventListener("mouseup", function (e) {
      if (!start) return;
      const gr = container.getBoundingClientRect();
      const rect = [start.clientX-gr.left, start.clientY-gr.top, e.clientX-gr.left, e.clientY-gr.top];
      if (overlay) { overlay.remove(); overlay = null; }
      start = null;
      if (Math.abs(rect[2]-rect[0])>=4 && Math.abs(rect[3]-rect[1])>=4) {
        setDashStore("rightclick-target", {kind:"_global", action:"select_box", rect_pixels:rect, viewport_size:[gr.width,gr.height], additive:!!e.shiftKey, ts:Date.now()});
      }
      e.preventDefault(); e.stopPropagation();
    }, true);
  }

  // ── Disorder hover (disorder_hover.js) ──────────────────────────
  function bindDisorderHover() {
    document.querySelectorAll(".disorder-row").forEach(function (row) {
      if (row.dataset.boundDisorderHover==="1") return;
      row.dataset.boundDisorderHover = "1";
      row.addEventListener("mouseenter", function () { setDashStore("disorder-hover-id", row.dataset.replicaId||null); });
      row.addEventListener("mouseleave", function () { setDashStore("disorder-hover-id", null); });
    });
  }

  // ── WS figure fast lane (ws_figure.js) ──────────────────────────
  (function connectWS() {
    if (window.MATTERVIS_WS_FIGURE === false || !window.WebSocket || !window.Plotly) return;
    let lastFigureSeq = 0, pendingPush = null;
    function currentSceneId() {
      const node = document.getElementById("fast-view-metadata");
      const text = node ? (node.textContent||"").trim() : "";
      if (!text) return null;
      try { const m = JSON.parse(text); return m && m.scene_id ? String(m.scene_id) : null; } catch(_) { return null; }
    }
    function has3DScene(fig) {
      const lay = fig && fig.layout;
      const data = (fig && Array.isArray(fig.data)) ? fig.data : [];
      if (!lay || typeof lay!=="object"||!lay.scene||typeof lay.scene!=="object") return false;
      return data.some(function(t){var ty=String((t&&t.type)||"").toLowerCase();return ty==="mesh3d"||ty==="scatter3d"||ty==="cone"||!!(t&&t.z);});
    }
    function applyPush(data, layout) {
      var gd = graphDiv(); if (!gd||!window.Plotly) return;
      var lay = layout||{};
      try { var lc = gd._fullLayout&&gd._fullLayout.scene&&gd._fullLayout.scene.camera; if (lc) lay.scene=Object.assign({},lay.scene||{},{camera:JSON.parse(JSON.stringify(lc))}); } catch(_){}
      window.Plotly.react(gd, data||[], lay);
    }
    function flushPending() { if (pendingPush) { var p=pendingPush; pendingPush=null; applyPush(p.data,p.layout); } }
    var proto = window.location.protocol==="https:"?"wss:":"ws:";
    var ws = new WebSocket(proto+"//"+window.location.host+"/api/v2/ws");
    ws.addEventListener("open",function(){ws.send(JSON.stringify({type:"subscribe_figure",enabled:true}));});
    ws.addEventListener("message",function(e){
      var p; try { p=JSON.parse(e.data||"{}"); } catch(_){return;}
      if (!p.figure||p.figure._mattervis_pending||!has3DScene(p.figure)) return;
      var cur=currentSceneId(); if (cur&&p.scene_id&&String(p.scene_id)!==cur) return;
      var seq=Number(p.figure_seq||p.figure_version||0); if (seq&&seq<=lastFigureSeq) return;
      if (seq) lastFigureSeq=seq;
      if (interactionActive) { pendingPush={data:p.figure.data||[],layout:p.figure.layout||{},seq:seq}; return; }
      applyPush(p.figure.data||[], p.figure.layout||{});
    });
    ws.addEventListener("close",function(){setTimeout(connectWS,1500);});
  })();

  // ── Single MutationObserver for all re-bind needs ───────────────
  function rebindAll() {
    bindRightClick();
    bindBoxSelect();
    bindDisorderHover();
  }
  new MutationObserver(rebindAll).observe(document.documentElement, {childList:true, subtree:true});

  // ── Pointer/wheel interaction tracking ──────────────────────────
  document.addEventListener("pointerdown", function(e) { if (inGraph(e.target)) markActive(); }, true);
  document.addEventListener("pointerup", function() { if (interactionActive) markInactiveSoon(); });
  document.addEventListener("pointercancel", function() { if (interactionActive) markInactiveSoon(); });
  document.addEventListener("wheel", function(e) { if (inGraph(e.target)) { markActive(); markInactiveSoon(); } }, {passive:true, capture:true});

  // ── Init ────────────────────────────────────────────────────────
  function init() { rebindAll(); }
  if (document.readyState==="loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
