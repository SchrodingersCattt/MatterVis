from __future__ import annotations

import math

import numpy as np

from crystal_viewer.loader import build_bundle_scene, build_loaded_crystal
from crystal_viewer.presets import DEFAULT_STYLE
from crystal_viewer.renderer import build_figure
from crystal_viewer.render.viewport import _scene_ranges


def _aspect_tuple(fig):
    ar = fig.layout.scene.aspectratio
    return (float(ar.x), float(ar.y), float(ar.z))


def _axis_ranges(fig):
    scene = fig.layout.scene.to_plotly_json()
    return [
        [float(scene[axis]["range"][0]), float(scene[axis]["range"][1])]
        for axis in ("xaxis", "yaxis", "zaxis")
    ]


def _assert_cartesian_scale_is_isometric(fig):
    ranges = _axis_ranges(fig)
    spans = np.array([hi - lo for lo, hi in ranges], dtype=float)
    if fig.layout.scene.aspectmode == "manual":
        aspect = np.array(_aspect_tuple(fig), dtype=float)
        scale = spans / np.maximum(aspect, 1e-12)
    else:
        assert fig.layout.scene.aspectmode == "cube"
        scale = spans
    assert np.allclose(scale, scale[0], rtol=1e-6, atol=1e-6), scale


def _sy_base_style(bundle):
    return {
        **DEFAULT_STYLE,
        **bundle.scene.get("style", {}),
        "show_axes": False,
        "show_axis_key": False,
        "show_unit_cell": False,
    }


def test_unit_cell_mode_preserves_cartesian_data_unit_scale():
    """In ``display_mode='unit_cell'`` the scene IS the cell, so the user
    expects one Angstrom to have the same screen scale along Cartesian x/y/z.
    The on/off Unit Cell Box toggle must not switch aspect modes or introduce
    anisotropic data-unit scale.
    """
    bundle = build_loaded_crystal(name="SY", cif_path="scripts/data/SY.cif", title="SY")
    base = {**_sy_base_style(bundle), "display_mode": "unit_cell"}

    fig_off = build_figure(bundle.scene, base)
    fig_on = build_figure(bundle.scene, {**base, "show_unit_cell": True})

    assert fig_off.layout.scene.aspectmode == "manual"
    assert fig_on.layout.scene.aspectmode == "manual"
    assert _aspect_tuple(fig_off) == _aspect_tuple(fig_on)

    _assert_cartesian_scale_is_isometric(fig_off)
    _assert_cartesian_scale_is_isometric(fig_on)

    ranges = _axis_ranges(fig_on)
    spans = np.array([hi - lo for lo, hi in ranges], dtype=float)
    expected = tuple(float(v / spans.max()) for v in spans)
    assert _aspect_tuple(fig_on) == expected


def test_all_display_modes_preserve_cartesian_data_unit_scale():
    bundle = build_loaded_crystal(name="SY", cif_path="scripts/data/SY.cif", title="SY")

    for display_mode in ("formula_unit", "asymmetric_unit", "unit_cell", "cluster"):
        scene = build_bundle_scene(bundle, display_mode=display_mode, show_hydrogen=True)
        base = {
            **DEFAULT_STYLE,
            **scene.get("style", {}),
            "display_mode": display_mode,
            "show_axes": False,
            "show_axis_key": False,
        }
        for show_unit_cell in (False, True):
            fig = build_figure(scene, {**base, "show_unit_cell": show_unit_cell})
            _assert_cartesian_scale_is_isometric(fig)


