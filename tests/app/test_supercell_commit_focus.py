"""Commit supercell selects the result scene and names the operand."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

from mat_viewer.app import ViewerBackend
from mat_viewer.app.callbacks_operations import commit_visible_supercell

ROOT = Path(__file__).resolve().parents[2]
NACL = ROOT / "tests" / "fixtures" / "structures" / "synthetic_nacl.cif"
MATTERVIS_JS = ROOT / "frontend" / "assets" / "mattervis.js"
SCENE_ID = re.compile(r"in scene (scene_[0-9a-f]{12})")


def _backend(tmp_path: Path) -> ViewerBackend:
    local = tmp_path / ".local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "catalog.local.json").write_text(
        json.dumps(
            {
                "structures": {
                    "synthetic-nacl": {
                        "cif_path": str(NACL.resolve()),
                        "title": "synthetic-nacl",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
        names=["synthetic-nacl"],
    )


def _sites(backend: ViewerBackend, scene_id: str) -> int:
    state = backend.get_state(scene_id)
    bundle = backend.get_bundle(str(state.get("structure") or ""))
    crystal = bundle.crystal
    records = crystal.get_site_records() if hasattr(crystal, "get_site_records") else bundle.raw_atoms
    return len(records)


def _lengths(backend: ViewerBackend, scene_id: str) -> tuple[float, float, float]:
    state = backend.get_state(scene_id)
    bundle = backend.get_bundle(str(state.get("structure") or ""))
    params = bundle.crystal.get_lattice_parameters()
    return tuple(float(params[index]) for index in range(3))


def _structure_id(backend: ViewerBackend, scene_id: str) -> str:
    state = backend.get_state(scene_id)
    bundle = backend.get_bundle(str(state.get("structure") or ""))
    return str(getattr(bundle, "scientific_structure_id", None) or bundle.name)


def test_commit_selects_16_then_128_and_refuses_the_old_view(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        source = backend.active_scene_id()
        assert _sites(backend, source) == 8
        copy = backend.duplicate_scene(source)
        assert _structure_id(backend, copy["id"]) == _structure_id(backend, source)

        first = commit_visible_supercell(backend, copy["id"], 2, 1, 1)
        first_id = SCENE_ID.search(first).group(1)
        assert "from revision 1" in first
        assert "revision 2" in first
        assert _sites(backend, first_id) == 16
        assert _lengths(backend, first_id) == (11.28, 5.64, 5.64)
        assert backend.active_scene_id() == first_id
        assert _structure_id(backend, first_id) == _structure_id(backend, copy["id"])
        catalogs = [
            item
            for item in backend._figure_broadcasts
            if item.get("type") == "scene_catalog"
        ]
        assert catalogs
        assert catalogs[-1]["focus"] is False
        assert _sites(backend, copy["id"]) == 8

        second = commit_visible_supercell(backend, first_id, 2, 2, 2)
        second_id = SCENE_ID.search(second).group(1)
        assert second_id != first_id
        assert "from revision 2" in second
        assert "revision 3" in second
        assert _sites(backend, second_id) == 128
        assert _lengths(backend, second_id) == (22.56, 11.28, 11.28)
        assert backend.active_scene_id() == second_id

        refused = commit_visible_supercell(backend, copy["id"], 2, 2, 2)
        assert refused.startswith("Supercell was not committed")
        assert "this view is revision 1" in refused
        assert "the shared history operand is revision 3" in refused
        assert "in scene" not in refused
        assert _sites(backend, copy["id"]) == 8
        assert backend.effect_session().current_ref(
            _structure_id(backend, copy["id"]), "0"
        ).revision == 3
    finally:
        backend.close(wait=False)


def test_repeated_commit_clicks_do_not_both_multiply(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        results: list[str] = []

        def _click() -> None:
            results.append(commit_visible_supercell(backend, scene_id, 2, 1, 1))

        threads = [threading.Thread(target=_click) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        successes = [text for text in results if text.startswith("Committed")]
        refusals = [text for text in results if text.startswith("Supercell was not committed")]
        assert len(successes) == 1
        assert len(refusals) == 1
        assert "in scene" not in refusals[0]
        assert _sites(backend, SCENE_ID.search(successes[0]).group(1)) == 16
        assert backend.effect_session().current_ref(
            _structure_id(backend, scene_id), "0"
        ).revision == 2
    finally:
        backend.close(wait=False)


def test_commit_status_selects_only_the_named_scene():
    node = shutil.which("node")
    assert node
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const stop = source.indexOf('  // ── Right-click menu');
assert.ok(stop > 0);
const selected = [];
const sandbox = {
  console, Date, JSON, Number, String, Object, Array, Promise,
  setTimeout() {}, clearTimeout() {}, requestAnimationFrame() {},
  MutationObserver: function () { this.observe = function () {}; },
  window: {
    location: {protocol: 'http:', host: 'localhost'},
    Plotly: null,
    WebSocket: null,
    dash_clientside: {no_update: 'NO_UPDATE', set_props() {}},
  },
  document: {
    getElementById() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
  },
};
vm.createContext(sandbox);
vm.runInContext(source.slice(0, stop) + '\n})();', sandbox);
const originalSelect = sandbox.window.MatterVisSelectScene;
sandbox.window.MatterVisSelectScene = function (id) {
  selected.push(id);
  if (typeof originalSelect === 'function') originalSelect(id);
};
const select = sandbox.window.MatterVisSelectCommittedScene;
assert.equal(typeof select, 'function');
const committed = select('Committed build_supercell from revision 1 as synthetic-nacl revision 2 in scene scene_abc123def456.');
assert.equal(committed, 'scene_abc123def456');
assert.deepEqual(selected, ['scene_abc123def456']);
const again = select('Committed build_supercell from revision 2 as synthetic-nacl revision 3 in scene scene_1234567890ab.');
assert.equal(again, 'scene_1234567890ab');
assert.deepEqual(selected, ['scene_abc123def456', 'scene_1234567890ab']);
const refused = select('Supercell was not committed: this view is revision 1; the shared history operand is revision 3. Select the current scene and commit again.');
assert.equal(refused, 'NO_UPDATE');
assert.deepEqual(selected, ['scene_abc123def456', 'scene_1234567890ab']);
const otherPage = sandbox.window.MatterVisReconcileSceneCatalog;
assert.equal(typeof otherPage, 'function');
"""
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_commit_callback_does_not_focus_the_catalog():
    source = (ROOT / "mat_viewer" / "app" / "callbacks_operations.py").read_text(encoding="utf-8")
    assert 'Output("scene-tabs", "value", allow_duplicate=True)' in source
    assert 'Input("operate-status", "children")' in source
    assert "MatterVisSelectCommittedScene" in source
    assert "focus_clients" not in source
    assert "_publish_scene_catalog(focus=True)" not in source
