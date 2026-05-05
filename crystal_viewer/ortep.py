from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import plotly.graph_objects as go

from .presets import ORTEP_MODES


CHI2_3D_50 = 2.3659738843753377
CHI2_2D_50 = 1.3862943611198906
DEFAULT_ORTEP_UISO = 0.04
DEFAULT_HYDROGEN_ORTEP_UISO = 0.012

# Visualization-only Uiso ceilings. Some CIFs (Materials Studio,
# legacy SHELX exports, etc.) approximate disordered or split-site
# atoms by inflating Uiso to 0.20-0.25 instead of writing proper
# PART/disorder records; rendering those as honest-to-physics ORTEP
# ellipsoids produces gigantic white blobs that swallow the whole
# scene. Mercury and OLEX2 deal with this by clamping the visible
# ellipsoid size for atoms whose Uiso clearly exceeds physical room-
# temperature thermal motion. We adopt the same convention. The cap
# only affects rendering; the underlying scene/CIF data is untouched.
MAX_ORTEP_UISO_BY_ELEMENT = {
    # Hydrogen cap is intentionally tight: a typical "well-behaved"
    # X-ray hydrogen sits at Uiso ~ 0.02-0.03; anything larger is
    # almost always a disorder placeholder. Capping at 0.025 makes
    # disordered ammonium / water hydrogens render at the same size
    # as ordered C-H atoms in the same scene -- which is what users
    # expect. The "this site is disordered" cue belongs on a
    # separate axis (outline rings, opacity), not on ellipsoid size.
    "H": 0.025,
    "D": 0.025,
}
DEFAULT_MAX_ORTEP_UISO = 0.08


def _probability_scale(probability: float, *, dimensions: int) -> float:
    p = float(probability)
    if not 0.0 < p < 1.0:
        raise ValueError("probability must be between 0 and 1")
    if dimensions == 2:
        return math.sqrt(-2.0 * math.log(1.0 - p))
    if abs(p - 0.5) < 1e-12:
        return math.sqrt(CHI2_3D_50)
    # Wilson-Hilferty approximation for chi-square inverse, k=3.
    # Accurate enough for sizing controls; the documented default is exact.
    z = _normal_ppf(p)
    k = 3.0
    return math.sqrt(k * (1.0 - 2.0 / (9.0 * k) + z * math.sqrt(2.0 / (9.0 * k))) ** 3)


def _normal_ppf(p: float) -> float:
    # Peter J. Acklam's rational approximation, trimmed to the central use
    # needed by ORTEP probability controls.
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


def _as_u_matrix(U: Iterable[Iterable[float]] | None, *, uiso: float | None = None) -> np.ndarray:
    if U is None:
        u = DEFAULT_ORTEP_UISO if uiso is None else max(float(uiso), 1e-6)
        return np.eye(3, dtype=float) * u
    mat = np.asarray(U, dtype=float)
    if mat.shape != (3, 3):
        raise ValueError("U must be a 3x3 matrix")
    if not np.allclose(mat, mat.T, atol=1e-10):
        raise ValueError("U must be symmetric")
    eigvals = np.linalg.eigvalsh(mat)
    if np.any(eigvals < -1e-10):
        raise ValueError("U must be positive semidefinite")
    return (mat + mat.T) / 2.0


def ellipsoid_principal_axes(U, *, probability: float = 0.5, uiso: float | None = None):
    mat = _as_u_matrix(U, uiso=uiso)
    eigvals, eigvecs = np.linalg.eigh(mat)
    order = np.argsort(eigvals)[::-1]
    eigvals = np.clip(eigvals[order], 0.0, None)
    eigvecs = eigvecs[:, order]
    lengths = _probability_scale(probability, dimensions=3) * np.sqrt(eigvals)
    return lengths, eigvecs