def test_unit_cell_viewport_is_owned_by_cell_not_outside_complete_fragments():
    """Unit-cell mode may draw complete molecule images just outside the box
    to avoid chopped boundary fragments. Those images must not own the Plotly
    range, or the visible cell is compressed into a thin strip.
    """
    scene = {
        "M": np.diag([10.0, 8.0, 6.0]),
        "display_mode": "unit_cell",
        "draw_atoms": [
            {"cart": [2.0, 2.0, 2.0], "atom_radius": 0.2, "is_minor": False},
            {"cart": [100.0, 2.0, 2.0], "atom_radius": 0.2, "is_minor": False},
        ],
    }
    style = {**DEFAULT_STYLE, "display_mode": "unit_cell", "show_unit_cell": True}

    xr, yr, zr = _scene_ranges(scene, style)
    spans = [float(axis[1] - axis[0]) for axis in (xr, yr, zr)]

    assert spans[0] < 12.0
    assert spans[1] < 10.0
    assert spans[2] < 8.0
    assert xr[0] <= 0.0 <= xr[1] and xr[0] <= 10.0 <= xr[1]
    assert yr[0] <= 0.0 <= yr[1] and yr[0] <= 8.0 <= yr[1]
    assert zr[0] <= 0.0 <= zr[1] and zr[0] <= 6.0 <= zr[1]


def test_unit_cell_viewport_includes_unwrapped_boundary_fragments():
    """In ``unit_cell`` mode the renderer keeps molecules chemically
    contiguous by drawing the atoms outside the cell that "complete" a
    boundary fragment (typical methyl / phenyl tails of a cation whose
    centroid sits in the cell but whose tail wraps past the wall). The
    viewport must include those atoms, otherwise the user sees half-a-CH3
    truncated at the cell edge -- the "很多截断" complaint observed on
    MPEP. ``_scene_ranges`` lets boundary atoms within ~15% of the cell
    span past any wall extend the viewport; far-replica outliers (~10x
    that distance) are still rejected by the contract test above.
    """
    scene = {
        "M": np.diag([10.0, 8.0, 6.0]),
        "display_mode": "unit_cell",
        "draw_atoms": [
            {"cart": [2.0, 2.0, 2.0], "atom_radius": 0.2, "is_minor": False},
            # tail atom poked +1.0 Å past the +x wall (well within the
            # 15% slack of a 10 Å cell, must extend the viewport):
            {"cart": [11.0, 4.0, 3.0], "atom_radius": 0.4, "is_minor": False},
            # tail atom poked -0.6 Å past the -y wall:
            {"cart": [3.0, -0.6, 2.0], "atom_radius": 0.3, "is_minor": False},
        ],
    }
    style = {**DEFAULT_STYLE, "display_mode": "unit_cell", "show_unit_cell": True}
    xr, yr, zr = _scene_ranges(scene, style)
    assert xr[1] >= 11.0 + 0.4 - 1e-6, (
        f"viewport x_max ({xr[1]:.3f}) must wrap the +x boundary tail at "
        f"x=11.0 + radius 0.4; otherwise the methyl/phenyl group renders "
        f"as a half-clipped sphere at the cell edge."
    )
    assert yr[0] <= -0.6 - 0.3 + 1e-6, (
        f"viewport y_min ({yr[0]:.3f}) must wrap the -y boundary tail at "
        f"y=-0.6 - radius 0.3."
    )


def test_formula_unit_does_not_inherit_lattice_aspect():
    """``display_mode='formula_unit'`` shows a molecular cluster carved out
    of the unit cell; the cluster's bounding box is roughly equiaxed even
    when the host cell is wildly anisotropic (SY: |c|=24.7 Å vs |a|=8.1
    Å). The earlier ``mode != 'cluster'`` predicate in
    ``figure_axis_layout`` blanket-applied the cell's manual aspectratio
    here too, which stretched the molecules along the long c axis and
    produced the visible flattening regression. The fix narrows manual aspect
    to ``mode == 'unit_cell'``; this test pins that behaviour and
    asserts the toggle is purely a visibility change.
    """
    bundle = build_loaded_crystal(name="SY", cif_path="scripts/data/SY.cif", title="SY")
    base = {**_sy_base_style(bundle), "display_mode": "formula_unit"}

    fig_off = build_figure(bundle.scene, base)
    fig_on = build_figure(bundle.scene, {**base, "show_unit_cell": True})

    assert fig_off.layout.scene.aspectmode != "manual", (
        "formula_unit must not pin manual lattice aspect; the molecule "
        "would otherwise be squished along anisotropic cell axes."
    )
    assert fig_on.layout.scene.aspectmode != "manual", (
        "toggling Unit Cell Box must not turn on manual lattice aspect."
    )
    assert fig_off.layout.scene.aspectmode == fig_on.layout.scene.aspectmode, (
        "box on/off must not switch between aspect modes."
    )


