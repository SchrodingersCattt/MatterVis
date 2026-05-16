from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .renderer_common import *
from .renderer_meshes import *
from .renderer_style import *
from .renderer_serialize import _round_coord_arrays

def _ring_segments(center: np.ndarray, radius: float, axis: np.ndarray, *, segments: int = 14):
    """Generate (start, end) line segments forming a circular ring of
    ``radius`` around ``center`` lying in the plane perpendicular to
    ``axis``. Used by the disorder-outline wireframe so the rings are
    real 3-D geometry that scales with the camera."""
    axis = np.asarray(axis, dtype=float)
    axis = axis / max(np.linalg.norm(axis), 1e-9)
    ref = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, ref)
    u = u / max(np.linalg.norm(u), 1e-9)
    v = np.cross(axis, u)
    pts = []
    for k in range(segments):
        theta = 2.0 * np.pi * k / segments
        pts.append(center + radius * (np.cos(theta) * u + np.sin(theta) * v))
    out = []
    for k in range(segments):
        out.append((pts[k], pts[(k + 1) % segments]))
    return out


def _minor_outline_traces(scene: dict, style: dict):
    """Wireframe sphere around each minor / disorder atom, built from
    three perpendicular rings of Mesh3d cylinders. Replaces the old
    ``Scatter3d(mode="markers", line=dict(width=...))`` rings whose
    pixel-fixed width meant the disorder outlines stayed the same
    screen size when the camera dollied out -- and ate the atoms whole
    once the structure shrank."""
    if style.get("disorder") not in ("outline_rings", "color_shift") and not style.get("minor_wireframe", False):
        return []
    groups: Dict[str, list[tuple[np.ndarray, float]]] = {}
    for atom in scene["draw_atoms"]:
        if not atom["is_minor"]:
            continue
        if not _atom_render_visible(atom):
            continue
        if style.get("show_minor_only", False) and not atom["is_minor"]:
            continue
        ring_scale = 1.34 if style.get("minor_wireframe", False) else 1.20
        radius = float(atom["atom_radius"]) * float(style["atom_scale"]) * ring_scale
        color = _atom_render_color(atom, style, light=True)
        groups.setdefault(color, []).append((np.asarray(atom["cart"], dtype=float), radius))
    if not groups:
        return []
    cylinder_radius = 0.022 if style.get("minor_wireframe", False) else 0.014
    axes = [
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    ]
    traces = []
    for color, minors in groups.items():
        segments: list[tuple[np.ndarray, np.ndarray]] = []
        for center, radius in minors:
            for axis in axes:
                segments.extend(_ring_segments(center, radius, axis, segments=14))
        trace = _segment_cylinder_trace(
            segments,
            radius=cylinder_radius,
            color=color,
            opacity=0.95,
            sides=4,
            name="minor-outline",
        )
        if trace is not None:
            traces.append(_annotate_trace(trace, "minor_overlay", is_minor=True))
    return traces


def _contact_traces(scene: dict, style: dict):
    """Render scene-level non-bonded contacts (e.g. ammonium...halide contacts).

    A "contact" is a thin dashed/dotted line segment specified in the same
    cartesian frame as the atoms, so it is guaranteed to land pixel-for-pixel
    on the rendered atom centres regardless of camera, viewport or panel
    aspect. Each contact dict supports::

        {"start": [x, y, z], "end": [x, y, z],
         "color": "#222222", "dash": "dot", "width": 4.0, "opacity": 0.9}

    Contacts whose endpoints fall outside the visible viewport are still
    drawn — Plotly only clips against the scene cube, never against the
    Matplotlib outer axes.
    """
    contacts = scene.get("contacts") or []
    if not contacts:
        return []
    groups: Dict[Tuple[str, str, float, float], list[list[float]]] = {}
    for c in contacts:
        try:
            start = [float(c["start"][i]) for i in range(3)]
            end = [float(c["end"][i]) for i in range(3)]
        except (KeyError, TypeError, IndexError, ValueError):
            continue
        color = str(c.get("color", "#1F1F1F"))
        dash = str(c.get("dash", "dot"))
        width = float(c.get("width", 4.0))
        opacity = float(c.get("opacity", 0.9))
        key = (color, dash, width, opacity)
        groups.setdefault(key, []).append([start, end])
    traces = []
    for (color, dash, width, opacity), segments in groups.items():
        xs, ys, zs = [], [], []
        for start, end in segments:
            xs.extend([start[0], end[0], None])
            ys.extend([start[1], end[1], None])
            zs.extend([start[2], end[2], None])
        traces.append(
            go.Scatter3d(
                x=xs,
                y=ys,
                z=zs,
                mode="lines",
                line=dict(color=color, width=width, dash=dash),
                opacity=opacity,
                hoverinfo="skip",
                showlegend=False,
                name="contact",
            )
        )
    return traces


