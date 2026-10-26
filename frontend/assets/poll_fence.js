/* Drop a state-poll response that would land on top of a newer edit or tab.

``sync_agent_state`` preserves sidebar drafts it can see in the request, and
it refuses a poll whose browser has already recorded a newer scene or
generation. This script covers the remaining window: the poll request left
the browser, the user then switched tabs or edited, and the response
arrives afterwards. A poll sent while that edit's callback is still
unacknowledged is dropped too, because the server may have answered from
the pre-edit snapshot. Dash would apply that response directly, including
``agent-state-store`` (groups, transforms, polyhedra, selection) and the
shared sidebar. Rewriting the payload to an empty update leaves the newer
interaction in place. The next poll carries the new generation.

A tab click or upload batched into the same request is that interaction's
own sync and must still apply.
*/
(function (root) {
  var MODIFIERS = {Shift: 1, Control: 1, Alt: 1, Meta: 1};

  var UNACKED_RELEASE_MS = 800;

  function liveFence() {
    return {
      epoch: Number(root.__mvUiEpoch || 0),
      scene: root.__mvSelectedSceneId ? String(root.__mvSelectedSceneId) : "",
      unacked: Number(root.__mvUnackedEpoch || 0),
    };
  }

  function sceneIdFromNode(node) {
    if (!node || !node.closest) return "";
    var select = node.closest(".tab-select");
    if (select && select.id) {
      try {
        var parsed = JSON.parse(select.id);
        if (parsed && parsed.scene_id) return String(parsed.scene_id);
      } catch (error) {
        /* Pattern ids are JSON; a plain id is handled below. */
      }
    }
    var tab = node.closest("[id^='scene-tab-']");
    if (!tab || !tab.id || tab.id === "scene-tabs") return "";
    if (tab.id.indexOf("scene-tab-close-") === 0) return "";
    if (tab.id.indexOf("scene-tab-label-") === 0) {
      return tab.id.slice("scene-tab-label-".length);
    }
    return tab.id.slice("scene-tab-".length);
  }

  function armUnackedRelease(epoch) {
    if (typeof root.setTimeout !== "function") return;
    root.setTimeout(function () {
      if (Number(root.__mvUnackedEpoch || 0) !== Number(epoch || 0)) return;
      if (Number(root.__mvUserWrites || 0) > 0) return;
      root.__mvUnackedEpoch = 0;
    }, UNACKED_RELEASE_MS);
  }

  function finishUserWrite(startedEpoch) {
    root.__mvUserWrites = Math.max(0, Number(root.__mvUserWrites || 1) - 1);
    if (Number(root.__mvUserWrites || 0) > 0) return;
    if (Number(root.__mvUiEpoch || 0) !== Number(startedEpoch || 0)) return;
    root.__mvUnackedEpoch = 0;
  }

  function noteInteraction(sceneId) {
    root.__mvUiEpoch = Number(root.__mvUiEpoch || 0) + 1;
    if (sceneId) root.__mvSelectedSceneId = String(sceneId);
    root.__mvUnackedEpoch = root.__mvUiEpoch;
    armUnackedRelease(root.__mvUnackedEpoch);
    if (root.__mvFenceFlush) return;
    root.__mvFenceFlush = true;
    var publish = function () {
      root.__mvFenceFlush = false;
      var clientside = root.dash_clientside;
      if (!clientside || typeof clientside.set_props !== "function") return;
      if (!root.__mvClientId) {
        root.__mvClientId = "mv-" + Math.random().toString(36).slice(2, 12);
        clientside.set_props("ui-client-id", {data: root.__mvClientId});
      }
      clientside.set_props("ui-interaction-epoch", {data: Number(root.__mvUiEpoch || 0)});
    };
    if (typeof root.Promise === "function") root.Promise.resolve().then(publish);
    else publish();
  }

  function isAgentStatePoll(body) {
    if (!body || typeof body !== "object") return false;
    var changed = body.changedPropIds || [];
    var names = [];
    var index;
    for (index = 0; index < changed.length; index += 1) {
      names.push(String(changed[index]).split(".")[0]);
    }
    if (names.indexOf("agent-state-poll") === -1) return false;
    if (names.indexOf("scene-tabs") !== -1) return false;
    if (names.indexOf("native-upload-sync") !== -1) return false;
    if (names.indexOf("display-defaults-btn") !== -1) return false;
    var rendered = JSON.stringify(body.output == null ? "" : body.output);
    return rendered.indexOf("agent-state-store") !== -1
      && rendered.indexOf("scene-tab-rename-input") !== -1;
  }

  function isBackgroundTick(body) {
    if (!body || typeof body !== "object") return false;
    var changed = body.changedPropIds || [];
    if (!changed.length) return false;
    var prefixes = ["agent-state-poll.", "perf-log-poll.", "status-dismiss-timer."];
    var index;
    var prop;
    var prefix;
    var matched;
    for (index = 0; index < changed.length; index += 1) {
      prop = String(changed[index]);
      matched = false;
      for (prefix = 0; prefix < prefixes.length; prefix += 1) {
        if (prop.indexOf(prefixes[prefix]) === 0) {
          matched = true;
          break;
        }
      }
      if (!matched) return false;
    }
    return true;
  }

  function isUserWrite(bodyText) {
    if (typeof bodyText !== "string" || !bodyText) return false;
    try {
      return !isBackgroundTick(JSON.parse(bodyText));
    } catch (error) {
      return false;
    }
  }

  function requestFence(bodyText, live) {
    if (typeof bodyText !== "string" || !bodyText) return null;
    var body;
    try {
      body = JSON.parse(bodyText);
    } catch (error) {
      return null;
    }
    if (!isAgentStatePoll(body)) return null;
    var snapshot = live || liveFence();
    return {
      epoch: Number(snapshot.epoch || 0),
      scene: snapshot.scene ? String(snapshot.scene) : "",
      unacked: Number(snapshot.unacked || 0),
    };
  }

  function responseIsStale(tracked, live) {
    if (!tracked) return false;
    var now = live || liveFence();
    // Sent while an edit or tab callback was still open, or one opened
    // before this response could be applied.
    if (Number(tracked.unacked || 0) > 0) return true;
    if (Number(now.unacked || 0) > 0) return true;
    if (Number(now.epoch || 0) !== Number(tracked.epoch || 0)) return true;
    var thenScene = tracked.scene ? String(tracked.scene) : "";
    var nowScene = now.scene ? String(now.scene) : "";
    return Boolean(thenScene && nowScene && thenScene !== nowScene);
  }

  function blankPollPayload(payload) {
    if (!payload || typeof payload !== "object") return payload;
    var copy = {};
    var key;
    for (key in payload) {
      if (Object.prototype.hasOwnProperty.call(payload, key)) copy[key] = payload[key];
    }
    copy.response = {};
    if (copy.sideUpdate && typeof copy.sideUpdate === "object") copy.sideUpdate = {};
    return copy;
  }

  function install() {
    if (root.__mvPollFenceInstalled) return;
    root.__mvPollFenceInstalled = true;
    var documentRef = root.document;
    if (!documentRef || typeof documentRef.addEventListener !== "function") return;

    documentRef.addEventListener("pointerdown", function (event) {
      if (!event || event.isTrusted === false) return;
      var node = event.target;
      if (!node || !node.closest) return;
      if (node.closest("#rightclick-target-fallback")) return;
      noteInteraction(sceneIdFromNode(node));
    }, true);

    documentRef.addEventListener("keydown", function (event) {
      if (!event || event.isTrusted === false || MODIFIERS[event.key]) return;
      var node = event.target;
      if (!node || !node.closest) return;
      if (!node.closest("input, textarea, select")) return;
      if (node.id === "rightclick-target-fallback") return;
      noteInteraction(null);
    }, true);

    function publishIdentity() {
      var clientside = root.dash_clientside;
      if (!clientside || typeof clientside.set_props !== "function") return false;
      if (!root.__mvClientId) {
        root.__mvClientId = "mv-" + Math.random().toString(36).slice(2, 12);
      }
      clientside.set_props("ui-client-id", {data: root.__mvClientId});
      clientside.set_props("ui-interaction-epoch", {data: Number(root.__mvUiEpoch || 0)});
      return true;
    }
    if (!publishIdentity() && typeof root.setTimeout === "function") {
      var attempts = 0;
      var timer = root.setInterval(function () {
        attempts += 1;
        if (publishIdentity() || attempts > 40) root.clearInterval(timer);
      }, 250);
    }

    if (typeof root.fetch !== "function" || root.fetch.__mvPollFence) return;
    var originalFetch = root.fetch;
    var wrapped = function (input, init) {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var bodyText = init && typeof init.body === "string" ? init.body : "";
      var tracked = null;
      var userWrite = false;
      var userWriteEpoch = 0;
      if (String(url).indexOf("_dash-update-component") !== -1) {
        var live = liveFence();
        tracked = requestFence(bodyText, live);
        if (!tracked && isUserWrite(bodyText)) {
          userWrite = true;
          userWriteEpoch = Number(live.epoch || 0);
          root.__mvUserWrites = Number(root.__mvUserWrites || 0) + 1;
        }
      }
      var pending = originalFetch.apply(this, arguments);
      // Epoch 0 is the real first generation. A truthiness check would
      // count that callback and never release it, and every later edit
      // would leave the fence shut. State polls are what copy a server
      // projection onto the radio and refresh the controls stamp, so a
      // stuck fence drops those payloads for the rest of the page.
      if (userWrite && pending && typeof pending.then === "function") {
        var startedEpoch = userWriteEpoch;
        pending = pending.then(function (response) {
          finishUserWrite(startedEpoch);
          return response;
        }, function (error) {
          finishUserWrite(startedEpoch);
          throw error;
        });
      } else if (userWrite) {
        finishUserWrite(userWriteEpoch);
      }
      if (!tracked || !pending || typeof pending.then !== "function") return pending;
      return pending.then(function (response) {
        if (!response || typeof response.clone !== "function") return response;
        return response.clone().json().then(function (payload) {
          if (!responseIsStale(tracked, liveFence())) return response;
          var headers = new root.Headers(response.headers);
          headers.delete("content-length");
          headers.delete("content-encoding");
          return new root.Response(JSON.stringify(blankPollPayload(payload)), {
            status: response.status,
            statusText: response.statusText,
            headers: headers,
          });
        }, function () {
          return response;
        });
      });
    };
    wrapped.__mvPollFence = true;
    root.fetch = wrapped;
  }

  var api = {
    noteInteraction: noteInteraction,
    sceneIdFromNode: sceneIdFromNode,
    isAgentStatePoll: isAgentStatePoll,
    requestFence: requestFence,
    responseIsStale: responseIsStale,
    blankPollPayload: blankPollPayload,
    isUserWrite: isUserWrite,
    finishUserWrite: finishUserWrite,
    install: install,
  };
  root.MatterVisPollFence = api;
  install();
})(typeof window !== "undefined" ? window : globalThis);
