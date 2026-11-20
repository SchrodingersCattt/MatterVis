"""Disorder summary names the drawn subset and does not count visual copies as sites."""

from __future__ import annotations

from pathlib import Path

import pytest

from mat_viewer.app import ViewerBackend
from mat_viewer.app.callbacks_view import register_view_callbacks
from mat_viewer.app.camera_helpers import _structure_summary
from mat_viewer.app.panel_status import atom_is_visual_copy, status_bar_parts
from mat_viewer.loader import build_bundle_scene, build_loaded_crystal
from mat_viewer.scene.drawing_identity import drawing_identity, stamp_drawing_identity
from mat_viewer.style.disorder import atom_is_disordered

COD_CAFFEINE = Path(__file__).parents[1] / "fixtures" / "cod_4504310.cif"

_ORDERED_PARTIAL_CIF = """data_ordered_partial
_symmetry_space_group_name_H-M   'P 1'
_symmetry_Int_Tables_number       1
_cell_length_a                    10.0
_cell_length_b                    10.0
_cell_length_c                    10.0
_cell_angle_alpha                 90
_cell_angle_beta                  90
_cell_angle_gamma                 90
loop_
_symmetry_equiv_pos_as_xyz
x,y,z
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0.10 0.20 0.30 0.50
"""

_DISORDERED_PARTIAL_CIF = """data_disordered_partial
_symmetry_space_group_name_H-M   'P 1'
_symmetry_Int_Tables_number       1
_cell_length_a                    10.0
_cell_length_b                    10.0
_cell_length_c                    10.0
_cell_angle_alpha                 90
_cell_angle_beta                  90
_cell_angle_gamma                 90
loop_
_symmetry_equiv_pos_as_xyz
x,y,z
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
_atom_site_disorder_assembly
_atom_site_disorder_group
C1 C 0.10 0.20 0.30 0.70 A 1
C2 C 0.40 0.50 0.60 0.30 A 2
"""


def _is_visual_copy(atom: dict) -> bool:
    return bool(
        atom.get("_is_boundary_replica")
        or atom.get("_is_framework_context_replica")
        or atom.get("_is_bonded_image_replica")
    )


def _site_key(atom: dict, ordinal: int):
    source = atom.get("_source_index")
    try:
        if source is not None:
            return int(source)
    except (TypeError, ValueError):
        pass
    return ("drawn", ordinal)


def _is_minor_site(atom: dict) -> bool:
    if "_is_minor" in atom:
        return bool(atom.get("_is_minor"))
    return bool(atom.get("is_minor"))


def _oracle(scene: dict) -> dict:
    """Count source sites from loader flags, excluding visual copies."""
    atoms = [atom for atom in scene["draw_atoms"] if isinstance(atom, dict)]
    bonds = [bond for bond in scene.get("bonds") or [] if isinstance(bond, dict)]
    home = [atom for atom in atoms if not _is_visual_copy(atom)]
    copies = [atom for atom in atoms if _is_visual_copy(atom)]
    disordered = {
        _site_key(atom, index)
        for index, atom in enumerate(home)
        if atom_is_disordered(atom)
    }
    minor = {
        _site_key(atom, index)
        for index, atom in enumerate(home)
        if _is_minor_site(atom)
    }

    def touches_copy(bond: dict) -> bool:
        for endpoint in ("i", "j"):
            index = bond.get(endpoint)
            if isinstance(index, int) and 0 <= index < len(atoms) and _is_visual_copy(atoms[index]):
                return True
        return False

    source = scene.get("_canonical_source_atoms") or []
    source_disorder = {
        index
        for index, atom in enumerate(source)
        if isinstance(atom, dict) and atom_is_disordered(atom)
    }
    drawn_source = {key for key in disordered if isinstance(key, int) and key in source_disorder}
    return {
        "home": len({_site_key(atom, index) for index, atom in enumerate(home)}),
        "copies": len(copies),
        "drawn_disorder": len(disordered),
        "minor": len(minor),
        "minor_bonds": sum(1 for bond in bonds if bond.get("is_minor") and not touches_copy(bond)),
        "inflated_disorder": sum(1 for atom in atoms if atom_is_disordered(atom)),
        "source_disorder": len(source_disorder),
        "outside": len(source_disorder - drawn_source),
    }