def test_formula_unit_box_does_not_dwarf_molecule_along_long_axis():
    """``formula_unit`` mode still draws the *full* unit-cell wireframe when
    the Unit Cell Box toggle is enabled. The viewport must therefore include
    the eight cell corners; otherwise Plotly clips the box and ASU/formula
    views show an incomplete cell. Keep the separate invariant that topology
    ``extra_overlays`` do not own non-unit-cell viewports (pinned below).
    """
    bundle = build_loaded_crystal(name="SY", cif_path="scripts/data/SY.cif", title="SY")
    base = {**_sy_base_style(bundle), "display_mode": "formula_unit"}

    fig_off = build_figure(bundle.scene, base)
    fig_on = build_figure(bundle.scene, {**base, "show_unit_cell": True})

    def _ranges_and_spans(fig):
        sa = fig.layout.scene.to_plotly_json()
        ranges = []
        spans = []
        for axis in ("xaxis", "yaxis", "zaxis"):
            r = sa[axis]["range"]
            lo, hi = float(r[0]), float(r[1])
            ranges.append((lo, hi))
            spans.append(hi - lo)
        return ranges, spans

    ranges_off, spans_off = _ranges_and_spans(fig_off)
    ranges_on, spans_on = _ranges_and_spans(fig_on)
    span_off = max(spans_off)
    span_on = max(spans_on)
    assert span_on >= span_off - 1e-6, (
        f"showing the unit-cell box should not shrink the viewport "
        f"(off={span_off:.2f}, on={span_on:.2f})."
    )

    M = np.asarray(bundle.scene["M"], dtype=float)
    a, b, c = M
    corners = np.array(
        [
            np.zeros(3),
            a,
            b,
            c,
            a + b,
            a + c,
            b + c,
            a + b + c,
        ],
        dtype=float,
    )
    for axis_idx, (lo, hi) in enumerate(ranges_on):
        assert corners[:, axis_idx].min() >= lo - 1e-6
        assert corners[:, axis_idx].max() <= hi + 1e-6

    cell_axis_span = float(np.ptp(corners, axis=0).max())
    allowed = max(cell_axis_span, span_off) * 1.13 + 0.75
    assert span_on <= allowed, (
        f"formula_unit cube ({span_on:.2f}) should be bounded by the visible "
        f"cell/cluster extent ({allowed:.2f}); off-cluster overlays must not "
        f"drive this range."
    )


