from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .common import *
from .meshes import _append_mesh, _sphere_mesh
from .style import *


def _atom_outline_payload(
    scene: dict,
    style: dict,
    *,
    labels: set[str] | None = None,
    source_indices: set[int] | None = None,
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
    if not labels and not source_indices:
        return payload
    atom_scale = float(style.get("atom_scale", 1.0))
    for atom in scene.get("draw_atoms") or []:
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
):
    selected = set(selected_labels or [])
    if not selected:
        return None
    payload = _atom_outline_payload(scene, style, labels=selected, radius_scale=1.18)
    if not payload["x"]:
        return None
    return _annotate_trace(go.Mesh3d(
        x=payload["x"],
        y=payload["y"],
        z=payload["z"],
        i=payload["i"],
        j=payload["j"],
        k=payload["k"],
        color=str(style.get("selection_highlight", "#FFD24A")),
        opacity=float(style.get("selection_opacity", 0.55)),
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
        xs.append(float(atom["cart"][0]))
        ys.append(float(atom["cart"][1]))
        zs.append(float(atom["cart"][2]))
        sizes.append(max(6.0, 48.0 * atom["atom_radius"] * float(style["atom_scale"])))
        labels.append(atom["label"])
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
        customdata.append([
            "atom",
            int(idx),
            str(atom["label"]),
            str(atom["elem"]),
            int(atom["is_minor"]),
            frag_label,
        ])
    return _annotate_trace(go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="markers",
        marker=dict(size=sizes, color="rgba(0,0,0,0)", opacity=0.02),
        customdata=customdata,
        hovertemplate="%{customdata[2]} (%{customdata[3]})<extra></extra>",
        showlegend=False,
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
    spec_results = topology_data.get("spec_results") or []
    if not spec_results:
        return None
    xs, ys, zs, customdata, hover = [], [], [], [], []
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
            customdata.append([
                "polyhedron",
                spec_id,
                label,
                int(bool(overlay.get("is_analysis_anchor"))),
            ])
            hover.append(f"{spec_name} \u00b7 {label}")
    if not xs:
        return None
    return go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="markers",
        marker=dict(size=18, color="rgba(0,0,0,0)", opacity=0.02),
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
    for bond in bonds:
        if not bool(bond.get("_render_visible", True)):
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
        ])
        hover.append(f"{label_i} \u2014 {label_j}")
    if not xs:
        return None
    return _annotate_trace(go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="markers",
        marker=dict(size=10, color="rgba(0,0,0,0)", opacity=0.02),
        customdata=customdata,
        hovertext=hover,
        hovertemplate="%{hovertext}<extra></extra>",
        showlegend=False,
        name="bond-selection",
    ), "bond_selection")



__all__ = [name for name in globals() if not name.startswith("__")]
