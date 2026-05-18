from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .common import *
from .meshes import *
from .style import *
from .serialize import _trace_to_json_safe_dict
from .traces_overlays import _dashed_segments, _segment_cylinder_trace

def _overlay_coords_and_hull(overlay) -> tuple[np.ndarray, dict]:
    if isinstance(overlay, dict):
        coords = np.asarray(overlay.get("shell_coords") or [], dtype=float)
        hull = overlay.get("hull") or {}
    else:
        coords = np.asarray(overlay or [], dtype=float)
        hull = {}
    return coords, hull


def _hull_simplices(coords: np.ndarray, hull: dict) -> np.ndarray:
    simplices = np.asarray(hull.get("simplices") or [], dtype=int)
    if simplices.ndim == 2 and simplices.shape[1] == 3:
        return simplices
    try:
        from scipy.spatial import ConvexHull
    except Exception:  # pragma: no cover - optional dependency
        return np.zeros((0, 3), dtype=int)
    try:
        return np.asarray(ConvexHull(coords).simplices, dtype=int)
    except Exception:
        return np.zeros((0, 3), dtype=int)


def _hull_edges(coords: np.ndarray, hull: dict) -> list[tuple[int, int]]:
    edges = hull.get("edges") or []
    if edges:
        return [tuple(sorted((int(edge[0]), int(edge[1])))) for edge in edges if len(edge) >= 2]
    edge_set: set[tuple[int, int]] = set()
    for simplex in _hull_simplices(coords, hull):
        a, b, c = simplex
        edge_set.add(tuple(sorted((int(a), int(b)))))
        edge_set.add(tuple(sorted((int(b), int(c)))))
        edge_set.add(tuple(sorted((int(a), int(c)))))
    return sorted(edge_set)




def shell_center_lines(center, shell_coords):
    center = np.array(center, dtype=float)
    coords = np.array(shell_coords, dtype=float)
    if len(coords) == 0:
        return []
    raw_segments = [(center, np.asarray(point, dtype=float)) for point in coords]
    # Dash length tied to the typical bond length so the dash pattern
    # holds its visual rhythm whether we're looking at a 1.5 Å Cl-O
    # bond or a 5 Å rare-earth-O coordination radius.
    lengths = [float(np.linalg.norm(b - a)) for a, b in raw_segments]
    typical = float(np.median(lengths)) if lengths else 1.0
    dash_segments = _dashed_segments(raw_segments, dash_len=0.18 * typical, gap_len=0.12 * typical)
    trace = _segment_cylinder_trace(
        dash_segments,
        radius=max(0.018, 0.012 * typical),
        color="#6A5ACD",
        opacity=0.85,
        sides=4,
        name="coordination-lines",
    )
    return [trace] if trace is not None else []


def _world_sphere_marker_trace(centers, *, radius, color, opacity=0.9):
    """Mesh3d-based "marker" sphere set. Unlike Scatter3d markers, the
    radius is in world (Å) coordinates so the on-screen size grows when
    the camera dollies in -- i.e. the markers actually feel like part of
    the scene instead of pixel-fixed overlays."""
    centers = np.array(centers, dtype=float).reshape(-1, 3)
    if len(centers) == 0:
        return None
    payload = {"x": [], "y": [], "z": [], "i": [], "j": [], "k": []}
    for center in centers:
        vertices, triangles = _sphere_mesh(center, float(radius), lat_steps=8, lon_steps=12)
        _append_mesh(payload, vertices, triangles)
    return go.Mesh3d(
        x=payload["x"],
        y=payload["y"],
        z=payload["z"],
        i=payload["i"],
        j=payload["j"],
        k=payload["k"],
        color=color,
        opacity=opacity,
        hoverinfo="skip",
        showlegend=False,
        flatshading=False,
    )


