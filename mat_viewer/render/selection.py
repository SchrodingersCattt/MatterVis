from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .common import *
from .meshes import _append_mesh, _sphere_mesh
from .style import *
from .picking import (
    atom_instance_id,
    atom_parent_targets,
    bond_instance_id,
    ensure_polyhedron_instance_ids,
    polyhedron_instance_id,
)


def _atom_outline_payload(
    scene: dict,
    style: dict,
    *,
    labels: set[str] | None = None,
    source_indices: set[int] | None = None,
    parts: list[dict] | None = None,
    radius_scale: float = 1.18,
) -> dict[str, list]:
    """Build outline-sphere mesh for the atoms selected by ``labels``
    (scene labels) and/or ``source_indices`` (raw-atom ``_source_index``).

    ``source_indices`` is the precise per-atom selector: scene labels
    collapse across symmetry copies, so highlighting by label would draw
    the wrong / shared set when distinct raw atoms share a label. An atom
    matches if it satisfies *either* selector that was provided.
    """
    payload = {"x": [], "y": [], "z": [], "i": [], "j": [], "k": []}
    labels = labels or set()
    source_indices = source_indices or set()
    parts = list(parts or [])
    if not labels and not source_indices and not parts:
        return payload
    atom_scale = float(style.get("atom_scale", 1.0))
    for atom in scene.get("draw_atoms") or []:
        if parts:
            matched = False
            for part in parts:
                if str(atom.get("label") or "") != str(part.get("label") or ""):
                    continue
                if "is_minor" in part and bool(atom.get("is_minor")) != bool(part["is_minor"]):
                    continue
                wanted = part.get("part")
                if wanted not in (None, ""):
                    actual = (
                        atom.get("part")
                        or atom.get("disorder_group")
                        or atom.get("disorder_assembly")
                        or ""
                    )
                    if str(actual) != str(wanted):
                        continue
                matched = True
                break
        else:
            matched = bool(labels) and str(atom.get("label") or "") in labels
        if not matched and source_indices:
            source = atom.get("_source_index")
            if source is not None:
                try:
                    matched = int(source) in source_indices
                except (TypeError, ValueError):
                    matched = False
        if not matched:
            continue
        if not _atom_render_visible(atom):
            continue
        radius = max(float(atom.get("atom_radius", 0.18)), 0.05) * atom_scale * radius_scale
        vertices, triangles = _sphere_mesh(atom.get("cart", [0.0, 0.0, 0.0]), radius, lat_steps=8, lon_steps=12)
        _append_mesh(payload, vertices, triangles)
    return payload


def selection_outline_trace(
    scene: dict,
    style: dict,
    *,
    selected_labels: set[str] | None = None,
    selected_targets: set[str] | None = None,
):
    """Build the atom outline for exact atom targets and legacy labels.

    Semantic selection also carries polyhedron/facet/geometry targets, but
    those kinds deliberately do not create an atom shell. Their renderers
    own their geometry-specific highlight path; filtering to atom IDs here
    prevents a non-atom click from outlining every atom or leaving a stale
    atom outline after the target list is cleared. Disorder-aware ``parts``
    remain supported for legacy box-selection payloads.
    """
    selection = style.get("selection") if isinstance(style.get("selection"), dict) else {}
    parts = [] if selected_targets else list(selection.get("parts") or [])
    selected = set() if selected_targets else set(selected_labels or selection.get("atom_labels") or [])
    targets = set(selected_targets or [])
    if not selected and not parts and not targets:
        return None
    payload = _atom_outline_payload(
        scene, style, labels=selected, parts=parts, radius_scale=1.18
    )
    if targets:
        exact_payload = {"x": [], "y": [], "z": [], "i": [], "j": [], "k": []}
        atom_scale = float(style.get("atom_scale", 1.0))
        for index, atom in enumerate(scene.get("draw_atoms") or []):
            if atom_instance_id(atom, index) not in targets or not _atom_render_visible(atom):
                continue
            radius = max(float(atom.get("atom_radius", 0.18)), 0.05) * atom_scale * 1.18
            vertices, triangles = _sphere_mesh(atom.get("cart", [0.0, 0.0, 0.0]), radius, lat_steps=8, lon_steps=12)
            _append_mesh(exact_payload, vertices, triangles)
        if exact_payload["x"]:
            if payload["x"]:
                base = len(payload["x"])
                payload["x"].extend(exact_payload["x"])
                payload["y"].extend(exact_payload["y"])
                payload["z"].extend(exact_payload["z"])
                payload["i"].extend([int(value) + base for value in exact_payload["i"]])
                payload["j"].extend([int(value) + base for value in exact_payload["j"]])
                payload["k"].extend([int(value) + base for value in exact_payload["k"]])
            else:
                payload = exact_payload
    if not payload["x"]:
        return None
    return _annotate_trace(go.Mesh3d(
        x=payload["x"],
        y=payload["y"],
        z=payload["z"],
        i=payload["i"],
        j=payload["j"],
        k=payload["k"],
        color=str(style.get("selection_highlight", "#74808c")),
        opacity=float(style.get("selection_opacity", 0.42)),
        flatshading=False,
        hoverinfo="skip",
        showlegend=False,
        name="selection-outline",
    ), "selection")


