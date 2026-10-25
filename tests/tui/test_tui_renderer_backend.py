"""Pluggable TUI renderer contract.

Frozen text files are the built-in compositor. Dispatch must reproduce them
for the default backend. External backends are selected by entry point and
must not receive chemistry records.
"""

from __future__ import annotations

from hashlib import sha256
from io import StringIO
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from mat_viewer.math.camera import Camera, project_points
from mat_viewer.tui import (
    NATIVE_BACKEND_ID,
    RENDER_API_VERSION,
    TerminalAction,
    TerminalSession,
)
from mat_viewer.tui.compositor import compose_frame
from mat_viewer.tui.crystal_ir import AtomIR, BondIR, CrystalIR, Lattice
from mat_viewer.tui.loader_adapter import load_for_tui
from mat_viewer.tui.render_backend import (
    native_host_sources_bound,
    render_picture,
    resolve_renderer_binding,
)
from mat_viewer.tui.render_contract import (
    RENDERER_ENTRY_GROUP,
    RendererContractError,
    build_render_request,
)
from mat_viewer.tui.session import ACTION_SCHEMA, run_jsonl_session


ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "tui_renderer_goldens"
WATER = ROOT / "benchmarks" / "structures" / "molecular_water.cif"
EXAMPLE_SRC = ROOT / "examples" / "mattervis_mark_renderer" / "src"
EXAMPLE_PACKAGE = EXAMPLE_SRC / "mattervis_mark_renderer"
PIN = ROOT / "examples" / "mattervis_mark_renderer" / "pins" / "example.marks-v1.json"
CANARY = "CANARY-GOLD-9f3a"
LEGACY_KEYS = {
    "capabilities",
    "camera",
    "display",
    "focus",
    "frame",
    "revision",
    "schema",
    "scope",
    "selection",
    "state_fingerprint",
    "title",
    "viewport",
    "warnings",
}


def _lattice(length: float = 8.0) -> Lattice:
    return Lattice(
        a=length,
        b=length,
        c=length,
        alpha=90.0,
        beta=90.0,
        gamma=90.0,
        matrix=np.diag([length, length, length]),
    )


def _atom(element: str, xyz, index: int, **kwargs) -> AtomIR:
    return AtomIR(
        element,
        np.array(xyz, dtype=float),
        np.zeros(3),
        atom_id=kwargs.pop("atom_id", f"site:{element}{index}"),
        label=kwargs.pop("label", f"{element}{index}"),
        index=index,
        display_copy_id=kwargs.pop("display_copy_id", f"copy:{element}{index}"),
        **kwargs,
    )


def _pair() -> CrystalIR:
    return CrystalIR(
        title="pair",
        formula="CO",
        lattice=_lattice(),
        atoms=[
            _atom("C", (-1.0, 0.0, 0.0), 0),
            _atom("O", (1.0, 0.0, 0.0), 1),
        ],
        bonds=[BondIR(0, 1, 2.0)],
        metadata={"bond_source": "distance_heuristic"},
    )


def _molecule() -> CrystalIR:
    return CrystalIR(
        title="molecules",
        lattice=_lattice(),
        atoms=[
            _atom("C", (-1.2, 0.0, 0.0), 0, molecule_index=0),
            _atom("O", (-0.2, 0.4, 0.0), 1, molecule_index=0),
            _atom("N", (1.4, 0.0, 0.2), 2, molecule_index=1),
        ],
        bonds=[BondIR(0, 1, 1.2)],
        n_molecules=2,
        species_map={"CO": [0], "N": [1]},
        metadata={"bond_source": "distance_heuristic"},
    )


def _framework() -> CrystalIR:
    atoms = [
        _atom("C", (float(index), 0.0, 0.1 * (index % 3)), index, molecule_index=0)
        for index in range(70)
    ]
    bonds = [BondIR(index - 1, index, 1.0) for index in range(1, 70)]
    return CrystalIR(
        title="framework",
        lattice=_lattice(80.0),
        atoms=atoms,
        bonds=bonds,
        n_molecules=1,
        species_map={"C70": [0]},
        metadata={"bond_source": "distance_heuristic"},
    )


def _boundary() -> CrystalIR:
    return CrystalIR(
        title="boundary",
        lattice=_lattice(),
        atoms=[
            _atom("C", (0.2, 0.0, 0.0), 0),
            _atom(
                "O",
                (7.5, 0.2, 0.0),
                1,
                image_shift=(1, 0, 0),
                is_boundary_replica=True,
                display_copy_id="copy:O1#img",
            ),
        ],
        bonds=[BondIR(0, 1, 1.4, image_relation=(1, 0, 0))],
        metadata={"bond_source": "distance_heuristic", "explicit_bond_table": False},
    )


