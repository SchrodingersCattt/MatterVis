"""Public disorder-component policy for polyhedron shells.

The disordered fixture stores two orientations of one cation in a single
molecule. Each orientation is a carbon tetrahedron around an ordered
nitrogen. ``major`` keeps the higher-occupancy orientation and draws the
same chlorine octahedron as the ordered control. ``exclusive`` draws one
overlay per orientation. No shell is invented when the policy still cannot
draw one.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.agent import load_structure
from mat_viewer.agent_topology import build_topology_data, parse_polyhedron_specs
from mat_viewer.cache_keys import topology_spec_geometry_key
from mat_viewer.cli import main
from mat_viewer.topology import disorder_policy as disorder_policy_module
from mat_viewer.topology.disorder_policy import (
    PolyhedronShellError,
    center_moiety_formulas,
    invoke_find_polyhedra,
    normalize_disorder_policy,
)

DISORDERED = Path("tests/fixtures/synthetic_disordered_cation.cif")
ORDERED = Path("tests/fixtures/synthetic_ordered_octahedron.cif")
DISORDERED_SHELL = '{"id":"cage","center":"C4N","ligand":"Cl","level":"molecule"}'
ORDERED_SHELL = '{"id":"cage","center":"N","ligand":"Cl","level":"molecule"}'

pytestmark = pytest.mark.filterwarnings(
    "ignore:Structure contains disorder:UserWarning"
)


def test_disorder_policy_names_match_molcryskit():
    assert normalize_disorder_policy(None) == "major"
    assert normalize_disorder_policy("exclusive") == "exclusive"
    assert normalize_disorder_policy("as_refined") == "as_refined"
    parsed = parse_polyhedron_specs(
        ['{"center":"N","ligand":"Cl","disorder_policy":"exclusive"}'],
        disorder_policy="major",
    )
    assert parsed[0]["disorder_policy"] == "exclusive"
    with pytest.raises(ValueError, match="disorder_policy"):
        parse_polyhedron_specs(['{"center":"N","ligand":"Cl","disorder_policy":"all"}'])


def test_geometry_cache_key_tracks_disorder_policy():
    base = {
        "id": "cage",
        "center_species": "N",
        "ligand_species": "Cl",
        "level": "molecule",
        "center_kind": "centroid",
    }
    assert topology_spec_geometry_key([base]) == topology_spec_geometry_key(
        [{**base, "disorder_policy": "major"}]
    )
    assert topology_spec_geometry_key([base]) != topology_spec_geometry_key(
        [{**base, "disorder_policy": "as_refined"}]
    )


def test_shell_call_forwards_disorder_policy_unchanged():
    seen: dict[str, object] = {}

    def fake(structure, central, ligand, **kwargs):
        seen["structure"] = structure
        seen["central"] = central
        seen["kwargs"] = kwargs
        return [{"coordination_number": 6}]

    crystal = object()
    records = invoke_find_polyhedra(
        fake,
        crystal,
        "C N",
        "Cl",
        level="molecule",
        disorder_policy="exclusive",
        central_indices=[0],
    )
    assert records[0]["coordination_number"] == 6
    assert seen["structure"] is crystal
    assert seen["central"] == "C N"
    assert seen["kwargs"]["disorder_policy"] == "exclusive"
    assert seen["kwargs"]["central_indices"] == [0]


class _SymbolMolecule:
    def get_chemical_symbols(self):
        return ["C", "C", "C", "C", "N"]


def test_missing_realization_api_keeps_the_as_loaded_formula(monkeypatch, caplog):
    monkeypatch.setattr(disorder_policy_module, "_missing_realization_api", False)
    real_import = __import__

    def _import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "molcrys_kit.analysis.disorder":
            raise ImportError("cannot import name 'disorder_realizations'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", _import)
    with caplog.at_level(logging.WARNING):
        assert center_moiety_formulas(_SymbolMolecule(), "major") is None
        assert center_moiety_formulas(_SymbolMolecule(), "exclusive") is None
    assert caplog.text.count("as-loaded formula") == 1


def test_realization_api_errors_are_warned(monkeypatch, caplog):
    def _boom(_molecule, _policy):
        raise TypeError("disorder_realizations signature changed")

    monkeypatch.setattr(
        disorder_policy_module, "_load_disorder_realizations", lambda: _boom
    )
    with caplog.at_level(logging.WARNING):
        assert center_moiety_formulas(_SymbolMolecule(), "major") is None
    assert "signature changed" in caplog.text


def _shell(path: Path, policy: str, spec: str):
    structure = load_structure(path)
    return build_topology_data(
        structure,
        [spec],
        disorder_policy=policy,
        display="unit_cell",
        show_hydrogen=False,
    )


def _hull_volume(shell_coords) -> float:
    from scipy.spatial import ConvexHull

    return float(ConvexHull(np.asarray(shell_coords, dtype=float)).volume)


def test_disordered_cation_major_policy_matches_the_ordered_shell():
    structure = load_structure(DISORDERED)
    bundle = structure.frames[0].bundle
    formulas = [row["formula"] for row in bundle.topology_fragment_table]
    assert formulas.count("C4N") == 1
    assert formulas.count("Cl") == 6
    cation = next(row for row in bundle.topology_fragment_table if row["formula"] == "C4N")
    molecule = bundle.molcrys_analysis.crystal.molecules[cation["source_molecule_index"]]
    assert molecule.get_chemical_formula() == "C8N"

    built = _shell(DISORDERED, "major", DISORDERED_SHELL)
    overlays = built["spec_results"][0]["overlays"]
    ordered = _shell(ORDERED, "major", ORDERED_SHELL)["spec_results"][0]["overlays"]
    assert overlays and ordered
    disordered_counts = sorted(len(item["shell_coords"]) for item in overlays)
    ordered_counts = sorted(len(item["shell_coords"]) for item in ordered)
    assert disordered_counts == ordered_counts
    assert disordered_counts[0] >= 4
    assert all(item["hull"]["simplices"] for item in overlays)
    assert all(_hull_volume(item["shell_coords"]) > 0 for item in overlays)
    from mat_viewer.topology.analysis import analyze_topology

    exclusive_analysis = analyze_topology(
        bundle,
        center_index=int(cation["index"]),
        cutoff=10.0,
        ligand_species=["Cl"],
        disorder_policy="exclusive",
        display_center=cation["center"],
        centroid_offset_frac=0.25,
    )
    assert exclusive_analysis["extra_shells"]
    assert all(
        (extra.get("hull") or {}).get("simplices")
        for extra in exclusive_analysis["extra_shells"]
    )
    exclusive = _shell(DISORDERED, "exclusive", DISORDERED_SHELL)["spec_results"][0]["overlays"]
    assert len(exclusive) > len(overlays)
    assert all(item["hull"]["simplices"] for item in exclusive)
    assert all(_hull_volume(item["shell_coords"]) > 0 for item in exclusive)


def test_ordered_octahedron_is_unchanged_by_either_policy():
    counts = []
    for policy in ("major", "as_refined"):
        overlays = _shell(ORDERED, policy, ORDERED_SHELL)["spec_results"][0]["overlays"]
        assert overlays
        assert all(item["hull"]["simplices"] for item in overlays)
        counts.append(sorted(len(item["shell_coords"]) for item in overlays))
    assert counts[0] == counts[1]
    assert counts[0][0] >= 4


def test_major_policy_does_not_invent_a_missing_shell():
    structure = load_structure(DISORDERED)
    with pytest.raises(PolyhedronShellError) as caught:
        build_topology_data(
            structure,
            ['{"id":"missing","center":"C4N","ligand":"Br","level":"molecule"}'],
            disorder_policy="major",
            display="unit_cell",
            show_hydrogen=False,
        )
    details = caught.value.to_dict()
    assert details["disorder_policy"] == "major"
    assert details["shell_points"] == 0
    assert details["ligand"] == "Br"


def _foreground_pixels(path: Path) -> int:
    from PIL import Image

    image = np.asarray(Image.open(path).convert("RGBA"))
    opaque = image[:, :, 3] > 0
    white = np.all(image[:, :, :3] == 255, axis=2)
    return int(np.count_nonzero(opaque & ~white))


def _render(tmp_path: Path, cif: Path, spec: str, *extra: str) -> tuple[Path, str]:
    output = tmp_path / "shell.png"
    main(
        [
            "render",
            str(cif),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--view",
            "unit_cell",
            "--no-hydrogen",
            "--width",
            "320",
            "--height",
            "240",
            "--scale",
            "1",
            "--polyhedron",
            spec,
            "--json",
            *extra,
        ]
    )
    return output, ""


def test_render_cli_major_policy_succeeds_for_disordered_and_ordered(tmp_path, capsys):
    output, _ = _render(
        tmp_path, DISORDERED, DISORDERED_SHELL, "--polyhedron-disorder", "major"
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert _foreground_pixels(output) > 50
    assert payload["polyhedra"][0]["coordination_numbers"][0] >= 4
    assert payload["polyhedra"][0]["disorder_policy"] == "major"
    disordered_cn = payload["polyhedra"][0]["coordination_numbers"]

    output = tmp_path / "ordered.png"
    main(
        [
            "render",
            str(ORDERED),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--view",
            "unit_cell",
            "--no-hydrogen",
            "--width",
            "320",
            "--height",
            "240",
            "--scale",
            "1",
            "--polyhedron",
            ORDERED_SHELL,
            "--polyhedron-disorder",
            "major",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert _foreground_pixels(output) > 50
    assert payload["polyhedra"][0]["coordination_numbers"] == disordered_cn


def test_render_cli_reports_structured_empty_shell(tmp_path, capsys):
    output = tmp_path / "empty.png"
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "render",
                str(DISORDERED),
                "-o",
                str(output),
                "--backend",
                "cpu",
                "--view",
                "unit_cell",
                "--polyhedron",
                '{"id":"missing","center":"C4N","ligand":"Br","level":"molecule"}',
                "--polyhedron-disorder",
                "as_refined",
                "--json",
            ]
        )
    assert caught.value.code == 2
    assert not output.exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "mattervis.error/v1"
    assert payload["ok"] is False
    assert payload["details"]["schema"] == "mattervis.polyhedron-shell-error/v1"
    assert payload["details"]["disorder_policy"] == "as_refined"
    assert payload["details"]["shell_points"] == 0
    assert payload["details"]["ligand"] == "Br"


def _tetrahedron(distance: float = 1.49) -> np.ndarray:
    raw = np.array(
        [
            [1.0, 1.0, 1.0],
            [1.0, -1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, 1.0],
        ],
        dtype=float,
    )
    raw /= np.linalg.norm(raw, axis=1)[:, None]
    return raw * distance


def _occupancy_only_cif(offset_angstrom: float, first: float, second: float) -> str:
    """NC4 cation with two occupancy-only orientations and no disorder labels.

    The second tetrahedron is translated along x. Carbon–nitrogen contacts
    stay at 1.49 angstrom for the first orientation. Chlorine ligands match
    the ordered octahedron.
    """

    cell = 20.0
    site = np.array([10.0, 10.0, 10.0])
    primary = site + _tetrahedron()
    secondary = primary + np.array([offset_angstrom, 0.0, 0.0])
    lines = [
        "data_synthetic_occupancy_only_cation",
        "_symmetry_space_group_name_H-M   'P 1'",
        "_symmetry_Int_Tables_number       1",
        "_cell_length_a                    20.0",
        "_cell_length_b                    20.0",
        "_cell_length_c                    20.0",
        "_cell_angle_alpha                 90",
        "_cell_angle_beta                  90",
        "_cell_angle_gamma                 90",
        "loop_",
        "_symmetry_equiv_pos_as_xyz",
        "x,y,z",
        "loop_",
        "_atom_site_label",
        "_atom_site_type_symbol",
        "_atom_site_fract_x",
        "_atom_site_fract_y",
        "_atom_site_fract_z",
        "_atom_site_occupancy",
        f"N1 N {site[0] / cell:.5f} {site[1] / cell:.5f} {site[2] / cell:.5f} 1.00",
    ]
    for index, (point, occupancy) in enumerate(
        [(point, first) for point in primary] + [(point, second) for point in secondary],
        start=1,
    ):
        frac = point / cell
        lines.append(
            f"C{index} C {frac[0]:.5f} {frac[1]:.5f} {frac[2]:.5f} {occupancy:.2f}"
        )
    for index, (axis, sign) in enumerate(
        [(axis, sign) for axis in range(3) for sign in (-1.0, 1.0)],
        start=1,
    ):
        point = site.copy()
        point[axis] += sign * 5.0
        frac = point / cell
        lines.append(
            f"Cl{index} Cl {frac[0]:.5f} {frac[1]:.5f} {frac[2]:.5f} 1.00"
        )
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    ("offset_angstrom", "first", "second"),
    [(0.2, 0.62, 0.38), (0.05, 0.5, 0.5)],
)
def test_occupancy_only_disorder_draws_split_orientations(
    tmp_path, offset_angstrom, first, second, capsys
):
    cif = tmp_path / "occupancy.cif"
    cif.write_text(_occupancy_only_cif(offset_angstrom, first, second), encoding="utf-8")
    structure = load_structure(cif)
    bundle = structure.frames[0].bundle
    cations = [
        row
        for row in bundle.topology_fragment_table
        if row["formula"] != "Cl"
    ]
    assert len(cations) == 1
    formula = cations[0]["formula"]
    assert formula == "C8N"
    molecule = bundle.molcrys_analysis.crystal.molecules[
        cations[0]["source_molecule_index"]
    ]
    from mat_viewer.topology.analysis import _as_loaded_molecule_formula

    assert _as_loaded_molecule_formula(molecule) == "C8N"
    assert center_moiety_formulas(molecule, "major") == ["C4N"]
    assert center_moiety_formulas(molecule, "exclusive") == ["C4N"]
    assert center_moiety_formulas(molecule, "as_refined") == ["C8N"]
    spec = json.dumps(
        {"id": "cage", "center": formula, "ligand": "Cl", "level": "molecule"}
    )
    major = _shell(cif, "major", spec)["spec_results"][0]["overlays"]
    exclusive = _shell(cif, "exclusive", spec)["spec_results"][0]["overlays"]
    assert major and exclusive
    assert len(exclusive) > len(major)
    assert all(item["hull"]["simplices"] for item in major + exclusive)
    assert all(_hull_volume(item["shell_coords"]) > 0 for item in major + exclusive)

    output = tmp_path / "occupancy.png"
    main(
        [
            "render",
            str(cif),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--view",
            "unit_cell",
            "--no-hydrogen",
            "--width",
            "320",
            "--height",
            "240",
            "--scale",
            "1",
            "--polyhedron",
            spec,
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["polyhedra"][0]["disorder_policy"] == "major"
    assert payload["polyhedra"][0]["coordination_numbers"][0] >= 4
    assert _foreground_pixels(output) > 50