def shell_atom_traces(shell_coords, distances, color="#7C5CBF"):
    coords = np.array(shell_coords, dtype=float)
    if len(coords) == 0:
        return []
    dists = np.array(distances, dtype=float)
    if len(dists) == 0:
        dists = np.ones(len(coords))
    # World-coord sphere radius (Å). Closer neighbours render slightly
    # larger -- same intent as the old pixel-based marker, but the size
    # now scales with zoom because it lives in 3-D space.
    radii = 0.22 + (dists.max() - dists + 0.05) * 0.05
    payload = {"x": [], "y": [], "z": [], "i": [], "j": [], "k": []}
    for pos, r in zip(coords, radii):
        vertices, triangles = _sphere_mesh(pos, float(r), lat_steps=8, lon_steps=12)
        _append_mesh(payload, vertices, triangles)
    return [
        go.Mesh3d(
            x=payload["x"],
            y=payload["y"],
            z=payload["z"],
            i=payload["i"],
            j=payload["j"],
            k=payload["k"],
            color=color,
            opacity=0.9,
            hoverinfo="skip",
            showlegend=False,
            flatshading=False,
        )
    ]


def _merged_hull_mesh(overlays: list[tuple[list, float]], color: str):
    """Pack any number of (shell_coords, opacity) overlays into the
    minimum number of Mesh3d traces -- one per distinct opacity value.
    With 40+ tiled polyhedra each contributing its own ConvexHull this
    drops the Plotly trace count by ~2x while keeping the same on-
    screen look (semi-transparent hulls layered front-to-back)."""
    bins: dict[float, dict] = {}
    for overlay, opacity in overlays:
        coords, hull = _overlay_coords_and_hull(overlay)
        if len(coords) < 4:
            continue
        simplices = _hull_simplices(coords, hull)
        if len(simplices) == 0:
            continue
        bin_payload = bins.setdefault(round(float(opacity), 4), {"x": [], "y": [], "z": [], "i": [], "j": [], "k": []})
        base = len(bin_payload["x"])
        bin_payload["x"].extend(coords[:, 0].tolist())
        bin_payload["y"].extend(coords[:, 1].tolist())
        bin_payload["z"].extend(coords[:, 2].tolist())
        bin_payload["i"].extend((simplices[:, 0] + base).tolist())
        bin_payload["j"].extend((simplices[:, 1] + base).tolist())
        bin_payload["k"].extend((simplices[:, 2] + base).tolist())
    traces = []
    for opacity, payload in bins.items():
        if not payload["x"]:
            continue
        traces.append(
            go.Mesh3d(
                x=payload["x"],
                y=payload["y"],
                z=payload["z"],
                i=payload["i"],
                j=payload["j"],
                k=payload["k"],
                color=color,
                opacity=float(opacity),
                flatshading=True,
                hoverinfo="skip",
                showlegend=False,
                name="coordination-hull",
            )
        )
    return traces


def _merged_hull_edges(overlays: list, color: str):
    """All polyhedron edges in the scene packed into a single
    ``Scatter3d`` line trace using NaN-separated segments.

    The previous implementation tessellated each edge into a Mesh3d
    pentagonal-prism cylinder -- visually nicer but ~7-10x heavier in
    JSON. With many polyhedra tiled (DAP-4 has 40+ centres) the trace
    blew past 300 KB and dominated every callback round-trip. Lines
    in WebGL render in well under a millisecond and serialize to a
    fraction of the size; a non-zero ``line.width`` keeps the visual
    weight close enough to the cylinder version for the interactive
    overlay. Static publication exports that want fat tubes can opt
    back into the cylinder path via the legacy renderer if needed."""
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for overlay in overlays:
        coords, hull = _overlay_coords_and_hull(overlay)
        if len(coords) < 4:
            continue
        for i, j in _hull_edges(coords, hull):
            p0 = coords[i]
            p1 = coords[j]
            xs.extend([float(p0[0]), float(p1[0]), float("nan")])
            ys.extend([float(p0[1]), float(p1[1]), float("nan")])
            zs.extend([float(p0[2]), float(p1[2]), float("nan")])
    if not xs:
        return []
    trace = go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="lines",
        line=dict(color=color, width=4),
        opacity=0.95,
        hoverinfo="skip",
        showlegend=False,
        name="coordination-edges",
    )
    return [trace]