def test_ordered_drawn_subset_does_not_call_the_loaded_structure_ordered():
    scene = {
        "display_mode": "formula_unit",
        "draw_atoms": [
            {"is_disordered": False, "is_minor": False, "occ": 0.5, "_source_index": 0}
        ],
        "bonds": [],
        "_canonical_source_atoms": [
            {"is_disordered": False, "occ": 0.5},
            {"is_disordered": True, "disorder_resolved": True, "_is_minor": False},
            {"is_disordered": True, "disorder_resolved": True, "_is_minor": True},
        ],
    }

    summary = _structure_summary(scene)

    assert "Formula unit cluster: drawn subset has no disorder in 1 source site(s)." in summary
    assert "Loaded structure: disorder in 2 source site(s) outside this drawn subset." in summary
    assert "Disorder: none detected" not in summary
    assert "0.5" not in summary


def test_unit_cell_counts_exclude_boundary_bonded_and_framework_copies():
    scene = {
        "display_mode": "unit_cell",
        "draw_atoms": [
            {
                "is_disordered": True,
                "disorder_resolved": True,
                "is_minor": False,
                "_is_minor": False,
                "_source_index": 0,
            },
            {
                "is_disordered": True,
                "disorder_resolved": False,
                "is_minor": True,
                "_is_minor": True,
                "_source_index": 1,
            },
            {
                "is_disordered": True,
                "disorder_resolved": True,
                "is_minor": False,
                "_is_minor": False,
                "_source_index": 0,
                "_is_boundary_replica": True,
            },
            {
                "is_disordered": True,
                "disorder_resolved": False,
                "is_minor": True,
                "_is_minor": True,
                "_source_index": 1,
                "_is_bonded_image_replica": True,
            },
            {
                "is_disordered": False,
                "is_minor": False,
                "_source_index": 2,
                "_is_framework_context_replica": True,
            },
        ],
        "bonds": [
            {"i": 0, "j": 1, "is_minor": True},
            {"i": 2, "j": 3, "is_minor": True},
        ],
        "_canonical_source_atoms": [
            {"is_disordered": True, "_is_minor": False},
            {"is_disordered": True, "_is_minor": True},
            {"is_disordered": False},
        ],
        "unwrap_overflow": [[1, 2]],
    }

    summary = _structure_summary(scene)

    assert "Unit cell: drawn subset has disorder in 2 source site(s)" in summary
    assert "1 minor source atom(s)" in summary
    assert "1 minor source bond(s)" in summary
    assert "1 site(s) have no resolved major/minor assignment" in summary
    assert "3 visual copies are repeated drawings, not additional source sites." in summary
    assert "Loaded structure: disorder in 2 source site(s)." in summary
    assert "outside this drawn subset" not in summary
    assert "5 source site" not in summary
    assert "Disorder detected: 4" not in summary
    assert "1 fragment(s) kept wrapped" in summary


def test_mixed_replica_counts_use_source_site_identity():
    """Unique canonical ids, visual copies, and drawings come from one identity."""
    source = stamp_drawing_identity(
        {},
        source_site_id="0",
        image_shift=(0, 0, 0),
        instance_kind="source",
    )
    boundary = stamp_drawing_identity(
        {"_is_boundary_replica": True},
        source_site_id="0",
        image_shift=(1, 0, 0),
        instance_kind="boundary",
    )
    bonded = stamp_drawing_identity(
        {},
        source_site_id="1",
        image_shift=(0, 1, 0),
        instance_kind="bonded_image",
    )
    framework = stamp_drawing_identity(
        {},
        source_site_id="2",
        image_shift=(0, 0, 1),
        instance_kind="framework_context",
    )
    atoms = [source, boundary, bonded, framework]
    scene = {"display_mode": "unit_cell", "draw_atoms": atoms, "bonds": []}

    identities = [drawing_identity(atom) for atom in atoms]
    source_ids = {source_id for source_id, _shift, _kind in identities}

    assert identities == [
        ("0", (0, 0, 0), "source"),
        ("0", (1, 0, 0), "boundary"),
        ("1", (0, 1, 0), "bonded_image"),
        ("2", (0, 0, 1), "framework_context"),
    ]
    assert boundary["_is_boundary_replica"] is True
    assert len(source_ids) == 3
    assert sum(atom_is_visual_copy(atom) for atom in atoms) == 3
    assert len(atoms) == 4

    summary = _structure_summary(scene)
    assert "drawn subset has no disorder in 1 source site(s)." in summary
    assert "3 visual copies are repeated drawings, not additional source sites." in summary
    _label, _scope, counts = status_bar_parts({"display_mode": "unit_cell"}, scene)
    assert counts == "1 source site + 3 visual copies · 0 bonds"


