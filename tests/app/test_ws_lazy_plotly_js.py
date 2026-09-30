"""WebSocket startup must survive Dash loading Plotly after app assets."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_websocket_connects_after_lazy_plotly_load():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to exercise frontend startup")
    source = Path(__file__).resolve().parents[2] / "frontend/assets/mattervis.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('  (function connectWS()');
const end = source.indexOf('\n  // ── Single MutationObserver', start);
assert.ok(start >= 0 && end > start);
const callbacks = [], connections = [];
class Socket {
  constructor(url) { connections.push(this); this.url = url; this.listeners = {}; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  send(value) { this.sent = JSON.parse(value); }
}
const window = {WebSocket: Socket, location: {protocol: 'http:', host: 'localhost:8058'}};
const sandbox = {window, WebSocket: Socket, requestAnimationFrame: fn => callbacks.push(fn)};
vm.createContext(sandbox);
vm.runInContext(source.slice(start, end), sandbox);
assert.equal(connections.length, 0);
assert.equal(callbacks.length, 1, 'startup was abandoned before Plotly loaded');
window.Plotly = {};
callbacks.shift()();
assert.equal(connections.length, 1);
assert.equal(callbacks.length, 0);
connections[0].listeners.open();
assert.equal(connections[0].sent.type, 'subscribe_figure');
assert.equal(connections[0].sent.enabled, true);
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr