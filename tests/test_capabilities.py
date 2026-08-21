from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

import mat_viewer.capabilities as capability_module
from mat_viewer.agent import render as agent_render
from mat_viewer.capabilities import (
    CAPABILITY_REGISTRY,
    capabilities,
    requirements_for_render,
    requirements_for_tui,
    resolve_requirements,
)
from mat_viewer.cli import main
from mat_viewer.render.contracts import (
    CameraSpec,
    RENDER_RESULT_SCHEMA,
    RenderPlan,
    RenderResult,
    RenderSpec,
    ViewportPlan,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def available_core_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        capability_module, "_molcryskit_contract_available", lambda: True
    )


def test_core_static_requirements_need_no_extra() -> None:
    result = resolve_requirements(["png", "ortep", "rings", "polyhedra"])

    assert result.capabilities == ("core",)
    assert result.extras == ()
    assert result.install_command == 'python -m pip install "matter-vis"'


def test_optional_requirements_combine_minimal_extras() -> None:
    result = resolve_requirements(["cube", "mp4"])

    assert result.capabilities == ("core", "cube", "animation")
    assert result.extras == ("animation", "cube")
    assert result.install_command == (
        'python -m pip install "matter-vis[animation,cube]"'
    )


@pytest.mark.parametrize("requirement", ["web-screenshot", "static-web-export"])
def test_web_static_aliases_combine_web_and_plotly_export(requirement: str) -> None:
    result = resolve_requirements(requirement)

    assert result.capabilities == ("core", "web", "plotly-export")
    assert result.extras == ("plotly-export", "web")
    assert result.install_command == (
        'python -m pip install "matter-vis[plotly-export,web]"'
    )


def test_cube_tui_requirements_are_combined_without_loading_input() -> None:
    assert requirements_for_tui("density.cube") == ("tui", "cube")
    assert requirements_for_tui("ambiguous.dat", "cube") == ("tui", "cube")
    assert requirements_for_tui("density.cube", "extxyz") == ("tui",)
    assert resolve_requirements(requirements_for_tui("density.cube")).extras == (
        "cube",
        "tui",
    )


def test_unknown_requirement_fails_instead_of_falling_back() -> None:
    with pytest.raises(ValueError, match="unknown MatterVis requirement"):
        resolve_requirements("magic-renderer")


def test_output_backend_resolution_is_explicit() -> None:
    assert requirements_for_render("figure.svg", "cpu") == ("svg",)
    assert requirements_for_render("figure.svg", "matplotlib") == ("svg",)
    assert requirements_for_render("figure.png", "matplotlib") == ("png",)
    assert requirements_for_render("figure.html", "plotly") == ("html", "plotly")
    assert requirements_for_render("figure.png", "plotly") == (
        "png",
        "plotly-export",
    )
    with pytest.raises(ValueError, match="HTML output requires"):
        requirements_for_render("figure.html", "cpu")
    with pytest.raises(ValueError, match="Matplotlib output must be"):
        requirements_for_render("movie.gif", "matplotlib")
    with pytest.raises(ValueError, match="GIF/MP4 output requires --backend cpu"):
        requirements_for_render("movie.gif", "plotly")


def test_core_capability_probes_public_molcryskit_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        capability_module, "_molcryskit_contract_available", lambda: False
    )
    assert not CAPABILITY_REGISTRY["core"].available()
    monkeypatch.setattr(
        capability_module, "_molcryskit_contract_available", lambda: True
    )
    assert CAPABILITY_REGISTRY["core"].available()