def test_replica_producers_stamp_identity_without_dropping_flags():
    """Boundary, bonded-image, and framework producers share one identity."""
    import numpy as np

    from mat_viewer.render.boundary_replicas import expand_boundary_replicas
    from mat_viewer.scene.periodic import (
        _manifest_spanning_bond_context,
        _manifest_strict_bonded_images,
    )

    matrix = np.eye(3) * 10.0
    near_face = {
        "elem": "C",
        "label": "C1",
        "frac": np.array([0.01, 0.5, 0.5]),
        "cart": np.array([0.1, 5.0, 5.0]),
        "_source_index": 4,
    }
    expanded = expand_boundary_replicas([near_face], matrix)
    boundary = [atom for atom in expanded if atom.get("_is_boundary_replica")]
    assert boundary
    assert drawing_identity(boundary[0]) == ("4", (1, 0, 0), "boundary")
    assert boundary[0]["_is_boundary_replica"] is True
    assert drawing_identity(near_face)[2] == "source"

    left = {
        "elem": "C",
        "dg": ".",
        "da": ".",
        "frac": np.array([0.9, 0.5, 0.5]),
        "cart": np.array([9.0, 5.0, 5.0]),
        "_source_index": 0,
        "_strict_unit_cell": True,
        "_wrapped_frac": np.array([0.9, 0.5, 0.5]),
    }
    right = {
        "elem": "C",
        "dg": ".",
        "da": ".",
        "frac": np.array([0.1, 0.5, 0.5]),
        "cart": np.array([1.0, 5.0, 5.0]),
        "_source_index": 1,
        "_strict_unit_cell": True,
        "_wrapped_frac": np.array([0.1, 0.5, 0.5]),
    }
    bonded_atoms = [dict(left), dict(right)]
    added = _manifest_strict_bonded_images(
        bonded_atoms,
        [left, right],
        matrix,
        [{"left": 0, "right": 1, "right_image_shift": [1, 0, 0]}],
    )
    assert added >= 1
    bonded_copies = [atom for atom in bonded_atoms if atom.get("instance_kind") == "bonded_image"]
    assert bonded_copies
    assert all(atom["_is_bonded_image_replica"] for atom in bonded_copies)
    assert {drawing_identity(atom)[2] for atom in bonded_copies} == {"bonded_image"}
    assert {drawing_identity(atom)[0] for atom in bonded_copies} <= {"0", "1"}

    home = {
        "elem": "C",
        "frac": np.array([0.5, 0.5, 0.5]),
        "cart": np.array([5.0, 5.0, 5.0]),
        "_source_index": 2,
        "_cell_spanning_component": True,
        "_wrapped_frac": np.array([0.5, 0.5, 0.5]),
    }
    framework_atoms = [dict(home)]
    context = _manifest_spanning_bond_context(
        framework_atoms,
        [{}, {}, home],
        matrix,
        [{"left": 2, "right": 2, "right_image_shift": [1, 0, 0]}],
        [],
    )
    assert context >= 1
    framework_copies = [
        atom for atom in framework_atoms if atom.get("instance_kind") == "framework_context"
    ]
    assert framework_copies
    sample = framework_copies[0]
    assert drawing_identity(sample)[0] == "2"
    assert drawing_identity(sample)[2] == "framework_context"
    assert sample["_is_boundary_replica"] is True
    assert sample["_is_framework_context_replica"] is True


def test_missing_source_atoms_do_not_imply_the_loaded_structure_is_ordered():
    scene = {
        "display_mode": "cluster",
        "draw_atoms": [{"is_disordered": False, "is_minor": False}],
        "bonds": [],
    }

    summary = _structure_summary(scene)

    assert "Isolated cluster: drawn subset has no disorder in 1 source site(s)." in summary
    assert "does not establish that the loaded structure is ordered" in summary
    assert "Disorder: none detected" not in summary


