"""Public disorder-component policy for polyhedron shells.

The disordered fixture stores two orientations of one cation in a single
molecule. The as-loaded formula does not match the highest-occupancy
component, so the default policy reports an empty shell. ``major`` selects
that component and the octahedron is drawable. An ordered octahedron is the
control. No shell is invented when the policy still cannot draw one.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.agent import load_structure
from mat_viewer.agent_topology import build_topology_data, parse_polyhedron_specs
from mat_viewer.cache_keys import topology_spec_geometry_key
from mat_viewer.cli import main
from mat_viewer.topology.disorder_policy import (
    PolyhedronShellError,
    invoke_find_polyhedra,
    normalize_disorder_policy,
)

DISORDERED = Path("tests/fixtures/synthetic_disordered_cation.cif")
ORDERED = Path("tests/fixtures/synthetic_ordered_octahedron.cif")
SHELL = '{"id":"cage","center":"N","ligand":"Cl","level":"molecule"}'

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


def _shell(path: Path, policy: str):
    structure = load_structure(path)
    return build_topology_data(
        structure,
        [SHELL],
        disorder_policy=policy,
        display="unit_cell",
        show_hydrogen=False,
    )


def test_disordered_cation_major_policy_matches_the_ordered_shell():
    structure = load_structure(DISORDERED)
    bundle = structure.frames[0].bundle
    formulas = [row["formula"] for row in bundle.topology_fragment_table]
    assert formulas.count("N") == 1
    assert formulas.count("Cl") == 6
    cation = next(
        index
        for index, row in enumerate(bundle.topology_fragment_table)
        if row["formula"] == "N"
    )
    molecule_index = bundle.topology_fragment_table[cation]["source_molecule_index"]
    molecule = bundle.molcrys_analysis.crystal.molecules[molecule_index]
    assert molecule.get_chemical_formula() == "C2N"

    built = _shell(DISORDERED, "major")
    overlays = built["spec_results"][0]["overlays"]
    ordered = _shell(ORDERED, "major")["spec_results"][0]["overlays"]
    assert overlays and ordered
    disordered_counts = sorted(len(item["shell_coords"]) for item in overlays)
    ordered_counts = sorted(len(item["shell_coords"]) for item in ordered)
    assert disordered_counts == ordered_counts
    assert disordered_counts[0] >= 4
    assert all(item["hull"]["simplices"] for item in overlays)
    from mat_viewer.topology.analysis import analyze_topology

    cation = next(row for row in bundle.topology_fragment_table if row["formula"] == "N")
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
    assert not (exclusive_analysis["extra_shells"][0].get("hull") or {}).get("simplices")
    exclusive = _shell(DISORDERED, "exclusive")["spec_results"][0]["overlays"]
    assert len(exclusive) == len(overlays)
    assert all(item["hull"]["simplices"] for item in exclusive)


def test_ordered_octahedron_is_unchanged_by_either_policy():
    counts = []
    for policy in ("major", "as_refined"):
        overlays = _shell(ORDERED, policy)["spec_results"][0]["overlays"]
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
            ['{"id":"missing","center":"N","ligand":"Br","level":"molecule"}'],
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


def _render(tmp_path: Path, cif: Path, *extra: str) -> tuple[Path, str]:
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
            SHELL,
            "--json",
            *extra,
        ]
    )
    return output, ""


def test_render_cli_major_policy_succeeds_for_disordered_and_ordered(tmp_path, capsys):
    output, _ = _render(tmp_path, DISORDERED, "--polyhedron-disorder", "major")
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
            SHELL,
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
                '{"id":"missing","center":"N","ligand":"Br","level":"molecule"}',
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
