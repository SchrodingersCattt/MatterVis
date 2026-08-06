from __future__ import annotations

import copy
import itertools

import pytest

from crystal_viewer.loader import build_empty_bundle
from crystal_viewer.renderer import DISORDER_DISPATCH, MATERIAL_DISPATCH, STYLE_DISPATCH, build_figure
from crystal_viewer.render.viewport import flat_projected_pixel_scale


def _scene_template():
    scene = build_empty_bundle().scene
    scene["draw_atoms"] = [
        {
            "label": "C1",
            "elem": "C",
            "cart": [0.0, 0.0, 0.0],
            "atom_radius": 0.18,
            "color": "#555555",
            "color_light": "#888888",
            "is_minor": False,
            "uiso": 0.04,
            "U": None,
        },
        {
            "label": "O1",
            "elem": "O",
            "cart": [1.2, 0.0, 0.0],
            "atom_radius": 0.17,
            "color": "#B85060",
            "color_light": "#D48A88",
            "is_minor": True,
            "uiso": 0.04,
            "U": None,
            "occ": 0.5,
        },
    ]
    scene["bonds"] = [
        {
            "i": 0,
            "j": 1,
            "start": [0.0, 0.0, 0.0],
            "end": [1.2, 0.0, 0.0],
            "color_i": "#555555",
            "color_j": "#B85060",
            "is_minor": True,
            "occ": 0.5,
        }
    ]
    return scene


@pytest.fixture(scope="module")
def scene_template():
    """Shared base scene built once per module.

    The previous ``_scene()`` helper rebuilt the empty bundle on every
    iteration of the 50-combo cartesian-product test, which was about
    half of the test's runtime. ``build_figure`` mutates only its own
    figure, not the scene, so a single shared template is safe -- but
    we ``copy.deepcopy`` the per-call return so a future change can't
    accidentally hand the same dict to two consumers.
    """
    return _scene_template()


def _scene(scene_template):
    return copy.deepcopy(scene_template)


# Cartesian product of the three dispatch axes. Parametrised (rather
# than looped inside one big test) so a regression points at the
# offending (material, style, disorder) triple instead of failing
# the whole bundle. The full product is intentional: each
# (material, style) pair branches the renderer dispatch table, and
# disorder semantics are layered on top -- e.g. (mesh, ortep,
# color_shift) and (flat, ortep, color_shift) hit different code
# paths inside ``ortep_billboard_traces`` vs ``ortep_mesh_traces``.
_DISPATCH_TRIPLES = list(
    itertools.product(MATERIAL_DISPATCH, STYLE_DISPATCH, DISORDER_DISPATCH)
)


@pytest.mark.parametrize(
    ("material", "render_style", "disorder"),
    _DISPATCH_TRIPLES,
    ids=[f"{m}-{s}-{d}" for m, s, d in _DISPATCH_TRIPLES],
)
def test_render_dispatch_combo_builds_figure(
    scene_template, material, render_style, disorder
):
    """Every dispatch triple must produce at least one trace and
    pick the right Plotly trace family for ``material``.

    A failure here means the renderer dispatch table is missing a
    branch (e.g. someone added a new style key without wiring the
    matching trace builder), or that the topology pipeline can't
    cope with the (style, disorder) combo on a minimal 2-atom scene.
    Each parametrised case fails independently so the diff in CI
    points at the one broken triple.
    """
    fig = build_figure(
        _scene(scene_template),
        {
            "material": material,
            "style": render_style,
            "disorder": disorder,
            "atom_scale": 1.0,
            "bond_radius": 0.1,
            "axis_scale": 0.1,
            "show_axes": False,
            "show_labels": False,
            "topology_enabled": False,
        },
    )
    assert len(fig.data) > 0, (
        f"({material}, {render_style}, {disorder}) produced an empty "
        "figure -- the dispatcher dropped both atoms and bonds."
    )
    if material == "flat":
        assert any(trace.type == "scatter3d" for trace in fig.data), (
            f"flat-material build for {render_style!r} must include "
            "scatter3d traces; mesh-only output indicates the dispatch "
            "table picked the wrong material path."
        )
    else:
        assert any(trace.type == "mesh3d" for trace in fig.data), (
            f"mesh-material build for {render_style!r} must include "
            "mesh3d traces; scatter-only output indicates the dispatch "
            "table picked the wrong material path."
        )


def test_wireframe_builds_ring_and_bond_meshes_without_spheres(scene_template):
    fig = build_figure(
        _scene(scene_template),
        {
            "material": "mesh",
            "style": "wireframe",
            "disorder": "none",
            "atom_scale": 1.0,
            "bond_radius": 0.1,
            "axis_scale": 0.1,
            "show_axes": False,
            "show_labels": False,
            "topology_enabled": False,
        },
    )
    names = {trace.name for trace in fig.data if getattr(trace, "name", None)}
    assert "wireframe-atoms" in names
    assert "wireframe-bonds" in names


