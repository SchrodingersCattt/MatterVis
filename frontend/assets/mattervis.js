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
    if (kind === "atom") return { kind:"atom", index:cd[1], label:cd[2], element:cd[3], is_minor:!!cd[4], fragment_label:cd[5]||"", id:cd[6]||("atom:index:"+cd[1]), source_index:cd[7], image_shift:cd[8], disorder_group:cd[9], part:cd[10]||"" };
    if (kind === "polyhedron") return { kind:"polyhedron", spec_id:cd[1], fragment_label:cd[2]||"", is_anchor:!!cd[3], id:cd[4]||("polyhedron:"+cd[1]+":"+cd[2]), source_index:cd[5], image_shift:cd[6] };
    if (kind === "bond") return { kind:"bond", label_pair:cd[1], element_pair:cd[2], is_minor:!!cd[3], id:cd[4]||("bond:"+cd[1]), endpoint_ids:[cd[5],cd[6]] };
    if (kind === "facet") return { kind:"facet", id:String(cd[1]||""), miller:cd[2], parent_id:"morphology:bfdh-morphology" };
    return null;
  }

  // Mesh3d has used both pointNumber and pointIndex in Plotly event payloads
  // across releases.  Some versions also expose the underlying cell as
  // faceNumber/faceIndex.  Always prefer an explicit face ordinal and never
  // let null coerce to zero: a zero fallback would select the first facet for
  // clicks whose event did not carry an ordinal at all.
  function meshFaceOrdinal(point) {
    if (!point || typeof point !== "object") return -1;
    const keys = ["faceNumber", "faceIndex", "pointNumber", "pointIndex"];
    for (let i = 0; i < keys.length; i++) {
      const raw = point[keys[i]];
      if (raw === undefined || raw === null || raw === "") continue;
      const value = Number(raw);
      if (Number.isInteger(value) && value >= 0) return value;
    }
    return -1;
  }

  function faceIdAt(meta, point) {
    const faceIds = meta && Array.isArray(meta.face_ids) ? meta.face_ids : [];
    const vertexIds = meta && Array.isArray(meta.vertex_face_ids) ? meta.vertex_face_ids : [];
    if (!point || typeof point !== "object") return null;
    // Plotly's faceNumber/faceIndex are triangle ordinals.  In releases
    // exposing only pointNumber/pointIndex, those values are vertex ordinals
    // and must use the explicit per-vertex map emitted by MatterVis.
    const hasFaceOrdinal = point.faceNumber !== undefined && point.faceNumber !== null && point.faceNumber !== ""
      || point.faceIndex !== undefined && point.faceIndex !== null && point.faceIndex !== "";
    const ordinal = meshFaceOrdinal(point);
    const ids = hasFaceOrdinal || !vertexIds.length ? faceIds : vertexIds;
    return ordinal >= 0 && ordinal < ids.length && ids[ordinal]
      ? String(ids[ordinal])
      : null;
  }

  function setDashStore(id, data) {
    if (!window.dash_clientside || typeof window.dash_clientside.set_props !== "function") return false;
    try { window.dash_clientside.set_props(id, {data:data}); return true; }
    catch (_) { return false; }
  }

  // ── Interaction state (graph_interaction_store.js) ─────────────
  const INTERACTION_SETTLE_MS = 40;
  let interactionActive = false, settleTimer = null, gestureGeneration = 0;
  let interactionSettled = Promise.resolve(), endInteraction = null;
  let pendingFigurePush = null;
  const pendingFigureByScene = Object.create(null);
  const latestFigureSeqByScene = Object.create(null);
  let sceneTabIntentId = null, sceneTabIntentAt = 0;
  let sceneTabIntentSource = null;
  const pendingFigureRetryTimers = Object.create(null);
  let renderServerEpoch = null;
  const expectedRenderRevisionByScene = Object.create(null);
  const expectedCameraRevisionByScene = Object.create(null);
  const expectedSceneGenerationByScene = Object.create(null);
  const MAX_RENDER_QUEUE = 32;

  /* Plotly work is asynchronous.  Keep one bounded FIFO per graph so a
   * burst of camera commands or duplicate Dash/WS frames cannot grow an
   * unbounded promise chain.  Dropped entries are already superseded by a
   * newer frame; resolving them lets their callers recover and continue. */
  function enqueueRender(gd, task, options) {
    if (!gd || typeof task !== "function") return Promise.resolve(gd);
    let queue = gd.__mvRenderQueueState;
    if (!queue) {
      queue = {items: [], running: false, last: Promise.resolve(gd)};
      gd.__mvRenderQueueState = queue;
    }
    if (queue.items.length >= MAX_RENDER_QUEUE) {
      const dropped = queue.items.splice(0);
      dropped.forEach(function (item) {
        if (options && options.rejectDropped) item.reject(new Error("render queue overflow"));
        else item.resolve(gd);
      });
    }
    const result = new Promise(function (resolve, reject) {
      queue.items.push({task: task, resolve: resolve, reject: reject, isCurrent: options && options.isCurrent});
    });
    queue.last = result.catch(function () { return gd; });
    gd.__mvRenderQueue = queue.last;
    function pump() {
      if (queue.running || !queue.items.length) return;
      const item = queue.items.shift();
      if (item.isCurrent && !item.isCurrent()) {
        item.reject(new Error("stale render command"));
        pump();
        return;
      }
      queue.running = true;
      Promise.resolve().then(item.task).then(item.resolve, item.reject).then(function () {
        queue.running = false;
        pump();
      }, function () {
        queue.running = false;
        pump();
      });
    }
    pump();
    return result;
  }
  window.MatterVisRenderQueue = window.MatterVisRenderQueue || {
    enqueue: enqueueRender,
    maxPending: MAX_RENDER_QUEUE,
  };
  function figurePushIsCurrent(p) {
    return !!(p && typeof p.isCurrent === "function" && p.isCurrent());
  }
  function sceneIdFromTabNode(node) {
    if (!node || !node.id) return null;
    // Compact scene chips use a JSON id on their select button, while
    // dcc.Tab wrappers use the scene-tab-* convention.
    if (node.id[0] === "{" || node.id[0] === "[") {
      try {
        const parsed = JSON.parse(node.id);
        if (parsed && parsed.scene_id) return String(parsed.scene_id);
      } catch (_) {}
    }
    if (node.id.indexOf("scene-tab-") !== 0) return null;
    if (node.id.indexOf("scene-tab-close-") === 0) return null;
    if (node.id === "scene-tabs" || node.id === "scene-tab-close-row") return null;
    // Labels are nested inside the dcc.Tab and carry their own stable id.
    if (node.id.indexOf("scene-tab-label-") === 0) {
      return node.id.slice("scene-tab-label-".length);
    }
    return node.id.slice("scene-tab-".length);
  }
  function noteSceneTabIntent(target) {
    const node = target && target.closest ? target.closest("[id^='scene-tab-']") : null;
    const sceneId = sceneIdFromTabNode(node);
    if (!sceneId) return;
    sceneTabIntentId = sceneId;
    sceneTabIntentAt = Date.now();
    sceneTabIntentSource = "pointer";
    if (typeof window.MatterVisInvalidateSemanticHover === "function") {
      window.MatterVisInvalidateSemanticHover();
    }
    resyncScene(sceneId);
  }
  function resyncScene(sceneId, attempt) {
    if (!window.fetch || !sceneId) return;
    attempt = Number(attempt || 0);
    window.fetch("/api/v2/view-updates?since=0&scene_id=" + encodeURIComponent(String(sceneId)), {credentials: "same-origin"})
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (body) {
        const rows = (body && body.updates || []).filter(function (item) {
          return item && String(item.scene_id || "") === String(sceneId) && (item.figure || item.type === "view_update" || item.type === "render_error");
        });
        const latest = rows.length ? rows[rows.length - 1] : null;
        const pending = !!(body && body.pending);
        if (body && body.terminal) return;
        if (!latest || !window.MatterVisViewUpdates || typeof window.MatterVisViewUpdates.submit !== "function") {
          // Keep retrying while a render is pending, but cap the total wait so
          // a failed worker cannot leave an endless polling loop behind.
          if (attempt < 40 || (pending && attempt < 120)) {
            setTimeout(function () { resyncScene(sceneId, attempt + 1); }, 250);
          } else if (pending && window.console && console.warn) {
            console.warn("MatterVis scene resync timed out", sceneId);
          }
          return;
        }
        // Feed resync frames through the same scene/sequence fence as the
        // live transport.  A rapid tab switch can make the selected scene
        // change between the fetch and submit; calling the typed submitter
        // directly would drop that frame as "stale" with no retry, leaving
        // the newly selected canvas on the previous scene.
        if (typeof window.__mvHandleFigurePayload === "function") {
          window.__mvHandleFigurePayload(latest);
        } else {
          Promise.resolve(window.MatterVisViewUpdates.submit(latest)).catch(function (error) {
            console.error("MatterVis scene resync failed", error);
          });
        }
      }).catch(function () {
        if (attempt < 40) setTimeout(function () { resyncScene(sceneId, attempt + 1); }, 250);
      });
  }
  function sceneTabNodeForId(sceneId) {
    if (sceneId === null || sceneId === undefined || sceneId === "") return null;
    const root = document.getElementById("scene-tabs");
    if (!root || typeof root.querySelectorAll !== "function") return null;
    const wanted = String(sceneId);
    const nodes = root.querySelectorAll("[id^='scene-tab-']");
    for (let index = 0; index < nodes.length; index += 1) {
      const node = nodes[index];
      if (sceneIdFromTabNode(node) === wanted) return node;
    }
    return null;
  }
  function selectedSceneTabId() {
    const root = document.getElementById("scene-tabs");
    if (!root || typeof root.querySelector !== "function") return null;
    // The compact chip is the visible selector.  Hidden dcc.Tab wrappers can
    // retain a stale selected class while Dash applies the chip change; query
    // the chip first so scroll/routing follows the scene the user sees.
    const selected = root.querySelector(".scene-tab-chip--selected .tab-select") ||
      root.querySelector("[aria-selected='true'], .tab--selected, .dash-tab--selected, .rc-tabs-tab-active");
    if (!selected) return null;
    const node = sceneIdFromTabNode(selected)
      ? selected
      : (selected.closest ? selected.closest("[id^='scene-tab-']") : null);
    return sceneIdFromTabNode(node);
  }
  // Keep the last real DOM selection so attribute churn on a tab (for
  // example, a tooltip/class update) cannot repeatedly scroll the active tab.
  // A null selection is transient while Dash swaps tab nodes; retain the last
  // known scene so that clearing and restoring the same selection is a no-op.
  let observedSceneTabId = null;
  let pendingSceneTabScrollId = null;
  let lastScrolledSceneId = null;
  function syncSceneTabSelection(schedule) {
    const current = selectedSceneTabId();
    if (!current) return;
    const changed = observedSceneTabId !== null &&
      String(current) !== String(observedSceneTabId);
    observedSceneTabId = String(current);
    if (changed && pendingSceneTabScrollId !== null &&
        String(current) !== String(pendingSceneTabScrollId)) {
      pendingSceneTabScrollId = null;
    }
    const pendingMatches = pendingSceneTabScrollId !== null &&
      String(current) === String(pendingSceneTabScrollId);
    // A Dash tab can report its old selected class for several turns after
    // the browser has already requested a new scene. Re-fetch that scene's
    // newest frame at the DOM selection barrier so a frame rejected against
    // the old canvas is not lost.
    if (changed) resyncScene(String(current));
    if (schedule && String(current) !== lastScrolledSceneId &&
        (changed || pendingMatches)) {
      scheduleSceneTabScroll(current);
    }
  }
  function scrollSceneTabIntoView(sceneId) {
    const root = document.getElementById("scene-tabs");
    if (!root) return false;
    const hasExplicitSceneId = sceneId !== null && sceneId !== undefined && sceneId !== "";
    let tab = hasExplicitSceneId ? sceneTabNodeForId(sceneId) : null;
    // An upload selects its new scene before Dash paints the corresponding
    // tab node. Never fall back to the old selected tab in that gap: doing so
    // consumes the scroll request and leaves the new tab off-screen forever.
    if (!tab && hasExplicitSceneId) return false;
    if (!tab && typeof root.querySelector === "function") {
      const selected = root.querySelector(
        "[aria-selected='true'], .tab--selected, .dash-tab--selected, .rc-tabs-tab-active, .scene-tab-chip--selected .tab-select"
      );
      tab = selected && (sceneIdFromTabNode(selected)
        ? selected
        : (selected.closest ? selected.closest("[id^='scene-tab-']") : null));
    }
    if (!tab || typeof tab.scrollIntoView !== "function") return false;
    // ``nearest`` keeps the side panel stationary and only moves the tab
    // strip when the selected tab is outside its horizontal viewport.
    tab.scrollIntoView({block: "nearest", inline: "nearest"});
    const scrolledSceneId = sceneIdFromTabNode(tab);
    if (scrolledSceneId) {
      lastScrolledSceneId = String(scrolledSceneId);
      if (String(pendingSceneTabScrollId) === lastScrolledSceneId) {
        pendingSceneTabScrollId = null;
      }
    }
    return true;
  }
  function scheduleSceneTabScroll(sceneId) {
    const run = function () {
      if (sceneId !== null && sceneId !== undefined && sceneId !== "" &&
          String(sceneId) === lastScrolledSceneId) return;
      scrollSceneTabIntoView(sceneId);
    };
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(run);
    } else {
      setTimeout(run, 0);
    }
  }
  function selectedSceneId() {
    const root = document.getElementById("scene-tabs");
    const intentAge = sceneTabIntentId ? Date.now() - sceneTabIntentAt : Infinity;
    const intentLive = sceneTabIntentId && intentAge < 10000;
    // Prefer the visible chip over stale hidden tab classes during a Dash
    // handoff; otherwise a frame for the newly clicked scene is fenced
    // against the previous tab until the next unrelated click.
    const selected = root && (root.querySelector(".scene-tab-chip--selected .tab-select") ||
      root.querySelector("[aria-selected='true'], .tab--selected, .dash-tab--selected, .rc-tabs-tab-active"));
    const node = selected
      ? (sceneIdFromTabNode(selected)
        ? selected
        : (selected.closest ? selected.closest("[id^='scene-tab-']") : null))
      : null;
    const domSceneId = sceneIdFromTabNode(node);
    // A pointer click is an optimistic intent until Dash paints the selected
    // class. Once the DOM reports a different selection (for example after an
    // upload calls set_props), the old intent must be discarded immediately;
    // keeping it for ten seconds routes the new figure to the old tab.
    if (domSceneId) {
      if (sceneTabIntentSource === "programmatic" &&
          String(domSceneId) !== String(sceneTabIntentId)) {
        // Upload completion is an authoritative server selection.  Keep it
        // ahead of the old DOM selection until Dash paints the new tab;
        // otherwise a frame arriving during that gap is routed to the old
        // scene and the queued frame is never flushed.
        return sceneTabIntentId;
      }
      if (intentLive && String(domSceneId) === String(sceneTabIntentId)) {
        sceneTabIntentSource = null;
        return sceneTabIntentId;
      }
      // Preserve a pointer-selected tab's optimistic intent for the full
      // handoff window. Rapid scene switches can leave Dash's selected class
      // on the previous tab while the complete frame is already available;
      // expiring this intent after 750 ms strands that frame on the old
      // canvas. A newer pointer click replaces the intent immediately.
      if (intentLive && sceneTabIntentSource === "pointer") return sceneTabIntentId;
      // Preserve a just-clicked tab's optimistic intent for the short window
      // before Dash applies its selected class. Older intents are stale when
      // a programmatic upload selection changes the value.
      if (intentLive && intentAge < 750) return sceneTabIntentId;
      if (sceneTabIntentId && String(domSceneId) !== String(sceneTabIntentId)) {
        sceneTabIntentId = null;
        sceneTabIntentAt = 0;
        sceneTabIntentSource = null;
      }
      return domSceneId;
    }
    return intentLive || sceneTabIntentSource === "programmatic" ? sceneTabIntentId : null;
  }
  window.MatterVisSelectScene = function (sceneId) {
    if (sceneId === null || sceneId === undefined || sceneId === "") return;
    sceneTabIntentId = String(sceneId);
    sceneTabIntentAt = Date.now();
    sceneTabIntentSource = "programmatic";
    resyncScene(sceneTabIntentId);
  };
  function fastViewMetadata() {
    const node = document.getElementById("fast-view-metadata");
    const text = node ? (node.textContent||"").trim() : "";
    if (!text) return null;
    try {
      const value=JSON.parse(text);
      if (value&&typeof value==="object") rememberExpectedRender(value, false);
      return value&&typeof value==="object"?value:null;
    }
    catch(_) { return null; }
  }
  function rememberExpectedRender(value, allowEpochChange) {
    if (!value||typeof value!=="object") return;
    const epoch=value.server_started_at?String(value.server_started_at):null;
    if (epoch&&renderServerEpoch&&epoch!==renderServerEpoch) {
      if (!allowEpochChange) return;
      Object.keys(expectedRenderRevisionByScene).forEach(function(key){delete expectedRenderRevisionByScene[key];});
      Object.keys(expectedCameraRevisionByScene).forEach(function(key){delete expectedCameraRevisionByScene[key];});
      Object.keys(expectedSceneGenerationByScene).forEach(function(key){delete expectedSceneGenerationByScene[key];});
    }
    if (epoch) renderServerEpoch=epoch;
    if (!value.scene_id) return;
    const revision=Number(value.render_revision);
    if (!Number.isFinite(revision)) return;
    const key=String(value.scene_id);
    let previous=expectedRenderRevisionByScene[key];
    const cameraRevision=Number(value.camera_revision||0);
    const generation=String(value.scene_generation||"");
    const priorGeneration=expectedSceneGenerationByScene[key];
    if (priorGeneration!==undefined && priorGeneration!==generation) {
      const priorNumber=Number(priorGeneration), nextNumber=Number(generation);
      /* Scene generations are monotonic within one server epoch. An older
       * payload must never replace the expectation established by fast
       * metadata for the same scene. */
      if (!allowEpochChange || (Number.isFinite(priorNumber)&&Number.isFinite(nextNumber)&&nextNumber<priorNumber)) return;
      delete expectedRenderRevisionByScene[key];
      delete expectedCameraRevisionByScene[key];
      previous=null;
    }
    expectedSceneGenerationByScene[key]=generation;
    const priorCamera=expectedCameraRevisionByScene[key];
    if (priorCamera===undefined || cameraRevision>priorCamera) expectedCameraRevisionByScene[key]=cameraRevision;
    if (!previous||revision>previous.revision||(revision===previous.revision&&cameraRevision>previous.cameraRevision)) {
      expectedRenderRevisionByScene[key]={revision:revision,cameraRevision:cameraRevision};
    }
  }
  function currentSceneId() {
    const tabScene = selectedSceneId();
    if (tabScene) return tabScene;
    const metadata = fastViewMetadata();
    return metadata&&metadata.scene_id ? String(metadata.scene_id) : null;
  }
  function sceneFrameNeedsReplay(sceneId) {
    const wanted = sceneId ? String(sceneId) : "";
    if (!wanted) return false;
    const selected = currentSceneId();
    const shown = canvasSceneId();
    return !!(selected && String(selected) === wanted && shown && String(shown) !== wanted);
  }
  window.MatterVisExpectedRender = function (sceneId) {
    const key = sceneId === null || sceneId === undefined ? "" : String(sceneId);
    if (!key) return null;
    const generation = expectedSceneGenerationByScene[key];
    const revision = expectedRenderRevisionByScene[key];
    const camera = expectedCameraRevisionByScene[key];
    if (generation === undefined && !revision && camera === undefined) return null;
    return {
      scene_generation: generation,
      render_revision: revision ? revision.revision : null,
      camera_revision: camera === undefined ? (revision ? revision.cameraRevision : null) : camera,
    };
  };
  // The shared view submitter runs in a separate asset and needs the same
  // optimistic tab intent while Plotly is still displaying the previous
  // scene.  Upload completion uses this hook before Dash paints the new tab.
  window.MatterVisCurrentScene = function () { return currentSceneId(); };
  window.__mattervisFigureDiagnostics = function () {
    const gd = graphDiv();
    const render = renderMetadataFromLayout(gd && gd.layout);
    return {
      currentSceneId: currentSceneId(),
      pendingFigureByScene: Object.keys(pendingFigureByScene),
      pendingFigureRetryTimers: Object.keys(pendingFigureRetryTimers),
      render: render ? {
        scene_id: render.scene_id || null,
        render_revision: Number(render.render_revision || 0),
        display_version: Number(render.display_version || 0),
      } : null,
    };
  };
  function currentRenderRevision() {
    const m = fastViewMetadata();
    const sceneId = currentSceneId();
    if (sceneId&&expectedRenderRevisionByScene[sceneId]) return expectedRenderRevisionByScene[sceneId].revision;
    if (!m || !m.scene_id || (sceneId && String(m.scene_id)!==sceneId)) return null;
    const revision=Number(m.render_revision);
    return Number.isFinite(revision)?revision:null;
  }
  function renderMetadataFromLayout(layout) {
    if (!layout || typeof layout!=="object") return null;
    let meta=layout.meta;
    if (typeof meta==="string") { try { meta=JSON.parse(meta); } catch(_) { return null; } }
    const render=meta&&typeof meta==="object"?meta.mattervis_render:null;
    return render&&typeof render==="object"?render:null;
  }
  function canvasSceneId() {
    const gd = graphDiv();
    const render = renderMetadataFromLayout(gd && gd.layout);
    return render && render.scene_id ? String(render.scene_id) : "";
  }
  function figureMetadataIsCurrent(render) {
    if (!render) return true;
    const sceneId=currentSceneId();
    if (sceneId&&render.scene_id&&String(render.scene_id)!==sceneId) return false;
    /* A background build for a scene the user did not select must not
     * replace the structure already on the canvas. */
    if (!sceneId && render.scene_id && canvasSceneId() && String(render.scene_id)!==canvasSceneId()) return false;
    const replacingScene = !!(render.scene_id && canvasSceneId() && String(render.scene_id) !== canvasSceneId());
    const targetScene = render.scene_id ? String(render.scene_id) : sceneId;
    const targetExpected = targetScene && window.MatterVisExpectedRender
      ? window.MatterVisExpectedRender(targetScene) : null;
    const expected=currentRenderRevision();
    const revision=Number(render.render_revision);
    if (expected!==null && !replacingScene && (!Number.isFinite(revision)||revision<expected)) return false;
    if (targetExpected) {
      if (targetExpected.scene_generation !== undefined && targetExpected.scene_generation !== null &&
          String(render.scene_generation||"") !== String(targetExpected.scene_generation)) return false;
      const hasGeneration = render.scene_generation !== undefined && render.scene_generation !== null && render.scene_generation !== "";
      if (targetExpected.render_revision !== null && targetExpected.render_revision !== undefined &&
          (!replacingScene || hasGeneration) &&
          (!Number.isFinite(revision) || revision < Number(targetExpected.render_revision))) return false;
      if (targetExpected.camera_revision !== null && targetExpected.camera_revision !== undefined &&
          (!replacingScene || hasGeneration) &&
          Number(render.camera_revision||0) < Number(targetExpected.camera_revision)) return false;
    }
    if (!replacingScene) {
      const expectedState=sceneId&&expectedRenderRevisionByScene[sceneId];
      if (expectedState&&Number(render.camera_revision||0)<expectedState.cameraRevision) return false;
      if (sceneId&&expectedCameraRevisionByScene[sceneId]!==undefined && Number(render.camera_revision||0)<expectedCameraRevisionByScene[sceneId]) return false;
      if (sceneId&&expectedSceneGenerationByScene[sceneId]!==undefined && String(render.scene_generation||"")!==expectedSceneGenerationByScene[sceneId]) return false;
    }
    if (renderServerEpoch&&render.server_started_at&&renderServerEpoch!==String(render.server_started_at)) return false;
    return true;
  }
  function installPlotlyRenderGate() {
    if (!window.Plotly) { setTimeout(installPlotlyRenderGate,25); return; }
    function wrap(name) {
      const original=window.Plotly[name];
      if (typeof original!=="function"||original.__mattervisRenderGate) return;
      const wrapped=function() {
        const args=Array.prototype.slice.call(arguments);
        let layout=null;
        const fullFigure=name==="newPlot"||name==="react";
        const objectFigure=fullFigure&&args[1]&&!Array.isArray(args[1])&&typeof args[1]==="object";
        if (fullFigure) layout=objectFigure?args[1].layout:args[2];
        else if (name==="update") layout=args[2];
        else if (name==="relayout") layout=args[1];
        const render=renderMetadataFromLayout(layout);
        const target=typeof args[0]==="string"?document.getElementById(args[0]):args[0];
        // Dash can replay an older figure prop without MatterVis render
        // metadata while a selected-scene handoff is still in flight.  The
        // metadata fence below cannot identify that payload, so it would
        // overwrite the freshly submitted replacement frame with the previous
        // scene.  Once a live canvas has a typed scene identity, keep an
        // untyped full-frame call out when the selected scene has moved on.
        if (fullFigure && target && !render) {
          const shown = renderMetadataFromLayout(target.layout);
          const selected = typeof currentSceneId === "function" ? currentSceneId() : null;
          if (shown && shown.scene_id && selected && String(shown.scene_id) !== String(selected)) {
            return Promise.resolve(target);
          }
        }
        if (name==="relayout" && layout) {
          const gd=typeof args[0]==="string"?document.getElementById(args[0]):args[0];
          const shown=renderMetadataFromLayout(gd && gd.layout);
          const canvasId=shown && shown.scene_id ? String(shown.scene_id) : "";
          let gestureId=window.__mvGestureSceneId ? String(window.__mvGestureSceneId) : "";
          const gestureKind=window.__mvGestureKind ? String(window.__mvGestureKind) : "";
          if (gestureKind === "wheel" && gestureId && canvasId && gestureId !== canvasId) {
            gestureId = "";
            window.__mvGestureSceneId = "";
            window.__mvLastGestureSceneId = "";
          }
          const touchesCamera=layout["scene.camera"] || (layout.scene && layout.scene.camera) ||
            Object.keys(layout).some(function(key){return key.indexOf("scene.camera.")===0;});
          if (gestureId && canvasId && gestureId!==canvasId && touchesCamera && gd && gd.__mvSceneCamera && gd.__mvSceneCameraOwner===canvasId) {
            return Promise.resolve(original.call(this, gd, {"scene.camera": JSON.parse(JSON.stringify(gd.__mvSceneCamera))}));
          }
        }
        if (render&&!figureMetadataIsCurrent(render)) return Promise.resolve(args[0]);
        if (name === "relayout" && layout &&
            typeof window.MatterVisInvalidateSemanticHover === "function") {
          const touchesCamera = layout["scene.camera"] ||
            (layout.scene && layout.scene.camera) ||
            Object.keys(layout).some(function (key) { return key.indexOf("scene.camera.") === 0; });
          if (touchesCamera) window.MatterVisInvalidateSemanticHover();
        }
        const gd=target;
        if (fullFigure&&render&&gd) {
          const receiver=this;
          const revision=function(lay) {return lay&&lay.scene&&lay.scene.uirevision!==undefined?lay.scene.uirevision:lay&&lay.uirevision;};
          const key=JSON.stringify([render.server_started_at,render.scene_id,render.render_revision,render.camera_revision||0,revision(layout)]);
            const deliver=function() {
            if (interactionActive) return interactionSettled.then(deliver);
            const shown = renderMetadataFromLayout(gd.layout);
            const shownScene = shown && shown.scene_id ? String(shown.scene_id) : "";
            const incomingScene = render.scene_id ? String(render.scene_id) : "";
            const replayFrame = !!(layout && layout.meta && layout.meta.mattervis_replay);
            const alreadyShowing = !replayFrame && gd.__mvDeliveredFrame===key && (!incomingScene || !shownScene || incomingScene===shownScene);
            /* Do not leave the busy pill behind when a stale/duplicate frame
             * is rejected. Mark the request busy only after the frame gate
             * confirms that this Plotly call will actually run. */
            if (!figureMetadataIsCurrent(render)||alreadyShowing) return gd;
            const statusNode = document.getElementById("mv-render-status");
            if (statusNode) {
              statusNode.hidden = false;
              statusNode.textContent = "Rendering structure…";
              statusNode.setAttribute("data-level", "busy");
            }
            if (layout && layout.scene) {
              gd.__mvCanonicalViewport = {
                aspectmode: layout.scene.aspectmode || null,
                aspectratio: layout.scene.aspectratio ? JSON.parse(JSON.stringify(layout.scene.aspectratio)) : null,
              };
            }
            const previous=renderMetadataFromLayout(gd.layout);
            let next=layout;
            if (layout.scene&&previous&&previous.scene_id===render.scene_id&&previous.server_started_at===render.server_started_at&&revision(gd.layout)===revision(layout)) {
              const camera=window.mattervisCurrentCamera&&window.mattervisCurrentCamera(gd);
              if (camera) next=Object.assign({},layout,{scene:Object.assign({},layout.scene,{camera:JSON.parse(JSON.stringify(camera))})});
            }
            if (objectFigure) args[1]=Object.assign({},args[1],{layout:next});
            else args[2]=next;
            return Promise.resolve(original.apply(receiver,args)).then(function(result) {
              /* A Plotly figure replacement can leave the previous hover
               * payload alive on the recycled graph node.  Clear semantic
               * hover state after every complete frame so a right-click on
               * the new scene cannot dispatch an object from the old one. */
              if (typeof window.MatterVisInvalidateSemanticHover === "function") {
                window.MatterVisInvalidateSemanticHover();
              }
              const done = document.getElementById("mv-render-status");
              if (done && done.getAttribute("data-level") === "busy") {
                done.hidden = true;
                done.textContent = "";
              }
              gd.__mvDeliveredFrame=key;
              gd.__mvGraphGeneration=Number(gd.__mvGraphGeneration||0)+1;
              if (next && next.scene && next.scene.camera) {
                gd.__mvSceneCamera = JSON.parse(JSON.stringify(next.scene.camera));
                gd.__mvSceneCameraOwner = String(render.scene_id || "");
              }
              if (typeof releaseStaleWebGL === "function") releaseStaleWebGL(gd);
              if (window.MatterVisViewCamera && typeof window.MatterVisViewCamera.installWheelZoom === "function") {
                window.MatterVisViewCamera.installWheelZoom(gd);
              }
              if (window.MatterVisViewCamera && typeof window.MatterVisViewCamera.installCanvasRefit === "function") {
                window.MatterVisViewCamera.installCanvasRefit(gd);
              }
              return result;
            });
          };
          // Both Dash signatures and WS pass here. Retry after failures;
          // neither receipt nor a rejected react is a delivered frame.
          return enqueueRender(gd, deliver);
        }
        return original.apply(this,args);
      };
      wrapped.__mattervisRenderGate=true;
      wrapped.__mattervisOriginal=original;
      window.Plotly[name]=wrapped;
    }
    wrap("newPlot");
    wrap("react");
    wrap("update");
    wrap("relayout");
    wrap("restyle");
  }
  installPlotlyRenderGate();
  function setInteraction(active) { setDashStore("graph-interaction-store", {active:!!active, ts:Date.now()}); }
  /* Compatibility fallback for the brief asset-load window before
   * view_updates.js installs the typed submitter. Normal complete-frame
   * delivery always reaches MatterVisViewUpdates first. */
  function applyFigurePush(data, layout) {
    var gd = graphDiv(); if (!gd||!window.Plotly) return Promise.resolve();
    return window.Plotly.react(gd, data||[], layout||{}).catch(function(error) {
      console.warn("MatterVis frame delivery failed", error);
    });
  }
  function scheduleQueuedSceneFigureRetry(sceneId, attempt) {
    const key = String(sceneId || "");
    const queued = key && pendingFigureByScene[key];
    if (!key || pendingFigureRetryTimers[key] || !queued) return;
    attempt = Number(attempt || 0);
    // Preserve the handoff budget when settle detects a stale tab paint and
    // has to return to the selected-scene retry path.  Without this marker,
    // the settle fallback restarted at zero and could schedule another full
    // handoff window indefinitely.
    queued.handoffAttempts = attempt;
    // Scene-tab callbacks can spend several seconds in a Dash turn while the
    // old figure is still mounted. Keep the retry bounded, but long enough to
    // bridge that handoff instead of dropping the only complete frame.
    if (attempt >= 120) {
      delete pendingFigureByScene[key];
      return;
    }
    pendingFigureRetryTimers[key] = setTimeout(function () {
      delete pendingFigureRetryTimers[key];
      flushQueuedSceneFigure(key, attempt + 1);
    }, Math.min(250, 25 * (attempt + 1)));
  }
  function queuedSceneFigureMatchesCanvas(queued) {
    if (typeof graphDiv !== "function") return true;
    const gd = graphDiv();
    if (!gd || !queued || !queued.figure) return true;
    const expected = renderMetadataFromLayout(queued.figure.layout || {});
    const shown = renderMetadataFromLayout(gd.layout || {});
    // Early Plotly mounts and the small VM harness do not expose metadata.
    // Once a live layout exists, require the complete render identity so a
    // delayed Dash repaint cannot release the only parked frame too early.
    if (!expected || !shown) return true;
    return String(expected.scene_id || "") === String(shown.scene_id || "") &&
      Number(expected.render_revision || 0) === Number(shown.render_revision || 0) &&
      Number(expected.display_version || 0) === Number(shown.display_version || 0);
  }
  function scheduleQueuedSceneFigureSettle(sceneId, queued, settleAttempt) {
    const key = String(sceneId || "");
    if (!key || pendingFigureByScene[key] !== queued || pendingFigureRetryTimers[key]) return;
    settleAttempt = Number(settleAttempt || 0);
    // A busy Dash callback can repaint the previous figure several seconds
    // after Plotly.react resolves (especially during rapid tab switching).
    // Keep the selected frame parked long enough for that repaint to settle;
    // the per-scene retry budget remains bounded.
    if (settleAttempt >= 40) {
      delete pendingFigureByScene[key];
      return;
    }
    pendingFigureRetryTimers[key] = setTimeout(function () {
      delete pendingFigureRetryTimers[key];
      if (pendingFigureByScene[key] !== queued) return;
      if (queuedSceneFigureMatchesCanvas(queued)) {
        // Dash may repaint with its stale figure prop just after Plotly.react
        // resolves. Require several stable observations before releasing the
        // parked frame so that repaint gets a second replay if needed.
        if (settleAttempt >= 8) {
          delete pendingFigureByScene[key];
          return;
        }
        scheduleQueuedSceneFigureSettle(key, queued, settleAttempt + 1);
        return;
      }
      // The first settle observation can see the stale Dash layout before
      // Plotly.react's promise has updated the canvas. Reapply the same
      // selected frame with the settle counter advancing independently from
      // the tab handoff counter; this keeps the replay bounded and avoids
      // resetting the counter on every stale observation.
      if (typeof figurePushIsCurrent === "function" && !figurePushIsCurrent(queued)) {
        scheduleQueuedSceneFigureRetry(key, Number(queued.handoffAttempts || 0));
        return;
      }
      replayQueuedSceneFigure(key, queued, settleAttempt + 1);
    }, settleAttempt ? 100 : 250);
  }
  function clearQueuedSceneFigure(sceneId, sequence) {
    const key = String(sceneId || "");
    if (!key) return;
    const queued = pendingFigureByScene[key];
    if (!queued) return;
    const queuedSeq = Number(queued.figure_seq || queued.seq || 0);
    const directSeq = Number(sequence || 0);
    // A direct frame supersedes only an equal/newer parked frame.  If either
    // frame has no sequence, retain the parked one unless the direct frame is
    // sequenced; an unknown frame must not accidentally discard a newer one.
    if (!directSeq && queuedSeq) return;
    if (directSeq && queuedSeq && queuedSeq > directSeq) return;
    if (pendingFigureRetryTimers[key]) {
      clearTimeout(pendingFigureRetryTimers[key]);
      delete pendingFigureRetryTimers[key];
    }
    delete pendingFigureByScene[key];
  }
  function replayQueuedSceneFigure(sceneId, queued, nextSettleAttempt) {
    const key = String(sceneId || "");
    nextSettleAttempt = Number(nextSettleAttempt || 0);
    const submitter = window.MatterVisViewUpdates &&
      typeof window.MatterVisViewUpdates.submit === "function"
      ? window.MatterVisViewUpdates.submit
      : null;
    if (submitter) {
      // Dash can repaint an older complete figure after the first submit.
      // Mark every bounded replay so the typed gate and Plotly render gate
      // may reapply an equal-key frame instead of treating it as delivered.
      queued._mvForceReplay = true;
      let submitted;
      try {
        // Re-enter the typed submitter on replay.  A parked frame may have
        // become stale while it waited for the tab handoff, so bypassing the
        // submitter here would skip its scene-generation, version, epoch and
        // delivered-key checks.
        submitted = submitter(queued);
      } catch (_) {
        scheduleQueuedSceneFigureRetry(key, Number(queued.handoffAttempts || 0));
        return;
      }
      if (submitted === "stale") {
        scheduleQueuedSceneFigureRetry(key, Number(queued.handoffAttempts || 0));
        return;
      }
      if (submitted && typeof submitted.then === "function") {
        submitted.then(function (result) {
          if (result === "stale") {
            scheduleQueuedSceneFigureRetry(key, Number(queued.handoffAttempts || 0));
            return;
          }
          scheduleQueuedSceneFigureSettle(key, queued, nextSettleAttempt);
        }, function () {
          scheduleQueuedSceneFigureRetry(key, Number(queued.handoffAttempts || 0));
        });
        return;
      }
      scheduleQueuedSceneFigureSettle(key, queued, nextSettleAttempt);
      return;
    }
    /* The typed submitter is normally present; retain a bounded startup
     * fallback while the separate asset is still loading. */
    const applied = applyFigurePush(queued.figure.data || [], queued.figure.layout || {});
    if (applied && typeof applied.then === "function") {
      applied.then(function () { scheduleQueuedSceneFigureSettle(key, queued, nextSettleAttempt); });
    } else {
      scheduleQueuedSceneFigureSettle(key, queued, nextSettleAttempt);
    }
  }
  function flushQueuedSceneFigure(targetSceneId, attempt) {
    const sceneId = targetSceneId ? String(targetSceneId) : currentSceneId();
    if (!sceneId) return;
    const queued = pendingFigureByScene[String(sceneId)];
    if (!queued) return;
    if (!figurePushIsCurrent(queued)) {
      // Once the requested tab is actually selected, let the typed submitter
      // make the final complete-frame decision. The fast handoff fence can
      // still lag the selected scene's metadata by one Dash turn even though
      // the frame is valid for this canvas; refusing to submit here strands
      // the parked frame until the retry budget expires.
      if (String(currentSceneId() || "") === String(sceneId)
          && window.MatterVisViewUpdates
          && typeof window.MatterVisViewUpdates.submit === "function") {
        replayQueuedSceneFigure(sceneId, queued);
        return;
      }
      // A completed frame may beat Dash's selected-tab paint by several
      // turns. Keep the revision-0 frame parked by scene and retry the handoff
      // briefly instead of evaluating its fence only once while the previous
      // scene is still selected.
      scheduleQueuedSceneFigureRetry(sceneId, attempt);
      return;
    }
    if (pendingFigureRetryTimers[String(sceneId)]) {
      clearTimeout(pendingFigureRetryTimers[String(sceneId)]);
      delete pendingFigureRetryTimers[String(sceneId)];
    }
    // A newer frame for this scene may have arrived while the parked frame
    // waited for the selected tab.  Keep the same per-scene sequence fence
    // used by the transport path so an older parked frame cannot repaint over
    // the newer one during replay.
    const queuedSeq = Number(queued.figure_seq || queued.seq || 0);
    const seenSeq = typeof latestFigureSeqByScene !== "undefined"
      ? Number(latestFigureSeqByScene[String(sceneId)] || 0)
      : 0;
    if (queuedSeq && seenSeq && queuedSeq < seenSeq) {
      clearQueuedSceneFigure(sceneId, queuedSeq);
      return;
    }
    // A newer frame may already be visible while this parked frame was waiting
    // for the selected tab. Never let the older frame repaint over it.
    const gd = typeof graphDiv === "function" ? graphDiv() : null;
    const queuedRender = typeof renderMetadataFromLayout === "function"
      ? renderMetadataFromLayout(queued.figure.layout || {})
      : null;
    const shownRender = typeof renderMetadataFromLayout === "function"
      ? renderMetadataFromLayout(gd && gd.layout)
      : null;
    const sameSceneEpoch = queuedRender && shownRender
      && String(queuedRender.scene_id || "") === String(shownRender.scene_id || "")
      && (!queuedRender.server_started_at || !shownRender.server_started_at
        || String(queuedRender.server_started_at) === String(shownRender.server_started_at));
    if (sameSceneEpoch
        && Number(shownRender.render_revision || 0) >= Number(queuedRender.render_revision || 0)) {
      clearQueuedSceneFigure(sceneId, Number(queued.figure_seq || queued.seq || 0));
      return;
    }
    replayQueuedSceneFigure(sceneId, queued);
  }
  // Dash applies ``scene-tabs.value`` and paints the selected tab in
  // separate turns.  A completed upload can therefore be queued by the
  // figure subscriber before the tab subtree changes; that change is an
  // attribute update and is intentionally outside the child-list observer
  // below.  Let the upload completion explicitly flush the queued frame at
  // the selection barrier, with a couple of deferred retries for the case
  // where Dash has not painted its value yet.
  window.MatterVisFlushSceneFrame = function (sceneId) {
    if (sceneId !== null && sceneId !== undefined && sceneId !== "") {
      window.MatterVisSelectScene(sceneId);
      pendingSceneTabScrollId = String(sceneId);
      if (typeof scheduleSceneTabScroll === "function") {
        scheduleSceneTabScroll(sceneId);
      }
    }
    flushQueuedSceneFigure();
    [0, 50, 250].forEach(function (delay) {
      setTimeout(flushQueuedSceneFigure, delay);
    });
  };
  function flushPendingFigurePush() {
    if (endInteraction) { endInteraction(); endInteraction=null; }
    if (!pendingFigurePush) return;
    var p = pendingFigurePush;
    pendingFigurePush = null;
    if (!figurePushIsCurrent(p)) {
      if (p.scene_id) {
        const key=String(p.scene_id);
        const previous=pendingFigureByScene[key];
        const previousSeq=Number(previous && (previous.figure_seq || previous.seq) || 0);
        const seq=Number(p.figure_seq || p.seq || 0);
        if (!previous || !previousSeq || (seq && seq >= previousSeq)) {
          pendingFigureByScene[key]=p;
          scheduleQueuedSceneFigureRetry(key, 0);
        }
      }
      return;
    }
    p._mvForceReplay = true;
    if (p.scene_id) {
      const key=String(p.scene_id);
      pendingFigureByScene[key]=p;
      replayQueuedSceneFigure(key,p);
    } else if (window.MatterVisViewUpdates && typeof window.MatterVisViewUpdates.submit === "function") {
      window.MatterVisViewUpdates.submit(p);
    } else {
      applyFigurePush(p.figure.data||[], p.figure.layout||{});
    }
  }
  function markActive() {
    gestureGeneration += 1;
    if (settleTimer) { clearTimeout(settleTimer); settleTimer = null; }
    if (!interactionActive) {
      interactionActive = true;
      interactionSettled = new Promise(function(resolve){endInteraction=resolve;});
      setInteraction(true);
    }
  }
  function markInactiveSoon() {
    const generation = gestureGeneration;
    if (settleTimer) clearTimeout(settleTimer);
    settleTimer = setTimeout(function () {
      settleTimer = null;
      if (generation !== gestureGeneration) return;
      if (interactionActive) { interactionActive = false; setInteraction(false); flushPendingFigurePush(); }
    }, INTERACTION_SETTLE_MS);
  }
  function inGraph(target) { return !!(target && target.closest && target.closest("#crystal-graph")); }

  // ── Right-click menu (right_click_menu.js) ─────────────────────
  let lastHovered = null, lastHoverPoint = null, lastClient = {x:0,y:0};
  // Keep additive/toggle picks isolated per scene.  A single browser-global
  // list leaked selections from the previous tab into the next tab because
  // the Dash store is only updated after the click callback returns.
  const persistentTargetsByScene = Object.create(null);
  function invalidateSemanticHover() {
    lastHovered = null;
    lastHoverPoint = null;
    lastClient = {x:0, y:0};
    // Plotly can retain a recycled payload on the graph node after a full
    // frame replacement. Clear it with the browser-local cache so a later
    // contextmenu cannot revive an object from the previous scene.
    const gd = graphDiv();
    if (gd) gd._hoverdata = null;
    const tip = document.getElementById("mv-atom-tip");
    if (tip) tip.hidden = true;
    const inspector = document.getElementById("mv-hover-inspector");
    if (inspector) inspector.remove();
  }
  // Plotly's render gate calls this after a scene/tab replacement. Keeping it
  // public also lets tab intent and camera controls invalidate the hover cache
  // at their handoff boundaries without duplicating DOM cleanup.
  window.MatterVisInvalidateSemanticHover = invalidateSemanticHover;
  function sceneSelectionKey(sceneId) {
    return sceneId === null || sceneId === undefined || sceneId === "" ? "__default__" : String(sceneId);
  }
  function persistentTargetsForScene(sceneId) {
    const key = sceneSelectionKey(sceneId);
    if (!Array.isArray(persistentTargetsByScene[key])) persistentTargetsByScene[key] = [];
    return persistentTargetsByScene[key];
  }
  function prunePersistentTargets() {
    const live = Object.create(null);
    let foundTab = false;
    document.querySelectorAll("#scene-tabs [id^='scene-tab-']").forEach(function (tab) {
      const id = sceneIdFromTabNode(tab);
      if (id) { live[String(id)] = true; foundTab = true; }
    });
    // During the initial Dash paint there may be no tab nodes yet.  Do not
    // discard valid scene state in that transient gap; prune only after at
    // least one real tab is present (including after a tab is removed).
    if (!foundTab) return;
    Object.keys(persistentTargetsByScene).forEach(function (key) {
      if (key !== "__default__" && !live[key]) delete persistentTargetsByScene[key];
    });
  }
  function targetFromPoint(point) {
    if (!point || typeof point !== "object") return null;
    const payload = decodeCustomdata(point.customdata);
    if (payload) return payload;
    const trace = point.data || point.fullData || {};
    const meta = trace.meta && typeof trace.meta === "object" ? trace.meta : {};
    const role = String(meta.mv_role || meta.kind || "").toLowerCase();
    const pointNumber = Number(point.pointNumber !== undefined ? point.pointNumber : point.pointIndex);
    if (role === "geometry_entity" || role === "geometry_entity_edge") {
      const geometryId = meta.geometry_id || meta.id;
      if (!geometryId) return null;
      const subentity = faceIdAt(meta, point);
      // BFDH is rendered through the generic depth-tested geometry path, but
      // each face has its own semantic facet ID. Preserve that identity so a
      // click on a morphology face can drive facet selection/style overrides
      // instead of collapsing to the parent mesh entity.
      if (String(meta.source || "") === "bfdh_morphology" && subentity) {
        return {kind:"facet", id:String(subentity), parent_id:"morphology:bfdh-morphology", subentity_id:String(subentity)};
      }
      return {kind:"geometry", id:String(geometryId), geometry_id:String(geometryId), subentity_id:subentity, face_id:subentity};
    }
    if (role === "polyhedron" || meta.spec_id) {
      const subentity = faceIdAt(meta, point);
      const id = subentity || meta.instance_id || ("polyhedron:"+String(meta.spec_id||"unknown"));
      return {kind:"polyhedron", id:String(id), spec_id:meta.spec_id||null, subentity_id:subentity};
    }
    if (role === "facet" || role === "morphology") {
      const facetIds = Array.isArray(meta.facet_ids) ? meta.facet_ids : [];
      const ordinal = meshFaceOrdinal(point);
      const id = ordinal >= 0 && ordinal < facetIds.length && facetIds[ordinal]
        ? facetIds[ordinal] : (meta.geometry_id || "facet:bfdh-morphology");
      return {kind:"facet", id:String(id), parent_id:"morphology:bfdh-morphology"};
    }
    return null;
  }
  function targetFromSelectionResponse(body, sceneId) {
    const selection = body && body.selection;
    const targets = selection && Array.isArray(selection.targets) ? selection.targets : [];
    const target = targets.find(function (item) {
      return item && typeof item === "object" && item.kind && item.id;
    });
    if (!target) return null;
    const payload = Object.assign({}, target);
    if (payload.kind === "atom" && !payload.label && Array.isArray(selection.atom_labels)) {
      payload.label = selection.atom_labels[0] || "";
    }
    if (payload.kind === "atom" && !payload.element && selection.parts && selection.parts[0]) {
      payload.element = selection.parts[0].elem || "";
      payload.is_minor = !!selection.parts[0].is_minor;
      payload.part = selection.parts[0].part || "";
    }
    if (sceneId !== null && sceneId !== undefined && sceneId !== "") {
      payload.scene_id = String(sceneId);
    }
    return payload;
  }
  function clearSelectionOutline() {
    const gd = graphDiv();
    if (!gd || !Array.isArray(gd.data)) return;
    const indices = [];
    gd.data.forEach(function (trace, index) {
      const meta = trace && trace.meta || {};
      const role = String(meta.mv_role || meta.kind || "").toLowerCase();
      const name = String(trace && trace.name || "").toLowerCase();
      const x = Array.isArray(trace && trace.x) ? trace.x : [];
      if ((role === "selection" || name === "selection-outline")
          && trace.visible !== false && x.length > 0) {
        indices.push(index);
      }
    });
    if (!indices.length) return;
    if (window.Plotly && typeof window.Plotly.restyle === "function") {
      try {
        window.Plotly.restyle(gd, {visible: false}, indices);
        return;
      } catch (_) {}
    }
    indices.forEach(function (index) {
      if (gd.data[index]) gd.data[index].visible = false;
    });
  }
  function publishSemanticSelection(target, mode) {
    if (!target && mode !== "clear") return;
    const nextMode = mode || "replace";
    const sceneId = currentSceneId();
    const persistentTargets = persistentTargetsForScene(sceneId);
    if (nextMode === "clear") {
      persistentTargetsByScene[sceneSelectionKey(sceneId)] = [];
      // Hide the previous outline immediately. The server callback will
      // publish the empty selection and subsequent figure refreshes remain
      // authoritative, but a delayed frame must not leave stale ink visible.
      clearSelectionOutline();
    } else if (nextMode === "add") {
      const exists = persistentTargets.some(function(item){return item.kind===target.kind&&item.id===target.id;});
      if (!exists) persistentTargets.push(target);
    }
    else if (nextMode === "toggle") {
      const found = persistentTargets.findIndex(function(item){return item.kind===target.kind&&item.id===target.id;});
      if (found >= 0) persistentTargets.splice(found, 1); else persistentTargets.push(target);
    } else persistentTargetsByScene[sceneSelectionKey(sceneId)] = [target];
    setDashStore("scene-selection-store", {scene_id:sceneId, targets:persistentTargetsForScene(sceneId).slice(), mode:nextMode, ts:Date.now()});
  }
  function hoverIsCurrent() {
    if (!lastHovered) return false;
    const scene = currentSceneId();
    if (lastHovered.scene_id && scene && String(lastHovered.scene_id) !== String(scene)) return false;
    const gd = graphDiv();
    const generation = gd ? Number(gd.__mvGraphGeneration || 0) : 0;
    if (lastHovered.graph_generation !== undefined && Number(lastHovered.graph_generation) !== generation) return false;
    return true;
  }
  function noteHover(raw) {
    if (!raw) { lastHovered = null; return; }
    const gd = graphDiv();
    lastHovered = Object.assign({}, raw, {
      scene_id: currentSceneId() || "",
      graph_generation: gd ? Number(gd.__mvGraphGeneration || 0) : 0,
    });
  }
  function pushTarget(payload) {
    if (setDashStore("rightclick-target", payload)) return;
    const input = document.getElementById("rightclick-target-fallback");
    if (input) { input.value = JSON.stringify(payload); input.dispatchEvent(new Event("input",{bubbles:true})); }
  }
  function hoverInspector(target, event) {
    let inspector = document.getElementById("mv-hover-inspector");
    if (!target) {
      if (inspector) inspector.remove();
      return;
    }
    // Atoms already have a coordinate tooltip.  Avoid painting a second
    // semantic label over it when Plotly also reports an atom hover.
    if (target.kind === "atom") {
      const tip = document.getElementById("mv-atom-tip");
      if (tip && !tip.hidden) {
        if (inspector) inspector.remove();
        return;
      }
    }
    if (!inspector) {
      inspector = document.createElement("div");
      inspector.id = "mv-hover-inspector";
      inspector.className = "mv-hover-inspector";
      inspector.setAttribute("role", "status");
      document.body.appendChild(inspector);
    }
    const label = target.label || target.fragment_label || target.spec_id ||
      target.geometry_id || target.id || "";
    inspector.textContent = String(target.kind || "target") + (label ? " · " + label : "");
    const x = event && event.clientX !== undefined ? event.clientX : (event && event.x) || lastClient.x;
    const y = event && event.clientY !== undefined ? event.clientY : (event && event.y) || lastClient.y;
    inspector.style.left = (Number(x) + 10) + "px";
    inspector.style.top = (Number(y) + 10) + "px";
  }
  function clearSelectionIfGraphBackground() {
    const gd = graphDiv();
    const hoverdata = gd && gd._hoverdata;
    // Plotly gl3d does not consistently emit plotly_click for an empty
    // canvas pixel. Its hover cache is updated on pointer movement, so a
    // deferred click fallback can distinguish a background click from a
    // point click and clear the semantic selection reliably.
    if (!hoverdata || !Array.isArray(hoverdata.points) || !hoverdata.points.length) {
      const sceneId = currentSceneId();
      if (persistentTargetsForScene(sceneId).length) publishSemanticSelection(null, "clear");
    }
  }
  function bindRightClick() {
    const container = document.getElementById("crystal-graph");
    if (!container || container.dataset.rcmBound === "1") return;
    const inner = container.querySelector(".js-plotly-plot");
    if (!inner || typeof inner.on !== "function") { requestAnimationFrame(bindRightClick); return; }
    container.dataset.rcmBound = "1";
    if (typeof inner.on !== "function") {
      container.dataset.rcmBound = "0";
      return;
    }
    let plotlyClickObserved = false;
    let gestureSequence = 0;
    let activeGestureId = null;
    let lastPointerGesture = null;
    let semanticClickGesture = null;
    let semanticClickAt = 0;
    let semanticClickPoint = null;
    // Coordinate picking is asynchronous. Keep a gesture pending until its
    // preview request settles so the capture-phase background clear cannot
    // erase a Ctrl/Shift toggle first.
    let pendingCoordinateGesture = null;
    const gestureGraceMs = 1000;

    function clearSemanticClickGesture(gestureId) {
      if (semanticClickGesture !== gestureId) return;
      semanticClickGesture = null;
      semanticClickAt = 0;
      semanticClickPoint = null;
    }

    function releaseSemanticClickGesture(gestureId) {
      // A gl3d plotly_click may arrive while the pointer is still held. Keep
      // ownership through pointerup/click, with a grace period for slower
      // native gestures.
      setTimeout(function () {
        if (semanticClickGesture !== gestureId) return;
        if (pointerDown && pointerDown.id === gestureId) {
          releaseSemanticClickGesture(gestureId);
          return;
        }
        if (pendingCoordinateGesture && pendingCoordinateGesture.id === gestureId) {
          releaseSemanticClickGesture(gestureId);
          return;
        }
        clearSemanticClickGesture(gestureId);
      }, gestureGraceMs);
    }

    function finishCoordinateGesture(gestureId) {
      if (pendingCoordinateGesture && pendingCoordinateGesture.id === gestureId) {
        pendingCoordinateGesture = null;
      }
    }

    function hasPendingCoordinateGesture() {
      return !!(pendingCoordinateGesture
        && Date.now() - pendingCoordinateGesture.at < gestureGraceMs * 2);
    }
    try {
      inner.on("plotly_hover", function (ed) {
        if (ed && ed.points && ed.points.length) {
          lastHovered = targetFromPoint(ed.points[0]);
          if (lastHovered) {
            noteHover(lastHovered);
            lastHoverPoint = {x:lastClient.x, y:lastClient.y, at:Date.now()};
          }
          hoverInspector(lastHovered, lastClient);
        }
      });
      inner.on("plotly_unhover", function () {
        lastHovered = null;
        lastHoverPoint = null;
        hoverInspector(null);
      });
      inner.on("plotly_click", function (ed) {
        const native = ed && ed.event;
        plotlyClickObserved = true;
        const recentPointer = lastPointerGesture && Date.now() - lastPointerGesture.at < gestureGraceMs
          ? lastPointerGesture.id : null;
        const gestureId = activeGestureId || recentPointer || ++gestureSequence;
        semanticClickGesture = gestureId;
        semanticClickAt = Date.now();
        semanticClickPoint = native && native.clientX !== undefined
          ? {x:native.clientX, y:native.clientY}
          : {x:lastClient.x, y:lastClient.y};
        setTimeout(function () { plotlyClickObserved = false; }, 0);
        releaseSemanticClickGesture(gestureId);
        const target = ed && ed.points && ed.points.length ? targetFromPoint(ed.points[0]) : null;
        if (!target) { publishSemanticSelection(null, "clear"); return; }
        const pointer = lastPointerGesture && lastPointerGesture.id === gestureId
          ? lastPointerGesture
          : pointerDown && pointerDown.id === gestureId ? pointerDown : null;
        const ctrl = !!(native && (native.ctrlKey || native.metaKey)) || !!(pointer && (pointer.ctrlKey || pointer.metaKey));
        const shift = !!(native && native.shiftKey) || !!(pointer && pointer.shiftKey);
        publishSemanticSelection(target, ctrl ? "toggle" : (shift ? "add" : "replace"));
      });
    } catch (_) {
      container.dataset.rcmBound = "0";
      return;
    }
    function handlePointerMove(e) {
      lastClient = {x:e.clientX, y:e.clientY};
      if (e.target && e.target.closest && e.target.closest(".mv-canvas-toolbar, .modebar")) return;
      queueHover(e.clientX, e.clientY);
    }
    /* Plotly emits native mouse events for real input, while automation and
     * assistive clients often dispatch PointerEvents directly. Keep semantic
     * hover sampling on both event families so the inspector and context menu
     * stay in sync with mesh3d depth picking. */
    container.addEventListener("mousemove", handlePointerMove, {passive:true});
    container.addEventListener("pointermove", handlePointerMove, {passive:true});
    function handlePointerLeave() {
      tipSeq += 1;
      if (tipTimer) clearTimeout(tipTimer);
      hideAtomTip();
      hoverInspector(null);
    }
    container.addEventListener("mouseleave", handlePointerLeave);
    container.addEventListener("pointerleave", handlePointerLeave);
    function pickRect(clientX, clientY, pad) {
      /* Selection coordinates are defined in the Plotly viewport.  The
       * outer crystal-graph also contains toolbars/overlays, so using its
       * bounds offsets every click and makes the backend miss the projected
       * atom centre. */
      const viewportNode = inner || container;
      const liveViewport = container.querySelector(".js-plotly-plot") || viewportNode;
      const gr = liveViewport.getBoundingClientRect();
      const x = clientX - gr.left;
      const y = clientY - gr.top;
      return {
        rect: [x - pad, y - pad, x + pad, y + pad],
        viewport: [gr.width, gr.height],
      };
    }
    function postSelection(rect, viewport, additive, commit) {
      return fetch("/api/v2/selection/by_box", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          rect_pixels: rect,
          viewport_size: viewport,
          scene_id: currentSceneId(),
          additive: !!additive,
          commit: commit !== false,
        }),
      }).then(function (response) { return response.json(); });
    }
    let tipSeq = 0;
    let tipTimer = null;
    function atomTip() {
      let tip = document.getElementById("mv-atom-tip");
      if (tip) return tip;
      tip = document.createElement("div");
      tip.id = "mv-atom-tip";
      tip.hidden = true;
      document.body.appendChild(tip);
      return tip;
    }
    function hideAtomTip() {
      const tip = document.getElementById("mv-atom-tip");
      if (tip) tip.hidden = true;
    }
    function showAtomTip(clientX, clientY, selection) {
      const tip = atomTip();
      const part = (selection && selection.parts && selection.parts[0]) || {};
      const label = (selection && selection.atom_labels && selection.atom_labels[0]) || "";
      if (!label) { tip.hidden = true; return; }
      const disorder = part.part ? " · " + part.part : (part.is_minor ? " · minor" : "");
      tip.textContent = label + (part.elem ? " (" + part.elem + ")" : "") + disorder;
      tip.style.left = (clientX + 14) + "px";
      tip.style.top = (clientY + 16) + "px";
      tip.hidden = false;
      // The atom tip is the authoritative hover label for coordinate hits;
      // remove a semantic inspector that may have been painted first.
      hoverInspector(null);
    }
    function queueHover(clientX, clientY) {
      const seq = ++tipSeq;
      if (tipTimer) clearTimeout(tipTimer);
      tipTimer = setTimeout(function () {
        const picked = pickRect(clientX, clientY, 14);
        postSelection(picked.rect, picked.viewport, false, false).then(function (body) {
          if (seq !== tipSeq) return;
          showAtomTip(clientX, clientY, body && body.selection);
        }).catch(function () {});
      }, 90);
    }
    let pointerDown = null;
    let lastPrimaryPick = null;
    function primaryPick(e, gestureId) {
      if ((e.button !== undefined && e.button !== 0) || e.altKey) return;
      if (e.target && e.target.closest && e.target.closest(".mv-canvas-toolbar, .modebar, .rightclick-menu")) return;
      const now = Date.now();
      if (!gestureId) {
        const recentPointer = lastPointerGesture && now - lastPointerGesture.at < gestureGraceMs &&
          Math.abs(lastPointerGesture.x - e.clientX) < 1 &&
          Math.abs(lastPointerGesture.y - e.clientY) < 1;
        const recentSemantic = semanticClickGesture && semanticClickPoint &&
          now - semanticClickAt < gestureGraceMs &&
          Math.abs(semanticClickPoint.x - e.clientX) < 1 &&
          Math.abs(semanticClickPoint.y - e.clientY) < 1;
        gestureId = activeGestureId || (recentPointer ? lastPointerGesture.id : null) ||
          (recentSemantic ? semanticClickGesture : null) || ++gestureSequence;
      }
      /* A native click follows pointerup for one gesture. Keep the id across
       * both events and defer the coordinate fallback so Plotly's custom
       * plotly_click handler can claim the gesture first. */
      if (lastPrimaryPick && lastPrimaryPick.id === gestureId) return;
      lastPrimaryPick = {id:gestureId, at:now, x:e.clientX, y:e.clientY};
      const picked = pickRect(e.clientX, e.clientY, 14);
      pendingCoordinateGesture = {
        id: gestureId,
        at: now,
        ctrlKey: !!e.ctrlKey || !!e.metaKey,
        shiftKey: !!e.shiftKey,
      };
      const schedule = window.requestAnimationFrame || function (callback) { setTimeout(callback, 0); };
      schedule(function () {
        setTimeout(function () {
        if (semanticClickGesture === gestureId) {
          finishCoordinateGesture(gestureId);
          return;
        }
        // Coordinate picking is a preview. Publish the resulting atom target
        // through the semantic store so it cannot overwrite a non-atom target
        // and Ctrl/Cmd uses the same toggle path as Plotly clicks.
        postSelection(picked.rect, picked.viewport, false, false).then(function (body) {
          // A newer pointer gesture owns the selection now. Do not let a
          // slower preview response from an older click overwrite it.
          if (gestureSequence !== gestureId) {
            finishCoordinateGesture(gestureId);
            return;
          }
          const selection = body && body.selection;
          const semanticTargets = selection && selection.targets || [];
          const atomTarget = semanticTargets.find(function (item) { return item && item.kind === "atom"; });
          if (!atomTarget) {
            publishSemanticSelection(null, "clear");
            finishCoordinateGesture(gestureId);
            return;
          }
          const mode = e.ctrlKey || e.metaKey ? "toggle" : (e.shiftKey ? "add" : "replace");
          publishSemanticSelection(atomTarget, mode);
          finishCoordinateGesture(gestureId);
        }).catch(function () {
          finishCoordinateGesture(gestureId);
        });
        }, 50);
      });
    }
    container.addEventListener("pointerdown", function (e) {
      if (e.button === undefined || e.button === 0) {
        const id = ++gestureSequence;
        if (semanticClickGesture !== null && semanticClickGesture !== id) {
          clearSemanticClickGesture(semanticClickGesture);
        }
        plotlyClickObserved = false;
        activeGestureId = id;
        pointerDown = {
          x:e.clientX,
          y:e.clientY,
          id:id,
          ctrlKey:!!e.ctrlKey,
          metaKey:!!e.metaKey,
          shiftKey:!!e.shiftKey,
        };
      }
    }, {passive:true});
    container.addEventListener("pointerup", function (e) {
      if (!pointerDown) return;
      const moved = Math.hypot(e.clientX - pointerDown.x, e.clientY - pointerDown.y);
      const gesture = pointerDown;
      lastPointerGesture = {
        id:gesture.id,
        at:Date.now(),
        x:e.clientX,
        y:e.clientY,
        moved:moved > 4,
        ctrlKey:!!e.ctrlKey,
        metaKey:!!e.metaKey,
        shiftKey:!!e.shiftKey,
      };
      pointerDown = null;
      if (moved <= 4) primaryPick(e, gesture.id);
      setTimeout(function () {
        if (activeGestureId === gesture.id) activeGestureId = null;
      }, 0);
    });
    container.addEventListener("click", function (e) {
      /* Synthetic click events do not always populate ``button``. Treat an
       * omitted value as the normal primary click while still ignoring an
       * explicit secondary/middle button. */
      const recentPointer = lastPointerGesture
        && Date.now() - lastPointerGesture.at < gestureGraceMs
        && Math.abs(lastPointerGesture.x - e.clientX) < 1
        && Math.abs(lastPointerGesture.y - e.clientY) < 1
        ? lastPointerGesture : null;
      if (recentPointer && recentPointer.moved) return;
      primaryPick(e);
    });
    container.addEventListener("click", function (e) {
      if (e.defaultPrevented) return;
      setTimeout(function () {
        // A point click is delivered through Plotly's custom event rather
        // than the DOM event. Avoid clearing a fresh semantic pick.
        const recentPointer = lastPointerGesture
          && Date.now() - lastPointerGesture.at < gestureGraceMs
          && Math.abs(lastPointerGesture.x - e.clientX) < 1
          && Math.abs(lastPointerGesture.y - e.clientY) < 1
          ? lastPointerGesture : null;
        const modifier = !!(e.ctrlKey || e.metaKey || e.shiftKey)
          || !!(recentPointer && (recentPointer.ctrlKey || recentPointer.metaKey || recentPointer.shiftKey));
        if (plotlyClickObserved || hasPendingCoordinateGesture() || modifier
          || !!(recentPointer && recentPointer.moved)) {
          plotlyClickObserved = false;
          return;
        }
        clearSelectionIfGraphBackground();
      }, 0);
    }, true);
    container.addEventListener("mousemove", function (e) { lastClient = {x:e.clientX, y:e.clientY}; }, {passive:true});
    container.addEventListener("contextmenu", function (e) {
      const sceneId = currentSceneId();
      // ``contextmenu`` may be synthesized without a preceding mousemove.
      // Always use its own coordinates for the fallback request; carrying a
      // previous pointer position can resolve a completely different atom.
      const x = Number(e.clientX);
      const y = Number(e.clientY);
      let current = lastHovered;
      const cachedHover = lastHoverPoint;
      // A missing cache is stale.  The graph node may still expose an old
      // ``_hoverdata`` payload after Plotly recycled it, and accepting that
      // payload when ``lastHoverPoint`` is null reopens a prior target.
      const cachedHoverFresh = !!cachedHover &&
        (Date.now() - cachedHover.at <= 250 &&
         Math.hypot(x - Number(cachedHover.x || 0), y - Number(cachedHover.y || 0)) <= 64);
      // Plotly can retain a hover payload after the pointer moved away. Never
      // use one older than a quarter second for a context-menu action.
      if (lastHoverPoint && Date.now() - lastHoverPoint.at > 250) current = null;
      // The graph's hover cache describes the pointer location more reliably
      // than the browser event ordering around contextmenu. Prefer it when it
      // contains a semantic point, even if lastHovered came from an earlier
      // Plotly hover callback.
      const gd = graphDiv();
      const hoverdata = gd && gd._hoverdata;
      if (hoverdata && hoverdata.points && hoverdata.points.length) {
        const liveTarget = targetFromPoint(hoverdata.points[0]);
        if (liveTarget && cachedHoverFresh) {
          // Stamp the cache-derived target so the scene/generation guard below
          // applies to the same object that is actually under the pointer.
          noteHover(liveTarget);
          lastHoverPoint = {x:x, y:y, at:Date.now()};
          current = lastHovered;
        } else current = null;
      }
      // A context menu can arrive without a fresh hover callback (for
      // example after a fast pointer move). Do not reuse the previous target
      // when the pointer is clearly elsewhere; the coordinate preview below
      // resolves the atom at the actual context-menu location.
      if (current && lastHoverPoint &&
          Math.hypot(x - Number(lastHoverPoint.x || 0), y - Number(lastHoverPoint.y || 0)) > 64) {
        current = null;
      }
      if (current && hoverIsCurrent()) {
        e.preventDefault();
        pushTarget({kind:current.kind, payload:current, scene_id:sceneId, x:x, y:y, ts:Date.now()});
        return;
      }
      // With no usable Plotly hover payload, retain the coordinate picker as
      // a right-click fallback so an atom can still open the menu without a
      // preceding hover event.
      e.preventDefault();
      const picked = pickRect(x, y, 14);
      // This is a preview only. Right-click opens a menu and must not replace
      // the persistent selection before the user chooses a menu action.
      postSelection(picked.rect, picked.viewport, false, false).then(function (body) {
        const target = targetFromSelectionResponse(body, sceneId);
        const selection = body && body.selection;
        const labels = selection && selection.atom_labels || [];
        const parts = selection && selection.parts || [];
        const part = parts[0] || {};
        const payload = target || (labels.length ? {
          kind: "atom",
          label: labels[0],
          is_minor: !!part.is_minor,
          part: part.part || "",
          scene_id: sceneId,
        } : null);
        pushTarget({kind: payload ? payload.kind : "_global", payload: payload, scene_id: sceneId, x:x, y:y, ts:Date.now()});
      }).catch(function () {
        pushTarget({kind:"_global", payload:null, scene_id:sceneId, x:x, y:y, ts:Date.now()});
      });
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
    if (lastHovered && !hoverIsCurrent()) lastHovered = null;
    const sceneId = currentSceneId();
    return setDashStore("rightclick-target", Object.assign(
      {kind:lastHovered?lastHovered.kind:"_global", payload:lastHovered, action:action, scene_id:sceneId, ts:Date.now()},
      extra||{}
    ));
  }
  window.MatterVisShortcuts = { pushAction: pushAction, isTypingTarget: isTypingTarget, hoverIsCurrent: hoverIsCurrent };
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
    if (key==="Escape") {
      toggleHelp(false);
      // Clear the browser's per-scene target cache immediately. The Dash
      // action callback persists the same clear, but waiting for that round
      // trip leaves a stale outline visible for one render turn.
      publishSemanticSelection(null, "clear");
      if (typeof window.MatterVisInvalidateSemanticHover === "function") {
        window.MatterVisInvalidateSemanticHover();
      }
      pushAction("select_clear");
      return;
    }
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
    const viewportNode = container.querySelector(".js-plotly-plot") || container;
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
    function begin(e) {
      if (start || !e.shiftKey || (e.button !== undefined && e.button !== 0)) return;
      if (e.target && e.target.closest && e.target.closest(".mv-canvas-toolbar, .modebar")) return;
      start = {clientX:e.clientX, clientY:e.clientY};
      start.additive = true;
      start.pointerId = e.pointerId;
      updateOverlay(e);
      e.preventDefault(); e.stopPropagation();
    }
    function move(e) {
      if (!start) return;
      updateOverlay(e);
      e.preventDefault();
    }
    function finish(e, cancelled) {
      if (!start) return;
      const liveViewport = container.querySelector(".js-plotly-plot") || viewportNode;
      const gr = liveViewport.getBoundingClientRect();
      const rect = [start.clientX-gr.left, start.clientY-gr.top, e.clientX-gr.left, e.clientY-gr.top];
      if (overlay) { overlay.remove(); overlay = null; }
      const additive = !!start.additive;
      start = null;
      if (!cancelled && Math.abs(rect[2]-rect[0])>=4 && Math.abs(rect[3]-rect[1])>=4) {
        setDashStore("rightclick-target", {kind:"_global", action:"select_box", source:"shift-drag", rect_pixels:rect, viewport_size:[gr.width,gr.height], additive:additive, scene_id:currentSceneId(), ts:Date.now()});
      }
      e.preventDefault(); e.stopPropagation();
    }
    container.addEventListener("mousedown", begin, true);
    container.addEventListener("pointerdown", begin, true);
    document.addEventListener("mousemove", move, true);
    document.addEventListener("pointermove", move, true);
    document.addEventListener("mouseup", function (e) { finish(e, false); }, true);
    document.addEventListener("pointerup", function (e) { finish(e, false); }, true);
    document.addEventListener("pointercancel", function (e) { finish(e, true); }, true);
    window.addEventListener("blur", function () {
      if (!start) return;
      if (overlay) { overlay.remove(); overlay = null; }
      start = null;
    });
  }

  // ── Disorder hover (disorder_hover.js) ──────────────────────────
  let disorderHoverInstalled = false;
  function bindDisorderHover() {
    if (disorderHoverInstalled) return;
    disorderHoverInstalled = true;
    document.addEventListener("mouseover", function (event) {
      const row = event.target && event.target.closest && event.target.closest(".disorder-row");
      if (!row) return;
      setDashStore("disorder-hover-id", row.dataset.replicaId || null);
    });
    document.addEventListener("click", function (event) {
      const row = event.target && event.target.closest && event.target.closest(".disorder-row");
      if (!row) return;
      document.querySelectorAll(".disorder-row.is-selected").forEach(function (el) {
        el.classList.remove("is-selected");
      });
      row.classList.add("is-selected");
      setDashStore("disorder-selected-id", row.dataset.replicaId || null);
      setDashStore("disorder-hover-id", row.dataset.replicaId || null);
    });
  }

  document.addEventListener("click", function (event) {
    const select = event.target && event.target.closest && event.target.closest(".tab-select");
    if (select) {
      let parsed = null;
      try { parsed = JSON.parse(select.id); } catch (_) { parsed = null; }
      if (parsed && parsed.scene_id && window.dash_clientside && dash_clientside.set_props) {
        // The compact chip is the visible scene selector. Establish the
        // optimistic intent before Dash paints scene-tabs.value so an
        // arriving frame is routed to the new scene instead of the old canvas.
        if (typeof window.MatterVisSelectScene === "function") {
          window.MatterVisSelectScene(parsed.scene_id);
        }
        const status = document.getElementById("upload-status");
        if (status) status.textContent = "";
        dash_clientside.set_props("scene-tabs", {value: parsed.scene_id});
      }
      return;
    }
    const tool = event.target && event.target.closest && event.target.closest("#canvas-view-reset, #canvas-view-fit");
    if (!tool) return;
    const action = tool.id === "canvas-view-fit" ? "fit" : "reset";
    fetch("/api/v2/camera/action", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: action, scene_id: currentSceneId()}),
    }).catch(function () {});
  });

  function bindSceneTabIntent() {
    const root = document.getElementById("scene-tabs");
    if (!root || root.dataset.mvSceneIntentBound === "1") return;
    root.dataset.mvSceneIntentBound = "1";
    const initialSelection = selectedSceneTabId();
    if (initialSelection) observedSceneTabId = String(initialSelection);
    const selectionObserver = new MutationObserver(function () {
      flushQueuedSceneFigure();
    });
    selectionObserver.observe(root, {
      attributes: true,
      attributeFilter: ["class", "aria-selected"],
      subtree: true,
    });
    root.__mvSceneSelectionObserver = selectionObserver;
    root.addEventListener("pointerdown", function (event) { noteSceneTabIntent(event.target); }, true);
    root.addEventListener("mousedown", function (event) { noteSceneTabIntent(event.target); }, true);
    root.addEventListener("click", function (event) {
      noteSceneTabIntent(event.target);
      const clickedNode = event.target && event.target.closest
        ? event.target.closest("[id^='scene-tab-']")
        : null;
      const clickedSceneId = sceneIdFromTabNode(clickedNode);
      if (clickedSceneId) pendingSceneTabScrollId = String(clickedSceneId);
      scheduleSceneTabScroll(clickedSceneId);
      // A figure can arrive between pointerdown and Dash applying the tab
      // value. It is then parked in ``pendingFigureByScene`` because the old
      // tab is still selected. The click is the handoff barrier for that
      // frame; replay it immediately instead of waiting for an unrelated DOM
      // child-list mutation or a later tab click.
      flushQueuedSceneFigure();
      // Dash applies the selected class/aria-selected attribute in a later
      // mutation turn. Replay after those handoff turns as well; otherwise a
      // frame parked while the old tab was still selected can remain frozen
      // until the next unrelated click.
      [0, 50, 250].forEach(function (delay) {
        setTimeout(flushQueuedSceneFigure, delay);
      });
      // Upload progress is global UI chrome. Once the user deliberately
      // changes scenes, the previous upload result must not look like a
      // status for the newly selected structure.
      const status = document.getElementById("upload-status");
      if (status && /^(Loaded|Uploaded):/.test((status.textContent || "").trim())) {
        status.textContent = "";
        status.className = "";
      }
    }, true);
    // Dash changes the selected class/aria-selected attribute after the click
    // callback. Observe those changes so keyboard navigation and programmatic
    // uploads get the same scroll-into-view behavior as pointer clicks.
    if (typeof MutationObserver === "function") {
      const observer = new MutationObserver(function () {
        ensureSceneTabTooltips();
        syncSceneTabSelection(true);
      });
      observer.observe(root, {
        attributes: true,
        attributeFilter: ["aria-selected", "class"],
        subtree: true,
      });
    }
  }

  function showBuildMismatch() {
    if (document.getElementById("mv-build-mismatch")) return;
    var node = document.createElement("div");
    node.id = "mv-build-mismatch";
    node.setAttribute("role", "status");
    node.textContent = "This server is still running older code than the files on disk. Restart the server, then reload this page.";
    node.style.cssText = "position:sticky;top:0;z-index:10000;background:#7a1f1f;color:#fff;padding:10px 14px;font:14px/1.4 sans-serif";
    (document.body || document.documentElement).prepend(node);
  }
  function noteBuildIdentity(payload) {
    var build = window.MATTERVIS_BUILD || {};
    var processSha = (payload && payload.build_sha) || build.process;
    var assetSha = (payload && payload.asset_sha) || build.assets;
    if (processSha && assetSha && processSha !== assetSha) showBuildMismatch();
  }


  // ── WS figure fast lane (ws_figure.js) ──────────────────────────
  (function connectWS() {
    function socketCensus() {
      var census = window.__mvCensus = window.__mvCensus || {};
      census.socketsOpened = Number(census.socketsOpened || 0);
      census.socketsOpen = Number(census.socketsOpen || 0);
      census.compassBinds = Number(census.compassBinds || 0);
      return census;
    }
    function startHttpFallback() {
      if (window.__mattervisFigureFallbackActive || !window.fetch) return;
      window.__mattervisFigureFallbackActive = true;
      function parkFallbackFigure(payload) {
        if (!payload || typeof payload !== "object" || !payload.figure || !payload.scene_id) return;
        if (isPendingFigure(payload.figure) || !has3DScene(payload.figure)) return;
        /* A parked journal frame still participates in the same sequence and
         * render-revision fences as a WebSocket frame.  Without these fields
         * flushQueuedSceneFigure treats it as stale forever, and a later WS
         * snapshot can overwrite it while Dash is painting the selected tab. */
        const sceneKey = String(payload.scene_id);
        const seq = Number(payload.figure_seq || payload.seq || 0);
        const previous = pendingFigureByScene[sceneKey];
        const previousSeq = Number(previous && (previous.figure_seq || previous.seq) || 0);
        const seenSeq = Number(latestFigureSeqByScene[sceneKey] || 0);
        /* The HTTP cursor is global, but tab handoff fences are per scene.
         * A WS frame for scene A can advance the global cursor before the
         * selected scene B frame is polled; rejecting B on that cursor would
         * strand its newest frame and leave the old canvas visible forever. */
        const highWater = Math.max(seenSeq, previousSeq);
        /* Handoff replay may repeat the current high-water frame after the
         * canvas changes scenes. It must never admit an older sequence. */
        if (seq && (seq < highWater || (seq === highWater && !sceneFrameNeedsReplay(sceneKey)))) return;
        if (seq) {
          latestFigureSeqByScene[sceneKey] = Math.max(seenSeq, seq);
          window.__mattervisFigureLastSeq = Math.max(Number(window.__mattervisFigureLastSeq || 0), seq);
        }
        if (payload.state && typeof payload.state === "object") rememberExpectedRender(payload.state, true);
        if (payload.render_revision !== undefined) rememberExpectedRender(payload, true);
        payload.isCurrent = function () { return isCurrentFigurePush(payload); };
        if (!previous || !previousSeq || (seq && seq >= previousSeq)) {
          pendingFigureByScene[sceneKey] = payload;
          scheduleQueuedSceneFigureRetry(sceneKey, 0);
        }
      }
      function deliverFallbackPayload(payload) {
        if (!payload || typeof payload !== "object") return;
        /* The journal contains complete frames for every scene. During a
         * tab handoff, never submit a non-selected frame through the generic
         * view gate: it can be marked stale against the canvas scene before
         * Dash paints the selected tab. Keep the newest one parked for the
         * normal selection barrier instead. Camera/display patches continue
         * through the typed gate and are scene-scoped there. */
        if (payload.figure && payload.scene_id &&
            String(payload.scene_id) !== String(currentSceneId() || "")) {
          parkFallbackFigure(payload);
          return;
        }
        handleFigurePayload(payload);
      }
      function poll() {
        if (!window.__mattervisFigureFallbackActive) return;
        // The journal sequence is global, but a global cursor can strand a
        // scene whose newest frame has a lower sequence than another scene's
        // frame already observed by this browser. Read the current journal
        // head on every fallback poll and let the per-scene cursors in
        // ``parkFallbackFigure``/``handleFigurePayload`` suppress duplicates.
        var since = 0;
        window.fetch("/api/v2/view-updates?since=" + encodeURIComponent(since), {credentials:"same-origin"})
          .then(function(response){ return response.ok ? response.json() : null; })
          .then(function(body){
            (body && body.updates || []).forEach(deliverFallbackPayload);
          })
          .catch(function(){})
          .then(function(){
            if (window.__mattervisFigureFallbackActive) window.__mattervisFigureFallbackTimer = setTimeout(poll, 1000);
          });
      }
      poll();
    }
    function stopHttpFallback() {
      window.__mattervisFigureFallbackActive = false;
      if (window.__mattervisFigureFallbackTimer) {
        clearTimeout(window.__mattervisFigureFallbackTimer);
        window.__mattervisFigureFallbackTimer = null;
      }
    }
    function handleFigurePayload(p) {
      if (!p || typeof p !== "object") return;
      var seq = Number(p.figure_seq || p.seq || 0);
      // Figure sequence numbers are global across scenes, while delivery
      // fences are scene-scoped. A frame for scene B can legitimately have a
      // lower sequence than the last frame received for scene A; comparing
      // against only the global cursor drops B's first paint during a tab
      // handoff. Keep the global value for the HTTP journal cursor, but use a
      // per-scene cursor to decide whether this payload is a duplicate.
      const payloadScene = p.scene_id ? String(p.scene_id) : "";
      const sceneSeq = Number(payloadScene && latestFigureSeqByScene[payloadScene] || 0);
      /* Only the equal high-water frame can be replayed for a handoff; older
       * sequence numbers remain stale even while another scene is displayed. */
      if (seq && (seq < sceneSeq || (seq === sceneSeq && !sceneFrameNeedsReplay(payloadScene)))) return;
      if (seq) {
        if (payloadScene) latestFigureSeqByScene[payloadScene] = Math.max(sceneSeq, seq);
        window.__mattervisFigureLastSeq = Math.max(Number(window.__mattervisFigureLastSeq || 0), seq);
      }
      if (p.state&&typeof p.state==="object") rememberExpectedRender(p.state, true);
      if (p.scene_id&&p.render_revision!==undefined) rememberExpectedRender(p, true);
      if (p.type === "view_update" || p.type === "render_error") {
        if (window.MatterVisViewUpdates && typeof window.MatterVisViewUpdates.submit === "function") {
          window.MatterVisViewUpdates.submit(p);
        } else {
          (window.__mv_pending_view_updates = window.__mv_pending_view_updates || []).push(p);
        }
        return;
      }
      if (!p.figure||isPendingFigure(p.figure)||!has3DScene(p.figure)) return;
      if (!isCurrentFigurePush(p)) {
        // A frame can fail the fence even when its scene already matches the
        // optimistic tab intent (for example, the expected revision arrives
        // one turn before the figure). Keep the newest frame by scene for
        // every failed fence, then replay it through the same bounded handoff
        // path. Restricting this to scene-id mismatches silently dropped the
        // duplicate scene's first frame after an upload.
        if (p.scene_id) {
          const sceneKey = String(p.scene_id);
          const previous = pendingFigureByScene[sceneKey];
          const seqValue = Number(p.figure_seq || p.seq || 0);
          const previousSeq = Number(previous && (previous.figure_seq || previous.seq) || 0);
          if (!previous || !previousSeq || (seqValue && seqValue >= previousSeq)) {
            p.isCurrent=function(){return isCurrentFigurePush(p);};
            pendingFigureByScene[String(p.scene_id)] = p;
            scheduleQueuedSceneFigureRetry(sceneKey, 0);
          }
        }
        return;
      }
      clearQueuedSceneFigure(p.scene_id, seq);
      if (interactionActive) { p.isCurrent=function(){return isCurrentFigurePush(p);}; pendingFigurePush=p; return; }
      if (sceneTabIntentId && p.scene_id && String(p.scene_id) === sceneTabIntentId) sceneTabIntentAt = 0;
      // Keep a short settle guard even after a successful typed submit. Dash
      // can repaint its stale figure prop immediately after Plotly.react
      // resolves; retaining this frame lets the settle observer replay it
      // through the same ordering fence instead of losing the canvas handoff.
      if (p.scene_id) {
        const sceneKey = String(p.scene_id);
        p.isCurrent=function(){return isCurrentFigurePush(p);};
        pendingFigureByScene[sceneKey] = p;
        replayQueuedSceneFigure(sceneKey, p);
      } else if (window.MatterVisViewUpdates && typeof window.MatterVisViewUpdates.submit === "function") {
        window.MatterVisViewUpdates.submit(p);
      } else {
        applyFigurePush(p.figure.data||[], p.figure.layout||{});
      }
    }
    // Scene-tab resync lives in the outer module scope; expose the same
    // transport fence so a fetch result cannot bypass per-scene sequencing.
    window.__mvHandleFigurePayload = handleFigurePayload;
    function canUseWebSocket() {
      return window.MATTERVIS_WS_FIGURE !== false && !!window.WebSocket &&
        !window.__mattervisFigureWsDisabled;
    }
    if (!canUseWebSocket()) { startHttpFallback(); return; }
    if (window.__mattervisFigureSocket &&
        (window.__mattervisFigureSocket.readyState === 0 ||
         window.__mattervisFigureSocket.readyState === 1)) return;
    // Dash loads Plotly lazily. Missing it on the asset's first execution
    // must not permanently disable completed-frame delivery.
    if (!window.Plotly) { requestAnimationFrame(connectWS); return; }
    /* Keep the journal poll alive while the WebSocket upgrade is in flight.
     * Werkzeug and a few reverse proxies answer the upgrade URL with a plain
     * HTTP 200; Chromium can leave that socket pending long enough to hide a
     * camera update. A successful open stops the poll before subscribing, so
     * there is still one active delivery path after the handshake. */
    startHttpFallback();
    noteBuildIdentity(null);
    function isCurrentFigurePush(p) {
      if (!p || !p.figure) return false;
      var cur=currentSceneId();
      if (cur&&p.scene_id&&String(p.scene_id)!==cur) return false;
      var currentRevision=currentRenderRevision();
      var pushedRevision=Number(p.render_revision);
      return currentRevision===null || (Number.isFinite(pushedRevision)&&pushedRevision>=currentRevision);
    }
    function has3DScene(fig) {
      const lay = fig && fig.layout;
      const data = (fig && Array.isArray(fig.data)) ? fig.data : [];
      if (!lay || typeof lay!=="object"||!lay.scene||typeof lay.scene!=="object") return false;
      return data.some(function(t){var ty=String((t&&t.type)||"").toLowerCase();return ty==="mesh3d"||ty==="scatter3d"||ty==="cone"||!!(t&&t.z);});
    }
    function isPendingFigure(fig) {
      if (!fig) return false;
      if (fig._mattervis_pending) return true;
      const meta = fig.layout && fig.layout.meta;
      return !!(meta && typeof meta==="object" && meta.mattervis_pending);
    }
    var proto = window.location.protocol==="https:"?"wss:":"ws:";
    var opened = false;
    var ws;
    try {
      ws = new WebSocket(proto+"//"+window.location.host+"/api/v2/ws");
    } catch (_) {
      window.__mattervisFigureWsDisabled = true;
      startHttpFallback();
      return;
    }
    var census = socketCensus();
    census.socketsOpened += 1;
    ws.__mvCensusCountedOpen = false;
    window.__mattervisFigureSocket = ws;
    ws.addEventListener("open",function(){
      opened = true;
      if (!ws.__mvCensusCountedOpen) {
        ws.__mvCensusCountedOpen = true;
        census.socketsOpen += 1;
      }
      try { ws.send(JSON.stringify({type:"subscribe_figure",enabled:true})); }
      catch (_) { window.__mattervisFigureWsDisabled = true; startHttpFallback(); }
    });
    ws.addEventListener("message",function(e){
      var p; try { p=JSON.parse(e.data||"{}"); } catch(_){return;}
      /* A Werkzeug/proxy HTTP-200 upgrade can report ``open`` and emit a
       * plain snapshot without ever becoming a figure transport. Keep the
       * journal poll alive until a real view envelope arrives; camera and
       * display updates must not wait for the socket's eventual close. */
      if (!p || typeof p !== "object" ||
          (!p.type && !p.figure && p.figure_seq === undefined && p.seq === undefined)) return;
      stopHttpFallback();
      handleFigurePayload(p);
    });
    ws.addEventListener("error",function(){
      if (!opened) {
        // A failed upgrade is commonly surfaced as "Invalid frame header".
        // Stop reconnecting that broken endpoint and keep the HTTP journal
        // delivering frames instead of flooding the browser console.
        window.__mattervisFigureWsDisabled = true;
        startHttpFallback();
      }
    });
    ws.addEventListener("close",function(){
      if (ws.__mvCensusCountedOpen) {
        ws.__mvCensusCountedOpen = false;
        census.socketsOpen = Math.max(0, Number(census.socketsOpen || 0) - 1);
      }
      if (window.__mattervisFigureSocket === ws) window.__mattervisFigureSocket = null;
      if (!opened) {
        window.__mattervisFigureWsDisabled = true;
        startHttpFallback();
        return;
      }
      startHttpFallback();
      // A cleanly established socket may be retried once the endpoint has
      // recovered.  The fallback remains active until the new handshake is
      // confirmed, so no frame is lost during the transition.
      setTimeout(connectWS,5000);
    });
  })();

  function ensureSceneTabTooltips() {
    document.querySelectorAll("#scene-tabs [id^='scene-tab-']").forEach(function (tab) {
      if (!tab || tab.id.indexOf("scene-tab-close-") === 0 || tab.id === "scene-tabs") return;
      const text = tab.querySelector(".scene-tab-text");
      const fullName = text && (text.getAttribute("title") || text.textContent || "").trim();
      if (!fullName) return;
      // Dash may render dcc.Tab's React label inside a wrapper that does not
      // inherit the label's title. Put the native tooltip on the actual tab
      // node as well, which remains visible when labels are ellipsized.
      tab.setAttribute("title", fullName);
      tab.setAttribute("aria-label", fullName);
      if (text) text.setAttribute("title", fullName);
    });
  }

  // ── Single MutationObserver for all re-bind needs ───────────────
  window.MatterVisSelectedScene = currentSceneId;
  const trackedGL = [];
  function installWebGLLedger() {
    const proto = window.HTMLCanvasElement && HTMLCanvasElement.prototype;
    if (!proto || !proto.getContext || proto.getContext.__mvLedger) return;
    const original = proto.getContext;
    function wrapped(type) {
      const gl = original.apply(this, arguments);
      const kind = String(type || "").toLowerCase();
      if (gl && kind.indexOf("webgl") === 0) {
        this.__mvGL = gl;
        const root = document.getElementById("crystal-graph");
        if (root && root.contains(this)) this.__mvCrystalCanvas = true;
        if (this.__mvCrystalCanvas && trackedGL.indexOf(this) < 0) trackedGL.push(this);
      }
      return gl;
    }
    wrapped.__mvLedger = true;
    proto.getContext = wrapped;
  }
  installWebGLLedger();
  function liveGLCanvas(gd) {
    const scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    const plot = scene && scene.glplot;
    if (plot && plot.canvas) return plot.canvas;
    const gl = plot && plot.regl && plot.regl._gl;
    if (gl && gl.canvas) return gl.canvas;
    return null;
  }
  function loseWebGL(canvas) {
    if (!canvas || canvas.__mvContextReleased) return;
    const gl = canvas.__mvGL;
    if (!gl) return;
    canvas.__mvContextReleased = true;
    try {
      if (typeof gl.isContextLost === "function" && gl.isContextLost()) return;
      const lose = gl.getExtension("WEBGL_lose_context");
      if (lose) lose.loseContext();
    } catch (_) {}
  }
  function releaseStaleWebGL(gd) {
    const root = document.getElementById("crystal-graph");
    const live = liveGLCanvas(gd);
    if (root) {
      root.querySelectorAll("canvas").forEach(function (canvas) {
        if (canvas.__mvGL) canvas.__mvCrystalCanvas = true;
        if (canvas.__mvCrystalCanvas && trackedGL.indexOf(canvas) < 0) trackedGL.push(canvas);
      });
    }
    for (let i = trackedGL.length - 1; i >= 0; i -= 1) {
      const canvas = trackedGL[i];
      if (live && canvas === live && canvas.isConnected) continue;
      const detached = !canvas.isConnected;
      const retiredInGraph = !!(live && root && root.contains(canvas) && canvas !== live);
      if (!detached && !retiredInGraph) continue;
      /* view_camera.js listens for genuine losses on the live canvas. Mark
       * this one before deliberately releasing a retired Plotly canvas so
       * that cleanup cannot surface a spurious recovery error. */
      canvas.__mvContextRetired = true;
      loseWebGL(canvas);
      if (retiredInGraph && canvas.parentNode && typeof canvas.parentNode.removeChild === "function") {
        /* Plotly leaves replaced gl3d canvases in the graph wrapper while
         * their contexts are retired. Remove only canvases we proved are no
         * longer the live scene so rapid tab switches cannot grow the DOM. */
        try { canvas.parentNode.removeChild(canvas); } catch (_) {}
      }
      trackedGL.splice(i, 1);
    }
    if (window.__mvCensus) window.__mvCensus.webglContexts = trackedGL.length;
  }
  function rebindAll() {
    prunePersistentTargets();
    bindRightClick();
    bindBoxSelect();
    bindDisorderHover();
    bindSceneTabIntent();
    ensureSceneTabTooltips();
    syncSceneTabSelection(true);
    flushQueuedSceneFigure();
    const gd = graphDiv();
    releaseStaleWebGL(gd);
    if (gd && window.MatterVisViewCamera && typeof window.MatterVisViewCamera.installWheelZoom === "function") {
      window.MatterVisViewCamera.installWheelZoom(gd);
    }
  }
  let rebindScheduled = false;
  let rebindObserver = null;
  function scheduleRebind() {
    if (rebindScheduled) return;
    rebindScheduled = true;
    const schedule = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 16); };
    schedule(function () {
      rebindScheduled = false;
      attachRebindObserver();
      rebindAll();
    });
  }
  function attachRebindObserver() {
    const preferred = document.getElementById("viewer-root");
    const root = preferred || document.body || document.documentElement;
    if (!root || (rebindObserver && rebindObserver.__root === root)) return;
    if (rebindObserver) rebindObserver.disconnect();
    rebindObserver = new MutationObserver(scheduleRebind);
    rebindObserver.__root = root;
    rebindObserver.observe(root, { childList: true, subtree: true });
  }
  attachRebindObserver();

  // ── Pointer/wheel interaction tracking ──────────────────────────
  function stampGestureScene(sceneId, kind) {
    const id = sceneId ? String(sceneId) : "";
    if (!id) return;
    const gd = graphDiv();
    const render = renderMetadataFromLayout(gd && gd.layout);
    window.__mvGestureSceneId = id;
    window.__mvLastGestureSceneId = sceneId;
    window.__mvGestureKind = kind || "pointer";
    window.__mvGestureSceneGeneration = render && render.scene_generation !== undefined
      ? render.scene_generation : null;
    window.__mvGestureCameraRevision = render && render.camera_revision !== undefined
      ? Number(render.camera_revision || 0) : null;
    if (kind === "wheel") {
      const stamp = id + ":" + String(Date.now());
      window.__mvWheelGestureStamp = stamp;
      setTimeout(function () {
        if (window.__mvWheelGestureStamp !== stamp || window.__mvGestureKind !== "wheel") return;
        window.__mvGestureSceneId = "";
        window.__mvLastGestureSceneId = "";
        window.__mvGestureKind = "";
        window.__mvGestureSceneGeneration = null;
        window.__mvGestureCameraRevision = null;
      }, 500);
    }
  }
  document.addEventListener("pointerdown", function(e) {
    if (!inGraph(e.target)) return;
    const sceneId = canvasSceneId() || window.__mvGestureSceneId || "";
    stampGestureScene(sceneId, "pointer");
    markActive();
  }, true);
  document.addEventListener("pointerup", function() {
    if (interactionActive) markInactiveSoon();
    const stamped = window.__mvGestureSceneId || "";
    setTimeout(function() {
      if ((window.__mvGestureSceneId || "") === stamped) window.__mvGestureSceneId = "";
    }, 1000);
  });
  document.addEventListener("pointercancel", function() { if (interactionActive) markInactiveSoon(); });
  document.addEventListener("wheel", function(e) {
    if (!inGraph(e.target)) return;
    // Wheel zoom does not necessarily emit a pointerdown after a scene-tab
    // switch. Refresh both gesture fences from the live canvas so a zoom on
    // the newly selected scene is persisted instead of being mistaken for a
    // delayed relayout from the previous scene.
    const sceneId = canvasSceneId() || window.__mvGestureSceneId || "";
    stampGestureScene(sceneId, "wheel");
    markActive();
    markInactiveSoon();
  }, {passive:true, capture:true});
  window.addEventListener("blur", function() { if (interactionActive) { interactionActive = false; setInteraction(false); flushPendingFigurePush(); } });

  // ── Init ────────────────────────────────────────────────────────
  function init() { rebindAll(); }
  if (document.readyState==="loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
/* Live compass overlay rendered in a *separate* SVG layered over the
 * graph div. Avoids ALL Plotly.relayout calls for compass updates,
 * which is what was previously freezing the gl3d render mid-drag.
 *
 * Why a separate SVG, not Plotly annotations:
 *
 *   The previous version updated compass arrows by calling
 *   ``Plotly.relayout(gd, { annotations, shapes })`` on every drag
 *   frame. Plotly's relayout machinery, on a 3D scene, ends up calling
 *   ``scene.draw()`` -> ``setCameraPosition(layout.scene.camera)``
 *   *during* the user's rotation drag. That overwrites the live
 *   gl-plot-3d ``view._matrix`` accumulated by the orbit controller
 *   with the LAST COMMITTED camera (the default eye, since Plotly
 *   does not commit to ``layout.scene.camera`` until mouseup -- see
 *   plotly/plotly.js#6359). The visible symptom was "the molecule
 *   doesn't rotate during drag, only snaps after release"; verified
 *   by Playwright (8 mid-drag screenshots all byte-identical).
 *
 *   Doing the compass in a sibling SVG means we never call Plotly
 *   while the user is dragging. The GL canvas is free to render every
 *   frame; we just paint our arrows on top in a layer Plotly never
 *   touches.
 *
 * The Python ``axis_key_overlay`` still bakes a Plotly-annotation
 * compass into the figure layout for *static* export (kaleido /
 * orbital panels / scripts/). On the live page we strip those
 * Plotly compass entries ONCE at attach time so they don't visually
 * collide with the SVG overlay; subsequent updates touch only the
 * SVG.
 */
(function () {
  // Sentinel value the Python ``axis_key_overlay`` writes into
  // ``annotation.name`` / ``shape.name``. Used both to strip the
  // Plotly-side compass once at attach and to identify our own SVG
  // child nodes for replacement.
  const COMPASS_ITEM_NAME = "mv_compass";
  const SVG_LAYER_ID = "mv-compass-svg";

  // Diagnostic touchpoint. Read in DevTools as ``window.__mv_compass_diag``.
  if (!window.__mv_compass_diag) {
    window.__mv_compass_diag = {
      iife_loaded: 0,
      maybe_attach_called: 0,
      maybe_attach_attached: 0,
      drag_poll_starts: 0,
      drag_poll_ticks: 0,
      drag_poll_redraws: 0,
      svg_redraws: 0,
      svg_redraws_with_camera: 0,
      svg_redraws_while_drag: 0,
      svg_redraws_with_event_camera: 0,
      svg_redraws_with_layout_camera: 0,
      svg_redraws_with_live_camera: 0,
      strip_attempts: 0,
      strip_completed: 0,
      last_attach_skip_reason: null,
    };
  }
  window.__mv_compass_diag.svg_redraws_while_drag = window.__mv_compass_diag.svg_redraws_while_drag || 0;
  window.__mv_compass_diag.svg_redraws_with_event_camera = window.__mv_compass_diag.svg_redraws_with_event_camera || 0;
  window.__mv_compass_diag.iife_loaded += 1;

  function graphRoot() { return document.getElementById("crystal-graph"); }
  function graphDiv() {
    const root = graphRoot();
    return root ? root.querySelector(".js-plotly-plot") : null;
  }

  function cross(a, b) {
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function scale(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }
  function norm3(v) { return Math.hypot(v[0], v[1], v[2]); }

  function xyzFrom(obj, fallback) {
    if (!obj) return fallback.slice();
    if (Array.isArray(obj)) return [Number(obj[0]) || 0, Number(obj[1]) || 0, Number(obj[2]) || 0];
    function coord(value, fallbackValue) {
      if (value === undefined || value === null) return fallbackValue;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : fallbackValue;
    }
    return [
      coord(obj.x, fallback ? fallback[0] : 0),
      coord(obj.y, fallback ? fallback[1] : 0),
      coord(obj.z, fallback ? fallback[2] : 0),
    ];
  }

  function cameraScreenBasis(camera) {
    if (!camera || !camera.eye || !camera.up) return null;
    const eye = xyzFrom(camera.eye, [0, 0, 1]);
    const center = xyzFrom(camera.center, [0, 0, 0]);
    const up = xyzFrom(camera.up, [0, 1, 0]);
    let view = sub(center, eye);
    let n = norm3(view);
    if (n < 1e-12) return null;
    view = scale(view, 1 / n);
    let right = cross(view, up);
    let rn = norm3(right);
    if (rn < 1e-12) return null;
    right = scale(right, 1 / rn);
    let screenUp = cross(right, view);
    const sn = norm3(screenUp);
    if (sn < 1e-12) return null;
    screenUp = scale(screenUp, 1 / sn);
    return { right: right, screenUp: screenUp };
  }

  function projectLattice(M, basis, cubeScale) {
    if (!M || !basis) return null;
    const out = [];
    for (let i = 0; i < 3; i++) {
      const row = M[i];
      if (!row || row.length < 3) return null;
      let v;
      if (cubeScale && cubeScale.length === 3) {
        const sx = cubeScale[0] || 1;
        const sy = cubeScale[1] || 1;
        const sz = cubeScale[2] || 1;
        v = [row[0] / sx, row[1] / sy, row[2] / sz];
      } else {
        v = row;
      }
      const n = Math.hypot(v[0], v[1], v[2]);
      if (!isFinite(n) || n < 1e-12) return null;
      v = [v[0] / n, v[1] / n, v[2] / n];
      out.push([dot(v, basis.right), dot(v, basis.screenUp)]);
    }
    return out;
  }

  function compassFromMeta(layout) {
    if (!layout || !layout.meta) return null;
    let meta = layout.meta;
    if (typeof meta === "string") {
      try { meta = JSON.parse(meta); } catch (err) { return null; }
    }
    return (meta && (meta.compass || meta.compass_context))
      ? (meta.compass || meta.compass_context)
      : null;
  }

  function hasCompleteCamera(camera) {
    return !!(camera && camera.eye && camera.center && camera.up);
  }

  function layoutSceneCamera(gd) {
    if (!gd) return null;
    const layoutScene = gd.layout && gd.layout.scene ? gd.layout.scene : null;
    if (layoutScene && hasCompleteCamera(layoutScene.camera)) return layoutScene.camera;
    const fullScene = gd._fullLayout && gd._fullLayout.scene ? gd._fullLayout.scene : null;
    if (fullScene && hasCompleteCamera(fullScene.camera)) return fullScene.camera;
    return null;
  }

  /* SVG layer management.
   *
   * The layer is parented to ``#crystal-graph`` (the wrapper div),
   * positioned absolute so it stretches over the Plotly graph div,
   * and ``pointer-events: none`` so mousedown/wheel still reach the
   * gl3d controller underneath. Z-index is high enough to sit above
   * Plotly's modebar but below the dash floating tooltips.
   */
  function ensureSvgLayer() {
    const root = graphRoot();
    if (!root) return null;
    let svg = root.querySelector("#" + SVG_LAYER_ID);
    if (svg) return svg;
    /* The wrapper must be position:relative so our absolute SVG
       inherits its frame. Plotly leaves ``#crystal-graph`` untyped,
       so set it once. Repeating the assignment is harmless and idempotent. */
    if (!root.style.position || root.style.position === "static") {
      root.style.position = "relative";
    }
    svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("id", SVG_LAYER_ID);
    svg.style.position = "absolute";
    svg.style.left = "0";
    svg.style.top = "0";
    svg.style.width = "100%";
    svg.style.height = "100%";
    svg.style.pointerEvents = "none";
    svg.style.zIndex = "5";
    /* SVG coordinate system stays in pixel space matching the graph
       div's bounding box (set viewBox on resize). Children use plain
       pixel coords. */
    root.appendChild(svg);
    return svg;
  }

  function clearSvg(svg) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
  }

  function svgEl(name, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", name);
    if (attrs) {
      for (const k in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, k)) el.setAttribute(k, attrs[k]);
      }
    }
    return el;
  }

  /* Render compass into the SVG layer.
   *
   * Coordinate convention: SVG coordinates here are pixel coords
   * relative to the GRAPH DIV (which is itself positioned inside the
   * wrapper). We compute the graph div's bounding box vs the wrapper
   * to translate. Y increases downward in pixel space, so the screen-up
   * vector contributes a negative dy.
   */
  function drawCompassSvg(svg, ctx, projections) {
    const root = graphRoot();
    const gd = graphDiv();
    if (!root || !gd) return;
    const rRect = root.getBoundingClientRect();
    const gRect = gd.getBoundingClientRect();
    const offsetX = gRect.left - rRect.left;
    const offsetY = gRect.top - rRect.top;
    const figW = gRect.width || 1024;
    const figH = gRect.height || 720;

    /* Set viewBox to wrapper pixel space so our pixel coords map 1:1. */
    svg.setAttribute("viewBox", "0 0 " + Math.max(1, rRect.width) + " " + Math.max(1, rRect.height));
    svg.setAttribute("width", String(rRect.width));
    svg.setAttribute("height", String(rRect.height));

    const labels = ctx.labels || ["a", "b", "c"];
    const colors = ctx.colors || ["#2F2F2F", "#2F2F2F", "#2F2F2F"];
    const anchor = ctx.anchor || [0.08, 0.12];
    const anchorX = Number(anchor[0]);
    const anchorY = Number(anchor[1]);
    const pixelLength = Number(ctx.pixel_length || 50);
    const lineWidth = Number(ctx.line_width || 2);
    const labelOffset = Number(ctx.label_pixel_offset || 10);
    const fontSize = Number(ctx.font_size || 14);
    const italic = !!ctx.italic;
    const dotThreshold = Number(ctx.dot_threshold || 0.05);
    const dotRadius = Number(ctx.dot_radius_px || 4);
    const arrowhead = Math.max(2, Number(ctx.arrowhead || 3));

    const norms = projections.map((p) => Math.hypot(p[0], p[1]));
    const maxNorm = Math.max.apply(null, norms);
    if (!isFinite(maxNorm) || maxNorm < 1e-8) {
      clearSvg(svg);
      return;
    }

    /* Anchor pixel coords inside the graph div, then translated into
       wrapper pixel coords by adding offsetX/offsetY. Y is in
       SVG-pixel-down convention; Python compass uses paper coords with
       y going up, so we flip when applying the screen-up basis. */
    const anchorPx = offsetX + anchorX * figW;
    const anchorPy = offsetY + (1 - anchorY) * figH;

    /* Same anchor-aware arrow-length cap as the Python overlay so the
       longest arrow + label stay inside the figure. */
    const edgeMarginPx = 8.0;
    const availLeft = Math.max(anchorX * figW - edgeMarginPx, 1.0);
    const availRight = Math.max((1 - anchorX) * figW - edgeMarginPx, 1.0);
    const availDown = Math.max(anchorY * figH - edgeMarginPx, 1.0);
    const availUp = Math.max((1 - anchorY) * figH - edgeMarginPx, 1.0);
    let cap = pixelLength;
    for (let i = 0; i < projections.length; i++) {
      const n = norms[i];
      if (n < 1e-12) continue;
      const ux = projections[i][0] / n;
      const uy = projections[i][1] / n;
      const rel = n / maxNorm;
      const wanted = (pixelLength + labelOffset) * rel;
      if (wanted <= 0) continue;
      let allowed = Infinity;
      if (ux > 0) allowed = Math.min(allowed, availRight / ux);
      else if (ux < 0) allowed = Math.min(allowed, availLeft / -ux);
      if (uy > 0) allowed = Math.min(allowed, availUp / uy);
      else if (uy < 0) allowed = Math.min(allowed, availDown / -uy);
      if (allowed < wanted) cap = Math.min(cap, pixelLength * (allowed / wanted));
    }
    const effectivePixelLength = Math.max(cap, 12.0);
    const scalePx = effectivePixelLength / maxNorm;

    clearSvg(svg);

    /* Shared dot anchor circle so the three arrows look like a triad
       (Python compass does this implicitly via shared anchor). Drawn
       once, regardless of arrow vs dot mode. */
    svg.appendChild(svgEl("circle", {
      cx: String(anchorPx),
      cy: String(anchorPy),
      r: "1.4",
      fill: "#2F2F2F",
      "fill-opacity": "0.75",
    }));

    for (let i = 0; i < labels.length && i < 3; i++) {
      const label = labels[i];
      const color = colors[i] || colors[0] || "#2F2F2F";
      const dxWorld = projections[i][0];
      const dyWorld = projections[i][1];
      const norm = norms[i];
      const rel = maxNorm > 0 ? norm / maxNorm : 0;

      if (rel < dotThreshold) {
        svg.appendChild(svgEl("circle", {
          cx: String(anchorPx),
          cy: String(anchorPy),
          r: String(dotRadius),
          fill: color,
        }));
        const labelPx = anchorPx + (dotRadius + labelOffset);
        const labelPy = anchorPy - (dotRadius + labelOffset);
        const text = svgEl("text", {
          x: String(labelPx),
          y: String(labelPy),
          fill: color,
          "font-size": String(fontSize),
          "font-family": "sans-serif",
          "font-style": italic ? "italic" : "normal",
          "text-anchor": "start",
          "dominant-baseline": "alphabetic",
        });
        text.textContent = label;
        svg.appendChild(text);
        continue;
      }

      /* dyWorld is in screen-up basis (positive = up). SVG y points
         down. Flip sign when laying out arrow tip in pixel space. */
      const dxPx = dxWorld * scalePx;
      const dyPx = dyWorld * scalePx;
      const tipX = anchorPx + dxPx;
      const tipY = anchorPy - dyPx;

      svg.appendChild(svgEl("line", {
        x1: String(anchorPx),
        y1: String(anchorPy),
        x2: String(tipX),
        y2: String(tipY),
        stroke: color,
        "stroke-width": String(lineWidth),
        "stroke-linecap": "round",
      }));

      /* Arrowhead as a small triangle aligned with the line direction.
         Plotly's ``arrowhead=3`` is roughly an open chevron at ~12px;
         we approximate with a filled triangle of the same scale so it
         still reads as a 3D triad's tip. */
      const lenPx = Math.hypot(dxPx, dyPx) || 1;
      const ux = dxPx / lenPx;
      const uy = -dyPx / lenPx; // SVG-down adjusted
      const arrowLen = 5 + arrowhead;
      const arrowWidth = 3 + arrowhead * 0.5;
      const baseX = tipX - ux * arrowLen;
      const baseY = tipY - uy * arrowLen;
      const perpX = -uy;
      const perpY = ux;
      const ax1 = baseX + perpX * arrowWidth;
      const ay1 = baseY + perpY * arrowWidth;
      const ax2 = baseX - perpX * arrowWidth;
      const ay2 = baseY - perpY * arrowWidth;
      svg.appendChild(svgEl("polygon", {
        points: tipX + "," + tipY + " " + ax1 + "," + ay1 + " " + ax2 + "," + ay2,
        fill: color,
      }));

      /* Label sits past the tip along the projection direction. */
      const labelPx = tipX + ux * labelOffset;
      const labelPy = tipY + uy * labelOffset;
      const text = svgEl("text", {
        x: String(labelPx),
        y: String(labelPy),
        fill: color,
        "font-size": String(fontSize),
        "font-family": "sans-serif",
        "font-style": italic ? "italic" : "normal",
        "text-anchor": "middle",
        "dominant-baseline": "central",
      });
      text.textContent = label;
      svg.appendChild(text);
    }
  }

  /* Camera-acquisition helpers ported from the previous version.
   * Documentation of WHY ``intScene.getCamera()`` is the live source
   * of truth lives below the function for searchability. */
  function liveSceneCamera(gd) {
    /* Plotly v3 source (src/plots/gl3d/scene.js):
     *   proto.getCamera = function() {
     *     scene.camera.view.recalcMatrix(scene.camera.view.lastT());
     *     return getLayoutCamera(scene.camera);
     *   };
     * Reading this is the only way to get the matrix-after-recalc
     * camera during a rotation drag; ``layout.scene.camera`` only
     * commits on mouseup.
     */
    if (!gd) return null;
    const full = gd._fullLayout || null;
    const sceneFull = full ? full.scene : null;
    const intScene = sceneFull ? sceneFull._scene : null;
    if (intScene && typeof intScene.getCamera === "function") {
      try {
        const c = intScene.getCamera();
        if (c && c.eye && c.center && c.up) return c;
      } catch (err) { /* fall through */ }
    }
    /* Manual recalcMatrix fallback for hypothetical Plotly versions
       that drop ``getCamera`` from the proto. */
    const ctrl = intScene ? (intScene.camera || (intScene.glplot && intScene.glplot.camera) || null) : null;
    if (ctrl && ctrl.view && typeof ctrl.view.recalcMatrix === "function" && typeof ctrl.view.lastT === "function") {
      try {
        ctrl.view.recalcMatrix(ctrl.view.lastT());
        const e = ctrl.eye, c = ctrl.center, u = ctrl.up;
        if (e && c && u) {
          return {
            eye:    { x: e[0], y: e[1], z: e[2] },
            center: { x: c[0], y: c[1], z: c[2] },
            up:     { x: u[0], y: u[1], z: u[2] },
            projection: { type: ctrl._ortho ? "orthographic" : "perspective" },
          };
        }
      } catch (err) { /* fall through */ }
    }
    if (sceneFull && sceneFull.camera && sceneFull.camera.eye) return sceneFull.camera;
    if (gd.layout && gd.layout.scene && gd.layout.scene.camera) return gd.layout.scene.camera;
    return null;
  }

  // Share the existing live camera reader with the frame gate; no second
  // camera snapshot or stale _fullLayout-only acquisition path.
  window.mattervisCurrentCamera = liveSceneCamera;

  function redrawCompass(gd, eventCamera, preferLiveCamera) {
    if (window.__mv_compass_diag) window.__mv_compass_diag.svg_redraws += 1;
    if (!gd || !gd.layout) return;
    if (window.__mv_axes_enabled === false) {
      const root = graphRoot();
      const svg = root ? root.querySelector("#" + SVG_LAYER_ID) : null;
      if (svg) clearSvg(svg);
      return;
    }
    const ctx = compassFromMeta(gd.layout);
    if (window.__mv_axes_enabled !== true && ctx && ctx.enabled === false) {
      const root = graphRoot();
      const svg = root ? root.querySelector("#" + SVG_LAYER_ID) : null;
      if (svg) clearSvg(svg);
      return;
    }
    if (!ctx || !ctx.M) {
      // Axes off (or a scene without a lattice) must also remove the previous
      // SVG overlay; an early return otherwise leaves stale arrows on screen.
      const root = graphRoot();
      const svg = root ? root.querySelector("#" + SVG_LAYER_ID) : null;
      if (svg) clearSvg(svg);
      return;
    }
    const dragActive = dragPollActive || !!dragArm;
    const preferLive = !!preferLiveCamera || dragActive;
    let camera = eventCamera || null;
    let cameraSource = camera ? "event" : null;
    if (!camera && preferLive) {
      camera = liveSceneCamera(gd);
      cameraSource = camera ? "live" : null;
    }
    if (!camera && !preferLive) {
      camera = layoutSceneCamera(gd);
      cameraSource = camera ? "layout" : null;
    }
    if (!camera && !preferLive) {
      camera = liveSceneCamera(gd);
      cameraSource = camera ? "live" : null;
    }
    if (!camera && preferLive) {
      camera = layoutSceneCamera(gd);
      cameraSource = camera ? "layout" : null;
    }
    if (!camera) return;
    if (window.__mv_compass_diag) window.__mv_compass_diag.svg_redraws_with_camera += 1;
    if (window.__mv_compass_diag && dragActive) window.__mv_compass_diag.svg_redraws_while_drag += 1;
    if (window.__mv_compass_diag && cameraSource === "event") window.__mv_compass_diag.svg_redraws_with_event_camera += 1;
    if (window.__mv_compass_diag && cameraSource === "layout") window.__mv_compass_diag.svg_redraws_with_layout_camera += 1;
    if (window.__mv_compass_diag && cameraSource === "live") window.__mv_compass_diag.svg_redraws_with_live_camera += 1;
    const basis = cameraScreenBasis(camera);
    if (!basis) return;
    const projections = projectLattice(ctx.M, basis, ctx.cube_scale);
    if (!projections) return;
    const svg = ensureSvgLayer();
    if (!svg) return;
    drawCompassSvg(svg, ctx, projections);
  }

  /* rAF coalesce: a high-frequency event burst still does at most
     one redraw per frame. */
  let scheduled = null;
  let scheduledGd = null;
  let scheduledCamera = null;
  function scheduleRedraw(gd, camera) {
    scheduledGd = gd;
    scheduledCamera = camera;
    if (scheduled !== null) return;
    scheduled = (window.requestAnimationFrame || window.setTimeout)(function () {
      const g = scheduledGd;
      const c = scheduledCamera;
      scheduled = null;
      scheduledGd = null;
      scheduledCamera = null;
      redrawCompass(g, c, dragPollActive || !!dragArm);
    });
  }

  /* During-drag camera poll: Plotly does not commit camera to layout
     until mouseup, so we read from gl-plot-3d every frame. */
  let dragPollActive = false;
  let dragPollRaf = null;
  let dragPollLastKey = null;
  let dragArm = null;
  const DRAG_POLL_THRESHOLD_PX = 3;
  function dragCameraKey(camera) {
    if (!camera) return null;
    function v(obj) {
      if (!obj) return ["?", "?", "?"];
      if (Array.isArray(obj)) return [obj[0], obj[1], obj[2]];
      return [obj.x, obj.y, obj.z];
    }
    return v(camera.eye).concat(v(camera.up), v(camera.center)).join(",");
  }

  function cameraFromRelayout(eventData) {
    if (!eventData || typeof eventData !== "object") return null;
    if (hasCompleteCamera(eventData["scene.camera"])) return eventData["scene.camera"];
    if (eventData.scene && hasCompleteCamera(eventData.scene.camera)) return eventData.scene.camera;
    const base = {};
    let changed = false;
    const groups = ["eye", "center", "up"];
    const axes = ["x", "y", "z"];
    for (let gi = 0; gi < groups.length; gi++) {
      const group = groups[gi];
      for (let ai = 0; ai < axes.length; ai++) {
        const axis = axes[ai];
        const key = "scene.camera." + group + "." + axis;
        if (Object.prototype.hasOwnProperty.call(eventData, key)) {
          if (!base[group]) base[group] = {};
          base[group][axis] = Number(eventData[key]);
          changed = true;
        }
      }
    }
    return changed && hasCompleteCamera(base) ? base : null;
  }

  function dragPollTick(gd) {
    if (!dragPollActive) return;
    if (window.__mv_compass_diag) window.__mv_compass_diag.drag_poll_ticks += 1;
    const camera = liveSceneCamera(gd);
    const key = dragCameraKey(camera);
    if (key && key !== dragPollLastKey) {
      dragPollLastKey = key;
      if (window.__mv_compass_diag) window.__mv_compass_diag.drag_poll_redraws += 1;
      redrawCompass(gd, camera, true);
    }
    dragPollRaf = window.requestAnimationFrame
      ? window.requestAnimationFrame(function () { dragPollTick(gd); })
      : window.setTimeout(function () { dragPollTick(gd); }, 16);
  }
  function startDragPoll(gd) {
    if (dragPollActive) return;
    dragPollActive = true;
    dragPollLastKey = null;
    if (window.__mv_compass_diag) window.__mv_compass_diag.drag_poll_starts += 1;
    dragPollTick(gd);
  }
  function pointerXY(event) {
    if (!event) return null;
    const x = Number(event.clientX);
    const y = Number(event.clientY);
    return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
  }
  function armDragPoll(event) {
    if (event && event.button !== undefined && event.button !== 0) return;
    const xy = pointerXY(event);
    if (!xy) return;
    dragArm = { x: xy[0], y: xy[1] };
  }
  function maybeStartDragPollFromMove(gd, event) {
    if (!dragArm || dragPollActive) return;
    const xy = pointerXY(event);
    if (!xy) return;
    const dx = xy[0] - dragArm.x;
    const dy = xy[1] - dragArm.y;
    if (Math.hypot(dx, dy) < DRAG_POLL_THRESHOLD_PX) return;
    startDragPoll(gd);
  }
  function clearDragArm() {
    dragArm = null;
  }
  function stopDragPoll(gd) {
    if (!dragPollActive) return;
    dragPollActive = false;
    if (dragPollRaf !== null) {
      if (window.cancelAnimationFrame) window.cancelAnimationFrame(dragPollRaf);
      else window.clearTimeout(dragPollRaf);
      dragPollRaf = null;
    }
    /* Wheel zoom does not reliably commit layout.scene.camera before this
       debounce fires. Finish from the live WebGL camera so the compass does
       not snap back to the pre-wheel layout camera. */
    const finalCamera = liveSceneCamera(gd);
    redrawCompass(gd, finalCamera, true);
  }
  let wheelStopTimer = null;
  function pulseDragPollOnWheel(gd) {
    startDragPoll(gd);
    if (wheelStopTimer !== null) clearTimeout(wheelStopTimer);
    wheelStopTimer = setTimeout(function () {
      wheelStopTimer = null;
      stopDragPoll(gd);
    }, 250);
  }

  /* Strip Python compass decorations whenever a layout generation puts
     them back. Identity of the graph div is not enough: Plotly.react
     reuses the same div and can restore annotations. */
  function layoutHasCompass(gd) {
    if (!gd || !gd.layout) return false;
    const annotations = Array.isArray(gd.layout.annotations) ? gd.layout.annotations : [];
    const shapes = Array.isArray(gd.layout.shapes) ? gd.layout.shapes : [];
    return annotations.some(function (a) { return a && a.name === COMPASS_ITEM_NAME; })
      || shapes.some(function (s) { return s && s.name === COMPASS_ITEM_NAME; });
  }
  function stripPlotlyCompassOnce(gd) {
    if (!gd || gd.__mvCompassStripping || !layoutHasCompass(gd)) return;
    if (!window.Plotly || typeof window.Plotly.relayout !== "function") return;
    if (window.__mv_compass_diag) window.__mv_compass_diag.strip_attempts += 1;
    const annotations = Array.isArray(gd.layout.annotations) ? gd.layout.annotations : [];
    const shapes = Array.isArray(gd.layout.shapes) ? gd.layout.shapes : [];
    const filteredAnn = annotations.filter(function (a) { return !(a && a.name === COMPASS_ITEM_NAME); });
    const filteredShapes = shapes.filter(function (s) { return !(s && s.name === COMPASS_ITEM_NAME); });
    gd.__mvCompassStripping = true;
    try {
      const result = window.Plotly.relayout(gd, { annotations: filteredAnn, shapes: filteredShapes });
      if (result && typeof result.finally === "function") {
        result.finally(function () { gd.__mvCompassStripping = false; });
      } else {
        gd.__mvCompassStripping = false;
      }
      if (window.__mv_compass_diag) window.__mv_compass_diag.strip_completed += 1;
    } catch (err) {
      gd.__mvCompassStripping = false;
    }
  }

  const attached = new WeakSet();

  function maybeAttach(gd) {
    if (window.__mv_compass_diag) window.__mv_compass_diag.maybe_attach_called += 1;
    if (!gd) {
      if (window.__mv_compass_diag) window.__mv_compass_diag.last_attach_skip_reason = "no_gd";
      return;
    }
    if (attached.has(gd)) {
      if (window.__mv_compass_diag) window.__mv_compass_diag.last_attach_skip_reason = "already_attached";
      return;
    }
    if (typeof gd.on !== "function") {
      if (window.__mv_compass_diag) window.__mv_compass_diag.last_attach_skip_reason = "no_gd_on";
      return;
    }
    attached.add(gd);
    window.__mvCensus = window.__mvCensus || {socketsOpened: 0, socketsOpen: 0, compassBinds: 0};
    window.__mvCensus.compassBinds += 1;
    if (window.__mv_compass_diag) window.__mv_compass_diag.maybe_attach_attached += 1;

    /* Plotly events: mouseup commit and (when emitted) per-frame
       drag updates. Both go to scheduleRedraw which coalesces. */
    gd.on("plotly_relayout", function (eventData) { scheduleRedraw(gd, cameraFromRelayout(eventData)); });
    gd.on("plotly_relayouting", function (eventData) { scheduleRedraw(gd, cameraFromRelayout(eventData)); });
    gd.on("plotly_afterplot", function () { redrawCompass(gd, null, true); });

    /* DOM-level drag arming. Window-level mouseup so we still
       disarm if the user releases outside the graph div. */
    const onMouseDown = function (event) { armDragPoll(event); };
    const onMouseMove = function (event) { maybeStartDragPollFromMove(gd, event); };
    const onMouseUp = function () { clearDragArm(); stopDragPoll(gd); };
    const onPointerCancel = function () { clearDragArm(); stopDragPoll(gd); };
    const onWheel = function () { pulseDragPollOnWheel(gd); };
    if (gd.addEventListener) {
      gd.addEventListener("mousedown", onMouseDown);
      gd.addEventListener("pointerdown", onMouseDown);
      gd.addEventListener("wheel", onWheel, { passive: true });
    }
    if (window.addEventListener) {
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("pointermove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
      window.addEventListener("pointerup", onMouseUp);
      window.addEventListener("blur", onPointerCancel);
      window.addEventListener("pointercancel", onPointerCancel);
    }
    /* Resize redraw: SVG viewBox tracks the wrapper rect. */
    if (window.addEventListener) {
      window.addEventListener("resize", function () { scheduleRedraw(gd, null); });
    }

    /* First-paint: strip Plotly compass, then draw SVG. The strip
       runs through Plotly.relayout exactly ONCE; that one relayout
       happens before any drag, so it cannot interfere with a live
       rotation. */
    setTimeout(function () {
      stripPlotlyCompassOnce(gd);
      redrawCompass(gd, null, false);
    }, 0);
  }

  function tick() { maybeAttach(graphDiv()); }

  let scopedObserver = null;
  let bodyObserver = null;
  let attachRetryTimer = null;
  let attachRetryDeadline = 0;
  function attachRetryTick() {
    attachRetryTimer = null;
    const before = window.__mv_compass_diag ? window.__mv_compass_diag.maybe_attach_attached : -1;
    tick();
    const after = window.__mv_compass_diag ? window.__mv_compass_diag.maybe_attach_attached : -1;
    if (after > before) {
      /* Schedule one more redraw a tick later so the gl3d scene's
         _scene is fully built before we sample its camera. */
      setTimeout(function () { redrawCompass(graphDiv(), null, false); }, 200);
      return;
    }
    if (Date.now() > attachRetryDeadline) return;
    attachRetryTimer = window.setTimeout(attachRetryTick, 200);
  }
  function startAttachRetry() {
    attachRetryDeadline = Date.now() + 15000;
    if (attachRetryTimer === null) attachRetryTick();
  }
  function bindObservers() {
    const root = graphRoot();
    if (root) {
      if (bodyObserver) { bodyObserver.disconnect(); bodyObserver = null; }
      if (!scopedObserver) {
        scopedObserver = new MutationObserver(function () {
          tick();
          /* On any DOM mutation in the graph wrapper (Dash callback,
             scene swap, ...) we need to re-strip and re-draw because
             Plotly may have reinjected the compass annotations. */
          const gd = graphDiv();
          stripPlotlyCompassOnce(gd);
          scheduleRedraw(gd, null);
        });
        scopedObserver.observe(root, { childList: true, subtree: true });
      }
      startAttachRetry();
      return;
    }
    if (!bodyObserver) {
      bodyObserver = new MutationObserver(function () {
        if (graphRoot()) bindObservers();
      });
      bodyObserver.observe(document.body, { childList: true, subtree: true });
    }
  }

  /* Explicit bridge used by view_compass.js. */
  window.MatterVisCompass = {
    redraw: function (gd, camera, preferLive) { redrawCompass(gd, camera, preferLive); },
    clear: function () {
      var root = graphRoot();
      var svg = root ? root.querySelector("#" + SVG_LAYER_ID) : null;
      if (svg) clearSvg(svg);
    },
    strip: function (gd) { stripPlotlyCompassOnce(gd); },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindObservers);
  } else {
    bindObservers();
  }
})();