@pytest.mark.parametrize(
    ("module_name", "record_name", "field_name"),
    [
        ("molcrys_kit.structures", "SiteRecord", "image_shift"),
        ("molcrys_kit.structures", "BondRecord", "vector_A"),
        ("molcrys_kit.analysis", "FormulaUnitMember", "species_id"),
    ],
)
def test_molcryskit_contract_probe_reports_incomplete_record_schema(
    monkeypatch,
    module_name: str,
    record_name: str,
    field_name: str,
) -> None:
    import importlib

    record_type = getattr(importlib.import_module(module_name), record_name)
    incomplete = dict(record_type.__dataclass_fields__)
    incomplete.pop(field_name)
    monkeypatch.setattr(record_type, "__dataclass_fields__", incomplete)

    assert (
        f"{record_name}.{field_name}"
        in capability_module.molcryskit_contract_missing()
    )


def test_render_check_reports_incomplete_molcryskit_schema(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from molcrys_kit.structures import SiteRecord

    incomplete = dict(SiteRecord.__dataclass_fields__)
    incomplete.pop("global_index")
    monkeypatch.setattr(SiteRecord, "__dataclass_fields__", incomplete)

    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "render",
                str(tmp_path / "not-loaded.cif"),
                "-o",
                str(tmp_path / "not-created.svg"),
                "--check",
                "--json",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["requirements"]["missing_capabilities"] == ["core"]
    assert any(
        "SiteRecord.global_index" in note
        for note in payload["requirements"]["notes"]
    )


def test_capabilities_payload_is_json_safe_and_complete() -> None:
    payload = capabilities()

    json.dumps(payload, allow_nan=False)
    assert [item["name"] for item in payload["capabilities"]] == list(
        CAPABILITY_REGISTRY
    )


def test_render_check_writes_no_file_and_does_not_require_input(
    tmp_path: Path, capsys, available_core_contract
) -> None:
    output = tmp_path / "not-created.svg"

    main(
        [
            "render",
            str(tmp_path / "not-loaded.cif"),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--check",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["schema"] == "mattervis.render-check/v1"
    assert payload["check_only"] is True
    assert payload["backend"] == "cpu"
    assert not output.exists()


def test_cube_render_check_requires_only_cube_extra(
    tmp_path: Path, capsys, available_core_contract
) -> None:
    output = tmp_path / "not-created.svg"

    main(
        [
            "render",
            str(tmp_path / "not-loaded.cube"),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--check",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["requirements"]["extras"] == ["cube"]
    assert "plotly" not in payload["requirements"]["capabilities"]
    assert not output.exists()


def test_explicit_cube_input_format_requires_only_cube_extra(
    tmp_path: Path, capsys, available_core_contract
) -> None:
    output = tmp_path / "not-created.svg"

    main(
        [
            "render",
            str(tmp_path / "ambiguous.dat"),
            "--input-format",
            "cube",
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--check",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["requirements"]["extras"] == ["cube"]
    assert not output.exists()


def test_capabilities_cli_keeps_stdout_machine_readable(
    capsys, available_core_contract
) -> None:
    main(["capabilities", "--require", "png", "ortep", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["schema"] == "mattervis.requirements/v1"
    assert payload["extras"] == []


def test_cube_tui_cli_reports_exact_combined_install_when_cube_is_missing(
    monkeypatch, capsys, available_core_contract
) -> None:
    real_find_spec = capability_module.util.find_spec

    def fake_find_spec(name: str):
        if name == "skimage":
            return None
        return real_find_spec(name)

    monkeypatch.setattr(capability_module.util, "find_spec", fake_find_spec)

    with pytest.raises(SystemExit) as exc:
        main(["tui", "not-loaded.cube", "--no-interaction"])

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert 'python -m pip install "matter-vis[cube,tui]"' in captured.err


def test_cube_tui_loader_reports_exact_combined_install_before_loading(
    monkeypatch, available_core_contract
) -> None:
    from mat_viewer.tui.loader_adapter import load_for_tui

    real_find_spec = capability_module.util.find_spec

    def fake_find_spec(name: str):
        if name == "skimage":
            return None
        return real_find_spec(name)

    monkeypatch.setattr(capability_module.util, "find_spec", fake_find_spec)

    with pytest.raises(
        RuntimeError,
        match=r'python -m pip install "matter-vis\[cube,tui\]"',
    ):
        load_for_tui("not-loaded.cube")


def test_agent_entrypoints_do_not_import_optional_frontends() -> None:
    code = """
import sys
import mat_viewer.agent
import mat_viewer.cli
import mat_viewer.app
import mat_viewer.cube
import mat_viewer.cube.core
import mat_viewer.ortep
import mat_viewer.render
import mat_viewer.render.plotly
import mat_viewer.renderer
forbidden = ('dash', 'plotly', 'kaleido', 'textual', 'skimage', 'imageio')
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise SystemExit(','.join(loaded))
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_top_level_agent_api_is_discoverable() -> None:
    import mat_viewer
    import mat_viewer.render as render_submodule

    for name in (
        "load_structure",
        "prepare_render",
        "render",
        "capabilities",
        "resolve_requirements",
    ):
        assert callable(getattr(mat_viewer, name))
    assert callable(render_submodule)
    assert callable(mat_viewer.render)
    assert mat_viewer.capabilities()["schema"] == "mattervis.capabilities/v1"


def test_top_level_callable_facades_survive_both_import_orders() -> None:
    code = """
import mat_viewer
assert callable(mat_viewer.render)
assert callable(mat_viewer.capabilities)
import mat_viewer.render
import mat_viewer.capabilities
assert callable(mat_viewer.render)
assert callable(mat_viewer.capabilities)
assert mat_viewer.capabilities()['schema'] == 'mattervis.capabilities/v1'
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_render_plan_backend_mismatch_is_fatal_before_drawing() -> None:
    plan = RenderPlan(
        width=10,
        height=10,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(
            ViewportPlan(
                "main",
                camera=CameraSpec.looking_along((0, 0, 1), up=(0, 1, 0)),
                primitives=(),
            ),
        ),
        metadata={"requested_backend": "plotly"},
    )

    with pytest.raises(ValueError, match="requested backend 'plotly'.*backend='cpu'"):
        agent_render(plan, backend="cpu")


def test_prepared_plan_backend_mismatch_is_also_fatal(monkeypatch) -> None:
    import mat_viewer.agent as agent_module

    plan = RenderPlan(
        width=10,
        height=10,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(
            ViewportPlan(
                "main",
                camera=CameraSpec.looking_along((0, 0, 1), up=(0, 1, 0)),
                primitives=(),
            ),
        ),
        metadata={"requested_backend": "plotly"},
    )
    monkeypatch.setattr(
        capability_module.CapabilitySpec, "available", lambda self: True
    )
    monkeypatch.setattr(agent_module, "prepare_render", lambda *args, **kwargs: plan)

    with pytest.raises(ValueError, match="requested backend 'plotly'.*backend='cpu'"):
        agent_module.render(object(), backend="cpu")


def test_python_render_result_contains_agent_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    import mat_viewer.render.cpu as cpu_module

    source = tmp_path / "source.cif"
    source.write_text("data_source\n", encoding="utf-8")
    camera = CameraSpec.looking_along((0, 0, 1), up=(0, 1, 0))
    plan = RenderPlan(
        width=10,
        height=10,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(ViewportPlan("main", camera=camera, primitives=()),),
        metadata={
            "requested_backend": "cpu",
            "source": str(source),
            "input_format": "cif",
            "frame_index": 0,
        },
    )
    low_level = RenderResult(
        schema=RENDER_RESULT_SCHEMA,
        backend="cpu",
        format="png",
        width=10,
        height=10,
        plan_sha256="plan",
        output_sha256="output",
        data=b"png",
    )
    monkeypatch.setattr(
        capability_module.CapabilitySpec, "available", lambda self: True
    )
    monkeypatch.setattr(cpu_module, "render", lambda *args, **kwargs: low_level)

    result = agent_render(plan, backend="cpu")

    assert result.schema == RENDER_RESULT_SCHEMA
    assert result.backend == "cpu"
    assert result.output_sha256 == "output"
    assert result.metadata["actual_backend"] == "cpu"
    assert result.metadata["camera"]["projection"] == "orthographic"
    assert result.metadata["source"]["input_format"] == "cif"
    assert result.metadata["source"]["sha256"]
    assert result.metadata["install"] == 'python -m pip install "matter-vis"'
    assert result.metadata["fallback"] is None


def test_plain_plotly_request_injects_backend_into_default_spec(monkeypatch) -> None:
    import mat_viewer.agent as agent_module
    import mat_viewer.render.plotly as plotly_module

    captured: dict[str, object] = {}
    camera = CameraSpec.looking_along((0, 0, 1), up=(0, 1, 0))
    plan = RenderPlan(
        width=10,
        height=10,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(ViewportPlan("main", camera=camera, primitives=()),),
        metadata={"requested_backend": "plotly"},
    )
    result = RenderResult(
        schema=RENDER_RESULT_SCHEMA,
        backend="plotly",
        format="html",
        width=10,
        height=10,
        plan_sha256="plan",
        output_sha256="output",
        data=b"html",
    )

    def fake_prepare(*args, **kwargs):
        captured["render_spec"] = kwargs["render_spec"]
        return plan

    monkeypatch.setattr(
        capability_module.CapabilitySpec, "available", lambda self: True
    )
    monkeypatch.setattr(agent_module, "prepare_render", fake_prepare)
    monkeypatch.setattr(plotly_module, "render", lambda *args, **kwargs: result)

    rendered = agent_module.render(object(), backend="plotly")

    assert captured["render_spec"] == {"backend": "plotly"}
    assert rendered.backend == "plotly"


def test_explicit_render_spec_backend_mismatch_is_fatal() -> None:
    with pytest.raises(ValueError, match="RenderSpec requested backend 'cpu'"):
        agent_render(
            object(),
            backend="plotly",
            render_spec=RenderSpec(backend="cpu"),
        )


def test_render_check_rejects_ignored_legacy_option(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "render",
                str(tmp_path / "unused.cif"),
                "-o",
                str(tmp_path / "unused.svg"),
                "--config",
                "style.json",
                "--check",
                "--json",
            ]
        )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert raised.value.code == 2
    assert "--config" in payload["error"]
    assert "--config" in captured.err


def test_render_check_rejects_no_axes_instead_of_ignoring_it(
    tmp_path: Path, capsys
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "render",
                str(tmp_path / "unused.cif"),
                "-o",
                str(tmp_path / "unused.svg"),
                "--no-axes",
                "--check",
                "--json",
            ]
        )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert raised.value.code == 2
    assert "--no-axes" in payload["error"]
    assert "--no-axes" in captured.err


def test_polyhedron_json_rejects_unimplemented_keys() -> None:
    from mat_viewer.agent_topology import parse_polyhedron_specs

    with pytest.raises(ValueError, match=r"unsupported key\(s\): edge_width"):
        parse_polyhedron_specs(['{"center":"Pb","ligand":"I","edge_width":2.0}'])


def test_polyhedron_json_rejects_level_specific_or_invalid_values() -> None:
    from mat_viewer.agent_topology import parse_polyhedron_specs

    with pytest.raises(ValueError, match="center_kind is only valid"):
        parse_polyhedron_specs(
            ['{"center":"Pb","ligand":"I","level":"atom","center_kind":"centroid"}']
        )
    with pytest.raises(ValueError, match="hard_cutoff must be positive"):
        parse_polyhedron_specs(['{"center":"C6N2","ligand":"ClO4","hard_cutoff":-1}'])
    parsed = parse_polyhedron_specs(
        ['{"center":"Pb","ligand":"I","fallback_max":"6.0"}']
    )
    assert parsed[0]["fallback_max"] == 6

    with pytest.raises(ValueError, match="conflicting id and spec_id"):
        parse_polyhedron_specs(
            ['{"id":"primary","spec_id":"other","center":"Pb","ligand":"I"}']
        )


def test_default_camera_fits_a_large_scene() -> None:
    from mat_viewer.cli import _camera_spec

    bundle = SimpleNamespace(
        M=np.diag([100.0, 100.0, 100.0]),
        scene={
            "bounds": {
                "center": [50.0, 50.0, 50.0],
                "mins": [0.0, 0.0, 0.0],
                "maxs": [100.0, 100.0, 100.0],
            }
        },
    )
    structure = SimpleNamespace(frames=(SimpleNamespace(bundle=bundle),))
    args = SimpleNamespace(
        width=900,
        height=720,
        show_hydrogen=False,
        show_cell=True,
        camera_position=None,
        camera_up=None,
        view_direction=None,
        camera_axis="c",
        camera_distance=1.8,
        projection="orthographic",
    )

    camera = _camera_spec(structure, args, display="unit_cell")
    distance = np.linalg.norm(np.asarray(camera.position) - np.asarray(camera.target))
    radius = np.sqrt(3.0) * 50.0
    assert distance >= 1.8 * radius
    assert camera.ortho_scale > 50.0
    assert camera.near < distance - radius
    assert camera.far > distance + radius


def test_animation_cli_passes_frame_selection_and_visual_contract(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    source = tmp_path / "frames.extxyz"
    source.write_text(
        """1
Properties=species:S:1:pos:R:3
C 0 0 0
1
Properties=species:S:1:pos:R:3
C 0.2 0 0
1
Properties=species:S:1:pos:R:3
C 0.4 0 0
1
Properties=species:S:1:pos:R:3
C 0.6 0 0
""",
        encoding="utf-8",
    )
    output = tmp_path / "selected.gif"
    captured_call: dict[str, object] = {}

    def fake_load_structure(path, **kwargs):
        indices = list(kwargs["frame_indices"])
        captured_call["frame_indices"] = indices
        frames = tuple(
            SimpleNamespace(
                index=index,
                bundle=SimpleNamespace(
                    M=np.eye(3),
                    scene={
                        "bounds": {
                            "center": [0.0, 0.0, 0.0],
                            "mins": [-1.0, -1.0, -1.0],
                            "maxs": [1.0, 1.0, 1.0],
                        }
                    },
                ),
            )
            for index in indices
        )
        return SimpleNamespace(
            path=Path(path).resolve(),
            input_format="extxyz",
            frames=frames,
            total_frames=4,
        )

    def fake_render(structure, **kwargs):
        captured_call["render_spec"] = kwargs["render_spec"]
        captured_call["fps"] = kwargs["fps"]
        Path(kwargs["output"]).write_bytes(b"GIF89a-test")
        return SimpleNamespace(
            schema=RENDER_RESULT_SCHEMA,
            backend="cpu",
            format="gif",
            width=32,
            height=32,
            plan_sha256="plan",
            output_sha256="output",
            warnings=(),
            metadata={"frame_count": len(structure.frames)},
        )

    import mat_viewer.agent as agent_module

    monkeypatch.setattr(
        capability_module.CapabilitySpec, "available", lambda self: True
    )
    monkeypatch.setattr(agent_module, "load_structure", fake_load_structure)
    monkeypatch.setattr(agent_module, "render", fake_render)

    main(
        [
            "render",
            str(source),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--frame-range",
            "1:4",
            "--stride",
            "2",
            "--fps",
            "7",
            "--style",
            "ortep",
            "--shading",
            "flat",
            "--ortep-mode",
            "ortep_hatch",
            "--aromatic-rings",
            "disk",
            "--missing-adp-policy",
            "sphere",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    spec = captured_call["render_spec"]
    assert captured_call["frame_indices"] == [1, 3]
    assert captured_call["fps"] == 7.0
    assert spec.shading == "flat"
    assert spec.ortep_mode == "hatch"
    assert spec.aromatic_rings == "disk"
    assert spec.missing_adp_policy == "sphere"
    assert payload["source"]["selected_frames"] == [1, 3]
