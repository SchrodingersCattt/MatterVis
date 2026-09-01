from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np

from mat_viewer.agent_topology import build_topology_data, polyhedron_summary

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
            "image_shift": [1, 0, 0],
        },
    ]
    bundle = SimpleNamespace(
        scene={"fragment_table": displayed},
        fragment_table=displayed,
        topology_fragment_table=[source],
    )
    return SimpleNamespace(frames=(SimpleNamespace(bundle=bundle),))


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
    assert len(expanded_overlays) == 2
    assert {overlay["center_source_index"] for overlay in expanded_overlays} == {7}
    assert {tuple(overlay["center_image_shift"]) for overlay in expanded_overlays} == {
        (0, 0, 0),
        (1, 0, 0),
    }
    delta = np.asarray(expanded_overlays[1]["shell_coords"]) - np.asarray(
        expanded_overlays[0]["shell_coords"]
    )
    assert np.allclose(delta, [10.0, 0.0, 0.0])

    assert polyhedron_summary(expanded) == [
        {
            "id": "a-cage",
            "level": "molecule",
            "center": "C5N2",
            "ligand": "ClO4",
            "displayed_centers": 2,
            "unique_source_centers": 1,
            "center_images": True,
            "center_image_shifts": [[0, 0, 0], [1, 0, 0]],
            "effective_colors": ["#7C5CBF"],
            "coordination_numbers": [4],
        }
    ]