def test_formula_unit_polyhedra_extras_do_not_extend_scene_cube():
    """When the user enables a per-instance polyhedra overlay
    (``extra_overlays``) in ``formula_unit`` mode, the overlays sit at
    the OTHER formula-unit replicas — scattered across the entire cell.
    Letting those points push the scene cube out is what shrinks the
    on-focus cluster to a dot in the middle of the viewport. The
    ``cell_owns_cube`` predicate in ``_scene_ranges`` excludes
    ``extra_overlays`` from non-``unit_cell`` modes; this test pins it.
    """
    bundle = build_loaded_crystal(name="SY", cif_path="scripts/data/SY.cif", title="SY")
    base = {
        **_sy_base_style(bundle),
        "display_mode": "formula_unit",
        # ``build_figure`` only forwards topology_data when the overlay is
        # actually enabled; without this the test would silently pass even
        # if ``_scene_ranges`` were broken for non-unit_cell modes.
        "topology_enabled": True,
    }

    # Anchor the focus polyhedron ON the actual molecule cluster (the
    # focus shell sits on the atoms by construction in the real data
    # path; only ``extra_overlays`` -- the OTHER replicas -- are off in
    # the cell). Use the visible-atom centroid so this test isolates the
    # ``extra_overlays`` exclusion behaviour from the focus-shell path.
    from crystal_viewer.render.viewport import _visible_atoms
    atom_carts = np.array(
        [a["cart"] for a in _visible_atoms(bundle.scene, base)],
        dtype=float,
    )
    cluster_center = atom_carts.mean(axis=0)
    M = np.asarray(bundle.scene["M"], dtype=float)
    b = M[1]
    fake_topology = {
        "center_coords": cluster_center,
        "shell_coords": [cluster_center],
        # Off-cluster replicas at +b/2 and -b/2 of the cluster centroid.
        # In ``unit_cell`` mode these would push the y range to ~|b|;
        # in ``formula_unit`` mode they MUST be ignored or the molecule
        # would be dwarfed.
        "extra_overlays": [
            {"center_coords": cluster_center + b * 0.5,
             "shell_coords": [cluster_center + b * 0.9]},
            {"center_coords": cluster_center - b * 0.5,
             "shell_coords": [cluster_center - b * 0.9]},
        ],
    }

    fig_no_topo = build_figure(bundle.scene, base)
    fig_with_topo = build_figure(bundle.scene, base, topology_data=fake_topology)

    def _spans(fig):
        sa = fig.layout.scene.to_plotly_json()
        return tuple(float(sa[ax]["range"][1] - sa[ax]["range"][0]) for ax in ("xaxis", "yaxis", "zaxis"))

    s0 = _spans(fig_no_topo)
    s1 = _spans(fig_with_topo)
    for axis_name, a0, a1 in zip("xyz", s0, s1):
        assert math.isclose(a0, a1, rel_tol=1e-3, abs_tol=1e-3), (
            f"formula_unit cube along {axis_name} grew from {a0:.2f} to "
            f"{a1:.2f} when extra_overlays were added; molecules would be "
            f"pushed to one side of the viewport. extra_overlays must NOT "
            f"extend the scene cube outside ``unit_cell`` mode."
        )


def test_unit_cell_mode_polyhedra_extras_do_not_extend_scene_cube():
    """Polyhedron replicas may be drawn outside the focused cell, but they
    must not own the main viewport. Otherwise enabling the overlay makes the
    unit cell collapse into a tiny strip inside an oversized range.
    """
    bundle = build_loaded_crystal(name="SY", cif_path="scripts/data/SY.cif", title="SY")
    base = {
        **_sy_base_style(bundle),
        "display_mode": "unit_cell",
        "show_unit_cell": True,
        "topology_enabled": True,
    }

    M = np.asarray(bundle.scene["M"], dtype=float)
    far = M[0] + M[1] * 2.0 + M[2]
    fake_topology = {
        "center_coords": M.sum(axis=0) * 0.5,
        "shell_coords": [M.sum(axis=0) * 0.5],
        "extra_overlays": [{"center_coords": far, "shell_coords": [far]}],
    }

    fig_no_topo = build_figure(bundle.scene, base)
    fig_with_topo = build_figure(bundle.scene, base, topology_data=fake_topology)

    spans_no_topo = [hi - lo for lo, hi in _axis_ranges(fig_no_topo)]
    spans_with_topo = [hi - lo for lo, hi in _axis_ranges(fig_with_topo)]
    for axis_name, before, after in zip("xyz", spans_no_topo, spans_with_topo):
        assert math.isclose(before, after, rel_tol=1e-3, abs_tol=1e-3), (
            f"unit_cell cube along {axis_name} grew from {before:.2f} to "
            f"{after:.2f} when extra_overlays were added."
        )
    _assert_cartesian_scale_is_isometric(fig_with_topo)


