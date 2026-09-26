"""Native bond-line annotations stay separate from chemical connectivity."""

from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.render import BondStyle
from mat_viewer.render.contracts import LinePrimitive
from mat_viewer.render.planning import prepare_render


def _scene() -> dict:
    return {
        "display_mode": "cluster",
        "atoms": [
            {"elem": "H", "cart": [-0.8, 0, 0], "label": "H1"},
            {"elem": "O", "cart": [0.8, 0, 0], "label": "O2"},
        ],
        "bonds": [],
        "matrix": np.eye(3) * 10,
    }


def test_dashed_annotation_is_depth_tested_and_not_a_chemical_bond() -> None:
    scene = _scene()
    style = BondStyle(color="#777777", opacity=0.35, width_px=3, dash=(7, 5))
    scene["bond_annotations"] = [{
        "id": "H1-O2", "atom_indices": [0, 1],
        "start": [-0.8, 0, 0], "end": [0.8, 0, 0], "style": style,
    }]
    plan = prepare_render(scene, render={"show_cell": False})
    lines = [p for p in plan.primitives if p.metadata.get("kind") == "bond_annotation"]
    assert len(lines) == 1
    assert isinstance(lines[0], LinePrimitive)
    assert lines[0].dash == (7, 5)
    assert lines[0].depth_test is True
    assert lines[0].rgba[-1] == pytest.approx(0.35)
    assert lines[0].metadata["display_only"] is True
    assert lines[0].metadata["atom_indices"] == [0, 1]
    assert not any(p.metadata.get("kind") == "bond" for p in plan.primitives)
    assert scene["bonds"] == []


def test_annotation_defaults_and_invalid_styles() -> None:
    scene = _scene()
    scene["bond_annotations"] = [{
        "id": "solid", "start": [0, 0, 0], "end": [1, 0, 0],
        "style": {"dash": [], "depth_test": False},
    }]
    line = next(
        p for p in prepare_render(scene, render={"show_cell": False}).primitives
        if p.metadata.get("kind") == "bond_annotation"
    )
    assert line.dash == ()
    assert line.depth_test is False
    scene["bond_annotations"][0]["style"] = {"unknown": 2}
    with pytest.raises(ValueError, match="unknown BondStyle"):
        prepare_render(scene, render={"show_cell": False})
    with pytest.raises(ValueError, match="opacity"):
        BondStyle(opacity=1.2)


def test_annotation_does_not_change_existing_bond_primitives() -> None:
    scene = _scene()
    scene["bonds"] = [{"i": 0, "j": 1}]
    baseline = prepare_render(scene, render={"show_cell": False})
    scene["bond_annotations"] = [{
        "id": "hint", "start": [-0.8, 0, 0], "end": [0.8, 0, 0],
    }]
    annotated = prepare_render(scene, render={"show_cell": False})
    def chemical(plan):
        return [p.semantic_id for p in plan.primitives if p.metadata.get("kind") == "bond"]
    assert chemical(annotated) == chemical(baseline)
    assert len(annotated.primitives) == len(baseline.primitives) + 1
