# ruff: noqa: F401,F405
from __future__ import annotations

import re

from .scene_traces import *  # noqa: F403
from .style import style_from_controls
from .topology import (
    representative_polyhedron_overlay,
    representative_polyhedron_traces,
    topology_histogram_figure,
    topology_results_markdown,
)
from .morphology import _morphology_traces
from .geometry import geometry_entity_traces, validate_geometry_style
from .compass import (
    _COMPASS_ITEM_NAME,
    axis_key_overlay,
    compass_clientside_context,
    compose_axis_key_layout,
)
from .viewport import (
    _axis_cube_scale,
    _camera_axis_projections,
    _normalize,
    _plotly_camera_from_scene,
    _scene_ranges,
    _visible_atoms,
    cell_aspect_ratio,
    figure_axis_layout,
    flat_visual_pixel_scale,
    uniform_viewport,
)


def _element_legend_annotations(scene: dict, style: dict) -> list[dict]:
    if not bool(style.get("show_element_legend", False)):
        return []
    present: dict[str, str] = {}
    for atom in scene.get("draw_atoms", []):
        element = str(atom.get("elem", ""))
        if element:
            present.setdefault(element, str(atom.get("color", "#808080")))
    order = [str(item) for item in style.get("element_legend_order", [])]
    elements = [item for item in order if item in present]
    elements.extend(sorted(set(present) - set(elements)))
    if not elements:
        return []
    safe_colors = {
        element: color if re.fullmatch(r"#[0-9A-Fa-f]{6}", color) else "#808080"
        for element, color in present.items()
    }
    maximum = max(1, int(style.get("element_legend_max_entries_per_row", 8)))
    rows = []
    for start in range(0, len(elements), maximum):
        rows.append(
            " &nbsp;&nbsp; ".join(
                f"<span style='color:{safe_colors[element]}'><b>● {element}</b></span>"
                for element in elements[start : start + maximum]
            )
        )
    swatches = "<br>".join(rows)
    return [
        {
            "x": float(style.get("element_legend_x", 0.5)),
            "y": float(style.get("element_legend_y", 0.02)),
            "xref": "paper",
            "yref": "paper",
            "text": swatches,
            "showarrow": False,
            "font": {"size": float(style.get("element_legend_font_size", 13))},
            "xanchor": "center",
            "yanchor": "bottom",
        }
    ]


def _publication_polyhedron_legend(spec_results: list[dict]) -> list[dict]:
    entries = []
    for result in spec_results:
        color = str(result.get("color") or "#7C5CBF")
        name = str(result.get("name") or result.get("center_species") or "Polyhedron")
        entries.append(f"<span style='color:{color}'><b>■ {name}</b></span>")
    if not entries:
        return []
    return [
        {
            "x": 0.99,
            "y": 0.905,
            "xref": "paper",
            "yref": "paper",
            "text": " &nbsp;&nbsp; ".join(entries),
            "showarrow": False,
            "font": {"size": 13},
            "xanchor": "right",
            "yanchor": "bottom",
        }
    ]


def _should_use_fast(scene: dict, style: dict) -> bool:
    """Resolve the optional fast atom path without hiding geometry entities."""
    is_flat_ortep = (
        style.get("material") == "flat" and style.get("style") == "ortep"
    )
    return bool(style.get("fast_rendering", False)) or (
        style.get("material") == "flat" and not is_flat_ortep
    ) or (
        len(scene.get("draw_atoms", [])) > 2000
        and not bool(scene.get("geometry_entities"))
    )