def test_dashed_disorder_fast_path_sets_dash_style(scene_template):
    fig = build_figure(
        _scene(scene_template),
        {
            "material": "flat",
            "style": "ball_stick",
            "disorder": "dashed_bonds",
            "atom_scale": 1.0,
            "bond_radius": 0.1,
            "axis_scale": 0.1,
            "show_axes": False,
            "show_labels": False,
            "topology_enabled": False,
        },
    )
    bond_lines = [trace for trace in fig.data if trace.type == "scatter3d" and getattr(trace, "mode", None) == "lines"]
    assert any(getattr(trace.line, "dash", None) == "dash" for trace in bond_lines)


def test_fast_ball_stick_draws_bonds_after_atoms(scene_template):
    """Fast marker atoms must not occlude short bonds in overview renders.

    Scatter3d marker diameters are fixed in screen pixels. At a fitted
    unit-cell overview they can cover an entire short N-H/O-H bond when the
    bond trace is inserted first. The fast path therefore overlays the
    endpoint-coloured bond halves after the atom markers; the mesh path keeps
    its depth-correct bond-before-atom ordering.
    """
    fig = build_figure(
        _scene(scene_template),
        {
            "material": "mesh",
            "style": "ball_stick",
            "disorder": "outline_rings",
            "fast_rendering": True,
            "atom_scale": 1.0,
            "bond_radius": 0.1,
            "axis_scale": 0.1,
            "show_axes": False,
            "show_labels": False,
            "topology_enabled": False,
        },
    )

    roles = [
        (trace.meta or {}).get("mv_role")
        if isinstance(trace.meta, dict)
        else None
        for trace in fig.data
    ]
    atom_indices = [index for index, role in enumerate(roles) if role == "atom"]
    bond_indices = [index for index, role in enumerate(roles) if role == "bond"]

    assert atom_indices
    assert bond_indices
    assert max(atom_indices) < min(bond_indices)


def test_fast_atom_scale_default_is_compact_and_cache_sensitive(scene_template):
    scene = _scene(scene_template)
    style = {
        "material": "mesh",
        "style": "ball_stick",
        "disorder": "outline_rings",
        "fast_rendering": True,
        "atom_scale": 1.0,
        "bond_radius": 0.1,
        "axis_scale": 0.1,
        "show_axes": False,
        "show_labels": False,
        "topology_enabled": False,
    }

    default_fig = build_figure(scene, style)
    larger_fig = build_figure(scene, {**style, "scatter_atom_scale": 0.8})

    def marker_sizes(fig):
        values = []
        for trace in fig.data:
            meta = trace.meta if isinstance(trace.meta, dict) else {}
            if meta.get("mv_role") != "atom" or getattr(trace, "mode", None) != "markers":
                continue
            size = trace.marker.size
            if isinstance(size, (list, tuple)):
                values.extend(float(value) for value in size)
            else:
                values.append(float(size))
        return values

    default_sizes = marker_sizes(default_fig)
    larger_sizes = marker_sizes(larger_fig)
    assert default_sizes
    projected_scale = flat_projected_pixel_scale(scene, style)
    assert max(default_sizes) == pytest.approx(2.0 * 0.17 * projected_scale * 0.45 * 1.12)
    assert min(default_sizes) < max(default_sizes)
    assert max(larger_sizes) > max(default_sizes)


def test_fast_white_bond_half_gets_background_contrast(scene_template):
    scene = _scene(scene_template)
    scene["draw_atoms"][1]["color"] = "#FFFFFF"
    scene["draw_atoms"][1]["color_light"] = "#FFFFFF"
    scene["bonds"][0]["color_j"] = "#FFFFFF"

    fig = build_figure(
        scene,
        {
            "material": "mesh",
            "style": "ball_stick",
            "disorder": "outline_rings",
            "fast_rendering": True,
            "atom_scale": 1.0,
            "bond_radius": 0.1,
            "axis_scale": 0.1,
            "show_axes": False,
            "show_labels": False,
            "topology_enabled": False,
            "background": "#FFFFFF",
        },
    )

    bond_colors = [
        str(trace.line.color).upper()
        for trace in fig.data
        if (
            isinstance(trace.meta, dict)
            and trace.meta.get("mv_role") == "bond"
            and getattr(trace, "mode", None) == "lines"
        )
    ]
    assert bond_colors
    assert "#FFFFFF" not in bond_colors
    assert "#6B7280" in bond_colors


def test_monochrome_style_renders_black_atoms_and_bonds(scene_template):
    fig = build_figure(
        _scene(scene_template),
        {
            "material": "flat",
            "style": "ball_stick",
            "disorder": "none",
            "atom_scale": 1.0,
            "bond_radius": 0.1,
            "axis_scale": 0.1,
            "show_axes": False,
            "show_labels": False,
            "topology_enabled": False,
            "monochrome": True,
        },
    )
    marker_colors = [
        trace.marker.color
        for trace in fig.data
        if trace.type == "scatter3d" and getattr(trace, "mode", None) == "markers" and getattr(trace.marker, "color", None)
    ]
    assert "#000000" in marker_colors