def _camera(crystal: CrystalIR) -> Camera:
    return Camera.from_view_name("diagonal", crystal)


def _picture(crystal: CrystalIR, *, charset: str, width: int, height: int, **kwargs) -> str:
    camera = kwargs.pop("camera", None) or _camera(crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    arguments = dict(
        width=width,
        height=height,
        mono=charset == "ascii7",
        charset=charset,
        label_mode="label",
        show_bonds=True,
        show_cell=True,
        show_minor=False,
        show_hydrogen=False,
        show_boundary=False,
        zoom=camera.viewport_zoom,
        pan_x=camera.pan_x,
        pan_y=camera.pan_y,
        display_level="atom",
        show_axes=True,
        show_depth=True,
    )
    arguments.update(kwargs)
    direct = compose_frame(crystal, camera, points, depth, **arguments)
    rendered = render_picture(crystal, camera, points, depth, **arguments)
    assert rendered.text == direct
    assert rendered.backend_id == NATIVE_BACKEND_ID
    return direct


def _frame_specs():
    return (
        ("atom_ascii7_80x24_depth", _pair(), dict(charset="ascii7", width=80, height=24)),
        ("atom_ascii7_100x36_nodepth", _pair(), dict(charset="ascii7", width=100, height=36, show_depth=False)),
        ("atom_unicode_80x24_depth", _pair(), dict(charset="unicode", width=80, height=24)),
        ("atom_unicode_100x36_nodepth", _pair(), dict(charset="unicode", width=100, height=36, show_depth=False)),
        ("molecule_ascii7_80x24", _molecule(), dict(charset="ascii7", width=80, height=24, display_level="molecule")),
        ("framework_ascii7_100x36", _framework(), dict(charset="ascii7", width=100, height=36, display_level="molecule")),
        ("boundary_ascii7_80x24", _boundary(), dict(charset="ascii7", width=80, height=24, show_boundary=True)),
    )


def _episode_stdout() -> str:
    source = StringIO()
    requests = (
        {"schema": ACTION_SCHEMA, "action": "observe"},
        {"schema": ACTION_SCHEMA, "action": "orbit", "arguments": {"yaw_deg": 15.0}},
        {
            "schema": ACTION_SCHEMA,
            "action": "set_display",
            "arguments": {"label_mode": "element", "show_depth": False},
        },
        {"schema": ACTION_SCHEMA, "action": "reset"},
        {"schema": ACTION_SCHEMA, "action": "close"},
    )
    for request in requests:
        source.write(json.dumps(request) + "\n")
    source.seek(0)
    target = StringIO()
    session = TerminalSession(_pair(), width=80, height=24, charset="ascii7")
    run_jsonl_session(session, input_stream=source, output_stream=target)
    return target.getvalue()


def write_goldens() -> None:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    index = {
        "description": (
            "Built-in compositor frames frozen for mattervis.native-v1. "
            "Picture files are exact bytes. The episode JSONL is a structural "
            "reference: viewport floats and state_fingerprint are not a "
            "cross-CPython byte contract."
        ),
        "cases": {},
    }
    for name, crystal, kwargs in _frame_specs():
        text = _picture(crystal, **kwargs)
        (GOLDEN / f"{name}.txt").write_text(text, encoding="utf-8")
        index["cases"][name] = {
            "file": f"{name}.txt",
            "sha256": sha256(text.encode("utf-8")).hexdigest(),
        }
    water = load_for_tui(str(WATER))
    for name, kwargs in (
        ("water_atom_unicode_80x24", dict(charset="unicode", width=80, height=24, display_level="atom")),
        ("water_molecule_ascii7_100x36", dict(charset="ascii7", width=100, height=36, display_level="molecule")),
    ):
        text = _picture(water, **kwargs)
        (GOLDEN / f"{name}.txt").write_text(text, encoding="utf-8")
        index["cases"][name] = {
            "file": f"{name}.txt",
            "sha256": sha256(text.encode("utf-8")).hexdigest(),
        }
    episode = _episode_stdout()
    (GOLDEN / "episode_ascii7_80x24.jsonl").write_text(episode, encoding="utf-8")
    index["episode"] = {
        "file": "episode_ascii7_80x24.jsonl",
        "sha256": sha256(episode.encode("utf-8")).hexdigest(),
    }
    (GOLDEN / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _legacy_fingerprint(session: TerminalSession, observation) -> str:
    state = observation.observation.state.as_dict()
    state.pop("revision", None)
    state["charset"] = session.charset
    material = json.dumps(
        state,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return sha256(material).hexdigest()


def _external_backend(render, **overrides):
    values = dict(
        backend_id="example.fake-v1",
        api_version=RENDER_API_VERSION,
        deterministic=True,
        package_version="0.0.0",
        implementation_digest="ab" * 32,
    )
    values.update(overrides)

    class Backend:
        backend_id = values["backend_id"]
        api_version = values["api_version"]
        deterministic = values["deterministic"]
        package_version = values["package_version"]
        implementation_digest = values["implementation_digest"]

        def validate_options(self, options):
            if options and not values.get("allow_options", False):
                raise ValueError("unsupported renderer options")
            return dict(options)

        def render(self, request):
            return render(self, request)

    return Backend()


def _install_mark_renderer(target: Path) -> None:
    shutil.copytree(EXAMPLE_PACKAGE, target / "mattervis_mark_renderer")
    dist = target / "mattervis_mark_renderer-0.1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: mattervis-mark-renderer\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (dist / "entry_points.txt").write_text(
        f"[{RENDERER_ENTRY_GROUP}]\nexample.marks-v1 = mattervis_mark_renderer:create_renderer\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("name,crystal,kwargs", _frame_specs(), ids=[item[0] for item in _frame_specs()])
def test_frozen_native_frames_match_dispatch(name, crystal, kwargs) -> None:
    text = _picture(crystal, **kwargs)
    golden = (GOLDEN / f"{name}.txt").read_text(encoding="utf-8")
    assert text == golden
    assert sha256(text.encode("utf-8")).hexdigest() == json.loads((GOLDEN / "index.json").read_text())["cases"][name]["sha256"]


def _assert_episode_matches_golden(actual: str, golden: str) -> None:
    """Compare one JSONL episode without requiring cross-CPython byte equality.

    Picture text and ``frame.sha256`` stay exact. Viewport and camera floats
    may differ by a few ULPs, which also changes ``state_fingerprint``. That
    fingerprint is still checked within a single run by the caller.
    """
    actual_rows = [json.loads(line) for line in actual.splitlines() if line]
    golden_rows = [json.loads(line) for line in golden.splitlines() if line]
    assert len(actual_rows) == len(golden_rows)
    for index, (got, expected) in enumerate(zip(actual_rows, golden_rows)):
        _assert_json_close(got, expected, path=f"$[{index}]")


def _assert_json_close(got, expected, path: str) -> None:
    if isinstance(expected, bool) or isinstance(got, bool):
        assert isinstance(got, bool) and isinstance(expected, bool) and got is expected, path
        return
    if isinstance(expected, float) or isinstance(got, float):
        assert isinstance(got, (int, float)) and isinstance(expected, (int, float)), path
        assert math.isclose(got, expected, rel_tol=1e-12, abs_tol=1e-15), (
            f"{path}: {got!r} != {expected!r}"
        )
        return
    if isinstance(expected, dict):
        assert isinstance(got, dict), path
        assert set(got) == set(expected), path
        for key, item in expected.items():
            if key == "state_fingerprint":
                assert isinstance(got[key], str) and len(got[key]) == 64, path
                assert isinstance(item, str) and len(item) == 64, path
                continue
            _assert_json_close(got[key], item, f"{path}.{key}")
        return
    if isinstance(expected, list):
        assert isinstance(got, list) and len(got) == len(expected), path
        for index, item in enumerate(expected):
            _assert_json_close(got[index], item, f"{path}[{index}]")
        return
    assert got == expected, path


def test_frozen_water_and_episode() -> None:
    water = load_for_tui(str(WATER))
    for name, kwargs in (
        ("water_atom_unicode_80x24", dict(charset="unicode", width=80, height=24, display_level="atom")),
        ("water_molecule_ascii7_100x36", dict(charset="ascii7", width=100, height=36, display_level="molecule")),
    ):
        text = _picture(water, **kwargs)
        assert text == (GOLDEN / f"{name}.txt").read_text(encoding="utf-8")
    episode = _episode_stdout()
    golden = (GOLDEN / "episode_ascii7_80x24.jsonl").read_text(encoding="utf-8")
    _assert_episode_matches_golden(episode, golden)
    rows = [json.loads(line) for line in episode.splitlines() if line]
    assert [row["ok"] for row in rows] == [True, True, True, True, True]
    initial = rows[0]["observation"]
    rotated = rows[1]["observation"]
    displayed = rows[2]["observation"]
    reset = rows[3]["observation"]
    assert rotated["state_fingerprint"] != initial["state_fingerprint"]
    assert reset["state_fingerprint"] == initial["state_fingerprint"]
    assert reset["frame"]["sha256"] == initial["frame"]["sha256"]
    assert displayed["display"]["label_mode"] == "element"
    assert displayed["display"]["show_depth"] is False
    assert "renderer" not in initial
    assert "observation_profile" not in initial
    assert set(initial) == LEGACY_KEYS


def test_episode_golden_tolerates_viewport_ulp_not_picture_bytes() -> None:
    golden = (GOLDEN / "episode_ascii7_80x24.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in golden.splitlines() if line]
    viewport = rows[0]["observation"]["viewport"]
    viewport["y_min"] = math.nextafter(viewport["y_min"], math.inf)
    viewport["y_max"] = math.nextafter(viewport["y_max"], -math.inf)
    rows[0]["observation"]["state_fingerprint"] = "ab" * 32
    nudged = "".join(json.dumps(row) + "\n" for row in rows)
    _assert_episode_matches_golden(nudged, golden)

    shifted = json.loads(nudged.splitlines()[0])
    shifted["observation"]["viewport"]["y_min"] += 1e-4
    broken_viewport = json.dumps(shifted) + "\n" + "\n".join(nudged.splitlines()[1:]) + "\n"
    with pytest.raises(AssertionError):
        _assert_episode_matches_golden(broken_viewport, golden)

    picture = json.loads(nudged.splitlines()[0])
    picture["observation"]["frame"]["text"] = picture["observation"]["frame"]["text"] + " "
    broken_picture = json.dumps(picture) + "\n" + "\n".join(nudged.splitlines()[1:]) + "\n"
    with pytest.raises(AssertionError):
        _assert_episode_matches_golden(broken_picture, golden)


def test_active_path_matches_compose_frame_after_orbit() -> None:
    from mat_viewer.tui.controller import TerminalViewController

    crystal = _pair()
    controller = TerminalViewController(
        crystal, width=80, height=24, mono=True, label_mode="label", display_level="atom"
    )
    controller.orbit(yaw_deg=20.0, pitch_deg=10.0)
    points, depth = project_points(controller.camera, crystal.cart_coords)
    direct = compose_frame(
        crystal,
        controller.camera,
        points,
        depth,
        width=controller.width,
        height=controller.height,
        mono=True,
        label_mode="label",
        show_bonds=True,
        show_cell=True,
        show_minor=False,
        show_hydrogen=False,
        show_boundary=False,
        zoom=controller.camera.viewport_zoom,
        pan_x=controller.camera.pan_x,
        pan_y=controller.camera.pan_y,
        display_level=controller._display_level,
        viewport=controller._fit_viewport,
        selected_display_index=controller._selection.display_index,
        charset="ascii7",
        show_axes=True,
        show_depth=True,
    )
    assert controller.render_frame(charset="ascii7") == direct
    assert native_host_sources_bound() is False


def test_static_frame_uses_the_same_dispatch() -> None:
    from argparse import Namespace

    from mat_viewer.tui.cli import compose_static_frame

    crystal = _pair()
    camera = _camera(crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    args = Namespace(
        width=80,
        height=24,
        charset="ascii7",
        mono=False,
        no_bonds=False,
        no_cell=False,
        show_minor=False,
        hydrogen=False,
        boundary=False,
        axes=True,
        no_depth=False,
        slice_axis="off",
        slice_index=0,
        slice_width=1.0,
        renderer=None,
        renderer_config=None,
        observation_profile="legacy",
    )
    static = compose_static_frame(
        args, crystal, camera, points, depth, label_mode="label", display_level="atom"
    )
    direct = _picture(crystal, charset="ascii7", width=80, height=24, camera=camera)
    assert static == direct


def test_default_session_fingerprint_and_keys_are_legacy() -> None:
    session = TerminalSession(_pair(), width=40, height=12, charset="ascii7")
    observation = session.observe()
    payload = observation.as_dict()
    assert set(payload) == LEGACY_KEYS
    assert observation.state_fingerprint == _legacy_fingerprint(session, observation)
    assert "renderer" not in payload
    manifest = session.replay_manifest()
    assert manifest["backend_id"] == NATIVE_BACKEND_ID
    assert manifest["schema"] == "mattervis.tui.renderer_replay/v1"
    assert session.observe().as_dict()["state_fingerprint"] == observation.state_fingerprint


def test_renderer_is_not_a_session_action_and_stays_fixed() -> None:
    marks = _load_marks()
    session = TerminalSession(_pair(), width=40, height=12, charset="ascii7", renderer=marks)
    before = session.observe()
    backend = session.controller._renderer
    assert "renderer" not in TerminalSession.ACTIONS
    with pytest.raises(ValueError, match="unknown arguments"):
        session.execute(TerminalAction("set_display", {"renderer": "example.marks-v1"}))
    with pytest.raises(ValueError, match="unknown arguments"):
        session.execute(TerminalAction("set_display", {"bond_policy": "bonds_off"}))
    assert session.observe().state_fingerprint == before.state_fingerprint
    session.execute(TerminalAction("orbit", {"yaw_deg": 5.0}))
    assert session.controller._renderer is backend
    session.reset()
    assert session.controller._renderer is backend


def test_renderer_swap_preserves_camera_actions_and_changes_pixels(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(EXAMPLE_SRC))
    from mattervis_mark_renderer import create_renderer

    native = TerminalSession(_pair(), width=40, height=12, charset="ascii7")
    marks = TerminalSession(
        _pair(), width=40, height=12, charset="ascii7", renderer=create_renderer()
    )
    native_view = native.execute(TerminalAction("orbit", {"yaw_deg": 15.0, "pitch_deg": 5.0}))
    marks_view = marks.execute(TerminalAction("orbit", {"yaw_deg": 15.0, "pitch_deg": 5.0}))
    assert native_view is not None and marks_view is not None
    assert native_view.state_fingerprint == marks_view.state_fingerprint
    assert native_view.state_fingerprint == _legacy_fingerprint(native, native_view)
    assert native_view.screen_hash != marks_view.screen_hash
    assert native_view.observation.state.camera.as_dict() == marks_view.observation.state.camera.as_dict()
    text = "".join(marks_view.screen_lines)
    assert set(text) <= set(" 123#.")
    assert "C" not in text and "O" not in text
    assert any(character in text for character in "123")


def test_ascii7_external_frame_stays_bounded_and_replays() -> None:
    crystal = _pair()
    camera = _camera(crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    backend = _load_marks()
    first = render_picture(
        crystal, camera, points, depth,
        width=40, height=12, charset="ascii7", mono=True, backend=backend,
    )
    second = render_picture(
        crystal, camera, points, depth,
        width=40, height=12, charset="ascii7", mono=True, backend=backend,
    )
    assert first.text == second.text
    assert first.content_sha256 == second.content_sha256
    assert first.input_digest == second.input_digest
    assert first.view_digest == second.view_digest
    assert all(ord(character) < 128 and (character == "\n" or 0x20 <= ord(character) <= 0x7E) for character in first.text)
    native = render_picture(
        crystal, camera, points, depth, width=40, height=12, charset="ascii7", mono=True
    )
    assert native.text != first.text


def test_static_and_active_initial_frames_agree() -> None:
    from mat_viewer.tui.controller import TerminalViewController

    crystal = _pair()
    camera = _camera(crystal)
    controller = TerminalViewController(
        crystal,
        camera=camera,
        width=40,
        height=12,
        mono=True,
        label_mode="element",
        display_level="atom",
    )
    active = controller.render_frame(charset="ascii7")
    points, depth = project_points(controller.camera, crystal.cart_coords)
    static = render_picture(
        crystal,
        controller.camera,
        points,
        depth,
        width=40,
        height=12,
        mono=True,
        charset="ascii7",
        label_mode="element",
        display_level="atom",
        zoom=controller.camera.viewport_zoom,
        pan_x=controller.camera.pan_x,
        pan_y=controller.camera.pan_y,
        viewport=controller._fit_viewport,
        show_minor=False,
        show_hydrogen=False,
        show_boundary=False,
    ).text
    assert static == active
    backend = _load_marks()
    marked = render_picture(
        crystal,
        controller.camera,
        points,
        depth,
        width=40,
        height=12,
        mono=True,
        charset="ascii7",
        label_mode="element",
        display_level="atom",
        zoom=controller.camera.viewport_zoom,
        pan_x=controller.camera.pan_x,
        pan_y=controller.camera.pan_y,
        viewport=controller._fit_viewport,
        show_minor=False,
        backend=backend,
    )
    again = render_picture(
        crystal,
        controller.camera,
        points,
        depth,
        width=40,
        height=12,
        mono=True,
        charset="ascii7",
        label_mode="element",
        display_level="atom",
        zoom=controller.camera.viewport_zoom,
        pan_x=controller.camera.pan_x,
        pan_y=controller.camera.pan_y,
        viewport=controller._fit_viewport,
        show_minor=False,
        backend=backend,
    )
    assert marked.text == again.text
    assert marked.text != static


def test_render_request_excludes_hidden_chemistry_and_preserves_bonds() -> None:
    crystal = _boundary()
    crystal.chemistry = _Poison((CANARY,))
    crystal.title = CANARY
    crystal.canonical_formula = CANARY
    crystal.spacegroup = CANARY
    crystal.metadata = {
        "bond_source": CANARY,
        "gold": CANARY,
        "explicit_bond_table": False,
        "pbc": (True, True, False),
    }
    crystal.species_map = {CANARY: [0, 1]}
    crystal.source_molecule_species = {0: CANARY}
    camera = _camera(crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    request = build_render_request(
        crystal,
        camera,
        points,
        depth,
        width=40,
        height=12,
        mono=True,
        label_mode="element",
        show_bonds=True,
        show_cell=True,
        show_minor=False,
        show_hydrogen=False,
        show_boundary=True,
        zoom=1.0,
        pan_x=0.0,
        pan_y=0.0,
        display_level="atom",
        viewport=None,
        selected_display_index=None,
        selected_display_copy_id=None,
        focus_copy_ids=(),
        charset="ascii7",
        show_axes=True,
        show_depth=True,
        slice_axis="off",
        slice_index=0,
        slice_width=1.0,
        ghost_points=None,
        bond_policy="manifested",
        backend_options={},
    )
    payload = json.dumps(request.as_dict())
    assert CANARY not in payload
    assert request.display_ir.bond_graph_provenance == "unspecified"
    assert request.display_ir.bonds[0].image_translation == (1, 0, 0)
    assert request.display_ir.bonds[0].enabled is True
    assert request.display_ir.sites[1].image_shift == (1, 0, 0)
    assert request.display_ir.cell is not None
    assert request.display_ir.cell.periodic_boundary == (True, True, False)
    assert not hasattr(request, "crystal")


def test_poisoned_chemistry_does_not_change_model_observations() -> None:
    left = _pair()
    right = _pair()
    left.chemistry = _Poison((f"{CANARY}-left",))
    right.chemistry = _Poison((f"{CANARY}-right",))
    left.title = f"{CANARY}-left"
    right.title = f"{CANARY}-right"
    left.canonical_formula = f"{CANARY}-left"
    right.canonical_formula = f"{CANARY}-right"
    left.spacegroup = "P-1"
    right.spacegroup = "P1"
    left.metadata = {**left.metadata, "gold": f"{CANARY}-left"}
    right.metadata = {**right.metadata, "gold": f"{CANARY}-right"}
    left_session = TerminalSession(left, width=40, height=12, charset="ascii7", observation_profile="model")
    right_session = TerminalSession(right, width=40, height=12, charset="ascii7", observation_profile="model")
    left_payload = left_session.observe().as_dict()
    right_payload = right_session.observe().as_dict()
    assert left_payload == right_payload
    assert CANARY not in json.dumps(left_payload)
    assert left_payload["title"] == "display"
    assert left_payload["warnings"] == []
    assert left_payload["observation_profile"] == "model"
    legacy = TerminalSession(left, width=40, height=12, charset="ascii7").observe()
    assert legacy.state_fingerprint == left_session.observe().state_fingerprint
    assert CANARY in json.dumps(legacy.as_dict())
    assert legacy.screen_hash == left_session.observe().screen_hash


def test_species_labels_stay_out_of_the_request_and_external_frame(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(EXAMPLE_SRC))
    from mattervis_mark_renderer import create_renderer

    crystal = _molecule()
    crystal.species_map = {CANARY: [0], "N": [1]}
    camera = _camera(crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    rendered = render_picture(
        crystal,
        camera,
        points,
        depth,
        width=40,
        height=12,
        charset="ascii7",
        mono=True,
        display_level="molecule",
        backend=create_renderer(),
    )
    assert CANARY not in json.dumps(build_render_request(
        crystal,
        camera,
        points,
        depth,
        width=40,
        height=12,
        mono=True,
        label_mode="label",
        show_bonds=True,
        show_cell=True,
        show_minor=False,
        show_hydrogen=False,
        show_boundary=False,
        zoom=camera.viewport_zoom,
        pan_x=0.0,
        pan_y=0.0,
        display_level="molecule",
        viewport=None,
        selected_display_index=None,
        selected_display_copy_id=None,
        focus_copy_ids=(),
        charset="ascii7",
        show_axes=True,
        show_depth=True,
        slice_axis="off",
        slice_index=0,
        slice_width=1.0,
        ghost_points=None,
        bond_policy="manifested",
        backend_options={"example.marks-v1.marker": "#"},
    ).as_dict())
    assert CANARY not in rendered.text


def test_geometry_builder_does_not_touch_chemistry() -> None:
    crystal = _pair()

    class Boom:
        def __getattr__(self, name):
            raise AssertionError(f"chemistry accessed: {name}")

    crystal.chemistry = Boom()
    camera = _camera(crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    render_picture(crystal, camera, points, depth, width=20, height=8, charset="ascii7", mono=True)


def test_provided_only_disables_inferred_bonds_without_repair() -> None:
    crystal = _pair()
    original = crystal.bonds
    camera = _camera(crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    common = dict(
        width=40,
        height=12,
        mono=True,
        label_mode="label",
        show_bonds=True,
        show_cell=True,
        show_minor=False,
        show_hydrogen=False,
        show_boundary=False,
        zoom=1.0,
        pan_x=0.0,
        pan_y=0.0,
        display_level="atom",
        viewport=None,
        selected_display_index=None,
        selected_display_copy_id=None,
        focus_copy_ids=(),
        charset="ascii7",
        show_axes=True,
        show_depth=True,
        slice_axis="off",
        slice_index=0,
        slice_width=1.0,
        ghost_points=None,
        backend_options={},
    )
    limited = build_render_request(
        crystal, camera, points, depth, bond_policy="provided_only", **common
    )
    empty = CrystalIR(
        lattice=_lattice(),
        atoms=list(crystal.atoms),
        bonds=[],
        metadata={"bond_source": "distance_heuristic"},
    )
    untouched = build_render_request(
        empty, camera, points, depth, bond_policy="manifested", **common
    )
    assert limited.display_ir.bonds[0].enabled is False
    assert limited.display_ir.bonds[0].provenance == "inferred"
    assert crystal.bonds is original
    assert untouched.display_ir.bonds == ()
    shown = render_picture(crystal, camera, points, depth, bond_policy="manifested", **common).text
    hidden = render_picture(crystal, camera, points, depth, bond_policy="bonds_off", **common).text
    assert shown != hidden
    assert crystal.bonds is original


def test_missing_and_incompatible_backends_fail_closed(monkeypatch) -> None:
    def no_entries(search_path=None):
        return []

    monkeypatch.setattr("mat_viewer.tui.render_backend._entry_points", no_entries)
    with pytest.raises(RendererContractError, match="refusing to fall back"):
        resolve_renderer_binding("example.missing-v1")

    class Spoof:
        backend_id = NATIVE_BACKEND_ID
        api_version = RENDER_API_VERSION
        deterministic = True
        package_version = "0"
        implementation_digest = "cd" * 32

        def validate_options(self, options):
            return {}

        def render(self, request):
            raise AssertionError("spoofed native renderer was called")

    with pytest.raises(RendererContractError, match="reserved"):
        resolve_renderer_binding(Spoof())
    crystal = _pair()
    camera = _camera(crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    with pytest.raises(RendererContractError, match="reserved"):
        render_picture(crystal, camera, points, depth, width=20, height=8, backend=Spoof())

    bad = _external_backend(lambda self, request: "ok", api_version="mattervis.tui.render/v0")
    with pytest.raises(RendererContractError, match="not"):
        resolve_renderer_binding(bad)
    drifting = _external_backend(lambda self, request: "ok", deterministic=False)
    with pytest.raises(RendererContractError, match="not deterministic"):
        resolve_renderer_binding(drifting)
    with pytest.raises(RendererContractError, match="does not accept"):
        resolve_renderer_binding(NATIVE_BACKEND_ID, renderer_options={"extra": 1})
    with pytest.raises(RendererContractError, match="does not match the pin"):
        resolve_renderer_binding(
            NATIVE_BACKEND_ID,
            renderer_config={
                "schema": "mattervis.tui.renderer_config/v1",
                "backend_id": NATIVE_BACKEND_ID,
                "require_implementation_digest": "0" * 64,
            },
        )


def test_plugin_crash_and_malformed_frame_do_not_fall_back() -> None:
    def explode(self, request):
        assert native_host_sources_bound() is False
        raise RuntimeError("plugin exploded")

    crashing = _external_backend(explode)
    session = TerminalSession(_pair(), width=20, height=6, charset="ascii7", renderer=crashing)
    with pytest.raises(RuntimeError, match="plugin exploded"):
        session.observe()
    source = StringIO('{"action":"observe"}\n{"action":"observe"}\n')
    target = StringIO()
    run_jsonl_session(session, input_stream=source, output_stream=target)
    rows = [json.loads(line) for line in target.getvalue().splitlines()]
    assert [row["ok"] for row in rows] == [False, False]
    assert "plugin exploded" in rows[0]["error"]["message"]
    assert "observation" not in rows[0]

    def wide(self, request):
        return "x" * (request.render_options.width + 1)

    malformed = _external_backend(wide)
    with pytest.raises(RendererContractError, match="exceeds"):
        TerminalSession(_pair(), width=20, height=6, charset="ascii7", renderer=malformed).observe()

    def ansi(self, request):
        return "\x1b[31mred"

    with pytest.raises(RendererContractError, match="control character"):
        render_picture(
            _pair(),
            _camera(_pair()),
            *project_points(_camera(_pair()), _pair().cart_coords),
            width=20,
            height=4,
            charset="ascii7",
            backend=_external_backend(ansi),
        )


def test_unicode_profile_allows_braille_and_ascii7_rejects_it() -> None:
    braille = "⣿"

    def draw(self, request):
        line = braille.ljust(request.render_options.width)
        return "\n".join([line] * request.render_options.height)

    crystal = _pair()
    camera = _camera(crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    rendered = render_picture(
        crystal, camera, points, depth, width=8, height=3, charset="unicode", backend=_external_backend(draw)
    )
    assert braille in rendered.text
    with pytest.raises(RendererContractError, match="ascii7"):
        render_picture(
            crystal, camera, points, depth, width=8, height=3, charset="ascii7", backend=_external_backend(draw)
        )


def test_installed_entry_point_changes_the_screen(tmp_path: Path, monkeypatch) -> None:
    _install_mark_renderer(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    session = TerminalSession(
        _pair(),
        width=40,
        height=12,
        charset="ascii7",
        renderer="example.marks-v1",
        renderer_config=PIN,
        renderer_search_path=(str(tmp_path),),
    )
    native = TerminalSession(_pair(), width=40, height=12, charset="ascii7")
    external = session.observe()
    baseline = native.observe()
    assert external.screen_hash != baseline.screen_hash
    assert external.state_fingerprint == baseline.state_fingerprint
    assert session.replay_manifest()["backend_id"] == "example.marks-v1"
    assert session.replay_manifest()["selection"] == "entry_point"
    assert "renderer" in external.as_dict()
    text = "\n".join(external.screen_lines)
    assert "C" not in text


def test_cli_renderer_fail_closed_and_external_smoke(tmp_path: Path) -> None:
    _install_mark_renderer(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
    missing = subprocess.run(
        [
            sys.executable,
            "-m",
            "mat_viewer",
            "tui",
            str(WATER),
            "--no-interaction",
            "--renderer",
            "example.missing-v1",
            "--width",
            "40",
            "--height",
            "12",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert missing.returncode == 2
    assert missing.stdout == ""
    assert "refusing to fall back" in missing.stderr

    native = subprocess.run(
        [
            sys.executable,
            "-m",
            "mat_viewer",
            "tui",
            str(WATER),
            "--no-interaction",
            "--charset",
            "ascii7",
            "--width",
            "40",
            "--height",
            "12",
            "--level",
            "atom",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    external = subprocess.run(
        [
            sys.executable,
            "-m",
            "mat_viewer",
            "tui",
            str(WATER),
            "--no-interaction",
            "--charset",
            "ascii7",
            "--width",
            "40",
            "--height",
            "12",
            "--level",
            "atom",
            "--renderer",
            "example.marks-v1",
            "--renderer-config",
            str(PIN),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert external.stdout != native.stdout
    frame = "\n".join(external.stdout.splitlines()[1:])
    assert any(character in frame for character in "123")
    assert "H" not in frame and "O" not in frame
    assert all(
        character == "\n" or 0x20 <= ord(character) <= 0x7E for character in frame
    )


class _Poison:
    def __init__(self, warnings: tuple[str, ...]) -> None:
        self.warnings = warnings
        self.status = "explicit"


def _load_marks():
    sys.path.insert(0, str(EXAMPLE_SRC))
    from mattervis_mark_renderer import create_renderer

    return create_renderer()


if __name__ == "__main__":
    write_goldens()