def build_publication_figure(
    scene: dict,
    style: dict,
    topology_data: dict,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    width: int = 1600,
    height: int = 1100,
    vector_overlays: list[dict] | None = None,
) -> "go.Figure":
    """Compose a crystal overview and isolated representative polyhedra."""
    from plotly.graph_objects import Figure as go_Figure

    style = validate_style_schema(style)
    bgcolor = str(style.get("background", "#FFFFFF"))
    main_style = {
        **style,
        "show_title": False,
        "show_element_legend": False,
        "axis_key_fig_width": float(width),
        "axis_key_fig_height": float(height),
        "axis_key_anchor": [0.075, 0.455],
    }
    main_figure = build_figure(
        scene,
        main_style,
        topology_data=topology_data,
        force_quality=True,
        vector_overlays=vector_overlays,
    )
    main_dict = main_figure.to_dict()
    traces = list(main_dict.get("data") or [])
    for trace in traces:
        trace["scene"] = "scene"

    spec_results = [
        result
        for result in (topology_data.get("spec_results") or [])
        if representative_polyhedron_overlay(result) is not None
    ]
    panel_count = len(spec_results)
    panel_layouts: dict[str, dict] = {}
    panel_annotations: list[dict] = []
    for index, result in enumerate(spec_results):
        overlay = representative_polyhedron_overlay(result)
        color = str(result.get("color") or "#7C5CBF")
        panel_traces, radius = representative_polyhedron_traces(
            overlay,
            color=color,
            center_color=str(result.get("center_color") or "#808080"),
            ligand_color=str(result.get("ligand_color") or "#E00000"),
            opacity=float(result.get("opacity", 0.55)),
            edge_opacity=float(result.get("edge_opacity", 0.90)),
            edge_width=float(result.get("edge_width", 3.0)),
            flatshading=bool(result.get("flatshading", True)),
            spec_id=str(result.get("spec_id") or ""),
        )
        scene_name = f"scene{index + 2}"
        for trace in panel_traces:
            trace["scene"] = scene_name
        traces.extend(panel_traces)
        x0 = index / panel_count
        x1 = (index + 1) / panel_count
        panel_layouts[scene_name] = {
            "domain": {"x": [x0 + 0.015, x1 - 0.015], "y": [0.015, 0.245]},
            "xaxis": {"visible": False, "range": [-radius, radius]},
            "yaxis": {"visible": False, "range": [-radius, radius]},
            "zaxis": {"visible": False, "range": [-radius, radius]},
            "aspectmode": "cube",
            "camera": main_dict.get("layout", {}).get("scene", {}).get("camera", {}),
            "bgcolor": bgcolor,
        }
        panel_annotations.append(
            {
                "x": (x0 + x1) / 2,
                "y": 0.255,
                "xref": "paper",
                "yref": "paper",
                "text": f"<b>{result.get('name') or result.get('center_species') or 'Polyhedron'}</b>",
                "showarrow": False,
                "font": {"size": 17, "color": color},
                "xanchor": "center",
                "yanchor": "bottom",
            }
        )

    main_scene_layout = dict(main_dict.get("layout", {}).get("scene") or {})
    main_scene_layout["domain"] = {"x": [0.0, 1.0], "y": [0.29, 0.94]}
    camera = dict(main_scene_layout.get("camera") or {})
    eye = dict(camera.get("eye") or {})
    if eye:
        camera["eye"] = {axis: float(value) * 0.38 for axis, value in eye.items()}
        main_scene_layout["camera"] = camera
    annotations = [
        {
            "x": 0.5,
            "y": 0.985,
            "xref": "paper",
            "yref": "paper",
            "text": f"<b>{title or scene.get('display_title') or scene.get('title') or scene.get('name') or ''}</b>",
            "showarrow": False,
            "font": {"size": 30, "color": "#111111"},
            "xanchor": "center",
            "yanchor": "top",
        },
    ]
    if subtitle:
        annotations.append(
            {
                "x": 0.5,
                "y": 0.945,
                "xref": "paper",
                "yref": "paper",
                "text": str(subtitle),
                "showarrow": False,
                "font": {"size": 15, "color": "#555555"},
                "xanchor": "center",
                "yanchor": "top",
            }
        )
    key_annotations, key_shapes = compose_axis_key_layout(scene, main_style)
    annotations.extend(key_annotations or [])
    annotations.extend(_publication_polyhedron_legend(spec_results))
    element_style = {
        **main_style,
        "show_element_legend": True,
        "element_legend_x": 0.5,
        "element_legend_y": 0.275,
    }
    annotations.extend(_element_legend_annotations(scene, element_style))
    annotations.extend(panel_annotations)

    layout = {
        "scene": main_scene_layout,
        **panel_layouts,
        "annotations": annotations,
        "shapes": list(key_shapes or []),
        "showlegend": False,
        "paper_bgcolor": bgcolor,
        "plot_bgcolor": bgcolor,
        "margin": {"l": 20, "r": 20, "t": 10, "b": 10},
        "width": int(width),
        "height": int(height),
    }
    return go_Figure(data=traces, layout=layout, _validate=False)