def ortep_mesh3d(center, U, *, probability: float = 0.5, lat_steps: int = 10, lon_steps: int = 18, uiso: float | None = None):
    center = np.asarray(center, dtype=float)
    lengths, axes = ellipsoid_principal_axes(U, probability=probability, uiso=uiso)
    vertices = [center + axes @ (lengths * np.array([0.0, 0.0, 1.0]))]
    for lat in range(1, lat_steps):
        theta = math.pi * lat / lat_steps
        for lon in range(lon_steps):
            phi = 2.0 * math.pi * lon / lon_steps
            unit = np.array([math.sin(theta) * math.cos(phi), math.sin(theta) * math.sin(phi), math.cos(theta)])
            vertices.append(center + axes @ (lengths * unit))
    south = len(vertices)
    vertices.append(center + axes @ (lengths * np.array([0.0, 0.0, -1.0])))
    triangles = []
    if lat_steps < 2 or lon_steps < 3:
        raise ValueError("lat_steps must be >= 2 and lon_steps must be >= 3")
    first_ring = 1
    last_ring = 1 + (lat_steps - 2) * lon_steps
    for lon in range(lon_steps):
        nxt = (lon + 1) % lon_steps
        triangles.append([0, first_ring + lon, first_ring + nxt])
    for lat in range(lat_steps - 2):
        ring = 1 + lat * lon_steps
        next_ring = ring + lon_steps
        for lon in range(lon_steps):
            nxt = (lon + 1) % lon_steps
            a = ring + lon
            b = ring + nxt
            c = next_ring + lon
            d = next_ring + nxt
            triangles.extend([[a, c, b], [b, c, d]])
    for lon in range(lon_steps):
        nxt = (lon + 1) % lon_steps
        triangles.append([last_ring + lon, south, last_ring + nxt])
    return np.asarray(vertices, dtype=float), np.asarray(triangles, dtype=int)


def ortep_billboard_polygon(center, U, view_x, view_y, *, probability: float = 0.5, n_pts: int = 48, uiso: float | None = None):
    center = np.asarray(center, dtype=float)
    view_x = np.asarray(view_x, dtype=float)
    view_y = np.asarray(view_y, dtype=float)
    mat = _as_u_matrix(U, uiso=uiso)
    P = np.array([view_x, view_y], dtype=float)
    U2 = P @ mat @ P.T
    U2 = (U2 + U2.T) / 2.0
    eigvals, eigvecs = np.linalg.eigh(U2)
    eigvals = np.clip(eigvals, 0.0, None)
    scale = _probability_scale(probability, dimensions=2)
    a_ax = scale * math.sqrt(float(eigvals[0]))
    b_ax = scale * math.sqrt(float(eigvals[1]))
    e0 = eigvecs[:, 0]
    e1 = eigvecs[:, 1]
    ax3d = e0[0] * view_x + e0[1] * view_y
    ay3d = e1[0] * view_x + e1[1] * view_y
    t = np.linspace(0.0, 2.0 * math.pi, int(n_pts), endpoint=False)
    verts = center[None, :] + (a_ax * np.cos(t))[:, None] * ax3d[None, :] + (b_ax * np.sin(t))[:, None] * ay3d[None, :]
    return verts, float(a_ax), float(b_ax)


def ortep_principal_axis_segments(center, U, *, probability: float = 0.5, uiso: float | None = None):
    center = np.asarray(center, dtype=float)
    lengths, axes = ellipsoid_principal_axes(U, probability=probability, uiso=uiso)
    return [(center - axes[:, idx] * lengths[idx], center + axes[:, idx] * lengths[idx]) for idx in range(3)]


def ortep_octant_shading(center, U, view_dir, *, probability: float = 0.5, uiso: float | None = None):
    center = np.asarray(center, dtype=float)
    view_dir = np.asarray(view_dir, dtype=float)
    view_dir = view_dir / max(np.linalg.norm(view_dir), 1e-12)
    lengths, axes = ellipsoid_principal_axes(U, probability=probability, uiso=uiso)
    octants = []
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                direction = axes @ (lengths * np.array([sx, sy, sz]))
                octants.append({"center": center + 0.5 * direction, "lit": bool(np.dot(direction, view_dir) >= 0.0)})
    return octants


def _atom_element(atom: dict) -> str:
    return str(atom.get("elem") or atom.get("element") or "").strip().capitalize()


def _default_uiso_for_atom(atom: dict) -> float:
    if _atom_element(atom) == "H":
        return DEFAULT_HYDROGEN_ORTEP_UISO
    return DEFAULT_ORTEP_UISO


def _max_visual_uiso_for_atom(atom: dict) -> float:
    return MAX_ORTEP_UISO_BY_ELEMENT.get(_atom_element(atom), DEFAULT_MAX_ORTEP_UISO)