def disorder_preview_outline_trace(
    scene: dict,
    style: dict | None = None,
    *,
    highlight_labels: set[str] | None = None,
    highlight_source_indices: set[int] | None = None,
    color: str = "#FFD400",
    opacity: float = 0.55,
    name: str = "disorder-preview-outline",
):
    style = style or {}
    payload = _atom_outline_payload(
        scene,
        style,
        labels=set(highlight_labels or []),
        source_indices=set(highlight_source_indices or []),
        radius_scale=1.24,
    )
    return _annotate_trace(go.Mesh3d(
        x=payload["x"],
        y=payload["y"],
        z=payload["z"],
        i=payload["i"],
        j=payload["j"],
        k=payload["k"],
        color=str(color),
        opacity=float(opacity),
        flatshading=False,
        hoverinfo="skip",
        showlegend=False,
        visible=bool(payload["x"]),
        name=name,
    ), "disorder_preview")

def _atom_selection_trace(scene: dict, style: dict, hidden_labels: set | None = None):
    xs, ys, zs, sizes, labels, customdata = [], [], [], [], [], []
    radii, parents, opacities = [], [], []
    hidden_labels = hidden_labels or set()
    fragment_labels = scene.get("atom_fragment_labels") or []
    for idx, atom in enumerate(scene["draw_atoms"]):
        # Phase 2: don't expose a hover/click target for an atom that's
        # not visually present (atom_groups visible:false). Otherwise
        # a click on the empty cell still selects an invisible atom.
        if str(atom.get("label")) in hidden_labels:
            continue
        if not _atom_render_visible(atom):
            continue
        if (atom.get("_render_style") or style.get("style")) == "stick":
            continue
        if style.get("show_minor_only") and not atom.get("is_minor"):
            continue
        xs.append(float(atom["cart"][0]))
        ys.append(float(atom["cart"][1]))
        zs.append(float(atom["cart"][2]))
        sizes.append(max(6.0, 48.0 * atom["atom_radius"] * float(style["atom_scale"])))
        labels.append(atom["label"])
        radii.append(float(atom["atom_radius"]) * float(style["atom_scale"]))
        parents.append(atom_parent_targets(atom, fragment_labels[idx] if idx < len(fragment_labels) else None))
        opacities.append(_atom_effective_opacity(atom, style))
        # Phase 4: customdata schema is now ``[kind, idx, label, elem,
        # is_minor, fragment_label]`` so the right-click handler can
        # demux on ``kind`` (atom / bond / polyhedron) without having
        # to track which trace name was hit. Pre-Phase 4 callers
        # (existing click handlers) read ``customdata[1]`` for the
        # atom index; the kind tag at index 0 is additive so they
        # keep working against the same trace.
        frag_label = (
            str(fragment_labels[idx]) if idx < len(fragment_labels) and fragment_labels[idx] is not None else ""
        )
        part = atom.get("part") or atom.get("disorder_group") or atom.get("disorder_assembly") or ""
        customdata.append([
            "atom",
            int(idx),
            str(atom["label"]),
            str(atom["elem"]),
            int(bool(atom.get("is_minor"))),
            frag_label,
            atom_instance_id(atom, idx),
            atom.get("_source_index", idx),
            atom.get("_image_shift", atom.get("_pbc_image", "")),
            atom.get("disorder_group", atom.get("dg", "")),
            str(part),
        ])
    return _annotate_trace(go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="markers",
        marker={
            "size": sizes,
            # Browser-local analytic picking consumes the trace metadata.
            # These probes must not wash out the chemical base colours.
            "color": "rgba(0,0,0,0)",
            "opacity": 0.0,
            "line": {"width": 0, "color": "rgba(0,0,0,0)"},
        },
        customdata=customdata,
        hovertemplate="%{customdata[2]} (%{customdata[3]})<extra></extra>",
        showlegend=False,
        meta={"mv_radii": radii, "mv_parents": parents, "mv_opacities": opacities},
        name="atom-selection",
    ), "atom_selection")