def test_known_ordered_source_is_explicit():
    scene = {
        "display_mode": "asymmetric_unit",
        "draw_atoms": [{"is_disordered": False, "is_minor": False, "occ": 1.0, "_source_index": 0}],
        "bonds": [],
        "_canonical_source_atoms": [{"is_disordered": False, "occ": 1.0}],
    }

    summary = _structure_summary(scene)

    assert "Asymmetric unit: drawn subset has no disorder in 1 source site(s)." in summary
    assert "Loaded structure: no disorder detected." in summary
    assert "Disorder: none detected" not in summary


def _write_cif(tmp_path: Path, text: str) -> str:
    path = tmp_path / "case.cif"
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.filterwarnings("ignore:Structure contains disorder:UserWarning")
def test_lone_partial_occupancy_stays_ordered(tmp_path: Path):
    bundle = build_loaded_crystal(
        name="ordered-partial",
        cif_path=_write_cif(tmp_path, _ORDERED_PARTIAL_CIF),
        title="ordered partial",
    )
    assert bundle.raw_atoms
    assert all(not atom_is_disordered(atom) for atom in bundle.raw_atoms)
    scene = build_bundle_scene(bundle, display_mode="unit_cell", show_hydrogen=True)

    summary = _structure_summary(scene)

    assert "Loaded structure: no disorder detected." in summary
    assert "has disorder in" not in summary
    assert "Disorder: none detected" not in summary
    assert float(bundle.raw_atoms[0]["occ"]) < 1.0


@pytest.mark.filterwarnings("ignore:Structure contains disorder:UserWarning")
def test_explicit_partial_occupancy_alternatives_stay_disordered(tmp_path: Path):
    bundle = build_loaded_crystal(
        name="disordered-partial",
        cif_path=_write_cif(tmp_path, _DISORDERED_PARTIAL_CIF),
        title="disordered partial",
    )
    disordered = [atom for atom in bundle.raw_atoms if atom_is_disordered(atom)]
    assert disordered
    scene = build_bundle_scene(bundle, display_mode="formula_unit", show_hydrogen=True)

    summary = _structure_summary(scene)

    assert "Loaded structure: disorder in" in summary
    assert "Loaded structure: no disorder detected." not in summary


@pytest.mark.filterwarnings("ignore:Structure contains disorder:UserWarning")
def test_public_caffeine_scopes_keep_source_disorder_and_drop_visual_copies():
    bundle = build_loaded_crystal(
        name="cod4504310",
        cif_path=str(COD_CAFFEINE),
        title="COD 4504310",
    )
    summaries = {}
    counts = {}
    for mode in ("formula_unit", "unit_cell", "asymmetric_unit", "cluster"):
        scene = build_bundle_scene(bundle, display_mode=mode, show_hydrogen=True)
        summaries[mode] = _structure_summary(scene)
        counts[mode] = _oracle(scene)

    formula = summaries["formula_unit"]
    formula_counts = counts["formula_unit"]
    assert formula_counts["drawn_disorder"] == 0
    assert formula_counts["source_disorder"] > 0
    assert formula_counts["copies"] == 0
    assert (
        "Formula unit cluster: drawn subset has no disorder in "
        f"{formula_counts['home']} source site(s)."
    ) in formula
    assert (
        "Loaded structure: disorder in "
        f"{formula_counts['source_disorder']} source site(s) outside this drawn subset."
    ) in formula
    assert "Disorder: none detected" not in formula

    cell = summaries["unit_cell"]
    cell_counts = counts["unit_cell"]
    assert cell_counts["copies"] > 0
    assert cell_counts["drawn_disorder"] < cell_counts["inflated_disorder"]
    assert cell_counts["outside"] == 0
    assert (
        "Unit cell: drawn subset has disorder in "
        f"{cell_counts['drawn_disorder']} source site(s), "
        f"{cell_counts['minor']} minor source atom(s), "
        f"{cell_counts['minor_bonds']} minor source bond(s)."
    ) in cell
    assert f"{cell_counts['copies']} visual copies are repeated drawings" in cell
    assert (
        f"Loaded structure: disorder in {cell_counts['source_disorder']} source site(s)."
    ) in cell
    assert "outside this drawn subset" not in cell
    assert str(cell_counts["inflated_disorder"]) not in cell

    asymmetric = summaries["asymmetric_unit"]
    asymmetric_counts = counts["asymmetric_unit"]
    assert 0 < asymmetric_counts["drawn_disorder"] < asymmetric_counts["source_disorder"]
    assert "Asymmetric unit: drawn subset has disorder in" in asymmetric
    assert (
        f"{asymmetric_counts['outside']} of those source site(s) are not in this drawn subset."
    ) in asymmetric

    cluster = summaries["cluster"]
    cluster_counts = counts["cluster"]
    assert cluster_counts["drawn_disorder"] == cluster_counts["source_disorder"]
    assert cluster_counts["copies"] == 0
    assert "Isolated cluster: drawn subset has disorder in" in cluster
    assert "outside this drawn subset" not in cluster
    assert (
        f"Loaded structure: disorder in {cluster_counts['source_disorder']} source site(s)."
    ) in cluster

    assert len({counts[mode]["source_disorder"] for mode in counts}) == 1
    assert len(summaries) == 4


