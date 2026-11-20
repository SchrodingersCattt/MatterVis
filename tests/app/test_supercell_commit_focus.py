"""Commit supercell selects the result scene and names the operand."""

from __future__ import annotations

import json
import multiprocessing
import shutil
import subprocess
import threading
from pathlib import Path

from mat_viewer.app import ViewerBackend
from mat_viewer.app.callbacks_operations import commit_visible_supercell
from mat_viewer.effects.errors import InjectedFailure, RevisionConflict

ROOT = Path(__file__).resolve().parents[2]
NACL = ROOT / "tests" / "fixtures" / "structures" / "synthetic_nacl.cif"
MATTERVIS_JS = ROOT / "frontend" / "assets" / "mattervis.js"


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

        first, first_focus = commit_visible_supercell(backend, copy["id"], 2, 1, 1)
        first_id = first_focus["result_scene_id"]
        assert first_focus["operation"] == "build_supercell"
        assert first_focus["source_scene_id"] == copy["id"]
        assert first_focus["revision"] == 2
        assert isinstance(first_focus["generation"], int)
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

        second, second_focus = commit_visible_supercell(backend, first_id, 2, 2, 2)
        second_id = second_focus["result_scene_id"]
        assert second_id != first_id
        assert second_focus["revision"] == 3
        assert second_focus["generation"] != first_focus["generation"]
        assert "from revision 2" in second
        assert "revision 3" in second
        assert _sites(backend, second_id) == 128
        assert _lengths(backend, second_id) == (22.56, 11.28, 11.28)
        assert backend.active_scene_id() == second_id

        refused, refused_focus = commit_visible_supercell(backend, copy["id"], 2, 2, 2)
        assert refused_focus is None
        assert refused.startswith("Supercell was not committed")
        assert "this view is revision 1" in refused
        assert "the shared history operand is revision 3" in refused
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
        results: list[tuple[str, dict | None]] = []

        def _click() -> None:
            results.append(commit_visible_supercell(backend, scene_id, 2, 1, 1))

        threads = [threading.Thread(target=_click) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        successes = [item for item in results if item[1] is not None]
        refusals = [item for item in results if item[1] is None]
        assert len(successes) == 1
        assert len(refusals) == 1
        assert refusals[0][0].startswith("Supercell was not committed")
        assert _sites(backend, successes[0][1]["result_scene_id"]) == 16
        assert backend.effect_session().current_ref(
            _structure_id(backend, scene_id), "0"
        ).revision == 2
    finally:
        backend.close(wait=False)


def test_commit_focus_waits_for_the_catalog_and_stays_on_one_page():
    node = shutil.which("node")
    assert node
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const stop = source.indexOf('  // ── Right-click menu');
assert.ok(stop > 0);
const focusBody = source.slice(source.indexOf('function noteCommitFocus'), source.indexOf('function claimOwnDuplicatedScene'));
assert.equal(focusBody.includes('setTimeout'), false);
assert.equal(focusBody.includes('/in scene'), false);
function page() {
  const selected = [];
  const sandbox = {
    console, Date, JSON, Number, String, Object, Array, Promise,
    setTimeout() {},
    clearTimeout() {}, requestAnimationFrame() {},
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
  const original = sandbox.window.MatterVisSelectScene;
  sandbox.window.MatterVisSelectScene = function (id) {
    selected.push(id);
    if (typeof original === 'function') original(id);
  };
  return {sandbox, selected};
}
const committing = page();
const other = page();
const payload = {
  operation: 'build_supercell',
  source_scene_id: 'scene_source00001',
  result_scene_id: 'scene_result00001',
  revision: 2,
  generation: 4,
};
const note = committing.sandbox.window.MatterVisNoteCommitFocus;
const statusText = 'Committed build_supercell from revision 1 as cell revision 2 in scene scene_result00001.';
assert.equal(note(statusText), null, 'status text is not a scene id');
assert.deepEqual(committing.selected, []);
assert.equal(note(payload), null, 'the result scene is not in the catalog yet');
assert.deepEqual(committing.selected, []);
const reconcile = committing.sandbox.window.MatterVisReconcileSceneCatalog;
reconcile({
  scene_catalog_seq: 1,
  scenes: [{id: 'scene_source00001', label: 'Source'}],
  active_id: 'scene_source00001',
  focus: false,
});
assert.deepEqual(committing.selected, [], 'a catalog without the result does not select it');
reconcile({
  scene_catalog_seq: 2,
  scenes: [
    {id: 'scene_source00001', label: 'Source'},
    {id: 'scene_result00001', label: 'Result'},
  ],
  active_id: 'scene_result00001',
  focus: false,
});
assert.deepEqual(committing.selected, ['scene_result00001']);
assert.equal(committing.sandbox.window.__mvLastCommitFocus.generation, 4);
reconcile({
  scene_catalog_seq: 2,
  scenes: [
    {id: 'scene_source00001', label: 'Source'},
    {id: 'scene_result00001', label: 'Result'},
  ],
  active_id: 'scene_result00001',
  focus: false,
});
assert.deepEqual(committing.selected, ['scene_result00001'], 'the same catalog does not select twice');
other.sandbox.window.MatterVisReconcileSceneCatalog({
  scene_catalog_seq: 2,
  scenes: [
    {id: 'scene_source00001', label: 'Source'},
    {id: 'scene_result00001', label: 'Result'},
  ],
  active_id: 'scene_result00001',
  focus: false,
});
assert.deepEqual(other.selected, [], 'the other page has no commit payload');
assert.equal(note(null), null);
const replaced = {
  operation: 'build_supercell',
  source_scene_id: 'scene_result00001',
  result_scene_id: 'scene_later000001',
  revision: 3,
  generation: 9,
};
assert.equal(note(replaced), null);
reconcile({
  scene_catalog_seq: 3,
  scenes: [{id: 'scene_later000001', label: 'Later'}],
  active_id: 'scene_later000001',
  focus: false,
});
assert.deepEqual(committing.selected, ['scene_result00001', 'scene_later000001']);
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
    script = MATTERVIS_JS.read_text(encoding="utf-8")
    assert 'Output("operate-scene-focus", "data")' in source
    assert 'Input("operate-scene-focus", "data")' in source
    assert 'Input("operate-status", "children")' not in source
    assert "MatterVisNoteCommitFocus" in source
    assert "MatterVisSelectCommittedScene" not in source
    assert "MatterVisSelectCommittedScene" not in script
    focus = script[script.index("function noteCommitFocus"):script.index("function claimOwnDuplicatedScene")]
    assert "setTimeout" not in focus
    assert "/in scene" not in focus
    assert "focus_clients" not in source
    assert "_publish_scene_catalog(focus=True)" not in source


def _commit_in_child(root: str, expected: int, connection) -> None:
    backend = _backend(Path(root))
    try:
        scene_id = backend.active_scene_id()
        result = backend.operate(
            "build_supercell",
            {"a": 2, "b": 1, "c": 1},
            scene_id=scene_id,
            expected_revision=expected,
        )
        connection.send(
            {
                "ok": True,
                "revision": int(result["output"]["revision"]),
                "scene_id": result["scene_id"],
            }
        )
    except RevisionConflict as exc:
        connection.send({"ok": False, "error": str(exc)})
    finally:
        backend.close(wait=False)
        connection.close()


def test_two_processes_commit_the_same_revision_once(tmp_path: Path):
    parent = _backend(tmp_path)
    parent.close(wait=False)
    context = multiprocessing.get_context("spawn")
    outcomes = []
    processes = []
    for _ in range(2):
        parent_conn, child_conn = context.Pipe()
        process = context.Process(target=_commit_in_child, args=(str(tmp_path), 1, child_conn))
        process.start()
        child_conn.close()
        processes.append((process, parent_conn))
    try:
        for process, connection in processes:
            process.join(40)
            assert process.exitcode == 0
            outcomes.append(connection.recv())
    finally:
        for process, _connection in processes:
            if process.is_alive():
                process.terminate()
    successes = [item for item in outcomes if item["ok"]]
    failures = [item for item in outcomes if not item["ok"]]
    assert len(successes) == 1, outcomes
    assert successes[0]["revision"] == 2
    assert len(failures) == 1, outcomes
    assert "expected revision 1" in failures[0]["error"]
    from mat_viewer.effects.persistence import load_session

    restored = load_session(str(tmp_path / ".local" / "effect-session.json"))
    assert len(restored._frames) == 1
    structure_id, frame = next(iter(restored._frames))
    current = restored.current_ref(structure_id, frame)
    assert current.revision == 2
    assert restored.snapshot(current).n_sites == 16


def test_failed_commit_does_not_advance_and_a_stale_flush_keeps_the_file(tmp_path: Path):
    first = _backend(tmp_path)
    second = _backend(tmp_path)
    try:
        second.effect_session()
        scene_id = first.active_scene_id()
        try:
            first.operate(
                "build_supercell",
                {"a": 2, "b": 1, "c": 1},
                scene_id=scene_id,
                expected_revision=1,
                inject_failure="persist",
            )
        except InjectedFailure:
            pass
        else:
            raise AssertionError("injected persistence failure committed")
        path = tmp_path / ".local" / "effect-session.json"
        assert not path.exists()
        _status, focus = commit_visible_supercell(first, scene_id, 2, 1, 1)
        assert focus is not None
        assert focus["revision"] == 2
        assert _sites(first, focus["result_scene_id"]) == 16
        second._flush_effect_session()
        from mat_viewer.effects.persistence import load_session

        restored = load_session(str(path))
        structure_id = _structure_id(first, focus["result_scene_id"])
        current = restored.current_ref(structure_id, "0")
        assert current.revision == 2
        assert restored.snapshot(current).n_sites == 16
    finally:
        first.close(wait=False)
        second.close(wait=False)
