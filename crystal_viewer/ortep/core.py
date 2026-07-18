from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import plotly.graph_objects as go

from ..presets import ORTEP_MODES


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
    # Phase 2: a per-group ``_render_color`` override (set by
    # :func:`crystal_viewer.atom_groups.tag_atoms_with_groups`) wins
    # over both the element palette and the legacy monochrome flag.
    override = atom.get("_render_color")
    if override:
        return str(override)
    return "#000000" if style.get("monochrome", False) else atom.get("color", "#808080")


def _atom_is_minor(atom: dict) -> bool:
    return bool(atom.get("is_minor"))


def _atom_render_visible(atom: dict) -> bool:
    """Mirror of :func:`crystal_viewer.renderer._atom_render_visible`.

    Inlined here so the ortep module stays self-contained (no
    renderer-import cycle). Atoms without the field default to
    visible.
    """
    return bool(atom.get("_render_visible", True))


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
    # Trigger on occ < 1 OR legacy is_minor flag (for test fixtures without occ).
    occ = float(atom.get("occ", 1.0))
    is_disorder = occ < 0.999 or _atom_is_minor(atom)
    return is_disorder and style.get("ortep_mode_minor") == "ortep_axes"


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
    # User can override via ortep_lat_steps / ortep_lon_steps style keys.
    user_lat = style.get("ortep_lat_steps")
    user_lon = style.get("ortep_lon_steps")
    if user_lat is not None and user_lon is not None:
        lat_steps, lon_steps = int(user_lat), int(user_lon)
    else:
        n_atoms = sum(1 for a in scene.get("draw_atoms", []) if not show_minor_only or a.get("is_minor"))
        if n_atoms > 400:
            lat_steps, lon_steps = 4, 8
        elif n_atoms > 150:
            lat_steps, lon_steps = 5, 10
        elif n_atoms > 60:
            lat_steps, lon_steps = 7, 12
        else:
            lat_steps, lon_steps = 10, 18

    # Fixed H-atom sphere radius (Å) in ORTEP mode. When set, hydrogen
    # atoms are rendered as small spheres instead of ADP ellipsoids.
    h_radius = style.get("ortep_hydrogen_radius")

    # Mesh3d lighting passthrough.
    mesh_lighting = style.get("mesh_lighting")

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
        if not _atom_render_visible(atom):
            continue
        is_minor = _atom_is_minor(atom)
        U, uiso = _atom_u(atom)
        if _minor_axes_outline_only(atom, style):
            ring, _, _ = ortep_billboard_polygon(atom["cart"], U, view_x, view_y, probability=probability, uiso=uiso)
            outline_segments.append(ring)
            continue
        occ = float(atom.get("occ", 1.0))
        is_partial = occ < 0.999 or is_minor
        opacity = occ if (is_partial and fade_minor) else major_opacity
        color = _atom_color(atom, style)
        key = (color, opacity)
        bucket = groups.setdefault(key, {"verts": [], "tris": [], "vert_offset": 0})
        # H atoms with ortep_hydrogen_radius: fixed-size sphere, skip ellipsoid.
        elem = _atom_element(atom)
        if h_radius is not None and elem in ("H", "D"):
            from ..render.meshes import _sphere_mesh
            verts, tris = _sphere_mesh(atom["cart"], float(h_radius), lat_steps=lat_steps, lon_steps=lon_steps)
        else:
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
        mesh_kwargs = dict(
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
        if mesh_lighting:
            mesh_kwargs["lighting"] = mesh_lighting
        traces.append(go.Mesh3d(**mesh_kwargs))
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
        if not _atom_render_visible(atom):
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
        if not _atom_render_visible(atom):
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
        if not _atom_render_visible(atom):
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


def ortep_octant_hatch_traces(scene: dict, style: dict):
    """Draw classic ORTEP-III parallel hatch lines on one octant per atom.

    The shaded octant is the one whose centroid is closest to the camera
    (most negative dot product with ``view_dir``).  On that octant we draw
    ``n_lines`` constant-θ "latitude" arcs in the principal-axis frame,
    plus three boundary arcs that frame the hatched region.  All segments
    are batched into one ``Scatter3d`` line trace, so the runtime cost is
    O(atoms × arcs) line vertices and a single WebGL draw call.

    Why arcs instead of straight chords?  In the principal-axis basis the
    parametric surface ``p(θ, φ) = c + axes · diag(L) · (sin θ cos φ, …)``
    yields arcs that are curves *on* the ellipsoid surface; their 2D
    projection traces the same look as the hatch fills on engraved ORTEP
    figures (Mercury and ORTEP-III both rasterise these surface curves).
    A chord-only implementation would float in front of the silhouette and
    look pasted-on at oblique camera angles.

    Style keys honoured (defaults in DEFAULT_STYLE):
      * ``ortep_octant_hatching`` (bool, gate)
      * ``ortep_octant_hatch_lines`` (int, density per octant)
      * ``ortep_octant_hatch_arc_pts`` (int, sampling per arc)
      * ``ortep_octant_hatch_color`` / ``_linewidth``
      * ``ortep_octant_edge_color`` / ``_linewidth``
      * Per-atom ``atom["hatch_density_multiplier"]`` (float ≥ 1.0) lets
        callers double-hatch heavy atoms without touching style.
    """
    probability = float(style.get("ortep_probability", 0.5))
    base_lines = int(style.get("ortep_octant_hatch_lines", 5))
    n_arc_pts = int(style.get("ortep_octant_hatch_arc_pts", 14))
    color = style.get(
        "ortep_octant_hatch_color",
        style.get("ortep_octant_shadow_color", "#1A1A1A"),
    )
    width = float(style.get("ortep_octant_hatch_linewidth", 1.4))
    edge_color = style.get("ortep_octant_edge_color", color)
    edge_width = float(style.get("ortep_octant_edge_linewidth", width * 1.4))
    show_minor_only = bool(style.get("show_minor_only", False))
    fade_minor = (
        style.get("disorder") == "opacity"
        or bool(style.get("force_minor_fade", False))
    )
    minor_opacity = max(0.05, float(style.get("minor_opacity", 0.35)))

    view_dir = np.asarray(
        scene.get("view_direction", scene.get("view_z", [1.0, 0.0, 0.0])),
        dtype=float,
    )
    nrm = float(np.linalg.norm(view_dir))
    view_dir = view_dir / nrm if nrm > 1e-9 else np.array([1.0, 0.0, 0.0])
    # Push the entire hatch toward the camera so it sits above the white
    # atom-fill disk.  Without this lift, hatch arcs near the ellipsoid
    # equator (whose own ``z`` is ≈ atom centre) get occluded by the fill,
    # and only the arcs that bulge most toward the camera survive.
    hatch_lift = view_dir * float(style.get("ortep_z_lift_hatch", 0.06))

    # Each list-of-three is (xs, ys, zs) for a separate trace bucket so that
    # the major/minor atoms can be drawn at different opacities without
    # dropping back to one trace per atom.
    hatch_buckets = {"major": ([], [], []), "minor": ([], [], [])}
    edge_buckets = {"major": ([], [], []), "minor": ([], [], [])}

    for atom in scene.get("draw_atoms", []):
        if show_minor_only and not atom.get("is_minor"):
            continue
        if not _atom_render_visible(atom):
            continue
        if not _mode_flag(
            atom, style, "ortep_octant_hatching",
            bool(style.get("ortep_octant_hatching", False)),
        ):
            continue
        if _atom_element(atom) == "H" and not bool(style.get("show_hydrogen", False)):
            continue

        bucket_key = "minor" if (_atom_is_minor(atom) and fade_minor) else "major"
        hatch_xs, hatch_ys, hatch_zs = hatch_buckets[bucket_key]
        edge_xs, edge_ys, edge_zs = edge_buckets[bucket_key]

        center = np.asarray(atom["cart"], dtype=float)
        U, uiso = _atom_u(atom)
        lengths, axes = ellipsoid_principal_axes(U, probability=probability, uiso=uiso)
        # No mesh body in classic-hatch mode → arcs sit on the natural
        # ellipsoid surface.  A tiny 1 % expansion still helps when the
        # caller pairs hatch with the legacy filled-octant Mesh3d (rare).
        line_lengths = lengths * 1.005

        # ``view_dir`` (a.k.a. ``scene["view_direction"]``) points FROM the
        # scene TOWARD the viewer — see ``view_rotation`` doc.  So the octant
        # facing the camera is the one whose centroid has the LARGEST positive
        # dot product with ``view_dir`` (i.e. sticks out toward the viewer).
        best_signs = (1.0, 1.0, 1.0)
        best_dot = -float("inf")
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                for sz in (-1.0, 1.0):
                    direction = axes @ (lengths * np.array([sx, sy, sz]))
                    d = float(np.dot(direction, view_dir))
                    if d > best_dot:
                        best_dot = d
                        best_signs = (sx, sy, sz)
        sx, sy, sz = best_signs

        # Per-atom hatch density (Br/heavy atoms can pass >1 to densify).
        n_lines = max(1, int(round(base_lines * float(
            atom.get("hatch_density_multiplier", 1.0)
        ))))

        # ── Hatch latitudes: constant θ, sweep φ ∈ [0, π/2] ──────────────
        for k in range(1, n_lines + 1):
            theta = 0.5 * math.pi * k / (n_lines + 1)
            st, ct = math.sin(theta), math.cos(theta)
            for j in range(n_arc_pts + 1):
                phi = 0.5 * math.pi * j / n_arc_pts
                local = np.array(
                    [sx * st * math.cos(phi), sy * st * math.sin(phi), sz * ct],
                    dtype=float,
                )
                p = center + axes @ (line_lengths * local) + hatch_lift
                hatch_xs.append(float(p[0]))
                hatch_ys.append(float(p[1]))
                hatch_zs.append(float(p[2]))
            hatch_xs.append(None)
            hatch_ys.append(None)
            hatch_zs.append(None)

        # ── Octant boundary arcs (3 quarter-circles framing the hatch) ──
        thetas = np.linspace(0.0, 0.5 * math.pi, n_arc_pts + 1)
        # Edge at φ=0 (e1-e3 plane) and φ=π/2 (e2-e3 plane)
        for phi_const in (0.0, 0.5 * math.pi):
            cp, sp = math.cos(phi_const), math.sin(phi_const)
            for theta in thetas:
                st, ct = math.sin(theta), math.cos(theta)
                local = np.array(
                    [sx * st * cp, sy * st * sp, sz * ct],
                    dtype=float,
                )
                p = center + axes @ (line_lengths * local) + hatch_lift
                edge_xs.append(float(p[0]))
                edge_ys.append(float(p[1]))
                edge_zs.append(float(p[2]))
            edge_xs.append(None)
            edge_ys.append(None)
            edge_zs.append(None)
        # Equator at θ=π/2, sweep φ
        for j in range(n_arc_pts + 1):
            phi = 0.5 * math.pi * j / n_arc_pts
            local = np.array(
                [sx * math.cos(phi), sy * math.sin(phi), 0.0],
                dtype=float,
            )
            p = center + axes @ (line_lengths * local) + hatch_lift
            edge_xs.append(float(p[0]))
            edge_ys.append(float(p[1]))
            edge_zs.append(float(p[2]))
        edge_xs.append(None)
        edge_ys.append(None)
        edge_zs.append(None)

    traces = []
    for key, opacity, suffix in (
        ("major", 0.95, ""),
        ("minor", minor_opacity, "-minor"),
    ):
        hxs, hys, hzs = hatch_buckets[key]
        if hxs:
            traces.append(go.Scatter3d(
                x=hxs, y=hys, z=hzs,
                mode="lines",
                line=dict(color=color, width=width),
                opacity=opacity,
                hoverinfo="skip",
                showlegend=False,
                name=f"ortep-octant-hatch{suffix}",
            ))
        exs, eys, ezs = edge_buckets[key]
        if exs:
            traces.append(go.Scatter3d(
                x=exs, y=eys, z=ezs,
                mode="lines",
                line=dict(color=edge_color, width=edge_width),
                opacity=opacity if key == "minor" else 0.98,
                hoverinfo="skip",
                showlegend=False,
                name=f"ortep-octant-edges{suffix}",
            ))
    return traces


def ortep_atom_fill_traces(scene: dict, style: dict):
    """Per-atom WHITE filled billboard disk (sits *between* bonds and outline).

    This is what makes the classic ORTEP-III layered look possible without
    a 3D mesh body: bond strokes drawn first, atom disks overpaint the
    bonds where they enter the atom, silhouette + hatch sit on top.  We
    push the disk slightly toward the camera (``-view_dir * z_lift``) so
    Plotly's z-buffer reliably draws atom over bond at every camera angle.

    Returns a single batched ``Mesh3d`` triangle-fan trace per fill colour;
    minor atoms use the same colour but reduced opacity so disorder still
    reads through the white fill.
    """
    if not bool(style.get("ortep_atom_fill", False)):
        return []
    probability = float(style.get("ortep_probability", 0.5))
    fill_color = style.get("ortep_atom_fill_color", "#FFFFFF")
    show_minor_only = bool(style.get("show_minor_only", False))
    fade_minor = (
        style.get("disorder") == "opacity"
        or bool(style.get("force_minor_fade", False))
    )
    minor_opacity = max(0.05, float(style.get("minor_opacity", 0.35)))
    z_lift = float(style.get("ortep_z_lift_fill", 0.04))
    view_x = np.asarray(scene.get("view_x", [1.0, 0.0, 0.0]), dtype=float)
    view_y = np.asarray(scene.get("view_y", [0.0, 1.0, 0.0]), dtype=float)

    view_dir = np.asarray(
        scene.get("view_direction", scene.get("view_z", [0.0, 0.0, 1.0])),
        dtype=float,
    )
    nrm = float(np.linalg.norm(view_dir))
    view_dir = view_dir / nrm if nrm > 1e-9 else np.array([0.0, 0.0, 1.0])
    cam = view_dir * z_lift  # ``view_dir`` points toward camera (see view_rotation)

    buckets = {"major": ([], [], [], [], 0), "minor": ([], [], [], [], 0)}
    # Each tuple: (xs, ys, zs, tris, vert_offset).  Lists are mutable; we
    # track the offset separately so triangle indices stay correct as we
    # concatenate per-atom triangle fans into one mesh.
    buckets_dict = {"major": {"x": [], "y": [], "z": [], "tris": [], "off": 0},
                    "minor": {"x": [], "y": [], "z": [], "tris": [], "off": 0}}

    for atom in scene.get("draw_atoms", []):
        if show_minor_only and not atom.get("is_minor"):
            continue
        if not _atom_render_visible(atom):
            continue
        if _atom_element(atom) == "H" and not bool(style.get("show_hydrogen", False)):
            continue
        if _minor_axes_outline_only(atom, style):
            continue
        bucket_key = "minor" if (_atom_is_minor(atom) and fade_minor) else "major"
        b = buckets_dict[bucket_key]

        U, uiso = _atom_u(atom)
        ring, _, _ = ortep_billboard_polygon(
            atom["cart"], U, view_x, view_y,
            probability=probability, uiso=uiso, n_pts=32,
        )
        ring = np.asarray(ring, dtype=float) + cam[None, :]
        center = np.asarray(atom["cart"], dtype=float) + cam

        # Triangle fan: center vertex + ring vertices, one triangle per ring edge.
        n = len(ring)
        b["x"].append(float(center[0]))
        b["y"].append(float(center[1]))
        b["z"].append(float(center[2]))
        ring_start = b["off"] + 1
        for v in ring:
            b["x"].append(float(v[0]))
            b["y"].append(float(v[1]))
            b["z"].append(float(v[2]))
        for k in range(n):
            b["tris"].append([b["off"], ring_start + k, ring_start + (k + 1) % n])
        b["off"] += 1 + n

    traces = []
    for key, opacity in (("major", 1.0), ("minor", minor_opacity)):
        b = buckets_dict[key]
        if not b["tris"]:
            continue
        tris = np.asarray(b["tris"], dtype=int)
        traces.append(go.Mesh3d(
            x=b["x"], y=b["y"], z=b["z"],
            i=tris[:, 0].tolist(),
            j=tris[:, 1].tolist(),
            k=tris[:, 2].tolist(),
            color=fill_color,
            opacity=opacity,
            flatshading=True,
            lighting=dict(ambient=1.0, diffuse=0.0, specular=0.0,
                          fresnel=0.0, roughness=1.0),
            hoverinfo="skip",
            showlegend=False,
            name=f"ortep-atom-fill-{key}",
        ))
    return traces


def ortep_silhouette_outline_traces(scene: dict, style: dict):
    """Per-atom 2D silhouette outline (the projected ellipse boundary).

    Drawn as a single batched Scatter3d line trace.  Without this trace the
    classic "open white ellipsoid" ORTEP look is impossible: the white
    Mesh3d ellipsoid body would blend into a white background and the
    octant hatch would float without an outline anchoring it.

    The billboard polygon vertices are shifted slightly toward the camera
    in 3D space (along ``-view_dir``) so the outline sits in front of the
    Mesh3d body and is not z-fighted away by it.
    """
    if not bool(style.get("ortep_silhouette_outline", False)):
        return []
    probability = float(style.get("ortep_probability", 0.5))
    color = style.get("ortep_silhouette_color", "#1A1A1A")
    width = float(style.get("ortep_silhouette_linewidth", 1.4))
    show_minor_only = bool(style.get("show_minor_only", False))
    fade_minor = (
        style.get("disorder") == "opacity"
        or bool(style.get("force_minor_fade", False))
    )
    minor_opacity = float(style.get("minor_opacity", 0.35))
    view_x = np.asarray(scene.get("view_x", [1.0, 0.0, 0.0]), dtype=float)
    view_y = np.asarray(scene.get("view_y", [0.0, 1.0, 0.0]), dtype=float)

    # Push the silhouette toward the camera by ``ortep_z_lift_outline`` so
    # it sits ON TOP of the white atom-fill disk (which is itself lifted by
    # ``ortep_z_lift_fill`` to sit on top of the bond strokes).  The
    # cumulative z-stack from far→near is therefore:
    #     bonds  <  atom-fill  <  hatch lines  <  silhouette outline
    # which matches the publication ORTEP-III drawing order.
    z_lift = float(style.get("ortep_z_lift_outline", 0.07))
    view_dir = np.asarray(
        scene.get("view_direction", scene.get("view_z", [0.0, 0.0, 1.0])),
        dtype=float,
    )
    nrm = float(np.linalg.norm(view_dir))
    view_dir = view_dir / nrm if nrm > 1e-9 else np.array([0.0, 0.0, 1.0])
    camera_offset = view_dir * z_lift  # +view_dir = toward camera

    rings_major: list = []
    rings_minor: list = []
    for atom in scene.get("draw_atoms", []):
        if show_minor_only and not atom.get("is_minor"):
            continue
        if not _atom_render_visible(atom):
            continue
        if _atom_element(atom) == "H" and not bool(style.get("show_hydrogen", False)):
            continue
        if _minor_axes_outline_only(atom, style):
            continue
        U, uiso = _atom_u(atom)
        ring, _, _ = ortep_billboard_polygon(
            atom["cart"], U, view_x, view_y, probability=probability, uiso=uiso,
        )
        ring = np.asarray(ring, dtype=float) + camera_offset[None, :]
        if _atom_is_minor(atom) and fade_minor:
            rings_minor.append(ring)
        else:
            rings_major.append(ring)

    traces = []
    major_trace = _ortep_outline_trace(
        rings_major, color=color, width=width, name="ortep-silhouette",
    )
    if major_trace is not None:
        traces.append(major_trace)
    minor_trace = _ortep_outline_trace(
        rings_minor, color=color, width=width, name="ortep-silhouette-minor",
    )
    if minor_trace is not None:
        # Reduce opacity in-place since _ortep_outline_trace fixes it at 0.95
        minor_trace.opacity = max(0.05, minor_opacity)
        traces.append(minor_trace)
    return traces


def build_ortep_panel_figure(scene: dict, *, probability: float = 0.5, show_axes: bool = True, shade_octants: bool = False, **kwargs):
    from ..renderer import build_figure

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