def _viewport_ranges_from_style(style: dict | None) -> np.ndarray | None:
    raw = (style or {}).get("_topology_viewport_ranges")
    if raw is None:
        return None
    try:
        ranges = np.asarray(raw, dtype=float)
    except (TypeError, ValueError):
        return None
    if ranges.shape != (3, 2) or not np.all(np.isfinite(ranges)):
        return None
    return ranges


def _viewport_cache_key(style: dict | None) -> tuple:
    """Hashable summary of the topology painter viewport for cache keys.

    Quantised to 0.5 angstrom buckets: the viewport drives a
    bounding-box intersection test on each overlay
    (:func:`_overlay_within_viewport`), so sub-angstrom drift -- the
    kind a user gets from nudging the atom-scale slider 1.0 -> 1.05 --
    must NOT invalidate this cache. Re-tessellating a few hundred
    hull-edge cylinders on every slider step was the dominant left
    sidebar latency complaint ("右边非常迟钝").
    """
    ranges = _viewport_ranges_from_style(style)
    if ranges is None:
        return ()
    bucket = 0.5
    return tuple(
        tuple(round(float(value) / bucket) for value in axis) for axis in ranges
    )


def _overlay_within_viewport(overlay: dict, ranges: np.ndarray | None) -> bool:
    """Predicate: should this non-anchor overlay be drawn?

    An overlay is kept when its axis-aligned bounding box (centre +
    shell points) **intersects** the scene viewport along every axis.
    The previous predicate required the bounding box to be fully
    *contained* in the viewport, which silently dropped every
    molecule-level packing-shell tile whose neighbours reached into the
    next PBC image -- e.g. on MPEP the C5N2 -> ClO4 spec ships 4
    fragments but only the analysis anchor survived because the other
    three had at least one ClO4 image just outside the unit cell along
    z. Visually that matched "polyhedra are missing for most of the
    displayed cations" exactly. The intersection predicate keeps every
    overlay that at least *touches* the camera frustum and lets Plotly
    handle the partial-clip painting; overlays that are entirely
    outside one axis (the regression case from PR #f20ad88: a far
    replica at x=40 in a viewport ending at ~10) are still rejected.
    """
    if ranges is None:
        return True
    coords, _hull = _overlay_coords_and_hull(overlay)
    points = []
    center = overlay.get("center_coords")
    if center is not None:
        try:
            points.append(np.asarray(center, dtype=float))
        except (TypeError, ValueError):
            return False
    if len(coords):
        points.extend(np.asarray(coords, dtype=float))
    if not points:
        return False
    arr = np.asarray(points, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3 or not np.all(np.isfinite(arr)):
        return False
    tol = 1e-6
    overlay_min = arr.min(axis=0)
    overlay_max = arr.max(axis=0)
    return bool(
        np.all(overlay_max >= ranges[:, 0] - tol)
        and np.all(overlay_min <= ranges[:, 1] + tol)
    )


def _multi_spec_cache_key(topology_data: dict, fallback_color: str) -> tuple:
    """Build a hashable key for the renderer's painter caches that
    captures every per-spec colour and per-overlay instance override
    (used by callers that haven't migrated to ``spec_results``)."""
    spec_results = topology_data.get("spec_results") or []
    parts = []
    for entry in spec_results:
        overlay_overrides = []
        for overlay in entry.get("overlays") or []:
            color_override = overlay.get("color")
            if color_override is None and not overlay.get("visible", True):
                color_override = "_hidden"
            overlay_overrides.append((str(overlay.get("center_label") or ""), color_override))
        parts.append(
            (
                entry.get("spec_id") or "",
                entry.get("color") or fallback_color,
                tuple(overlay_overrides),
            )
        )
    return (tuple(parts), fallback_color)


def topology_background_traces(topology_data: dict | None, style: dict | None = None):
    """Hull mesh + edges for every overlay, painted per-spec when
    ``topology_data["spec_results"]`` is set; otherwise falls back to the
    legacy single-colour path keyed on ``style["topology_hull_color"]``.

    Designed to be added to the figure *before* the atom traces so atoms
    (especially faded minor / disorder positions) stay visible on top of
    the semi-transparent hull instead of getting washed out by Plotly's
    painter-order alpha stacking. Result is cached on the
    ``topology_data`` dict keyed on the per-spec colour tuple so
    toggling a cosmetic checkbox doesn't re-tessellate several thousand
    hull-edge cylinders for tiled polyhedra.
    """
    if not topology_data:
        return []
    style = style or {}
    fallback_color = str(style.get("topology_hull_color", "#7C5CBF"))
    viewport_ranges = _viewport_ranges_from_style(style)
    cache = topology_data.setdefault("_background_dict_cache", {})
    cache_key = (_multi_spec_cache_key(topology_data, fallback_color), _viewport_cache_key(style))
    if cache_key in cache:
        return cache[cache_key]
    primary_opacity = 0.22
    extra_opacity = 0.12

    traces: list = []
    spec_results = topology_data.get("spec_results") or []
    if spec_results:
        for entry in spec_results:
            spec_color = str(entry.get("color") or fallback_color)
            # Bucket overlays by the colour they will paint with: any
            # ``instance_overrides`` entry can swap a single fragment to
            # a different hue, and we want every solid colour to take
            # exactly one merged-mesh trace (otherwise the per-shape
            # cylinder counts explode for tiled polyhedra).
            overlays_by_color: dict[str, list[tuple[list, float]]] = {}
            for overlay in entry.get("overlays") or []:
                shell = overlay.get("shell_coords")
                if not shell:
                    continue
                if not overlay.get("visible", True):
                    continue
                if not overlay.get("is_analysis_anchor") and not _overlay_within_viewport(overlay, viewport_ranges):
                    continue
                opacity = primary_opacity if overlay.get("is_analysis_anchor") else extra_opacity
                color = str(overlay.get("color") or spec_color)
                overlays_by_color.setdefault(color, []).append((overlay, opacity))
            for color, group in overlays_by_color.items():
                traces.extend(_merged_hull_mesh(group, color=color))
                traces.extend(_merged_hull_edges([overlay for overlay, _ in group], color=color))
    else:
        # Legacy single-colour path: callers (or test fixtures) that
        # construct a topology_data dict by hand still see the original
        # behaviour, no colour table required.
        overlays_with_opacity = []
        if topology_data.get("shell_coords"):
            overlays_with_opacity.append((topology_data, primary_opacity))
        for extra in topology_data.get("extra_overlays") or []:
            if extra.get("shell_coords") and _overlay_within_viewport(extra, viewport_ranges):
                overlays_with_opacity.append((extra, extra_opacity))
        traces.extend(_merged_hull_mesh(overlays_with_opacity, color=fallback_color))
        traces.extend(_merged_hull_edges([overlay for overlay, _ in overlays_with_opacity], color=fallback_color))

    cache[cache_key] = [_trace_to_json_safe_dict(tr) for tr in traces]
    return cache[cache_key]


def topology_foreground_traces(topology_data: dict | None, style: dict | None = None):
    """Centre markers, connecting lines and shell-atom highlights.

    Multi-spec mode: one marker cluster per spec, coloured to match the
    hull. The single fragment marked ``is_analysis_anchor`` keeps the
    bigger orange marker and connecting lines so the user can always
    see which site owns the histogram / results panel. Falls back to
    the legacy single-colour layout when ``spec_results`` is absent.
    """
    if not topology_data:
        return []
    style = style or {}
    fallback_color = str(style.get("topology_hull_color", "#7C5CBF"))
    viewport_ranges = _viewport_ranges_from_style(style)
    cache = topology_data.setdefault("_foreground_dict_cache", {})
    cache_key = (_multi_spec_cache_key(topology_data, fallback_color), _viewport_cache_key(style))
    if cache_key in cache:
        return cache[cache_key]

    traces: list = []
    spec_results = topology_data.get("spec_results") or []
    primary_center = topology_data.get("center_coords")
    primary_coords = topology_data.get("shell_coords") or []
    primary_distances = topology_data.get("distances") or []
    # Pick the colour the analysis anchor's spec uses, so the
    # distance-coloured shell-atom markers blend with their hull.
    anchor_color = fallback_color
    if spec_results:
        anchor_spec_id = topology_data.get("analysis_spec_id")
        for entry in spec_results:
            if entry.get("spec_id") == anchor_spec_id:
                anchor_color = str(entry.get("color") or fallback_color)
                break
        else:
            anchor_color = str(spec_results[0].get("color") or fallback_color)

    if primary_center is not None and len(primary_coords) > 0:
        traces.extend(shell_center_lines(primary_center, primary_coords))
        primary_marker = _world_sphere_marker_trace(
            [primary_center],
            radius=0.55,
            color="#E07C24",
            opacity=0.95,
        )
        if primary_marker is not None:
            traces.append(primary_marker)
        if primary_distances:
            traces.extend(shell_atom_traces(primary_coords, primary_distances, color=anchor_color))

    if spec_results:
        # One faint marker cluster per spec covering its non-anchor
        # overlays. We skip the anchor centre because it already has
        # the bright orange marker above. ``instance_overrides`` may
        # paint individual fragments a different colour or hide them
        # entirely; we honour those here so the centre marker matches
        # the hull beneath.
        for entry in spec_results:
            spec_color = str(entry.get("color") or fallback_color)
            centers_by_color: dict[str, list[list[float]]] = {}
            for overlay in entry.get("overlays") or []:
                if overlay.get("is_analysis_anchor"):
                    continue
                if not overlay.get("visible", True):
                    continue
                if not _overlay_within_viewport(overlay, viewport_ranges):
                    continue
                center = overlay.get("center_coords")
                coords = overlay.get("shell_coords") or []
                if center is None or len(coords) == 0:
                    continue
                color = str(overlay.get("color") or spec_color)
                centers_by_color.setdefault(color, []).append(center)
            for color, centers in centers_by_color.items():
                extra_marker = _world_sphere_marker_trace(
                    centers,
                    radius=0.32,
                    color=color,
                    opacity=0.55,
                )
                if extra_marker is not None:
                    traces.append(extra_marker)
    else:
        extra_centers = []
        for extra in topology_data.get("extra_overlays") or []:
            center = extra.get("center_coords")
            coords = extra.get("shell_coords") or []
            if center is None or len(coords) == 0:
                continue
            if not _overlay_within_viewport(extra, viewport_ranges):
                continue
            extra_centers.append(center)
        if extra_centers:
            extra_marker = _world_sphere_marker_trace(
                extra_centers,
                radius=0.32,
                color=fallback_color,
                opacity=0.55,
            )
            if extra_marker is not None:
                traces.append(extra_marker)

    cache[cache_key] = [_trace_to_json_safe_dict(tr) for tr in traces]
    return cache[cache_key]




def topology_histogram_figure(topology_data: dict | None) -> go.Figure:
    fig = go.Figure()
    distances = (topology_data or {}).get("all_distances", [])
    shell = set((topology_data or {}).get("distances", []))
    if distances:
        colors = ["#7C5CBF" if dist in shell else "#C9C9E8" for dist in distances]
        fig.add_trace(go.Bar(x=list(range(1, len(distances) + 1)), y=distances, marker_color=colors))
    fig.update_layout(
        margin=dict(l=18, r=18, t=28, b=28),
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis_title="Neighbor rank",
        yaxis_title="Distance (Å)",
        showlegend=False,
        title="Distance Histogram",
    )
    return fig


def topology_results_markdown(topology_data: dict | None) -> str:
    if not topology_data:
        return (
            "Topology analysis inactive.\n"
            "Either no fragment of the requested type exists in this structure, "
            "or the topology overlay is disabled."
        )
    shape = topology_data.get("shape") or {}
    planarity = topology_data.get("planarity", {})
    prism = topology_data.get("prism_analysis", {})
    cn = int(topology_data.get("coordination_number", 0) or 0)
    gap_info = topology_data.get("gap_info") or {}
    gap = gap_info.get("gap_value")
    primary_gap_cn = gap_info.get("primary_gap_cn")
    enclosed = bool(gap_info.get("enclosed"))
    enclosure_expanded = bool(gap_info.get("enclosure_expanded"))
    shell = topology_data.get("shell") or []
    center_formula = topology_data.get("center_formula") or topology_data.get("center_species")
    center_descriptor = center_formula or topology_data.get("center_type", "?")
    lines = [
        f"Center: {topology_data.get('center_label', '?')} ({center_descriptor})",
        f"CN: {cn}" + (f"   |   gap = {gap:.3f} \u00c5" if gap is not None else ""),
    ]
    for warning in topology_data.get("warnings") or []:
        lines.append(f"Warning: {warning}")
    if enclosure_expanded and primary_gap_cn is not None:
        lines.append(
            f"  (gap-only CN was {primary_gap_cn}; expanded so the hull "
            "actually wraps the centre — XYn requires X inside the Y cage.)"
        )
    elif not enclosed and cn >= 4:
        lines.append(
            "  \u26a0 hull does not enclose centre even at the cutoff. "
            "Consider raising the search cutoff or treat this as a partial shell."
        )
    if shell:
        neighbours = ", ".join(
            f"{atom.get('label', '?')}({atom.get('species', '?')}) "
            f"d={atom.get('distance', 0):.2f}"
            for atom in shell[:cn or len(shell)]
        )
        lines.append(f"Shell: {neighbours}")
    label = shape.get("primary_label")
    modifier = shape.get("label_modifier")
    cshm_value = shape.get("cshm_value")
    if label:
        modifier_text = f"{modifier} " if modifier else ""
        cshm_text = f"  (CShM = {cshm_value:.2f})" if cshm_value is not None else ""
        lines.append(f"Shape: {modifier_text}{label}{cshm_text}")
        description = shape.get("structural_description") or ""
        residuals = shape.get("residuals") or []
        # Only print the verbose structural description when it adds info
        # beyond the headline label (i.e. when ``classify_shell`` had to
        # peel off residual atoms to fit a smaller core polyhedron).
        if residuals and description:
            lines.append(f"  {description}")
    elif cn:
        lines.append(
            f"No ideal-polyhedron reference for CN={cn} "
            "(shape registry covers CN 4\u201312)."
        )
    if planarity.get("best_rms") is not None:
        rms = planarity["best_rms"]
        warn = ""
        # A real octahedron / square plane has 4-coplanar RMS << 0.1 Å. Anything
        # above ~0.5 Å says "this isn't a real coordination polyhedron", which is
        # exactly the false-positive the misclassified-B-site bug used to surface.
        if rms > 0.5:
            warn = "  \u26a0 large \u2014 shell may not be a real coordination polyhedron"
        lines.append(f"Best planarity RMS: {rms:.3f} \u00c5{warn}")
    if prism.get("classification"):
        lines.append(
            f"Prism test: {prism['classification']} ({prism['twist_deg']:.1f}\u00b0)"
        )
    return "\n".join(lines)


__all__ = [name for name in globals() if not name.startswith("__")]
