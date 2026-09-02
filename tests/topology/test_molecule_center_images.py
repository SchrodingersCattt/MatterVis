from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mat_viewer.agent_topology import build_topology_data, polyhedron_summary
from mat_viewer.loader.core import build_bundle_scene
from mat_viewer.loader.structure_input import load_structure_input

_TETRAHEDRON = np.asarray(
    [
        [1.0, 1.0, 1.0],
        [1.0, -1.0, -1.0],
        [-1.0, 1.0, -1.0],
        [-1.0, -1.0, 1.0],
    ]
)


def _structure():
    source = {
        "index": 7,
        "source_molecule_index": 5,
        "formula": "C5N2",
        "species": "CN",
        "center": [0.0, 0.0, 0.0],
        "frac_center": [0.0, 0.0, 0.0],
    }
    displayed = [
        {
            **source,
            "index": 0,
            "label": "A0",
            "image_shift": [0, 0, 0],
        },
        {
            **source,
            "index": 1,
            "label": "A0@1,0,0",
            "center": [10.0, 0.0, 0.0],
            "frac_center": [1.0, 0.0, 0.0],
            # Deliberately stale relative metadata: the absolute center
            # offset is the only valid half-open-cell identity.
            "image_shift": [0, 0, 0],
        },
    ]
    bundle = SimpleNamespace(
        scene={"fragment_table": displayed},
        fragment_table=displayed,
        topology_fragment_table=[source],
        M=np.diag([10.0, 10.0, 10.0]),
    )
    return SimpleNamespace(frames=(SimpleNamespace(bundle=bundle),))


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "scripts" / "data" / "DAP-4.cif").exists(),
    reason="DAP-4 CIF not available",
)
def test_dap4_unit_cell_center_images_follow_absolute_fractional_identity():
    path = Path(__file__).resolve().parents[2] / "scripts" / "data" / "DAP-4.cif"
    loaded = load_structure_input(path)
    bundle = loaded.frames[0].bundle
    scene = build_bundle_scene(
        bundle,
        display_mode="unit_cell",
        show_hydrogen=True,
        include_boundary_replicas=True,
    )
    sources = {
        int(fragment["source_molecule_index"]): fragment
        for fragment in bundle.topology_fragment_table
    }
    displayed = [fragment for fragment in scene["fragment_table"] if fragment["formula"] == "N"]
    expected_home = sum(
        np.allclose(
            np.asarray(fragment["frac_center"], dtype=float)
            - (
                np.asarray(
                    sources[int(fragment["source_molecule_index"])]["frac_center"],
                    dtype=float,
                )
                % 1.0
            ),
            0.0,
            rtol=0.0,
            atol=1e-6,
        )
        for fragment in displayed
    )
    spec = json.dumps(
        {
            "id": "dap4-b",
            "center": "N",
            "ligand": "ClO4",
            "level": "molecule",
            "cutoff": 10.0,
            "center_images": False,
        }
    )
    strict = build_topology_data(
        loaded,
        [spec],
        display="unit_cell",
        show_hydrogen=True,
    )
    expanded = build_topology_data(
        loaded,
        [spec.replace('"center_images": false', '"center_images": true')],
        display="unit_cell",
        show_hydrogen=True,
    )
    assert expected_home < len(displayed)
    assert strict["spec_results"][0]["overlays"]
    assert polyhedron_summary(strict)[0]["displayed_centers"] == expected_home
    assert polyhedron_summary(expanded)[0]["displayed_centers"] == len(displayed)
    assert polyhedron_summary(strict)[0]["unique_source_centers"] == len(
        {int(fragment["source_molecule_index"]) for fragment in displayed}
    )


def _fake_analyze_topology(
    bundle,
    *,
    center_index,
    display_center,
    display_label,
    **kwargs,
):
    center = np.asarray(display_center, dtype=float)
    shell = center + _TETRAHEDRON
    return {
        "center_coords": center.tolist(),
        "source_center_coords": [0.0, 0.0, 0.0],
        "center_label": display_label,
        "shell_coords": shell.tolist(),
        "distances": [float(np.sqrt(3.0))] * 4,
        "hull": {
            "vertices": shell.tolist(),
            "simplices": [
                [0, 1, 2],
                [0, 1, 3],
                [0, 2, 3],
                [1, 2, 3],
            ],
        },
    }


def _spec(center_images: bool) -> str:
    return json.dumps(
        {
            "id": "a-cage",
            "center": "C5N2",
            "ligand": "ClO4",
            "level": "molecule",
            "center_images": center_images,
            "cutoff": 8.0,
        }
    )


def test_molecule_center_images_translate_complete_shells(monkeypatch) -> None:
    from mat_viewer import topology

    monkeypatch.setattr(topology, "analyze_topology", _fake_analyze_topology)
    strict = build_topology_data(_structure(), [_spec(False)])
    expanded = build_topology_data(_structure(), [_spec(True)])

    strict_overlays = strict["spec_results"][0]["overlays"]
    expanded_overlays = expanded["spec_results"][0]["overlays"]
    assert len(strict_overlays) == 1
    assert len(expanded_overlays) == 8
    assert {overlay["center_source_index"] for overlay in expanded_overlays} == {7}
    shifts = {tuple(overlay["center_image_shift"]) for overlay in expanded_overlays}
    assert shifts == {
        (a, b, c) for a in (0, 1) for b in (0, 1) for c in (0, 1)
    }
    home = next(o for o in expanded_overlays if tuple(o["center_image_shift"]) == (0, 0, 0))
    x_image = next(o for o in expanded_overlays if tuple(o["center_image_shift"]) == (1, 0, 0))
    delta = np.asarray(x_image["shell_coords"]) - np.asarray(home["shell_coords"])
    assert np.allclose(delta, [10.0, 0.0, 0.0])

    summary = polyhedron_summary(expanded)[0]
    assert summary["id"] == "a-cage"
    assert summary["displayed_centers"] == 8
    assert summary["unique_source_centers"] == 1
    assert summary["center_images"] is True
    assert {tuple(shift) for shift in summary["center_image_shifts"]} == shifts
    assert summary["effective_colors"] == ["#7C5CBF"]
    assert summary["coordination_numbers"] == [4]