def _polyhedron_selection_trace(topology_data: dict | None) -> "go.Scatter3d | None":
    """Invisible Scatter3d markers at every polyhedron centre, carrying
    ``customdata=[kind, spec_id, fragment_label, is_anchor]`` so the
    right-click menu can identify which polyhedron the user picked.

    Plotly's Mesh3d accepts neither per-face nor per-vertex customdata,
    so a separate Scatter3d marker layer is the only way to make
    polyhedron centres click-targetable. The markers are placed at the
    centroid of each overlay (matching ``_world_sphere_marker_trace``'s
    geometry) and rendered fully transparent; the rounded marker size
    and ``hoverinfo="text"`` keep the hover hit area generous enough
    that the user doesn't have to land exactly on a face vertex.
    """
    if not topology_data:
        return None
    ensure_polyhedron_instance_ids(topology_data)
    spec_results = topology_data.get("spec_results") or []
    if not spec_results:
        return None
    xs, ys, zs, customdata, hover = [], [], [], [], []
    ordinal = 0
    for entry in spec_results:
        spec_id = str(entry.get("spec_id") or "")
        spec_name = str(entry.get("name") or "")
        for overlay in entry.get("overlays") or []:
            center = overlay.get("center_coords")
            if center is None or not overlay.get("visible", True):
                continue
            label = str(overlay.get("center_label") or "")
            xs.append(float(center[0]))
            ys.append(float(center[1]))
            zs.append(float(center[2]))
            instance_id = polyhedron_instance_id(spec_id, overlay, ordinal)
            customdata.append([
                "polyhedron",
                spec_id,
                label,
                int(bool(overlay.get("is_analysis_anchor"))),
                instance_id,
                overlay.get("center_source_index"),
                overlay.get("center_image_shift", overlay.get("center_image", "")),
            ])
            hover.append(f"{spec_name} \u00b7 {label}")
            ordinal += 1
    if not xs:
        return None
    return go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="markers",
        marker={
            "size": 18,
            "color": "rgba(0,0,0,0)",
            "opacity": 0.02,
            "line": {"width": 0, "color": "rgba(0,0,0,0)"},
        },
        customdata=customdata,
        hovertext=hover,
        hovertemplate="%{hovertext}<extra></extra>",
        showlegend=False,
        name="polyhedron-selection",
    )


def _bond_selection_trace(scene: dict, style: dict) -> "go.Scatter3d | None":
    """Invisible Scatter3d markers at each bond midpoint, carrying
    ``customdata=[kind, label_pair, elem_pair, is_minor]`` so the
    right-click menu has somewhere to land for chemical-bond picks.

    Like :func:`_polyhedron_selection_trace` this is a separate marker
    layer; the bond-mesh path (cylinders) cannot expose hover targets.
    Half-bond endpoints with ``_render_visible=False`` are skipped so
    a hidden bond doesn't generate a phantom hover target.
    """
    bonds = scene.get("bonds") or []
    atoms = scene.get("draw_atoms") or []
    if not bonds:
        return None
    n = len(atoms)
    xs, ys, zs, customdata, hover = [], [], [], [], []
    segments, bond_radii = [], []
    if {(a.get("_render_style") or style.get("style")) for a in atoms} == {"ball"}:
        return None
    for bond_index, bond in enumerate(bonds):
        if not bool(bond.get("_render_visible", True)) or float(bond.get("_render_opacity_scale", 1)) <= 0 or float(bond.get("_render_radius_scale", 1)) <= 0:
            continue
        i = int(bond.get("i", -1))
        j = int(bond.get("j", -1))
        if not (0 <= i < n and 0 <= j < n):
            continue
        if not _atom_render_visible(atoms[i]) or not _atom_render_visible(atoms[j]):
            continue
        start = np.asarray(bond["start"], dtype=float)
        end = np.asarray(bond["end"], dtype=float)
        mid = (start + end) / 2.0
        segments.append([start.tolist(), end.tolist()])
        bond_radii.append(float(style.get("bond_radius", .15)) * float(bond.get("_render_radius_scale", 1)))
        label_i = str(atoms[i].get("label") or "")
        label_j = str(atoms[j].get("label") or "")
        elem_i = str(atoms[i].get("elem") or "")
        elem_j = str(atoms[j].get("elem") or "")
        xs.append(float(mid[0]))
        ys.append(float(mid[1]))
        zs.append(float(mid[2]))
        customdata.append([
            "bond",
            f"{label_i}-{label_j}",
            f"{elem_i}-{elem_j}",
            int(bool(bond.get("is_minor"))),
            bond_instance_id(bond, atoms, bond_index),
            atom_instance_id(atoms[i], i),
            atom_instance_id(atoms[j], j),
        ])
        hover.append(f"{label_i} \u2014 {label_j}")
    if not xs:
        return None
    return _annotate_trace(go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="markers",
        marker={
            "size": 10,
            "color": "rgba(0,0,0,0)",
            "opacity": 0.02,
            "line": {"width": 0, "color": "rgba(0,0,0,0)"},
        },
        customdata=customdata,
        hovertext=hover,
        hovertemplate="%{hovertext}<extra></extra>",
        showlegend=False,
        meta={"mv_segments": segments, "mv_bond_radii": bond_radii},
        name="bond-selection",
    ), "bond_selection")



__all__ = [name for name in globals() if not name.startswith("__")]