def _highlight_traces(scene: dict, style: dict):
    if style.get("show_minor_only", False):
        return []
    light_dir = (
        -0.28 * np.array(scene["view_x"], dtype=float)
        + 0.34 * np.array(scene["view_y"], dtype=float)
        + 0.72 * np.array(scene["view_z"], dtype=float)
    )
    norm = np.linalg.norm(light_dir)
    if norm < 1e-8:
        return []
    light_dir /= norm

    groups: Dict[str, dict] = {}
    for atom in scene["draw_atoms"]:
        if atom["is_minor"] or atom["elem"] == "H":
            continue
        size = max(5.0, 55.0 * atom["atom_radius"] * float(style["atom_scale"]))
        center = np.array(atom["cart"], dtype=float) + light_dir * (atom["atom_radius"] * float(style["atom_scale"]) * 0.25)
        key = atom["color_light"]
        groups.setdefault(key, {"x": [], "y": [], "z": [], "size": []})
        groups[key]["x"].append(float(center[0]))
        groups[key]["y"].append(float(center[1]))
        groups[key]["z"].append(float(center[2]))
        groups[key]["size"].append(size)

    traces = []
    for color, payload in groups.items():
        traces.append(
            go.Scatter3d(
                x=payload["x"],
                y=payload["y"],
                z=payload["z"],
                mode="markers",
                marker=dict(
                    size=payload["size"],
                    color=color,
                    opacity=0.65,
                    line=dict(color="rgba(255,255,255,0.6)", width=1.5),
                ),
                hoverinfo="skip",
                showlegend=False,
            )
        )
    return traces


def _label_traces(scene: dict, style: dict, hidden_labels: set | None = None):
    hidden_labels = hidden_labels or set()
    cache = scene.setdefault("_label_trace_cache", {})
    # ``monochrome`` is inert once atom_groups is non-empty (see
    # ``_style_color``); collapse it in the key so the same cache hit
    # serves both pre- and post-migration callers.
    monochrome_effective = bool(style.get("monochrome", False)) and not style.get("atom_groups")
    key = (
        monochrome_effective,
        str(style.get("label_color", "#111111")),
        str(style.get("minor_label_color", "#666666")),
        round(float(style.get("label_font_size", 12)), 3),
        # Cache key carries the hidden set so toggling a "Hide H"
        # group rule actually re-emits the label trace; otherwise a
        # warm cache from before the rule still draws the H labels.
        tuple(sorted(hidden_labels)),
    )
    if key in cache:
        perf_log.record("cache:mesh", kind="cache", info={"hit": True, "entries": len(cache)})
        return cache[key]
    perf_log.record("cache:mesh", kind="cache", info={"hit": False, "entries": len(cache)})
    # Use a single font size for every atom label (was 10 vs 11 split by
    # minor-disorder flag, which read as inconsistent typography rather than
    # signalling "minor"). Disorder is conveyed by colour only; size stays uniform.
    label_size = float(style.get("label_font_size", 12))
    major_label_color = style.get("label_color", "#111111")
    minor_label_color = style.get("minor_label_color", "#666666")
    buckets = {
        False: {"x": [], "y": [], "z": [], "text": [], "color": major_label_color},
        True: {"x": [], "y": [], "z": [], "text": [], "color": minor_label_color},
    }
    for item in scene["label_items"]:
        if str(item.get("text")) in hidden_labels:
            continue
        bucket = buckets[item["is_minor"]]
        bucket["x"].append(float(item["label_cart"][0]))
        bucket["y"].append(float(item["label_cart"][1]))
        bucket["z"].append(float(item["label_cart"][2]))
        bucket["text"].append(item["text"])

    traces = []
    for is_minor, bucket in buckets.items():
        if not bucket["x"]:
            continue
        traces.append(
            _annotate_trace(go.Scatter3d(
                x=bucket["x"],
                y=bucket["y"],
                z=bucket["z"],
                mode="text",
                text=bucket["text"],
                textfont=dict(size=label_size, color=bucket["color"]),
                hoverinfo="skip",
                showlegend=False,
            ), "labels", is_minor=is_minor)
        )
    cache[key] = [_round_coord_arrays(tr.to_plotly_json()) for tr in traces]
    return cache[key]


