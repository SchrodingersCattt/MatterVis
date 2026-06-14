# ruff: noqa: F401,F405
from __future__ import annotations

from ..render.scene_traces import *  # noqa: F403
from ..render.style import style_from_controls
from ..render.topology import topology_histogram_figure, topology_results_markdown
from ..render.compass import (
    _COMPASS_ITEM_NAME,
    axis_key_overlay,
    compass_clientside_context,
    compose_axis_key_layout,
)
from ..render.viewport import (
    _axis_cube_scale,
    _camera_axis_projections,
    _normalize,
    _plotly_camera_from_scene,
    _scene_ranges,
    _visible_atoms,
    cell_aspect_ratio,
    figure_axis_layout,
    uniform_viewport,
)

def build_row_figure(
    scene_style_pairs: list[tuple[dict, dict]],
    bgcolor: str = "#FFFFFF",
) -> go.Figure:
    """Pack N scenes side-by-side in a 1×N Plotly subplot figure.

    Each scene gets its own 3D scene (``scene``, ``scene2``, …) with an
    independent camera and viewport.  Calling code should call
    :func:`uniform_viewport` on all scenes *before* this function so the
    rendered structures share a common physical scale.

    Parameters
    ----------
    scene_style_pairs:
        List of ``(scene_dict, style_dict)`` tuples, one per column.
    bgcolor:
        Figure and scene background colour.

    Returns
    -------
    go.Figure
        A multi-column Plotly figure ready for ``write_image``.
    """
    from plotly.subplots import make_subplots

    n = len(scene_style_pairs)
    if n == 0:
        return go.Figure()

    # Build the subplot template to get the correct domain layout.
    fig_template = make_subplots(
        rows=1, cols=n,
        specs=[[{"type": "scene"}] * n],
        horizontal_spacing=0.01,
    )
    layout_dict = fig_template.to_dict()["layout"]

    # Scene names follow Plotly's convention: scene, scene2, scene3, …
    scene_names = ["scene"] + [f"scene{i + 2}" for i in range(n - 1)]

    all_trace_dicts: list[dict] = []
    for col_idx, (scene, style) in enumerate(scene_style_pairs):
        style_norm = validate_style_schema(style)
        xr, yr, zr = _scene_ranges(scene, style_norm)
        use_fast = (
            bool(style_norm.get("fast_rendering", False))
            or style_norm.get("material") == "flat"
            or len(scene.get("draw_atoms", [])) > 2000
        )
        mesh_payload = _cached_atom_bond_meshes(scene, style_norm, use_fast=use_fast)

        # Same hidden-label propagation as build_figure.
        hidden_labels_row: set = set()
        atom_groups_row = style_norm.get("atom_groups") or []
        if atom_groups_row:
            from .style.atom_groups import hidden_atom_label_set, tag_atoms_with_groups

            tagged_row = tag_atoms_with_groups(scene["draw_atoms"], atom_groups_row)
            hidden_labels_row = hidden_atom_label_set(tagged_row)

        trace_dicts: list[dict] = []
        trace_dicts.extend(mesh_payload["bond_dicts"])
        trace_dicts.extend(mesh_payload["minor_bond_dicts"])
        trace_dicts.extend(mesh_payload["atom_dicts"])
        trace_dicts.extend(mesh_payload["minor_outline_dicts"])
        trace_dicts.extend(_traces_to_dicts(_contact_traces(scene, style_norm)))
        trace_dicts.extend(_traces_to_dicts(_label_traces(scene, style_norm, hidden_labels=hidden_labels_row)))
        trace_dicts.extend(_traces_to_dicts(_axis_traces(scene, style_norm)))
        trace_dicts.extend(_traces_to_dicts(_unit_cell_traces(scene, style_norm)))
        trace_dicts.append(
            _round_coord_arrays(_atom_selection_trace(scene, style_norm, hidden_labels=hidden_labels_row).to_plotly_json())
        )

        trace_dicts = _style_trace_dicts(trace_dicts, style_norm)
        scene_name = scene_names[col_idx]
        for td in trace_dicts:
            td["scene"] = scene_name
        all_trace_dicts.extend(trace_dicts)

        layout_dict[scene_name] = figure_axis_layout(scene, style_norm, xr, yr, zr)
        layout_dict[scene_name]["bgcolor"] = bgcolor

    layout_dict.update(
        showlegend=False,
        paper_bgcolor=bgcolor,
        plot_bgcolor=bgcolor,
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
    )

    fig = go.Figure(data=all_trace_dicts, layout=layout_dict, _validate=False)
    return fig


