"""Real CIF → scene → Mesh3d regressions, independent of UI delivery races."""

from copy import deepcopy
import base64
import json

import numpy as np
import pytest

from mat_viewer.app import ViewerBackend
from mat_viewer.app import backend_camera
from mat_viewer.loader import build_loaded_crystal
from mat_viewer.renderer import build_figure
from mat_viewer.scene import build_scene_from_atoms, build_scene_from_cif


@pytest.fixture
def water_path(tmp_path):
    path = tmp_path / "water.cif"
    path.write_text(
        """data_water
_cell_length_a 10
_cell_length_b 10
_cell_length_c 10
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
_chemical_formula_sum 'H2 O'
_chemical_formula_moiety 'H2 O'
_cell_formula_units_Z 1
loop_
_space_group_symop_operation_xyz
'x,y,z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
O1 O 0.5000 0.5000 0.5000 1
H1 H 0.5957 0.5000 0.5000 1
H2 H 0.4760 0.5927 0.5000 1
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def preset():
    return {
        "style": {
            "show_hydrogen": True,
            "show_labels": True,
            "show_axes": True,
            "show_axis_key": False,
            "material": "mesh",
            "style": "ball_stick",
        },
        "structures": {},
    }


def _role_traces(figure, role):
    return [
        trace for trace in figure.data
        if isinstance(trace.meta, dict) and trace.meta.get("mv_role") == role
    ]


def _trace_values(value):
    # Plotly's cache snapshot may retain binary-array envelopes instead of the
    # original NumPy arrays. Compare decoded geometry, not transport containers.
    if isinstance(value, dict) and "bdata" in value:
        array = np.frombuffer(base64.b64decode(value["bdata"]), dtype=np.dtype(value["dtype"]))
    else:
        array = np.asarray(value)
    return tuple(array.ravel().tolist())


def _mesh_signature(figure, role):
    return tuple(
        tuple(_trace_values(getattr(trace, key)) for key in ("x", "y", "z", "i", "j", "k"))
        for trace in _role_traces(figure, role)
        if trace.type == "mesh3d" and trace.visible not in (False, "legendonly")
    )


def _assert_water_scene(scene, show_h):
    assert sorted(atom["elem"] for atom in scene["draw_atoms"]) == (
        ["H", "H", "O"] if show_h else ["O"]
    )
    assert len(scene["bonds"]) == (2 if show_h else 0)
    for bond in scene["bonds"]:
        assert {scene["draw_atoms"][bond[end]]["elem"] for end in ("i", "j")} == {"O", "H"}
    assert scene["show_hydrogen"] is show_h
    assert scene["style"]["show_hydrogen"] is show_h
    # Hidden atoms and bonds remain in the canonical source, not the display.
    assert sorted(atom["elem"] for atom in scene["_canonical_source_atoms"]) == ["H", "H", "O"]
    assert len(scene["_canonical_bond_records"]) == 2


@pytest.mark.parametrize("builder", ["atoms", "cif"])
@pytest.mark.parametrize("setting", ["omitted", None, False, True])
@pytest.mark.parametrize("entry_value", [None, False, True])
def test_explicit_hydrogen_overrides_preset(water_path, preset, builder, setting, entry_value):
    if entry_value is not None:
        preset["structures"]["water"] = {"show_hydrogen": entry_value}
    original = deepcopy(preset)
    kwargs = {} if setting == "omitted" else {"show_hydrogen": setting}
    common = dict(name="water", title="Water", preset=preset, display_mode="cluster", **kwargs)
    if builder == "cif":
        scene = build_scene_from_cif(cif_path=str(water_path), **common)
    else:
        bundle = build_loaded_crystal(
            name="water", title="Water", cif_path=str(water_path),
            preset=preset, source="upload",
        )
        scene = build_scene_from_atoms(
            atoms=bundle.raw_atoms, cell=bundle.cell, M=bundle.M,
            R=np.eye(3), molcrys_analysis=bundle.molcrys_analysis, **common,
        )
    expected = (True if entry_value is None else entry_value) if setting in ("omitted", None) else setting
    _assert_water_scene(scene, expected)
    figure = build_figure(scene, scene["style"])
    assert _mesh_signature(figure, "atom")
    assert bool(_mesh_signature(figure, "bond")) is expected
    assert preset == original


@pytest.fixture
def backend(tmp_path, water_path, preset):
    preset_path = tmp_path / "preset.json"
    preset_path.write_text(json.dumps(preset), encoding="utf-8")
    backend = ViewerBackend(preset_path=str(preset_path), root_dir=str(tmp_path))
    try:
        # Real loader, with local registration to avoid upload/prewarm jobs.
        bundle = build_loaded_crystal(
            name="water", title="Water", cif_path=str(water_path),
            preset=backend.preset, source="upload",
        )
        backend.bundles[bundle.name] = bundle
        backend.structure_names.append(bundle.name)
        backend.create_scene(structure=bundle.name)
        backend.patch_state({
            "display_mode": "cluster", "material": "mesh",
            "style": "ball_stick", "fast_rendering": False,
            "topology_enabled": False,
        })
        yield backend
    finally:
        backend.close()


@pytest.fixture
def real_builds(monkeypatch):
    calls = []
    original = backend_camera.build_figure

    def record_build(*args, **kwargs):
        calls.append(None)
        return original(*args, **kwargs)

    monkeypatch.setattr(backend_camera, "build_figure", record_build)
    return calls


def test_backend_hydrogens_on_off_on_changes_meshes(backend, real_builds):
    scenes = []
    geometry = []
    for show_h in (True, False, True):
        state = backend.patch_state({"display_options": ["hydrogens", "axes"] if show_h else ["axes"]})
        scene = backend.scene_for_state(state)
        _assert_water_scene(scene, show_h)
        scenes.append(scene)
        figure, _ = backend.figure_for_state(state)
        atoms = _mesh_signature(figure, "atom")
        bonds = _mesh_signature(figure, "bond")
        assert atoms
        assert bool(bonds) is show_h
        geometry.append((atoms, bonds))
    # The vector-overlay mixin makes tab-local shallow scene dictionaries;
    # the manifested geometry still comes from the same cached base scene.
    assert scenes[0]["draw_atoms"] is scenes[2]["draw_atoms"]
    assert scenes[0]["draw_atoms"] is not scenes[1]["draw_atoms"]
    assert geometry[0] != geometry[1]
    assert geometry[0] == geometry[2]
    # Equal sphere tessellation: the off frame has exactly one of three atoms.
    def vertices(signature):
        return sum(len(trace[0]) for trace in signature)

    assert vertices(geometry[0][0]) == 3 * vertices(geometry[1][0])
    assert len(real_builds) == 2  # final on frame uses the actual figure cache


@pytest.mark.parametrize("option", ["labels", "axes"])
def test_backend_labels_and_axes_real_figure_and_cache(backend, real_builds, option):
    geometry = []
    for enabled in (False, True, False, True, False):
        options = ["hydrogens"] + ([option] if enabled else [])
        state = backend.patch_state({"display_options": options})
        figure, _ = backend.figure_for_state(state)
        geometry.append((_mesh_signature(figure, "atom"), _mesh_signature(figure, "bond")))
        labels = _role_traces(figure, "labels")
        assert labels  # actual text traces, not metadata-only placeholders
        # The existing label policy labels heavy sites, not hydrogens.
        assert {text for trace in labels for text in trace.text} == {"O1"}
        assert all(len(_trace_values(trace.x)) == len(trace.text) for trace in labels)
        assert all((trace.visible not in (False, "legendonly")) == (option == "labels" and enabled) for trace in labels)
        axes_on = option == "axes" and enabled
        meta = figure.layout.meta or {}
        assert ("compass" in meta) is axes_on
        compass_annotations = [item for item in figure.layout.annotations if item.name == "mv_compass"]
        compass_shapes = [item for item in figure.layout.shapes if item.name == "mv_compass"]
        # Native interactive frames deliberately use the SVG compass driven by
        # meta.compass, not Plotly annotations that interrupt dragging.
        assert not compass_annotations
        assert not compass_shapes
        if axes_on:
            assert meta["compass"]["labels"] == ["a", "b", "c"]
    assert geometry[0][0] and geometry[0][1]
    assert all(item == geometry[0] for item in geometry)
    assert len(real_builds) == 2  # both off and on frames revisited as cache hits