def test_off_viewport_polyhedra_extras_are_not_drawn_as_clipped_edges():
    scene = {
        "name": "synthetic",
        "title": "Synthetic",
        "M": np.diag([10.0, 8.0, 6.0]),
        "display_mode": "unit_cell",
        "view_direction": np.array([0.0, 0.0, 1.0]),
        "up": np.array([0.0, 1.0, 0.0]),
        "draw_atoms": [
            {
                "cart": [2.0, 2.0, 2.0],
                "atom_radius": 0.2,
                "elem": "C",
                "label": "C1",
                "is_minor": False,
                "color": "#444444",
                "color_light": "#777777",
                "disorder_alpha": 1.0,
                "_depth_t": 0.5,
            }
        ],
        "bonds": [],
        "label_items": [],
    }
    style = {
        **DEFAULT_STYLE,
        "display_mode": "unit_cell",
        "show_unit_cell": True,
        "show_axes": False,
        "show_axis_key": False,
        "topology_enabled": True,
    }
    inside = {
        "center_coords": [5.0, 4.0, 3.0],
        "shell_coords": [[4.0, 4.0, 3.0], [6.0, 4.0, 3.0], [5.0, 3.0, 3.0], [5.0, 4.0, 4.0]],
        "hull": {"simplices": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]},
        "is_analysis_anchor": True,
    }
    outside = {
        "center_coords": [40.0, 4.0, 3.0],
        "shell_coords": [[39.0, 4.0, 3.0], [41.0, 4.0, 3.0], [40.0, 3.0, 3.0], [40.0, 4.0, 4.0]],
        "hull": {"simplices": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]},
        "is_analysis_anchor": False,
    }
    topology_data = {
        "center_coords": inside["center_coords"],
        "shell_coords": inside["shell_coords"],
        "distances": [1.0, 1.0, 1.0, 1.0],
        "hull": inside["hull"],
        "analysis_spec_id": "spec",
        "spec_results": [
            {
                "spec_id": "spec",
                "name": "test",
                "color": "#7C5CBF",
                "overlays": [inside, outside],
            }
        ],
    }

    fig = build_figure(scene, style, topology_data=topology_data)
    ranges = _axis_ranges(fig)
    coordination_values = []
    for trace in fig.data:
        if not str(getattr(trace, "name", "")).startswith("coordination-"):
            continue
        for values in (getattr(trace, "x", None), getattr(trace, "y", None), getattr(trace, "z", None)):
            if values is None:
                continue
            coordination_values.extend(float(value) for value in values if np.isfinite(value))

    assert coordination_values
    assert max(coordination_values) < 12.0
    assert ranges[0][1] < 12.0