def build_figure(scene: dict, style: dict, topology_data: dict | None = None) -> go.Figure:
    style = validate_style_schema(style)
    xr, yr, zr = _scene_ranges(scene, style, topology_data=topology_data if style.get("topology_enabled", False) else None)
    style["_topology_viewport_ranges"] = [list(xr), list(yr), list(zr)]
    # Mesh3d atoms are 3D world-coordinate spheres -- they grow when the
    # camera dollies in, which is what users expect from "zoom". Scatter3d
    # markers are pixel-fixed and break that expectation (the user reported
    # that toggling Hydrogens on PEP unit-cell suddenly produced "flat"
    # atoms because the threshold tripped). With the per-scene mesh cache
    # in place even ~700-atom scenes stay responsive on the warm path,
    # so the threshold is now ~3x looser. The explicit "Fast rendering
    # fallback" checkbox remains the user-controlled escape hatch.
    use_fast = bool(style.get("fast_rendering", False)) or style.get("material") == "flat" or len(scene.get("draw_atoms", [])) > 2000

    mesh_payload = _cached_atom_bond_meshes(scene, style, use_fast=use_fast)
    topology_on = bool(style.get("topology_enabled", False)) and topology_data is not None

    # Phase 2: derive labels of atoms hidden by atom_groups visible:false
    # so labels and the click-target overlay stay in sync with what's
    # actually drawn. The mesh cache may already have done this work --
    # but it restores ``scene["draw_atoms"]`` afterwards, so we have to
    # tag again here. The cost is one shallow-dict-per-atom decoration
    # which is well below the Plotly-validation cost we just saved.
    hidden_labels: set = set()
    atom_groups = style.get("atom_groups") or []
    if atom_groups:
        from .style.atom_groups import hidden_atom_label_set, tag_atoms_with_groups

        tagged = tag_atoms_with_groups(scene["draw_atoms"], atom_groups)
        hidden_labels = hidden_atom_label_set(tagged)

    # Build a flat list of trace dicts (skipping per-trace Plotly validation
    # by passing dicts straight to ``go.Figure``) instead of repeated
    # ``add_trace`` calls. ``add_trace`` re-runs the full validator chain
    # on every call -- profiling showed ~70% of warm rebuild time was in
    # that machinery alone.
    trace_dicts: list[dict] = []
    if topology_on:
        trace_dicts.extend(_traces_to_dicts(topology_background_traces(topology_data, style)))
    trace_dicts.extend(mesh_payload["bond_dicts"])
    trace_dicts.extend(mesh_payload["minor_bond_dicts"])
    trace_dicts.extend(mesh_payload["atom_dicts"])
    trace_dicts.extend(mesh_payload["minor_outline_dicts"])
    selection_trace = selection_outline_trace(
        scene,
        style,
        selected_labels=set((style.get("selection") or {}).get("atom_labels") or []),
    )
    if selection_trace is not None:
        trace_dicts.extend(_traces_to_dicts([selection_trace]))
    trace_dicts.extend(_traces_to_dicts([disorder_preview_outline_trace(scene, style, highlight_labels=set())]))
    trace_dicts.extend(_traces_to_dicts(_contact_traces(scene, style)))
    # _highlight_traces (fake specular dots) are deliberately *not* added.
    # They were Scatter3d markers with pixel-fixed sizes -- in the static
    # publication path they read as ugly translucent halos that engulf the
    # atoms when the scene is zoomed out, and the proper Mesh3d shading on
    # `_atom_mesh_traces` already gives a believable highlight.
    trace_dicts.extend(_traces_to_dicts(_label_traces(scene, style, hidden_labels=hidden_labels)))
    trace_dicts.extend(_traces_to_dicts(_axis_traces(scene, style)))
    trace_dicts.extend(_traces_to_dicts(_unit_cell_traces(scene, style)))
    if topology_on:
        trace_dicts.extend(_traces_to_dicts(topology_foreground_traces(topology_data, style)))
    trace_dicts.append(_round_coord_arrays(_atom_selection_trace(scene, style, hidden_labels=hidden_labels).to_plotly_json()))
    # Phase 4: extra invisible markers so the right-click menu has
    # click targets for polyhedron centres and bond midpoints.
    if topology_on:
        poly_pick = _polyhedron_selection_trace(topology_data)
        if poly_pick is not None:
            trace_dicts.append(_round_coord_arrays(poly_pick.to_plotly_json()))
    bond_pick = _bond_selection_trace(scene, style)
    if bond_pick is not None:
        trace_dicts.append(_round_coord_arrays(bond_pick.to_plotly_json()))

    # ``_validate=False`` skips Plotly's per-property validator chain when
    # constructing the figure. We've already validated the dicts via
    # ``to_plotly_json()`` upstream, so skipping here is safe and shaves
    # another ~50% off the warm rebuild path on small / medium scenes.
    trace_dicts = _style_trace_dicts(trace_dicts, style)
    fig = go.Figure(data=trace_dicts, _validate=False)

    show_title = bool(style.get("show_title", True))
    title_text = scene.get("display_title") or scene.get("title") or scene.get("name") or ""
    title_arg = dict(text=str(title_text), x=0.5) if show_title else None
    top_margin = 50 if show_title else 0

    ui_revision = style.get("uirevision", str(scene.get("name", "scene")))
    compass_ctx = compass_clientside_context(scene, style)
    layout_meta = {"compass": compass_ctx} if compass_ctx else {}
    layout_kwargs = dict(
        title=title_arg,
        showlegend=False,
        uirevision=ui_revision,
        paper_bgcolor=style.get("background", "#FFFFFF"),
        plot_bgcolor=style.get("background", "#FFFFFF"),
        margin=dict(l=0, r=0, t=top_margin, b=0),
        scene={
            **figure_axis_layout(scene, style, xr, yr, zr),
            "domain": {"x": [0, 1], "y": [0, 1]},
        },
        meta=layout_meta,
    )
    key_annotations, key_shapes = compose_axis_key_layout(scene, style)
    if key_annotations:
        layout_kwargs["annotations"] = key_annotations
    if key_shapes:
        layout_kwargs["shapes"] = key_shapes
    fig.update_layout(**layout_kwargs)
    return fig
