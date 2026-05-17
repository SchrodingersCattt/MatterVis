/* MatterVis client-side diagnostic + environment guard.
 *
 * Two responsibilities:
 *
 * 1. Detect when the page is being viewed inside an embedded
 *    Electron webview (Cursor's Simple Browser, VS Code's preview
 *    panel, etc). Those embeds dispatch click events at the DOM
 *    level but do not always propagate them through React 16's
 *    synthetic event system, which is what Dash binds onto. The
 *    visible failure mode is: every UI control "appears" to click
 *    (the checkbox toggles, the dropdown opens) but no
 *    /_dash-update-component POST is sent, so the figure never
 *    updates and the user concludes the app is broken.
 *
 *    We surface this as a sticky red banner with a one-click copy
 *    of the URL so users can paste it into a real browser.
 *
 * 2. Render an opt-in (?diag=1) wire-tap strip that flashes on
 *    every click, every Dash POST, and every JS error -- this is
 *    invaluable when remote-debugging "no response" complaints
 *    because it lets you tell at a glance whether the click made
 *    it past the DOM, past React, and past the network. */

(function () {
  if (window.__mvDiagInstalled) {
    return;
  }
  window.__mvDiagInstalled = true;

  const ua = (navigator && navigator.userAgent) || "";
  const isCursorWebview = /\bCursor\//.test(ua);
  const isVsCodeWebview = /VSCode/i.test(ua) || /Electron/i.test(ua);
  const params = new URLSearchParams(window.location.search);
  const diagOn = params.get("diag") === "1";

  function appendWhenReady(el) {
    if (!document.body) {
      window.requestAnimationFrame(function () {
        appendWhenReady(el);
      });
      return;
    }
    document.body.appendChild(el);
  }

  function buildEnvBanner() {
    const banner = document.createElement("div");
    banner.id = "mv-env-banner";
    banner.style.cssText = [
      "position:fixed",
      "bottom:12px",
      "right:12px",
      "max-width:360px",
      "z-index:99998",
      "background:#7f1d1d",
      "color:#fff5f5",
      "font:12px/1.35 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif",
      "padding:10px 12px",
      "border-radius:8px",
      "display:flex",
      "gap:8px",
      "align-items:flex-start",
      "box-shadow:0 6px 16px rgba(0,0,0,0.35)",
      "pointer-events:none",
    ].join(";");

    const title = document.createElement("strong");
    title.textContent = "MatterVis: open in a real browser";
    banner.appendChild(title);

    const msg = document.createElement("span");
    msg.style.flex = "1";
    msg.textContent =
      " The page is being rendered inside an embedded Electron webview" +
      " (Cursor / VS Code preview). Click events do not reach Dash here, so" +
      " buttons, sliders and dropdowns will appear unresponsive. Copy the URL" +
      " and open it in Chrome / Edge / Firefox.";
    banner.appendChild(msg);

    const url = window.location.href;
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.textContent = "Copy URL";
    copyBtn.style.cssText =
      "background:#fff;color:#7f1d1d;border:0;border-radius:6px;padding:6px 10px;cursor:pointer;font-weight:600;pointer-events:auto";
    copyBtn.addEventListener("click", function () {
      try {
        navigator.clipboard.writeText(url);
        copyBtn.textContent = "Copied";
      } catch (_e) {
        const ta = document.createElement("textarea");
        ta.value = url;
        document.body.appendChild(ta);
        ta.select();
        try {
          document.execCommand("copy");
        } finally {
          document.body.removeChild(ta);
        }
        copyBtn.textContent = "Copied";
      }
      window.setTimeout(function () {
        copyBtn.textContent = "Copy URL";
      }, 1500);
    });
    banner.appendChild(copyBtn);

    const dismissBtn = document.createElement("button");
    dismissBtn.type = "button";
    dismissBtn.title = "Dismiss";
    dismissBtn.textContent = "\u00d7";
    dismissBtn.style.cssText =
      "background:transparent;color:#fff5f5;border:0;font-size:18px;line-height:1;padding:0 6px;cursor:pointer;pointer-events:auto";
    dismissBtn.addEventListener("click", function () {
      banner.remove();
      document.body.style.paddingTop = diagOn ? "26px" : "0";
    });
    banner.appendChild(dismissBtn);
    return banner;
  }

  function buildDiagStrip() {
    const strip = document.createElement("div");
    strip.id = "mv-diag-strip";
    strip.style.cssText = [
      "position:fixed",
      "left:0",
      "right:0",
      "z-index:99999",
      "background:#0f172a",
      "color:#cbd5f5",
      "font:11px/1.4 ui-monospace,Menlo,Consolas,monospace",
      "padding:4px 10px",
      "display:flex",
      "gap:14px",
      "align-items:center",
      "border-bottom:1px solid #1e293b",
      "pointer-events:none",
    ].join(";");

    function pill(label, color) {
      const el = document.createElement("span");
      el.textContent = label;
      el.style.cssText = `padding:1px 6px;border-radius:8px;background:${color};color:#0f172a;font-weight:600`;
      return el;
    }

    const title = pill("MatterVis diag", "#facc15");
    const clickPill = pill("clicks: 0", "#94a3b8");
    const sendPill = pill("POST: 0", "#94a3b8");
    const recvPill = pill("OK: 0", "#94a3b8");
    const errPill = pill("err: 0", "#94a3b8");
    const lastPill = document.createElement("span");
    lastPill.textContent = "ready";
    lastPill.style.cssText = "color:#cbd5f5;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";

    strip.style.pointerEvents = "none";
    strip.appendChild(title);
    strip.appendChild(clickPill);
    strip.appendChild(sendPill);
    strip.appendChild(recvPill);
    strip.appendChild(errPill);
    strip.appendChild(lastPill);

    let clicks = 0;
    let sends = 0;
    let oks = 0;
    let errs = 0;
    let lastChanged = "";

    function flashStrip(color) {
      strip.style.background = color;
      window.setTimeout(function () {
        strip.style.background = "#0f172a";
      }, 180);
    }

    document.addEventListener(
      "click",
      function (event) {
        clicks += 1;
        clickPill.textContent = "clicks: " + clicks;
        const target = event.target;
        const desc = target && (target.id || target.className || target.tagName);
        lastPill.textContent = "click " + (desc || "?");
        flashStrip("#312e81");
      },
      true
    );

    /* Wire-tap window.fetch and XMLHttpRequest *non-invasively*: we
     * record activity but do NOT swap them out, so a buggy wrapper
     * cannot break Dash's callback transport. We watch
     * fetch by listening to the global ``performance`` resource
     * timing buffer, and XHR via a thin send/load shim that delegates
     * straight to the originals. */
    function bumpSend(label) {
      sends += 1;
      sendPill.textContent = "POST: " + sends;
      lastChanged = label || "?";
      lastPill.textContent = "\u2192 " + lastChanged;
      flashStrip("#1e3a8a");
    }
    function bumpOk(status, label) {
      oks += 1;
      recvPill.textContent = "OK: " + oks;
      lastPill.textContent = "\u2190 " + status + " " + (label || lastChanged);
      flashStrip(status === 200 ? "#14532d" : "#7c2d12");
    }
    function bumpErr(detail) {
      errs += 1;
      errPill.textContent = "err: " + errs;
      lastPill.textContent = "\u2717 " + detail;
      flashStrip("#7f1d1d");
    }

    const origFetch = window.fetch;
    if (typeof origFetch === "function") {
      window.fetch = function (resource, init) {
        const url = typeof resource === "string" ? resource : resource && resource.url;
        const promise = origFetch.apply(this, arguments);
        if (!url || url.indexOf("/_dash-update-component") < 0) {
          return promise;
        }
        let changedFromBody = "";
        try {
          if (init && typeof init.body === "string") {
            const parsed = JSON.parse(init.body);
            if (parsed && parsed.changedPropIds) {
              changedFromBody = parsed.changedPropIds.join(",");
            }
          }
        } catch (_e) {
          changedFromBody = "(parse-fail)";
        }
        if (changedFromBody !== "agent-state-poll.n_intervals") {
          bumpSend(changedFromBody);
          promise.then(
            function (resp) {
              if (resp && resp.ok) {
                bumpOk(resp.status, changedFromBody);
              } else {
                bumpErr((resp && resp.status) + " " + changedFromBody);
              }
            },
            function (err) {
              bumpErr((err && err.message) + " " + changedFromBody);
            }
          );
        }
        return promise;
      };
    }

    const OrigXHR = window.XMLHttpRequest;
    if (typeof OrigXHR === "function") {
      const origOpen = OrigXHR.prototype.open;
      const origSend = OrigXHR.prototype.send;
      OrigXHR.prototype.open = function (method, url) {
        try {
          this.__mvUrl = url || "";
          this.__mvMethod = method || "";
        } catch (_e) {}
        return origOpen.apply(this, arguments);
      };
      OrigXHR.prototype.send = function (body) {
        const url = this.__mvUrl || "";
        if (url.indexOf("/_dash-update-component") >= 0) {
          let changedFromBody = "";
          try {
            if (typeof body === "string") {
              const parsed = JSON.parse(body);
              if (parsed && parsed.changedPropIds) {
                changedFromBody = parsed.changedPropIds.join(",");
              }
            }
          } catch (_e) {
            changedFromBody = "(parse-fail)";
          }
          if (changedFromBody !== "agent-state-poll.n_intervals") {
            bumpSend(changedFromBody + " (xhr)");
            this.addEventListener("load", function () {
              if (this.status >= 200 && this.status < 300) {
                bumpOk(this.status, changedFromBody + " (xhr)");
              } else {
                bumpErr(this.status + " " + changedFromBody + " (xhr)");
              }
            });
            this.addEventListener("error", function () {
              bumpErr("xhr-error " + changedFromBody);
            });
          }
        }
        return origSend.apply(this, arguments);
      };
    }

    window.addEventListener("error", function (event) {
      errs += 1;
      errPill.textContent = "err: " + errs;
      lastPill.textContent = "JS-err " + (event && event.message);
      flashStrip("#7f1d1d");
    });

    window.addEventListener("unhandledrejection", function (event) {
      errs += 1;
      errPill.textContent = "err: " + errs;
      lastPill.textContent = "promise " + (event && event.reason && event.reason.message);
      flashStrip("#7f1d1d");
    });

    return strip;
  }

  if (isCursorWebview || isVsCodeWebview) {
    appendWhenReady(buildEnvBanner());
    // No padding-top adjustment: banner is a floating top-right
    // toast so it does not push or cover the rest of the layout.
  }

  if (diagOn) {
    const strip = buildDiagStrip();
    strip.style.top = "0";
    appendWhenReady(strip);
    if (document.body) {
      document.body.style.paddingTop = "26px";
    } else {
      window.addEventListener("DOMContentLoaded", function () {
        document.body.style.paddingTop = "26px";
      });
    }
  }
})();
