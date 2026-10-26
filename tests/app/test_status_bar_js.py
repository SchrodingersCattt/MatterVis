"""Working tracks foreground Dash requests independently of render completion."""

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const busyText = {textContent: ''};
const busy = {
  classes: new Set(['is-hidden']),
  attrs: {},
  querySelector() { return busyText; },
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
  observe(target) { this.target = target; }
  disconnect() {}
}
const requests = [];
let synchronousError = null;
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
    fetch() {
      if (synchronousError) throw synchronousError;
      return new Promise((resolve, reject) => { requests.push({resolve, reject}); });
    },
  },
};
sandbox.window.window = sandbox.window;
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
function fetchChanges(changedPropIds) {
  return sandbox.window.fetch('/_dash-update-component', {body: JSON.stringify({changedPropIds})});
}
function foreground() { return fetchChanges(['display-mode-selector.value']); }
function visible() { return !busy.classes.has('is-hidden'); }
function rendering(on) {
  pill.hidden = !on;
  pill.setAttribute('data-level', on ? 'busy' : 'info');
  pill.textContent = on ? 'Rendering structure…' : '';
  observers.find(observer => observer.target === pill).callback();
}
"""


def _run_status_bar(program: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "status_bar.js"
    result = subprocess.run(
        [node, "-e", _HARNESS + "\n(async () => {\n" + program + r"""
})().catch(error => { console.error(error); process.exitCode = 1; });
""", str(source)],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_request_end_releases_busy_unless_the_render_pill_is_still_busy():
    _run_status_bar(r"""
const pending = foreground();
assert.equal(visible(), true);
requests[0].resolve({ok: true});
await pending;
assert.equal(visible(), false);
assert.equal(busy.getAttribute('data-hold'), null);

const pendingRender = foreground();
// The pill changes before its observer runs; release must synchronize it.
pill.hidden = false;
pill.setAttribute('data-level', 'busy');
pill.textContent = 'Rendering structure…';
requests[1].resolve({ok: true});
await pendingRender;
assert.equal(busy.getAttribute('data-hold'), 'render');
assert.equal(visible(), true);
rendering(false);
assert.equal(busy.getAttribute('data-hold'), null);
assert.equal(visible(), false);
// Rendering may start only after Dash has consumed the completed response.
rendering(true);
assert.equal(visible(), true);
rendering(false);
assert.equal(visible(), false);
const idleBeforeObserver = foreground();
rendering(true);
pill.hidden = true;
pill.setAttribute('data-level', 'info');
requests[2].resolve({ok: true});
await idleBeforeObserver;
assert.equal(busy.getAttribute('data-hold'), null);
assert.equal(visible(), false, 'release clears a stale render hold before the observer');
""")


@pytest.mark.parametrize("first", [0, 1])
def test_overlapping_foreground_requests_keep_working_until_both_finish(first):
    _run_status_bar(f"""
const pending = [foreground(), foreground()];
requests[{first}].resolve({{ok: true}});
await pending[{first}];
assert.equal(visible(), true, 'another foreground request is still pending');
requests[{1 - first}].resolve({{ok: true}});
await pending[{1 - first}];
assert.equal(visible(), false);
""")


def test_render_completion_does_not_hide_pending_foreground_work():
    _run_status_bar(r"""
const pending = foreground();
rendering(true);
assert.equal(busy.getAttribute('data-hold'), 'render');
rendering(false);
assert.equal(busy.getAttribute('data-hold'), null);
assert.equal(visible(), true, 'request remains pending after rendering');
assert.equal(busyText.textContent, 'Working…');
requests[0].resolve({ok: true});
await pending;
assert.equal(visible(), false);
""")


@pytest.mark.parametrize("poll", [
    "agent-state-poll.n_intervals",
    "perf-log-poll.n_intervals",
    "status-dismiss-timer.n_intervals",
])
def test_background_completion_is_silent_during_requests_and_rendering(poll):
    _run_status_bar(f"""
const background = fetchChanges([{poll!r}]);
assert.equal(visible(), false);
const pending = foreground();
requests[0].resolve({{ok: true}});
await background;
assert.equal(visible(), true);
rendering(true);
requests[1].resolve({{ok: true}});
await pending;
const latePoll = fetchChanges([{poll!r}]);
requests[2].resolve({{ok: true}});
await latePoll;
assert.equal(visible(), true);
assert.equal(busy.getAttribute('data-hold'), 'render');
rendering(false);
assert.equal(visible(), false);
const idlePoll = fetchChanges([{poll!r}]);
requests[3].resolve({{ok: true}});
await idlePoll;
assert.equal(visible(), false);
""")


@pytest.mark.parametrize("name", ["Error", "AbortError"])
def test_failed_or_cancelled_request_releases_only_its_own_work(name):
    _run_status_bar(f"""
const error = Object.assign(new Error('request failed'), {{name: {name!r}}});
const failed = foreground();
const pending = foreground();
const rejected = assert.rejects(failed, actual => actual === error);
requests[0].reject(error);
await rejected;
assert.equal(visible(), true);
requests[1].resolve({{ok: false, status: 500}});
await pending;
assert.equal(visible(), false);
const renderingFailure = foreground();
rendering(true);
const rejectedRender = assert.rejects(renderingFailure, actual => actual === error);
requests[2].reject(error);
await rejectedRender;
assert.equal(visible(), true);
rendering(false);
assert.equal(visible(), false);
""")


def test_synchronous_fetch_failure_does_not_leak_a_foreground_request():
    _run_status_bar(r"""
synchronousError = new TypeError('invalid fetch options');
assert.throws(foreground, actual => actual === synchronousError);
assert.equal(visible(), false);
synchronousError = null;
const pending = foreground();
requests[0].resolve({ok: true});
await pending;
assert.equal(visible(), false);
""")