def _clamp_u_for_visualisation(U, uiso: float, atom: dict) -> tuple:
    """Cap the *visual* size of an ellipsoid without touching the
    underlying ADP data.

    Returns ``(U_for_render, uiso_for_render)``. For pure-Uiso atoms
    we just clip the scalar against the per-element ceiling. For
    anisotropic U we scale the whole matrix down so its largest
    eigenvalue stops exceeding the ceiling, preserving the principal
    directions and the *shape* of the ellipsoid; only its overall
    magnitude is clamped. This is what Mercury/OLEX2 do for rogue
    "exploded" ellipsoids that come from disorder-as-Uiso hacks.
    """

    cap = _max_visual_uiso_for_atom(atom)
    if U is None:
        return None, min(float(uiso), cap)
    mat = np.asarray(U, dtype=float)
    eigvals = np.linalg.eigvalsh((mat + mat.T) / 2.0)
    max_eig = float(eigvals.max(initial=0.0))
    if max_eig > cap and max_eig > 0.0:
        mat = mat * (cap / max_eig)
    return mat, min(float(uiso), cap)


def _atom_u(atom: dict):
    uiso = atom.get("uiso")
    if uiso is None or float(uiso) <= 0.0:
        uiso = _default_uiso_for_atom(atom)
    return _clamp_u_for_visualisation(atom.get("U"), float(uiso), atom)


def _atom_color(atom: dict, style: dict) -> str:
    return "#000000" if style.get("monochrome", False) else atom.get("color", "#808080")


def _atom_is_minor(atom: dict) -> bool:
    return bool(atom.get("is_minor"))


def _mode_for_atom(atom: dict, style: dict) -> str | None:
    if _atom_is_minor(atom) and style.get("ortep_mode_minor") is not None:
        return str(style.get("ortep_mode_minor"))
    mode = style.get("ortep_mode")
    return str(mode) if mode is not None else None


def _mode_flag(atom: dict, style: dict, key: str, default: bool) -> bool:
    mode = _mode_for_atom(atom, style)
    if mode in ORTEP_MODES:
        return bool(ORTEP_MODES[mode].get(key, default))
    return bool(style.get(key, default))


def _minor_axes_outline_only(atom: dict, style: dict) -> bool:
    # Keep historic ortep_axes behaviour for normal calls. Only the explicit
    # per-minor mode gets the publication convention of unfilled minor sites.
    return _atom_is_minor(atom) and style.get("ortep_mode_minor") == "ortep_axes"


def _ortep_outline_trace(segments, *, color: str, width: float, name: str):
    if not segments:
        return None
    xs, ys, zs = [], [], []
    for ring in segments:
        for point in ring:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
            zs.append(float(point[2]))
        first = ring[0]
        xs.extend([float(first[0]), None])
        ys.extend([float(first[1]), None])
        zs.extend([float(first[2]), None])
    return go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="lines",
        line=dict(color=color, width=width),
        opacity=0.95,
        hoverinfo="skip",
        showlegend=False,
        name=name,
    )