def _ordered_atom_bond_trace_dicts(mesh_payload: dict, *, use_fast: bool) -> list[dict]:
    """Return atom/bond layers in a pipeline-appropriate draw order.

    Fast atoms are fixed-size Scatter3d markers. At a fitted overview their
    screen radii can cover a complete short N-H/O-H bond when bonds are
    inserted first. Endpoint-coloured fast bonds therefore overlay markers;
    Mesh3d keeps the conventional depth-correct bond-before-atom order.
    """
    if use_fast:
        return [
            *mesh_payload["atom_dicts"],
            *mesh_payload["bond_dicts"],
            *mesh_payload["minor_bond_dicts"],
            *mesh_payload["minor_outline_dicts"],
        ]
    return [
        *mesh_payload["bond_dicts"],
        *mesh_payload["minor_bond_dicts"],
        *mesh_payload["atom_dicts"],
        *mesh_payload["minor_outline_dicts"],
    ]


def build_row_figure(
    scene_style_pairs: list[tuple[dict, dict]],
    bgcolor: str = "#FFFFFF",
    *,
    include_interaction_traces: bool = True,
    vector_overlays_by_scene: list[list[dict] | None] | None = None,
) -> "go.Figure":
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
    from plotly.graph_objects import Figure as go_Figure
    from plotly.subplots import make_subplots

    n = len(scene_style_pairs)
    if n == 0:
        return go_Figure()

    # Build the subplot template to get the correct domain layout.
    fig_template = make_subplots(
        rows=1,
        cols=n,
        specs=[[{"type": "scene"}] * n],
        horizontal_spacing=0.01,
    )
    layout_dict = fig_template.to_dict()["layout"]

    # Scene names follow Plotly's convention: scene, scene2, scene3, …
    scene_names = ["scene"] + [f"scene{i + 2}" for i in range(n - 1)]

    all_trace_dicts: list[dict] = []
    for col_idx, (scene, style) in enumerate(scene_style_pairs):
        scene = dict(scene)
        if vector_overlays_by_scene is not None:
            if len(vector_overlays_by_scene) != n:
                raise ValueError("vector_overlays_by_scene must match scene count")
            scene["vector_overlays"] = vector_overlays_by_scene[col_idx] or []
        style_norm = validate_style_schema(style)
        validate_geometry_style(scene, style_norm)
        xr, yr, zr = _scene_ranges(scene, style_norm)
        if style_norm.get("material") == "flat":
            style_norm["_flat_visual_pixel_scale"] = flat_visual_pixel_scale(style_norm)
        use_fast = _should_use_fast(scene, style_norm)
        mesh_payload = _cached_atom_bond_meshes(scene, style_norm, use_fast=use_fast)

        # Same hidden-label propagation as build_figure.
        hidden_labels_row: set = set()
        atom_groups_row = style_norm.get("atom_groups") or []
        if atom_groups_row:
            from .style.atom_groups import hidden_atom_label_set, tag_atoms_with_groups

            tagged_row = tag_atoms_with_groups(scene["draw_atoms"], atom_groups_row)
            hidden_labels_row = hidden_atom_label_set(tagged_row)

        trace_dicts: list[dict] = []
        if scene.get("vector_overlays"):
            from .overlay.vectors import vector_mesh_traces

            trace_dicts.extend(
                vector_mesh_traces(scene["vector_overlays"], lattice=scene.get("M"))
            )
        trace_dicts.extend(
            _ordered_atom_bond_trace_dicts(mesh_payload, use_fast=use_fast)
        )
        trace_dicts.extend(_traces_to_dicts(_contact_traces(scene, style_norm)))
        trace_dicts.extend(
            _traces_to_dicts(
                _label_traces(scene, style_norm, hidden_labels=hidden_labels_row)
            )
        )
        trace_dicts.extend(_traces_to_dicts(_axis_traces(scene, style_norm)))
        trace_dicts.extend(_traces_to_dicts(_unit_cell_traces(scene, style_norm)))
        trace_dicts.extend(_traces_to_dicts(geometry_entity_traces(scene)))
        trace_dicts.extend(_traces_to_dicts(_morphology_traces(scene, style_norm)))
        if include_interaction_traces:
            trace_dicts.append(
                _round_coord_arrays(
                    _atom_selection_trace(
                        scene, style_norm, hidden_labels=hidden_labels_row
                    ).to_plotly_json()
                )
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

    fig = go_Figure(data=all_trace_dicts, layout=layout_dict, _validate=False)
    return fig


def build_figure(
    scene: dict,
    style: dict,
    topology_data: dict | None = None,
    *,
    force_quality: bool = False,
    include_interaction_traces: bool = True,
    vector_overlays: list[dict] | None = None,
) -> "go.Figure":
    from plotly.graph_objects import Figure as go_Figure

    scene = dict(scene)
    if vector_overlays is not None:
        scene["vector_overlays"] = vector_overlays
    style = validate_style_schema(style)
    validate_geometry_style(scene, style)
    xr, yr, zr = _scene_ranges(
        scene,
        style,
        topology_data=topology_data if style.get("topology_enabled", False) else None,
    )
    if style.get("material") == "flat":
        style["_flat_visual_pixel_scale"] = flat_visual_pixel_scale(style)
    style["_topology_viewport_ranges"] = [list(xr), list(yr), list(zr)]
    # Mesh3d atoms are 3D world-coordinate spheres -- they grow when the
    # camera dollies in, which is what users expect from "zoom". Scatter3d
    # markers are pixel-fixed and therefore must never be selected merely
    # because a structure crosses an atom-count threshold. Fast rendering is
    # an explicit caller/UI choice (or the deliberately selected flat material).
    # flat+ortep is excluded: it uses the open-ORTEP billboard pipeline,
    # not the scatter fast-path.
    is_flat_ortep = style.get("material") == "flat" and style.get("style") == "ortep"
    use_fast = bool(style.get("fast_rendering", False)) or (
        style.get("material") == "flat" and not is_flat_ortep
    )

    mesh_payload = _cached_atom_bond_meshes(scene, style, use_fast=use_fast)
    topology_on = (
        bool(style.get("topology_enabled", False)) and topology_data is not None
    )

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
        trace_dicts.extend(
            _traces_to_dicts(topology_background_traces(topology_data, style))
        )
    # Isosurface overlay: inserted before bonds/atoms so the molecular
    # skeleton renders on top of the translucent orbital lobes.
    from .traces_isosurface import isosurface_overlay_traces

    trace_dicts.extend(isosurface_overlay_traces(scene, style))
    if scene.get("vector_overlays"):
        from .overlay.vectors import vector_mesh_traces

        trace_dicts.extend(
            vector_mesh_traces(scene["vector_overlays"], lattice=scene.get("M"))
        )
    trace_dicts.extend(_ordered_atom_bond_trace_dicts(mesh_payload, use_fast=use_fast))
    selection_trace = selection_outline_trace(
        scene,
        style,
        selected_labels=set((style.get("selection") or {}).get("atom_labels") or []),
    )
    if selection_trace is not None:
        trace_dicts.extend(_traces_to_dicts([selection_trace]))
    if include_interaction_traces:
        trace_dicts.extend(
            _traces_to_dicts(
                [disorder_preview_outline_trace(scene, style, highlight_labels=set())]
            )
        )
    trace_dicts.extend(_traces_to_dicts(_contact_traces(scene, style)))
    # Flat rendering emits one grouped, fully opaque white dot from
    # `_atom_scatter_traces`, placed at the screen upper-right of each atom.
    # Mesh rendering keeps its native Mesh3d lighting; no raster post-process
    # or per-atom highlight traces are used.
    trace_dicts.extend(
        _traces_to_dicts(_label_traces(scene, style, hidden_labels=hidden_labels))
    )
    trace_dicts.extend(_traces_to_dicts(_axis_traces(scene, style)))
    trace_dicts.extend(_traces_to_dicts(_unit_cell_traces(scene, style)))
    trace_dicts.extend(_traces_to_dicts(geometry_entity_traces(scene)))
    trace_dicts.extend(_traces_to_dicts(_morphology_traces(scene, style)))
    if topology_on:
        trace_dicts.extend(
            _traces_to_dicts(topology_foreground_traces(topology_data, style))
        )
    if include_interaction_traces:
        trace_dicts.append(
            _round_coord_arrays(
                _atom_selection_trace(
                    scene, style, hidden_labels=hidden_labels
                ).to_plotly_json()
            )
        )
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
    property_payload = style.get("atom_property_color") or {}
    show_property_colorbar = bool(property_payload.get("show_colorbar"))
    if show_property_colorbar:
        from .property_colorbar import plotly_colorbar_trace

        trace_dicts.append(plotly_colorbar_trace(property_payload))
    fig = go_Figure(data=trace_dicts, _validate=False)

    show_title = bool(style.get("show_title", True))
    title_text = (
        scene.get("display_title") or scene.get("title") or scene.get("name") or ""
    )
    top_margin = 50 if show_title else 0

    ui_revision = style.get("uirevision", str(scene.get("name", "scene")))
    compass_ctx = compass_clientside_context(scene, style)
    layout_meta = {"compass": compass_ctx} if compass_ctx else {}
    if style.get("material") == "flat":
        layout_meta["flat_visual_pixel_scale"] = style.get("_flat_visual_pixel_scale")
    layout_kwargs = dict(
        showlegend=False,
        uirevision=ui_revision,
        paper_bgcolor=style.get("background", "#FFFFFF"),
        plot_bgcolor=style.get("background", "#FFFFFF"),
        margin=dict(l=0, r=0, t=top_margin, b=0),
        scene={
            **figure_axis_layout(scene, style, xr, yr, zr),
            "domain": {
                "x": [0, 0.86 if show_property_colorbar else 1],
                "y": [0, 1],
            },
        },
        meta=layout_meta,
    )
    if show_title:
        layout_kwargs["title"] = dict(text=str(title_text), x=0.5)
    key_annotations, key_shapes = compose_axis_key_layout(scene, style)
    if scene.get("vector_overlays"):
        from .overlay.vectors import paper_vector_label_annotations

        camera = layout_kwargs["scene"]["camera"]
        projection = camera.get("projection") or {}
        if (
            projection.get("type") if isinstance(projection, dict) else projection
        ) == "orthographic":
            key_annotations = list(
                key_annotations or []
            ) + paper_vector_label_annotations(
                scene["vector_overlays"],
                lattice=scene.get("M"),
                camera=camera,
                ranges=(xr, yr, zr),
            )
    key_annotations = list(key_annotations or []) + _element_legend_annotations(
        scene, style
    )
    if key_annotations:
        layout_kwargs["annotations"] = key_annotations
    if key_shapes:
        layout_kwargs["shapes"] = key_shapes
    fig.update_layout(**layout_kwargs)
    return fig
