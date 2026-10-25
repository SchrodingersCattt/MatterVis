"""Exercise request/render status ownership with native browser fetch and observers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.app.test_camera_browser_regression import (
    _chrome,
    _playwright_module,
    _unavailable,
)

pytestmark = pytest.mark.browser


def test_overlapping_requests_and_render_status_in_chromium():
    node = shutil.which("node")
    chrome = _chrome()
    playwright = _playwright_module(node) if node else None
    if not node or not chrome or not playwright:
        _unavailable("status-bar browser gate needs node, Chrome/Chromium, and Playwright")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "status_bar.js"
    program = r"""
const assert = require('node:assert/strict');
const {chromium} = require(process.argv[1]);
(async () => {
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:process.argv[2],
      args:['--no-sandbox','--disable-dev-shm-usage']});
  } catch (error) {
    console.error('BROWSER_GATE_UNAVAILABLE: ' + error.message);
    process.exitCode = 2;
    return;
  }
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(5000);
    const routes = new Map(), arrivals = new Map();
    await page.route('http://mattervis.test/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({contentType:'text/html', body:
        '<style>.is-hidden{display:none}</style>' +
        '<div id="mv-status-busy" class="is-hidden"><span class="mv-status-busy-text">Working…</span></div>' +
        '<div id="mv-render-status" hidden></div>'});
      const id = url.searchParams.get('id');
      routes.set(id, route);
      if (arrivals.has(id)) arrivals.get(id)(route);
    });
    await page.goto('http://mattervis.test/');
    await page.addScriptTag({path:process.argv[3]});
    const busy = page.locator('#mv-status-busy');
    async function start(id, changed = ['display-mode-selector.value']) {
      const arrived = new Promise(resolve => arrivals.set(id, resolve));
      await page.evaluate(({id, changed}) => {
        window.pending = window.pending || {};
        window.controllers = window.controllers || {};
        const controller = window.controllers[id] = new AbortController();
        window.pending[id] = fetch('/_dash-update-component?id=' + id, {
          method:'POST', body:JSON.stringify({changedPropIds:changed}), signal:controller.signal,
        }).then(() => 'ok', error => error.name);
      }, {id, changed});
      await arrived;
    }
    async function finish(id, failed = false) {
      if (failed) await routes.get(id).abort('failed');
      else await routes.get(id).fulfill({contentType:'application/json', body:'{}'});
      return page.evaluate(id => window.pending[id], id);
    }
    async function rendering(on) {
      // Resolve after the real MutationObserver microtask observes this mutation.
      await page.evaluate(on => {
        const pill = document.getElementById('mv-render-status');
        pill.hidden = !on;
        pill.textContent = on ? 'Rendering structure…' : '';
        pill.setAttribute('data-level', on ? 'busy' : 'info');
        return new Promise(resolve => queueMicrotask(resolve));
      }, on);
    }
    async function visible(expected, message) {
      assert.equal(await busy.isVisible(), expected, message);
    }

    await start('poll', ['agent-state-poll.n_intervals']);
    await visible(false, 'background polling must stay silent');
    await start('first');
    await start('second');
    await visible(true, 'foreground work starts Working');
    assert.equal(await finish('first'), 'ok');
    await visible(true, 'another foreground request is still pending');
    assert.equal(await finish('poll'), 'ok');
    await visible(true, 'late background completion cannot release foreground work');
    await rendering(true);
    assert.equal(await finish('second'), 'ok');
    await visible(true, 'genuine rendering outlives the completed request');
    assert.equal(await busy.getAttribute('data-hold'), 'render');
    await rendering(false);
    await visible(false, 'completed rendering releases the final hold');

    await start('during-render');
    await rendering(true);
    await rendering(false);
    await visible(true, 'render completion cannot release a pending foreground request');
    assert.equal(await busy.getAttribute('data-hold'), null);
    assert.equal(await finish('during-render'), 'ok');
    await visible(false, 'the final foreground completion releases Working immediately');

    await start('remaining');
    await start('failed');
    assert.equal(await finish('failed', true), 'TypeError');
    await visible(true, 'a network failure leaves other foreground work visible');
    await start('cancelled');
    await page.evaluate(() => window.controllers.cancelled.abort());
    assert.equal(await page.evaluate(() => window.pending.cancelled), 'AbortError');
    await visible(true, 'cancellation leaves other foreground work visible');
    assert.equal(await finish('remaining'), 'ok');
    await visible(false, 'failed and cancelled requests do not leak request holds');

    await start('failed-alone');
    assert.equal(await finish('failed-alone', true), 'TypeError');
    await visible(false, 'a final failed request releases Working');
    await start('cancelled-alone');
    await page.evaluate(() => window.controllers['cancelled-alone'].abort());
    assert.equal(await page.evaluate(() => window.pending['cancelled-alone']), 'AbortError');
    await visible(false, 'a final cancelled request releases Working');
    for (const prop of ['perf-log-poll.n_intervals', 'status-dismiss-timer.n_intervals']) {
      await start(prop, [prop]);
      await visible(false, 'background polling must not flash Working');
      assert.equal(await finish(prop), 'ok');
      await visible(false, 'background completion must stay silent');
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = process.exitCode || 1; });
"""
    result = subprocess.run(
        [node, "-e", program, playwright, chrome, str(source)],
        capture_output=True,
        check=False,
        text=True,
        timeout=45,
    )
    if result.returncode == 2 and "BROWSER_GATE_UNAVAILABLE:" in result.stderr:
        _unavailable(result.stderr.strip())
    assert result.returncode == 0, result.stderr or result.stdout
