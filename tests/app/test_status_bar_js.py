"""The status bar drops Working as soon as a Dash request finishes."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_request_end_releases_busy_unless_the_render_pill_is_still_busy():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "status_bar.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
assert.equal(source.includes('450'), false);
const busy = {
  classes: new Set(['is-hidden']),
  attrs: {},
  classList: null,
  querySelector() { return {textContent: ''}; },
  getAttribute(key) { return Object.prototype.hasOwnProperty.call(this.attrs, key) ? this.attrs[key] : null; },
  setAttribute(key, value) { this.attrs[key] = value; },
  removeAttribute(key) { delete this.attrs[key]; },
};
busy.classList = {
  add(name) { busy.classes.add(name); },
  remove(name) { busy.classes.delete(name); },
};
const pill = {
  hidden: true,
  dataset: {},
  attrs: {},
  textContent: '',
  getAttribute(key) { return Object.prototype.hasOwnProperty.call(this.attrs, key) ? this.attrs[key] : null; },
  setAttribute(key, value) { this.attrs[key] = value; },
};
const observers = [];
class MutationObserver {
  constructor(callback) { this.callback = callback; observers.push(this); }
  observe() {}
  disconnect() {}
}
let resolveFetch = null;
const sandbox = {
  MutationObserver,
  document: {
    readyState: 'complete',
    documentElement: {},
    getElementById(id) {
      if (id === 'mv-status-busy') return busy;
      if (id === 'mv-render-status') return pill;
      return null;
    },
    addEventListener() {},
  },
  window: {
    fetch() { return new Promise(resolve => { resolveFetch = resolve; }); },
  },
};
sandbox.window.window = sandbox.window;
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const userBody = JSON.stringify({changedPropIds: ['display-mode-selector.value']});
const pending = sandbox.window.fetch('/_dash-update-component', {body: userBody});
assert.equal(busy.classes.has('is-hidden'), false);
resolveFetch({ok: true});
pending.then(() => {
  assert.equal(busy.classes.has('is-hidden'), true);
  assert.equal(busy.getAttribute('data-hold'), null);

  pill.hidden = false;
  pill.setAttribute('data-level', 'busy');
  pill.textContent = 'Rendering structure…';
  const rendering = sandbox.window.fetch('/_dash-update-component', {body: userBody});
  assert.equal(busy.classes.has('is-hidden'), false);
  resolveFetch({ok: true});
  return rendering.then(() => {
    assert.equal(busy.getAttribute('data-hold'), 'render');
    assert.equal(busy.classes.has('is-hidden'), false);
    pill.hidden = true;
    pill.attrs = {};
    observers[observers.length - 1].callback();
    assert.equal(busy.getAttribute('data-hold'), null);
    assert.equal(busy.classes.has('is-hidden'), true);

    const poll = sandbox.window.fetch('/_dash-update-component', {
      body: JSON.stringify({changedPropIds: ['agent-state-poll.n_intervals']}),
    });
    assert.equal(busy.classes.has('is-hidden'), true);
    resolveFetch({ok: true});
    return poll;
  });
}).catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(source)],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr or result.stdout