def test_partially_overlapping_polyhedra_are_kept_when_pbc_neighbours_poke_out():
    """Per-fragment polyhedra in ``unit_cell`` mode have centres inside
    the cell but their MolCrysKit-supplied shell may include PBC-image
    neighbours that poke a fraction of an Angstrom past the cell wall.
    The viewport-clip predicate must not drop those overlays wholesale,
    or the user only ever sees the analysis anchor's polyhedron and
    every other tile vanishes (regression observed on MPEP C5N2 ->
    ClO4: 4 fragments displayed, only the anchor rendered because the
    other three had a single neighbour at z=18.9 vs cell zmax=16.6).

    Off-viewport replicas (centre and entire shell firmly outside the
    viewport) must still be filtered: that contract is pinned by
    ``test_off_viewport_polyhedra_extras_are_not_drawn_as_clipped_edges``
    above and we re-assert it here for symmetry.
    """
    scene = {
        "name": "synthetic",
        "title": "Synthetic",
        "M": np.diag([10.0, 8.0, 6.0]),
        "display_mode": "unit_cell",
        "view_direction": np.array([0.0, 0.0, 1.0]),
        "up": np.array([0.0, 1.0, 0.0]),
        "draw_atoms": [
            {
                "cart": [2.0, 2.0, 2.0],
                "atom_radius": 0.2,
                "elem": "C",
                "label": "C1",
                "is_minor": False,
                "color": "#444444",
                "color_light": "#777777",
                "disorder_alpha": 1.0,
                "_depth_t": 0.5,
            }
        ],
        "bonds": [],
        "label_items": [],
    }
    style = {
        **DEFAULT_STYLE,
        "display_mode": "unit_cell",
        "show_unit_cell": True,
        "show_axes": False,
        "show_axis_key": False,
        "topology_enabled": True,
    }
    anchor = {
        "center_coords": [5.0, 4.0, 3.0],
        "shell_coords": [[4.0, 4.0, 3.0], [6.0, 4.0, 3.0], [5.0, 3.0, 3.0], [5.0, 4.0, 4.0]],
        "hull": {"simplices": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]},
        "is_analysis_anchor": True,
    }
    # Centre is firmly inside the cell, but one shell vertex peeks out
    # along +z to 7.5 (cell zmax = 6.0). The previous "fully contained"
    # predicate dropped this overlay; the new "intersects" predicate
    # must keep it because the polyhedron is still mostly visible.
    pbc_poke = {
        "center_coords": [5.0, 4.0, 3.0],
        "shell_coords": [
            [4.0, 4.0, 3.0],
            [6.0, 4.0, 3.0],
            [5.0, 3.0, 3.0],
            [5.0, 4.0, 7.5],
        ],
        "hull": {"simplices": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]},
        "is_analysis_anchor": False,
    }
    far_replica = {
        "center_coords": [40.0, 4.0, 3.0],
        "shell_coords": [[39.0, 4.0, 3.0], [41.0, 4.0, 3.0], [40.0, 3.0, 3.0], [40.0, 4.0, 4.0]],
        "hull": {"simplices": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]},
        "is_analysis_anchor": False,
    }
    topology_data = {
        "center_coords": anchor["center_coords"],
        "shell_coords": anchor["shell_coords"],
        "distances": [1.0, 1.0, 1.0, 1.0],
        "hull": anchor["hull"],
        "analysis_spec_id": "spec",
        "spec_results": [
            {
                "spec_id": "spec",
                "name": "test",
                "color": "#7C5CBF",
                "overlays": [anchor, pbc_poke, far_replica],
            }
        ],
    }

    fig = build_figure(scene, style, topology_data=topology_data)
    coord_values_z: list[float] = []
    for trace in fig.data:
        if not str(getattr(trace, "name", "")).startswith("coordination-"):
            continue
        zs = getattr(trace, "z", None)
        if zs is None:
            continue
        coord_values_z.extend(float(v) for v in zs if np.isfinite(v))

    assert coord_values_z, (
        "polyhedra with PBC-image neighbours that poke just past the "
        "cell boundary should still draw (regression: only the anchor "
        "rendered on MPEP)"
    )
    # Anchor + pbc_poke contribute z values up to 7.5; far_replica
    # would dump x ~= 40 traces if it were not filtered. Assert both:
    assert max(coord_values_z) >= 7.0, (
        "the slightly off-cell pbc_poke shell vertex should appear in "
        "the coordination edges/lines"
    )
    coord_values_x: list[float] = []
    for trace in fig.data:
        if not str(getattr(trace, "name", "")).startswith("coordination-"):
            continue
        xs = getattr(trace, "x", None)
        if xs is None:
            continue
        coord_values_x.extend(float(v) for v in xs if np.isfinite(v))
    assert max(coord_values_x) < 20.0, (
        "the far_replica overlay (centre at x=40) must still be "
        "filtered out of the rendered coordination traces"
    )