def ortep_atom_mesh_traces(scene: dict, style: dict):
    """Batch all ORTEP ellipsoids that share a (color, opacity) group
    into a single ``Mesh3d`` trace.

    Plotly issues one WebGL draw call per Mesh3d, and the React/Dash
    update path validates and serialises each trace independently.
    Emitting one trace per atom (the original implementation) was
    fine for a 24-atom asymmetric unit but turned a 200-atom DAP-4
    unit cell into ~200+ traces -- ~2 MB of figure JSON before the
    topology overlay even joins, with a visible per-frame stutter
    in the WebGL renderer. Batching by colour collapses this to one
    trace per element (~5-7 traces total) at no visual cost: the
    individual ellipsoids stay disjoint inside the merged mesh
    because every atom contributes a closed sphere of triangles.
    """

    probability = float(style.get("ortep_probability", 0.5))
    show_minor_only = bool(style.get("show_minor_only", False))
    minor_opacity = float(style.get("minor_opacity", 0.35))
    major_opacity = float(style.get("major_opacity", 1.0))
    # ``force_minor_fade`` lets callers combine dashed-bond disorder with
    # translucent minor ellipsoids (the default ``disorder == "opacity"``
    # behaviour is otherwise mutually exclusive with dashed bonds).
    fade_minor = (
        style.get("disorder") == "opacity"
        or bool(style.get("force_minor_fade", False))
    )

    # Subdivision budget mirrors ``_atom_mesh_traces``. ORTEP scenes
    # are typically denser than ball-stick (every atom carries an
    # ellipsoid plus principal-axis dashes) so we step down a tier.
    n_atoms = sum(1 for a in scene.get("draw_atoms", []) if not show_minor_only or a.get("is_minor"))
    if n_atoms > 400:
        lat_steps, lon_steps = 4, 8
    elif n_atoms > 150:
        lat_steps, lon_steps = 5, 10
    elif n_atoms > 60:
        lat_steps, lon_steps = 7, 12
    else:
        lat_steps, lon_steps = 10, 18

    # group_key = (color, opacity). Same color but different opacity
    # (because of the half-occupied-disorder fade) needs distinct
    # traces so we don't collapse two visually different sets.
    groups: dict[tuple[str, float], dict] = {}
    outline_segments = []
    view_x = np.asarray(scene.get("view_x", [1.0, 0.0, 0.0]), dtype=float)
    view_y = np.asarray(scene.get("view_y", [0.0, 1.0, 0.0]), dtype=float)
    for atom in scene.get("draw_atoms", []):
        if show_minor_only and not atom.get("is_minor"):
            continue
        is_minor = _atom_is_minor(atom)
        U, uiso = _atom_u(atom)
        if _minor_axes_outline_only(atom, style):
            ring, _, _ = ortep_billboard_polygon(atom["cart"], U, view_x, view_y, probability=probability, uiso=uiso)
            outline_segments.append(ring)
            continue
        opacity = minor_opacity if (is_minor and fade_minor) else major_opacity
        color = _atom_color(atom, style)
        key = (color, opacity)
        bucket = groups.setdefault(key, {"verts": [], "tris": [], "vert_offset": 0})
        verts, tris = ortep_mesh3d(
            atom["cart"], U,
            probability=probability,
            lat_steps=lat_steps,
            lon_steps=lon_steps,
            uiso=uiso,
        )
        bucket["verts"].append(verts)
        bucket["tris"].append(tris + bucket["vert_offset"])
        bucket["vert_offset"] += len(verts)

    traces = []
    for (color, opacity), bucket in groups.items():
        if not bucket["verts"]:
            continue
        verts = np.concatenate(bucket["verts"], axis=0)
        tris = np.concatenate(bucket["tris"], axis=0)
        traces.append(
            go.Mesh3d(
                x=verts[:, 0],
                y=verts[:, 1],
                z=verts[:, 2],
                i=tris[:, 0],
                j=tris[:, 1],
                k=tris[:, 2],
                color=color,
                opacity=opacity,
                name=f"{color} ORTEP",
                hoverinfo="skip",
                showlegend=False,
                flatshading=False,
            )
        )
    outline_trace = _ortep_outline_trace(
        outline_segments,
        color=style.get("ortep_axis_color", "#222222"),
        width=float(style.get("ortep_axis_linewidth", 1.6)),
        name="ortep-minor-outlines",
    )
    if outline_trace is not None:
        traces.append(outline_trace)
    return traces


def ortep_atom_billboard_traces(scene: dict, style: dict):
    traces = []
    probability = float(style.get("ortep_probability", 0.5))
    view_x = np.asarray(scene.get("view_x", [1.0, 0.0, 0.0]), dtype=float)
    view_y = np.asarray(scene.get("view_y", [0.0, 1.0, 0.0]), dtype=float)
    outline_segments = []
    for atom in scene.get("draw_atoms", []):
        if style.get("show_minor_only", False) and not atom.get("is_minor"):
            continue
        U, uiso = _atom_u(atom)
        ring, _, _ = ortep_billboard_polygon(atom["cart"], U, view_x, view_y, probability=probability, uiso=uiso)
        if _minor_axes_outline_only(atom, style):
            outline_segments.append(ring)
            continue
        center = np.asarray(atom["cart"], dtype=float)
        verts = np.vstack([center[None, :], ring])
        n = len(ring)
        tris = np.asarray([[0, idx, 1 + (idx % n)] for idx in range(1, n + 1)], dtype=int)
        traces.append(
            go.Mesh3d(
                x=verts[:, 0],
                y=verts[:, 1],
                z=verts[:, 2],
                i=tris[:, 0],
                j=tris[:, 1],
                k=tris[:, 2],
                color=_atom_color(atom, style),
                opacity=1.0,
                hoverinfo="skip",
                showlegend=False,
                flatshading=True,
            )
        )
    outline_trace = _ortep_outline_trace(
        outline_segments,
        color=style.get("ortep_axis_color", "#222222"),
        width=float(style.get("ortep_axis_linewidth", 1.6)),
        name="ortep-minor-outlines",
    )
    if outline_trace is not None:
        traces.append(outline_trace)
    return traces