def _axis_traces(scene: dict, style: dict):
    """Deprecated: the in-scene 3D axis triad has been retired.

    The 3D cylinder shafts foreshortened to invisible stubs on cameras
    aligned with a lattice vector, and the opposite case (oblique
    cameras on long cells like EMAP) drew a single long shaft through
    the structure. The ``show_axes`` toggle now drives the paper-coord
    compass via :func:`axis_key_overlay`, which always sits in a stable
    figure corner. Kept as an empty-list shim so any external caller
    that still imports the symbol keeps working.
    """
    return []


_COMPASS_ITEM_NAME = "mv_compass"








def _segment_cylinder_trace(segments, *, radius: float, color: str, opacity: float = 0.95, sides: int = 5, name: str | None = None):
    """Materialise a list of (start, end) line segments as a single
    Mesh3d cylinder bundle. Unlike a Scatter3d ``line.width`` (pixels),
    the cylinder radius lives in world (Å) coordinates so the segment
    thickness scales with the camera zoom -- matching the rest of the
    scene geometry. ``sides=5`` keeps the per-edge triangle count low
    (10 verts per segment) so dense overlays stay cheap."""
    verts, tris = _cylinder_mesh_batch(segments, float(radius), sides=int(sides))
    if len(verts) == 0:
        return None
    return go.Mesh3d(
        x=verts[:, 0],
        y=verts[:, 1],
        z=verts[:, 2],
        i=tris[:, 0],
        j=tris[:, 1],
        k=tris[:, 2],
        color=color,
        opacity=opacity,
        flatshading=True,
        hoverinfo="skip",
        showlegend=False,
        name=name or "line-mesh",
    )


def _dashed_segments(segments, *, dash_len: float, gap_len: float):
    """Break each (start, end) into shorter sub-segments alternating
    drawn / skipped, so the rendered cylinder bundle reads as a dashed
    line. ``dash_len`` and ``gap_len`` are in world (Å) units."""
    out: list[tuple[np.ndarray, np.ndarray]] = []
    period = float(dash_len) + float(gap_len)
    if period <= 0:
        return list(segments)
    for start, end in segments:
        start = np.asarray(start, dtype=float)
        end = np.asarray(end, dtype=float)
        vec = end - start
        length = float(np.linalg.norm(vec))
        if length < 1e-8:
            continue
        direction = vec / length
        cursor = 0.0
        while cursor < length:
            seg_start = start + direction * cursor
            seg_end_dist = min(length, cursor + float(dash_len))
            seg_end = start + direction * seg_end_dist
            out.append((seg_start, seg_end))
            cursor += period
    return out


def _unit_cell_traces(scene: dict, style: dict):
    if scene.get("M") is None:
        return []
    origin = np.zeros(3, dtype=float)
    a = np.array(scene["M"][0], dtype=float)
    b = np.array(scene["M"][1], dtype=float)
    c = np.array(scene["M"][2], dtype=float)
    corners = {
        "000": origin,
        "100": a,
        "010": b,
        "001": c,
        "110": a + b,
        "101": a + c,
        "011": b + c,
        "111": a + b + c,
    }
    edges = [
        ("000", "100"), ("000", "010"), ("000", "001"),
        ("100", "110"), ("100", "101"),
        ("010", "110"), ("010", "011"),
        ("001", "101"), ("001", "011"),
        ("110", "111"), ("101", "111"), ("011", "111"),
    ]
    segments = [(corners[s], corners[e]) for s, e in edges]
    trace = _segment_cylinder_trace(
        segments,
        radius=0.04,
        color="#777777",
        opacity=0.8,
        sides=4,
        name="unit-cell-box",
    )
    return [_annotate_trace(trace, "unit_cell")] if trace is not None else []





__all__ = [name for name in globals() if not name.startswith("__")]