def test_unit_cell_viewport_grows_to_cover_every_visible_polyhedron():
    """The viewport ranges in ``unit_cell`` mode must cover every overlay
    that ``_overlay_within_viewport`` will eventually keep, not just the
    analysis anchor's centre + shell. ``_scene_ranges`` previously only
    folded the anchor's coords into ``extras``, which left non-anchor
    overlays whose bounding box poked past the cell + slack to render
    clipped at the canvas edge (the "画布截断" follow-up report on MPEP
    after the slack/intersect viewport rewrite).

    Synthetic scene: 10 x 8 x 6 cell, anchor inside, plus three
    non-anchor overlays whose centres sit at ``z = -1``, ``y = 9`` and
    ``x = 11`` -- each just outside the cell wall but well inside the
    legitimate molecule-level packing-shell radius. The viewport must
    encompass all of them.
    """
    scene = {
        "name": "synthetic",
        "title": "Synthetic",
        "M": np.diag([10.0, 8.0, 6.0]),
        "display_mode": "unit_cell",
        "view_direction": np.array([0.0, 0.0, 1.0]),
        "up": np.array([0.0, 1.0, 0.0]),
        "draw_atoms": [
            {
                "cart": [5.0, 4.0, 3.0],
                "atom_radius": 0.2,
                "elem": "C",
                "label": "C1",
                "is_minor": False,
                "color": "#444444",
                "color_light": "#777777",
                "disorder_alpha": 1.0,
                "_depth_t": 0.5,
            }
        ],
        "bonds": [],
        "label_items": [],
    }
    style = {
        **DEFAULT_STYLE,
        "display_mode": "unit_cell",
        "show_unit_cell": True,
        "show_axes": False,
        "show_axis_key": False,
        "topology_enabled": True,
    }
    anchor = {
        "center_coords": [5.0, 4.0, 3.0],
        "shell_coords": [[4.0, 4.0, 3.0], [6.0, 4.0, 3.0], [5.0, 3.0, 3.0], [5.0, 4.0, 4.0]],
        "hull": {"simplices": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]},
        "is_analysis_anchor": True,
    }
    poke_z_low = {
        "center_coords": [5.0, 4.0, -1.0],
        "shell_coords": [[4.0, 4.0, -1.5], [6.0, 4.0, -1.5], [5.0, 3.0, -0.5], [5.0, 5.0, -0.5]],
        "hull": {"simplices": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]},
        "is_analysis_anchor": False,
    }
    poke_y_high = {
        "center_coords": [5.0, 9.0, 3.0],
        "shell_coords": [[4.5, 9.5, 2.5], [5.5, 9.5, 2.5], [5.0, 8.5, 3.5], [5.0, 9.0, 3.5]],
        "hull": {"simplices": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]},
        "is_analysis_anchor": False,
    }
    poke_x_high = {
        "center_coords": [11.0, 4.0, 3.0],
        "shell_coords": [[10.5, 4.0, 3.0], [11.5, 4.0, 3.0], [11.0, 3.5, 3.0], [11.0, 4.5, 3.0]],
        "hull": {"simplices": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]},
        "is_analysis_anchor": False,
    }
    topology_data = {
        "center_coords": anchor["center_coords"],
        "shell_coords": anchor["shell_coords"],
        "distances": [1.0, 1.0, 1.0, 1.0],
        "hull": anchor["hull"],
        "analysis_spec_id": "spec",
        "spec_results": [
            {
                "spec_id": "spec",
                "name": "test",
                "color": "#7C5CBF",
                "overlays": [anchor, poke_z_low, poke_y_high, poke_x_high],
            }
        ],
    }

    xr, yr, zr = _scene_ranges(scene, style, topology_data=topology_data)
    assert xr[1] >= 11.5, (
        f"viewport x_max={xr[1]:.2f} must cover the +x poke overlay "
        f"(shell vertex at x=11.5); without the spec_results sweep it "
        f"snapped to the cell+slack 0.15 boundary at ~11.5."
    )
    assert yr[1] >= 9.5, (
        f"viewport y_max={yr[1]:.2f} must cover the +y poke overlay"
    )
    assert zr[0] <= -1.5, (
        f"viewport z_min={zr[0]:.2f} must cover the -z poke overlay"
    )


def test_cluster_without_lattice_falls_back_to_auto_aspectmode():
    scene = {
        "name": "cluster",
        "title": "Cluster",
        "view_direction": np.array([0.0, 0.0, 1.0]),
        "up": np.array([0.0, 1.0, 0.0]),
        "display_mode": "cluster",
        "draw_atoms": [],
        "bonds": [],
        "label_items": [],
    }

    fig = build_figure(
        scene,
        {
            **DEFAULT_STYLE,
            "show_axes": False,
            "show_axis_key": False,
            "show_unit_cell": False,
        },
    )

    assert fig.layout.scene.aspectmode == "cube"
    assert "aspectratio" not in fig.layout.scene.to_plotly_json()