def ortep_axis_dash_traces(scene: dict, style: dict):
    xs, ys, zs = [], [], []
    probability = float(style.get("ortep_probability", 0.5))
    for atom in scene.get("draw_atoms", []):
        if style.get("show_minor_only", False) and not atom.get("is_minor"):
            continue
        if not _mode_flag(atom, style, "ortep_show_principal_axes", bool(style.get("ortep_show_principal_axes", True))):
            continue
        U, uiso = _atom_u(atom)
        for start, end in ortep_principal_axis_segments(atom["cart"], U, probability=probability, uiso=uiso):
            xs.extend([float(start[0]), float(end[0]), None])
            ys.extend([float(start[1]), float(end[1]), None])
            zs.extend([float(start[2]), float(end[2]), None])
    if not xs:
        return []
    return [
        go.Scatter3d(
            x=xs,
            y=ys,
            z=zs,
            mode="lines",
            line=dict(color=style.get("ortep_axis_color", "#222222"), width=float(style.get("ortep_axis_linewidth", 1.6))),
            opacity=0.8,
            hoverinfo="skip",
            showlegend=False,
            name="ortep-principal-axes",
        )
    ]


def ortep_octant_shade_traces(scene: dict, style: dict):
    probability = float(style.get("ortep_probability", 0.5))
    color = style.get("ortep_octant_shadow_color", "#000000")
    opacity = float(style.get("ortep_octant_shadow_alpha", 0.18))
    steps = int(style.get("ortep_octant_steps", 5))
    selected_octants = [
        (sx, sy, sz)
        for sx in (-1.0, 1.0)
        for sy in (-1.0, 1.0)
        for sz in (-1.0, 1.0)
        if sx * sy * sz > 0.0
    ]

    vertices: list[np.ndarray] = []
    triangles: list[list[int]] = []
    for atom in scene.get("draw_atoms", []):
        if style.get("show_minor_only", False) and not atom.get("is_minor"):
            continue
        if not _mode_flag(atom, style, "ortep_octant_shading", bool(style.get("ortep_octant_shading", False))):
            continue
        center = np.asarray(atom["cart"], dtype=float)
        U, uiso = _atom_u(atom)
        lengths, axes = ellipsoid_principal_axes(U, probability=probability, uiso=uiso)
        for sx, sy, sz in selected_octants:
            base = len(vertices)
            for ti in range(steps + 1):
                theta = 0.5 * math.pi * ti / steps
                for pi in range(steps + 1):
                    phi = 0.5 * math.pi * pi / steps
                    local = np.array(
                        [
                            sx * math.sin(theta) * math.cos(phi),
                            sy * math.sin(theta) * math.sin(phi),
                            sz * math.cos(theta),
                        ],
                        dtype=float,
                    )
                    vertices.append(center + axes @ (lengths * local))
            stride = steps + 1
            for ti in range(steps):
                for pi in range(steps):
                    a = base + ti * stride + pi
                    b = a + 1
                    c = a + stride
                    d = c + 1
                    triangles.extend([[a, c, b], [b, c, d]])

    if not vertices:
        return []
    verts = np.asarray(vertices, dtype=float)
    tris = np.asarray(triangles, dtype=int)
    return [
        go.Mesh3d(
            x=verts[:, 0],
            y=verts[:, 1],
            z=verts[:, 2],
            i=tris[:, 0],
            j=tris[:, 1],
            k=tris[:, 2],
            color=color,
            opacity=opacity,
            hoverinfo="skip",
            showlegend=False,
            flatshading=True,
            name="ortep-octant-shading",
        )
    ]


def build_ortep_panel_figure(scene: dict, *, probability: float = 0.5, show_axes: bool = True, shade_octants: bool = False, **kwargs):
    from .renderer import build_figure

    style = {
        "material": kwargs.pop("material", "mesh"),
        "style": "ortep",
        "disorder": kwargs.pop("disorder", "outline_rings"),
        "ortep_probability": probability,
        "ortep_show_principal_axes": show_axes,
        "ortep_octant_shading": shade_octants,
    }
    style.update(kwargs)
    return build_figure(scene, style)