class _CallbackRegistry:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def register(fn):
            self.callbacks[fn.__name__] = fn
            return fn

        return register


def test_scope_switch_keeps_the_summary_on_the_drawn_scene(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()
    formula = {
        "display_mode": "formula_unit",
        "draw_atoms": [{"is_disordered": False, "is_minor": False, "_source_index": 0}],
        "bonds": [],
        "_canonical_source_atoms": [
            {"is_disordered": False},
            {"is_disordered": True, "disorder_resolved": True, "_is_minor": True},
        ],
    }
    unit = {
        "display_mode": "unit_cell",
        "draw_atoms": [
            {
                "is_disordered": True,
                "disorder_resolved": True,
                "is_minor": True,
                "_is_minor": True,
                "_source_index": 1,
            },
            {
                "is_disordered": True,
                "disorder_resolved": True,
                "is_minor": True,
                "_is_minor": True,
                "_source_index": 1,
                "_is_boundary_replica": True,
            },
        ],
        "bonds": [],
        "_canonical_source_atoms": formula["_canonical_source_atoms"],
    }
    scenes = {"formula_unit": formula, "unit_cell": unit}

    def scene_for_state(state=None):
        state = backend.get_state(scene_id) if state is None else state
        return scenes[state["display_mode"]]

    backend.scene_for_state = scene_for_state
    registry = _CallbackRegistry()
    register_view_callbacks(registry, backend)
    update_view = registry.callbacks["update_view"]
    figure = {
        "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
        "layout": {"scene": {"camera": {}}},
    }
    try:
        state = backend.get_state(scene_id)
        backend.broadcast_figure(scene_id=scene_id, figure=figure, state=state)
        _fig, _hist, _md, formula_summary = update_view({"scene_id": scene_id}, None, None)
        assert "Formula unit cluster" in formula_summary
        assert "outside this drawn subset" in formula_summary

        backend.patch_state({"display_mode": "unit_cell"}, scene_id=scene_id, broadcast=False)
        state = backend.get_state(scene_id)
        backend.broadcast_figure(scene_id=scene_id, figure=figure, state=state)
        _fig, _hist, _md, cell_summary = update_view({"scene_id": scene_id}, None, None)
        assert "Unit cell: drawn subset has disorder in 1 source site(s)" in cell_summary
        assert "1 visual copy" in cell_summary

        _fig, _hist, _md, cached_summary = update_view({"scene_id": scene_id}, None, None)
        assert cached_summary == cell_summary
        assert "Formula unit cluster" not in cached_summary

        backend.patch_state({"display_mode": "formula_unit"}, scene_id=scene_id, broadcast=False)
        pending = update_view({"scene_id": scene_id}, None, None)
        assert pending[0] is pending[1] is pending[2]
        from dash import no_update

        assert pending[0] is no_update
        assert "Formula unit cluster" in pending[3]
        assert "outside this drawn subset" in pending[3]
    finally:
        backend.close()


def test_missing_ids_and_repeated_labels_do_not_merge_source_sites():
    """A blank id is private to that drawing. The same label is not an id."""
    from mat_viewer.app.panel_status import _source_site_count

    unlabeled = [
        {"label": "C1", "elem": "C"},
        {"label": "C1", "elem": "C"},
    ]
    labeled = [
        stamp_drawing_identity(
            {"label": "C1"},
            source_site_id="site-a",
            image_shift=(0, 0, 0),
            instance_kind="source",
        ),
        stamp_drawing_identity(
            {"label": "C1"},
            source_site_id="site-b",
            image_shift=(0, 0, 0),
            instance_kind="source",
        ),
    ]
    assert _source_site_count(unlabeled) == 2
    assert _source_site_count(labeled) == 2
    assert {drawing_identity(atom)[0] for atom in labeled} == {"site-a", "site-b"}


def test_several_images_of_one_site_count_as_one_source_site():
    atoms = [
        stamp_drawing_identity(
            {"label": "C1"},
            source_site_id="7",
            image_shift=(0, 0, 0),
            instance_kind="source",
        ),
        stamp_drawing_identity(
            {"label": "C1", "_is_boundary_replica": True},
            source_site_id="7",
            image_shift=(1, 0, 0),
            instance_kind="boundary",
        ),
        stamp_drawing_identity(
            {"label": "C1-image"},
            source_site_id="7",
            image_shift=(0, -1, 0),
            instance_kind="bonded_image",
        ),
    ]
    scene = {"display_mode": "unit_cell", "draw_atoms": atoms, "bonds": []}
    summary = _structure_summary(scene)
    assert "no disorder in 1 source site(s)" in summary
    assert "2 visual copies" in summary
    _label, _scope, counts = status_bar_parts({"display_mode": "unit_cell"}, scene)
    assert counts.startswith("1 source site + 2 visual copies")
    shifts = {drawing_identity(atom)[1] for atom in atoms}
    assert shifts == {(0, 0, 0), (1, 0, 0), (0, -1, 0)}


def test_legacy_flags_read_image_shift_until_identity_is_stamped():
    """Flag fallback is compatibility, and it still keeps the image axis."""
    legacy = {
        "_source_index": 4,
        "_image_shift": (0, 1, 0),
        "_is_bonded_image_replica": True,
        "_is_boundary_replica": True,
    }
    assert drawing_identity(legacy) == ("4", (0, 1, 0), "boundary")
    explicit = dict(legacy)
    stamp_drawing_identity(
        explicit,
        source_site_id="4",
        image_shift=(0, 1, 0),
        instance_kind="framework_context",
    )
    assert drawing_identity(explicit) == ("4", (0, 1, 0), "framework_context")
    from mat_viewer.scene.drawing_identity import ensure_source_identity

    kept = ensure_source_identity([dict(legacy)])
    assert drawing_identity(kept[0]) == ("4", (0, 1, 0), "boundary")
    assert "instance_kind" not in kept[0]


def test_display_mode_switch_stamps_source_identity_and_keeps_copies():
    import numpy as np

    from mat_viewer.render.display_modes import selected_atoms_for_mode

    matrix = np.eye(3) * 10.0
    atoms = [
        {
            "elem": "C",
            "label": "C1",
            "frac": np.array([0.01, 0.5, 0.5]),
            "cart": np.array([0.1, 5.0, 5.0]),
            "_source_index": 0,
        },
        {
            "elem": "C",
            "label": "C1",
            "frac": np.array([0.5, 0.2, 0.5]),
            "cart": np.array([5.0, 2.0, 5.0]),
            "_source_index": 1,
        },
    ]
    unit = selected_atoms_for_mode(
        None,
        atoms,
        matrix,
        None,
        display_mode="unit_cell",
        include_boundary_replicas=True,
    )
    identities = [drawing_identity(atom) for atom in unit]
    sources = [item for item in identities if item[2] == "source"]
    copies = [item for item in identities if item[2] != "source"]
    assert {item[0] for item in sources} == {"0", "1"}
    assert ("0", (0, 0, 0), "source") in identities
    assert any(item[0] == "0" and item[2] == "boundary" for item in copies)
    assert all(item[2] == "source" or item[1] != (0, 0, 0) for item in identities)

    cluster = selected_atoms_for_mode(
        None,
        [
            {"elem": "O", "label": "O1"},
            {"elem": "O", "label": "O1"},
        ],
        matrix,
        None,
        display_mode="cluster",
    )
    assert [drawing_identity(atom)[2] for atom in cluster] == ["source", "source"]
    assert _structure_summary(
        {"display_mode": "cluster", "draw_atoms": cluster, "bonds": []}
    ).startswith("Isolated cluster: drawn subset has no disorder in 2 source site(s).")

    wrapped = selected_atoms_for_mode(
        None,
        atoms,
        matrix,
        None,
        display_mode="unit_cell",
        include_boundary_replicas=False,
        lattice_site_display=True,
    )
    assert {drawing_identity(atom) for atom in wrapped} == {
        ("0", (0, 0, 0), "source"),
        ("1", (0, 0, 0), "source"),
    }
