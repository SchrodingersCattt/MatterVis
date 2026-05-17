from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots


BOHR_TO_ANGSTROM = 0.529177210903

ELEMENT_SYMBOLS = {
    1: "H",
    6: "C",
    7: "N",
    8: "O",
    9: "F",
    15: "P",
    16: "S",
    17: "Cl",
    26: "Fe",
    27: "Co",
    28: "Ni",
    29: "Cu",
    30: "Zn",
    35: "Br",
    53: "I",
}

ELEMENT_COLORS = {
    "H": "#DDDDDD",
    "C": "#909090",
    "N": "#3050F8",
    "O": "#FF0D0D",
    "F": "#90E050",
    "P": "#FF8000",
    "S": "#FFD43B",
    "Cl": "#1FF01F",
    "Cu": "#C77800",
    "Fe": "#B7410E",
    "Ni": "#4C8C4A",
    "Co": "#3F5FBF",
    "Zn": "#7D80B8",
    "Br": "#A52A2A",
    "I":  "#940094",
}

COVALENT_RADII_ANG = {
    "H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57,
    "P": 1.07, "S": 1.05, "Cl": 1.02, "Cu": 1.32, "Fe": 1.24, "Ni": 1.21, "Co": 1.26, "Zn": 1.22, "Br": 1.20, "I": 1.39,
}

ATOM_DISPLAY_RADII_ANG = {
    "H": 0.30, "C": 0.55, "N": 0.55, "O": 0.55, "F": 0.50,
    "P": 0.75, "S": 0.75, "Cl": 0.70, "Cu": 0.85, "Fe": 0.82, "Ni": 0.82, "Co": 0.82, "Zn": 0.82, "Br": 0.85, "I": 0.95,
}


@dataclass
class CubeAtom:
    atomic_number: int
    charge: float
    coord: np.ndarray

    @property
    def element(self) -> str:
        return ELEMENT_SYMBOLS.get(self.atomic_number, str(self.atomic_number))


@dataclass
class CubeData:
    title: str
    comment: str
    atoms: list[CubeAtom]
    origin: np.ndarray
    axes: np.ndarray
    values: np.ndarray
    path: Path

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(int(x) for x in self.values.shape)

    @property
    def lattice(self) -> np.ndarray:
        """3x3 lattice matrix (rows = cell vectors in Angstrom)."""
        return self.axes * np.asarray(self.shape, dtype=float)[:, None]


def tile_cube(cube: CubeData, neg: tuple[int, int, int],
              pos: tuple[int, int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Tile cube data over a range of periodic images.

    ``neg[i]`` cells on the negative side and ``pos[i]`` cells on the positive
    side along axis ``i``. Returns ``(values, origin)`` where ``values`` has
    shape ``(N0*reps0, N1*reps1, N2*reps2)`` and ``origin`` is the cartesian
    origin of the tiled grid. Step vectors (``cube.axes``) are unchanged.
    """
    reps = (neg[0] + pos[0], neg[1] + pos[1], neg[2] + pos[2])
    if any(r <= 0 for r in reps):
        raise ValueError(f"reps must be positive, got {reps}")
    big_values = np.tile(cube.values, reps)
    shift = (neg[0] * cube.axes[0] * cube.shape[0]
             + neg[1] * cube.axes[1] * cube.shape[1]
             + neg[2] * cube.axes[2] * cube.shape[2])
    big_origin = cube.origin - shift
    return big_values, big_origin


def tile_cube_data(cube: CubeData, neg: tuple[int, int, int], pos: tuple[int, int, int]) -> CubeData:
    """Return a new :class:`CubeData` with values tiled over PBC images."""
    values, origin = tile_cube(cube, neg, pos)
    return CubeData(
        title=cube.title,
        comment=cube.comment,
        atoms=list(cube.atoms),
        origin=origin,
        axes=np.array(cube.axes, dtype=float),
        values=values,
        path=cube.path,
    )


def _as_angstrom(vec: Iterable[float]) -> np.ndarray:
    return np.asarray(list(vec), dtype=float) * BOHR_TO_ANGSTROM


def read_cube(path: str | Path) -> CubeData:
    """Read a Gaussian/CP2K cube file and return coordinates in Angstrom."""
    cube_path = Path(path)
    with cube_path.open("r", encoding="utf-8", errors="replace") as handle:
        title = handle.readline().rstrip()
        comment = handle.readline().rstrip()

        natom_line = handle.readline().split()
        natoms = abs(int(natom_line[0]))
        origin = _as_angstrom(float(x) for x in natom_line[1:4])

        shape: list[int] = []
        axes: list[np.ndarray] = []
        for _ in range(3):
            parts = handle.readline().split()
            shape.append(abs(int(parts[0])))
            axes.append(_as_angstrom(float(x) for x in parts[1:4]))

        atoms: list[CubeAtom] = []
        for _ in range(natoms):
            parts = handle.readline().split()
            atoms.append(
                CubeAtom(
                    atomic_number=int(parts[0]),
                    charge=float(parts[1]),
                    coord=_as_angstrom(float(x) for x in parts[2:5]),
                )
            )

        raw_values = np.fromiter((float(x) for line in handle for x in line.split()), dtype=float)

    expected = int(np.prod(shape))
    if raw_values.size != expected:
        raise ValueError(f"{cube_path} contains {raw_values.size} values, expected {expected}")

    return CubeData(
        title=title,
        comment=comment,
        atoms=atoms,
        origin=origin,
        axes=np.asarray(axes, dtype=float),
        values=raw_values.reshape(shape),
        path=cube_path,
    )


def cube_grid(cube: CubeData, stride: int = 1) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return flattened x/y/z/value arrays, optionally downsampled."""
    stride = max(1, int(stride))
    values = cube.values[::stride, ::stride, ::stride]
    ii, jj, kk = np.indices(values.shape, dtype=float)
    coords = (
        cube.origin[:, None, None, None]
        + ii[None, ...] * cube.axes[0, :, None, None, None] * stride
        + jj[None, ...] * cube.axes[1, :, None, None, None] * stride
        + kk[None, ...] * cube.axes[2, :, None, None, None] * stride
    )
    return coords[0].ravel(), coords[1].ravel(), coords[2].ravel(), values.ravel()


def default_isovalue(values: np.ndarray, percentile: float = 98.5) -> float:
    """Pick a robust orbital isovalue from the absolute-value distribution."""
    nonzero = np.abs(values[np.nonzero(values)])
    if nonzero.size == 0:
        raise ValueError("Cube values are all zero")
    return float(np.percentile(nonzero, percentile))


def orbital_isosurface_traces(
    cube: CubeData,
    *,
    isovalue: float | None = None,
    percentile: float = 98.5,
    stride: int = 2,
    positive_color: str = "#D55E00",
    negative_color: str = "#0072B2",
    opacity: float = 0.55,
) -> list[go.Isosurface]:
    """Create positive and negative orbital isosurface traces (Plotly volume)."""
    x, y, z, values = cube_grid(cube, stride=stride)
    iso = float(isovalue) if isovalue is not None else default_isovalue(values, percentile=percentile)
    vmax = float(np.max(values))
    vmin = float(np.min(values))

    traces: list[go.Isosurface] = []
    if vmax >= iso:
        traces.append(
            go.Isosurface(
                x=x,
                y=y,
                z=z,
                value=values,
                isomin=iso,
                isomax=vmax,
                surface_count=1,
                opacity=opacity,
                colorscale=[[0.0, positive_color], [1.0, positive_color]],
                caps=dict(x_show=False, y_show=False, z_show=False),
                showscale=False,
                name="+ orbital",
            )
        )
    if vmin <= -iso:
        traces.append(
            go.Isosurface(
                x=x,
                y=y,
                z=z,
                value=values,
                isomin=vmin,
                isomax=-iso,
                surface_count=1,
                opacity=opacity,
                colorscale=[[0.0, negative_color], [1.0, negative_color]],
                caps=dict(x_show=False, y_show=False, z_show=False),
                showscale=False,
                name="- orbital",
            )
        )
    return traces


def mask_to_atoms(
    cube: CubeData,
    *,
    radius: float = 4.5,
    extra_positions: np.ndarray | None = None,
) -> np.ndarray:
    """Boolean mask, True for voxels within ``radius`` Å of any atom.

    ``extra_positions`` lets callers add image positions (e.g. unwrapped
    fragment atoms that lie outside the cube atom list).
    """
    coords = np.asarray([atom.coord for atom in cube.atoms], dtype=float)
    if extra_positions is not None and len(extra_positions) > 0:
        coords = np.vstack([coords, np.asarray(extra_positions, dtype=float)])
    if coords.size == 0:
        return np.ones(cube.values.shape, dtype=bool)

    Nx, Ny, Nz = cube.values.shape
    ax = cube.axes
    origin = cube.origin
    # Voxel pitch (assuming orthogonal-ish axes; OK for monoclinic too).
    dx = float(np.linalg.norm(ax[0]))
    dy = float(np.linalg.norm(ax[1]))
    dz = float(np.linalg.norm(ax[2]))
    inv_axes = np.linalg.inv(ax.T)  # ax.T @ frac = cart - origin

    mask = np.zeros((Nx, Ny, Nz), dtype=bool)
    rsq = radius * radius
    pad = (int(np.ceil(radius / dx)) + 1,
           int(np.ceil(radius / dy)) + 1,
           int(np.ceil(radius / dz)) + 1)

    for p in coords:
        frac = inv_axes @ (p - origin)
        ic = int(round(frac[0]))
        jc = int(round(frac[1]))
        kc = int(round(frac[2]))
        i0, i1 = max(0, ic - pad[0]), min(Nx, ic + pad[0] + 1)
        j0, j1 = max(0, jc - pad[1]), min(Ny, jc + pad[1] + 1)
        k0, k1 = max(0, kc - pad[2]), min(Nz, kc + pad[2] + 1)
        if i0 >= i1 or j0 >= j1 or k0 >= k1:
            continue
        ii = np.arange(i0, i1)
        jj = np.arange(j0, j1)
        kk = np.arange(k0, k1)
        I, J, K = np.meshgrid(ii, jj, kk, indexing="ij")
        # Cartesian coordinates of the sub-block.
        X = origin[0] + I * ax[0, 0] + J * ax[1, 0] + K * ax[2, 0]
        Y = origin[1] + I * ax[0, 1] + J * ax[1, 1] + K * ax[2, 1]
        Z = origin[2] + I * ax[0, 2] + J * ax[1, 2] + K * ax[2, 2]
        dist2 = (X - p[0]) ** 2 + (Y - p[1]) ** 2 + (Z - p[2]) ** 2
        sub = dist2 <= rsq
        mask[i0:i1, j0:j1, k0:k1] |= sub
    return mask


def orbital_mesh_traces(
    cube: CubeData,
    *,
    isovalue: float | None = None,
    percentile: float = 98.5,
    stride: int = 1,
    positive_color: str = "#D55E00",
    negative_color: str = "#0072B2",
    opacity: float = 0.6,
    flatshading: bool = False,
    min_volume_voxels: int = 0,
    atom_mask_radius: float | None = None,
    extra_atom_positions: np.ndarray | None = None,
) -> list[go.Mesh3d]:
    """Build positive/negative orbital surfaces as ``go.Mesh3d`` traces.

    Uses ``skimage.measure.marching_cubes`` to extract the iso-mesh, which
    renders reliably both interactively and via kaleido static export
    (unlike ``go.Isosurface`` whose volume ray-marching can drop out under
    Kaleido). This is the recommended path for publication-quality figures.
    """
    try:
        from skimage.measure import marching_cubes
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "scikit-image is required for orbital_mesh_traces; "
            "install with `pip install scikit-image`"
        ) from exc

    stride = max(1, int(stride))
    values = cube.values[::stride, ::stride, ::stride]
    iso = float(isovalue) if isovalue is not None else default_isovalue(values, percentile=percentile)
    a0 = cube.axes[0] * stride
    a1 = cube.axes[1] * stride
    a2 = cube.axes[2] * stride

    pos_field = values
    neg_field = values

    if atom_mask_radius is not None and atom_mask_radius > 0:
        # Mask out voxels that are far from any atom: this removes spurious
        # PBC-image lobes that appear when the cube has been tiled.
        strided_cube = CubeData(
            title=cube.title,
            comment=cube.comment,
            atoms=cube.atoms,
            origin=cube.origin,
            axes=cube.axes * stride,
            values=values,
            path=cube.path,
        )
        keep_mask = mask_to_atoms(
            strided_cube,
            radius=atom_mask_radius,
            extra_positions=extra_atom_positions,
        )
        pos_field = np.where(keep_mask, pos_field, 0.0)
        neg_field = np.where(keep_mask, neg_field, 0.0)

    if min_volume_voxels > 0:
        from scipy.ndimage import label as ndi_label  # type: ignore

        struct = np.ones((3, 3, 3), dtype=bool)
        thr = int(min_volume_voxels)

        def _filter(mask: np.ndarray) -> np.ndarray:
            lbl, n = ndi_label(mask, structure=struct)
            if n == 0:
                return mask
            counts = np.bincount(lbl.ravel())
            keep = np.zeros(counts.size, dtype=bool)
            keep[1:] = counts[1:] >= thr
            return keep[lbl]

        pos_mask = _filter(pos_field > iso)
        neg_mask = _filter(neg_field < -iso)
        pos_field = np.where(pos_mask, pos_field, 0.0)
        neg_field = np.where(neg_mask, neg_field, 0.0)

    def _iso_mesh(field: np.ndarray, level: float, color: str, name: str) -> go.Mesh3d | None:
        try:
            verts, faces, _, _ = marching_cubes(field, level=level)
        except (ValueError, RuntimeError):
            return None
        if verts.size == 0:
            return None
        cart = (
            cube.origin[None, :]
            + verts[:, 0:1] * a0[None, :]
            + verts[:, 1:2] * a1[None, :]
            + verts[:, 2:3] * a2[None, :]
        )
        return go.Mesh3d(
            x=cart[:, 0],
            y=cart[:, 1],
            z=cart[:, 2],
            i=faces[:, 0],
            j=faces[:, 1],
            k=faces[:, 2],
            color=color,
            opacity=opacity,
            flatshading=flatshading,
            lighting=dict(ambient=0.85, diffuse=0.55, specular=0.2, roughness=0.55),
            lightposition=dict(x=200, y=200, z=200),
            name=name,
            hoverinfo="name",
            showlegend=False,
        )

    traces: list[go.Mesh3d] = []
    pos = _iso_mesh(pos_field, +iso, positive_color, "+ orbital")
    neg = _iso_mesh(neg_field, -iso, negative_color, "- orbital")
    if pos is not None:
        traces.append(pos)
    if neg is not None:
        traces.append(neg)
    return traces


def cube_atom_trace(cube: CubeData, *, atom_scale: float = 5.0) -> go.Scatter3d:
    """Create a light atom overlay using Plotly's 2D-projected markers.

    Faster but less convincingly 3D than :func:`atom_sphere_traces`; kept
    for backwards compatibility and small/structureless previews.
    """
    labels = [f"{atom.element}{idx + 1}" for idx, atom in enumerate(cube.atoms)]
    colors = [ELEMENT_COLORS.get(atom.element, "#999999") for atom in cube.atoms]
    coords = np.asarray([atom.coord for atom in cube.atoms], dtype=float)
    return go.Scatter3d(
        x=coords[:, 0],
        y=coords[:, 1],
        z=coords[:, 2],
        mode="markers",
        text=labels,
        hovertemplate="%{text}<extra></extra>",
        marker=dict(size=atom_scale, color=colors, opacity=0.9, line=dict(color="#333333", width=0.5)),
        showlegend=False,
        name="atoms",
    )


def _unit_sphere(n_lat: int = 12, n_lon: int = 18) -> tuple[np.ndarray, np.ndarray]:
    """Return (vertices, faces) of a unit sphere triangulation."""
    phi = np.linspace(0.0, np.pi, n_lat)
    theta = np.linspace(0.0, 2.0 * np.pi, n_lon, endpoint=False)
    pp, tt = np.meshgrid(phi, theta, indexing="ij")
    x = np.sin(pp) * np.cos(tt)
    y = np.sin(pp) * np.sin(tt)
    z = np.cos(pp)
    verts = np.stack([x.ravel(), y.ravel(), z.ravel()], axis=1)

    faces: list[tuple[int, int, int]] = []
    for i in range(n_lat - 1):
        for j in range(n_lon):
            a = i * n_lon + j
            b = i * n_lon + (j + 1) % n_lon
            c = (i + 1) * n_lon + j
            d = (i + 1) * n_lon + (j + 1) % n_lon
            faces.append((a, c, b))
            faces.append((b, c, d))
    return verts, np.asarray(faces, dtype=int)


def atom_sphere_traces(
    cube: CubeData,
    *,
    radius_scale: float = 0.55,
    radii: dict[str, float] | None = None,
    n_lat: int = 12,
    n_lon: int = 18,
    flatshading: bool = False,
) -> list[go.Mesh3d]:
    """Create per-element 3D sphere meshes for all atoms in the cube.

    Renders convincingly in static (kaleido) export, unlike Scatter3d markers
    which always look flat.
    """
    if not cube.atoms:
        return []
    sphere_v, sphere_f = _unit_sphere(n_lat=n_lat, n_lon=n_lon)
    rmap = {**ATOM_DISPLAY_RADII_ANG, **(radii or {})}

    by_elem: dict[str, list[CubeAtom]] = {}
    for atom in cube.atoms:
        by_elem.setdefault(atom.element, []).append(atom)

    traces: list[go.Mesh3d] = []
    for elem, atoms in by_elem.items():
        r = rmap.get(elem, 0.55) * radius_scale
        color = ELEMENT_COLORS.get(elem, "#999999")
        all_v: list[np.ndarray] = []
        all_f: list[np.ndarray] = []
        nv = sphere_v.shape[0]
        for k, atom in enumerate(atoms):
            all_v.append(sphere_v * r + atom.coord[None, :])
            all_f.append(sphere_f + k * nv)
        V = np.concatenate(all_v, axis=0)
        F = np.concatenate(all_f, axis=0)
        traces.append(
            go.Mesh3d(
                x=V[:, 0], y=V[:, 1], z=V[:, 2],
                i=F[:, 0], j=F[:, 1], k=F[:, 2],
                color=color,
                opacity=1.0,
                flatshading=flatshading,
                lighting=dict(ambient=0.75, diffuse=0.7, specular=0.25, roughness=0.45, fresnel=0.1),
                lightposition=dict(x=200, y=200, z=200),
                name=elem,
                hoverinfo="name",
                showlegend=False,
            )
        )
    return traces


def _cylinder(p0: np.ndarray, p1: np.ndarray, radius: float, n_seg: int = 8
              ) -> tuple[np.ndarray, np.ndarray]:
    axis = p1 - p0
    L = float(np.linalg.norm(axis))
    if L < 1e-9:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=int)
    z = axis / L
    a = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = np.cross(z, a); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    angles = np.linspace(0.0, 2.0 * np.pi, n_seg, endpoint=False)
    ring = radius * (np.cos(angles)[:, None] * x[None, :] + np.sin(angles)[:, None] * y[None, :])
    bottom = ring + p0[None, :]
    top = ring + p1[None, :]
    verts = np.concatenate([bottom, top], axis=0)
    faces: list[tuple[int, int, int]] = []
    for i in range(n_seg):
        a_ = i
        b_ = (i + 1) % n_seg
        c_ = i + n_seg
        d_ = (i + 1) % n_seg + n_seg
        faces.append((a_, c_, b_))
        faces.append((b_, c_, d_))
    return verts, np.asarray(faces, dtype=int)


def bond_traces(
    cube: CubeData,
    *,
    tolerance: float = 1.15,
    radius: float = 0.10,
    color: str = "#888888",
    n_seg: int = 8,
    skip_pairs: tuple[tuple[str, str], ...] = (("H", "H"),),
) -> list[go.Mesh3d]:
    """Build cylindrical bonds based on covalent-radii distance criterion.

    Bonds are drawn as half-cylinders colored by their endpoint elements,
    giving a clean ball-and-stick rendering that is robust under static
    export. Pairs in ``skip_pairs`` (e.g. H–H) are never bonded.
    """
    atoms = cube.atoms
    if len(atoms) < 2:
        return []
    coords = np.asarray([a.coord for a in atoms], dtype=float)
    elems = [a.element for a in atoms]
    skip = {tuple(sorted(p)) for p in skip_pairs}

    by_color: dict[str, tuple[list[np.ndarray], list[np.ndarray]]] = {}
    nv_total: dict[str, int] = {}

    for i in range(len(atoms)):
        ri = COVALENT_RADII_ANG.get(elems[i], 0.75)
        for j in range(i + 1, len(atoms)):
            rj = COVALENT_RADII_ANG.get(elems[j], 0.75)
            if tuple(sorted((elems[i], elems[j]))) in skip:
                continue
            cutoff = (ri + rj) * tolerance
            d = float(np.linalg.norm(coords[i] - coords[j]))
            if d > cutoff or d < 0.4:
                continue
            mid = 0.5 * (coords[i] + coords[j])
            for p0, p1, elem in ((coords[i], mid, elems[i]), (mid, coords[j], elems[j])):
                col = ELEMENT_COLORS.get(elem, color)
                v, f = _cylinder(p0, p1, radius=radius, n_seg=n_seg)
                if v.shape[0] == 0:
                    continue
                lst = by_color.setdefault(col, ([], []))
                offset = nv_total.get(col, 0)
                lst[0].append(v)
                lst[1].append(f + offset)
                nv_total[col] = offset + v.shape[0]

    traces: list[go.Mesh3d] = []
    for col, (vlist, flist) in by_color.items():
        if not vlist:
            continue
        V = np.concatenate(vlist, axis=0)
        F = np.concatenate(flist, axis=0)
        traces.append(
            go.Mesh3d(
                x=V[:, 0], y=V[:, 1], z=V[:, 2],
                i=F[:, 0], j=F[:, 1], k=F[:, 2],
                color=col,
                opacity=1.0,
                flatshading=True,
                lighting=dict(ambient=0.8, diffuse=0.6, specular=0.15, roughness=0.7),
                hoverinfo="skip",
                showlegend=False,
                name="bonds",
            )
        )
    return traces


def build_orbital_figure(
    cube: CubeData,
    *,
    isovalue: float | None = None,
    percentile: float = 98.5,
    stride: int = 2,
    show_atoms: bool = True,
    title: str | None = None,
) -> go.Figure:
    """Build a standalone Plotly figure for a cube orbital."""
    fig = go.Figure()
    for trace in orbital_isosurface_traces(cube, isovalue=isovalue, percentile=percentile, stride=stride):
        fig.add_trace(trace)
    if show_atoms and cube.atoms:
        fig.add_trace(cube_atom_trace(cube))

    coords = np.asarray([atom.coord for atom in cube.atoms], dtype=float) if cube.atoms else np.zeros((0, 3))
    if coords.size:
        mins = coords.min(axis=0)
        maxs = coords.max(axis=0)
    else:
        x, y, z, _ = cube_grid(cube, stride=max(stride, 4))
        mins = np.array([x.min(), y.min(), z.min()])
        maxs = np.array([x.max(), y.max(), z.max()])
    center = 0.5 * (mins + maxs)
    half = 0.5 * max(maxs - mins) + 1.5
    ranges = [[float(c - half), float(c + half)] for c in center]

    fig.update_layout(
        title=dict(text=title or cube.path.name, x=0.5),
        margin=dict(l=0, r=0, t=40, b=0),
        paper_bgcolor="white",
        scene=dict(
            xaxis=dict(visible=False, range=ranges[0]),
            yaxis=dict(visible=False, range=ranges[1]),
            zaxis=dict(visible=False, range=ranges[2]),
            aspectmode="cube",
            bgcolor="white",
        ),
    )
    return fig


def _scene_ranges(cube: CubeData, padding: float = 0.6, stride: int = 4) -> list[list[float]]:
    coords = np.asarray([atom.coord for atom in cube.atoms], dtype=float) if cube.atoms else np.zeros((0, 3))
    if coords.size:
        mins = coords.min(axis=0)
        maxs = coords.max(axis=0)
    else:
        x, y, z, _ = cube_grid(cube, stride=stride)
        mins = np.array([x.min(), y.min(), z.min()])
        maxs = np.array([x.max(), y.max(), z.max()])
    center = 0.5 * (mins + maxs)
    half = 0.5 * float(np.max(maxs - mins)) + padding
    return [[float(c - half), float(c + half)] for c in center]


DEFAULT_TRACE_ORDER: tuple[str, ...] = ("cell", "orbital", "bonds", "atoms")
"""Default mesh insertion order in :func:`build_orbital_panel_figure`.

Plotly/WebGL composites transparent meshes in **insertion order** within a
scene: the trace added LAST is drawn on top. Half-transparent orbital
isosurfaces therefore overlay any opaque atom/bond meshes inserted after
them, which makes structures with dense orbitals appear visually
inconsistent with sparser ones (the molecular skeleton looks "thinner" or
"washed out" wherever the orbital is delocalised).

Inserting orbitals BEFORE bonds and atoms guarantees the molecular
skeleton stays legible regardless of orbital density. ``cell`` (the unit
cell wireframe) goes first so it sits behind everything; if absent it is
silently skipped.

Override ``trace_order`` in :func:`build_orbital_panel_figure` only when
deliberately wanting the inverse stacking (e.g. emphasising orbital phase
over the framework).
"""


def build_orbital_panel_figure(
    cubes: Sequence[CubeData],
    *,
    titles: Sequence[str] | None = None,
    isovalues: Sequence[float] | None = None,
    percentile: float = 98.5,
    stride: int = 2,
    show_atoms: bool = True,
    show_bonds: bool = True,
    show_cell_box: bool = False,
    cell_box_color: str = "#444444",
    cell_box_width: float = 3.0,
    positive_color: str = "#D55E00",
    negative_color: str = "#0072B2",
    opacity: float = 0.55,
    atom_radius_scale: float = 0.55,
    bond_radius: float = 0.10,
    bond_tolerance: float = 1.15,
    atom_marker_scale: float | None = None,
    camera: dict | None = None,
    horizontal_spacing: float = 0.02,
    use_mesh: bool = True,
    use_atom_spheres: bool = True,
    min_volume_voxels: int = 0,
    atom_mask_radius: float | None = None,
    extra_atom_positions: np.ndarray | None = None,
    title_fontsize: int = 14,
    scene_y_top: float = 0.92,
    trace_order: Sequence[str] = DEFAULT_TRACE_ORDER,
) -> go.Figure:
    """Build a single Plotly figure with one 3D scene per cube, side-by-side.

    Useful for publication-quality side-by-side rendering of HOCO/LUCO,
    spin-up/spin-down, or any pair/triplet of orbitals.
    """
    n = len(cubes)
    if n == 0:
        raise ValueError("At least one cube is required")
    titles = list(titles) if titles is not None else [c.path.name for c in cubes]
    isovalues = list(isovalues) if isovalues is not None else [None] * n
    if len(titles) != n or len(isovalues) != n:
        raise ValueError("titles and isovalues must have the same length as cubes")

    specs = [[{"type": "scene"}] * n]
    fig = make_subplots(
        rows=1,
        cols=n,
        specs=specs,
        subplot_titles=list(titles),
        horizontal_spacing=horizontal_spacing,
    )
    default_camera = dict(eye=dict(x=1.0, y=1.0, z=0.7), up=dict(x=0, y=0, z=1))
    cam = {**default_camera, **(camera or {})}

    valid_kinds = {"cell", "orbital", "bonds", "atoms"}
    order = tuple(trace_order)
    unknown = set(order) - valid_kinds
    if unknown:
        raise ValueError(
            f"trace_order contains unknown kinds {sorted(unknown)}; "
            f"valid kinds are {sorted(valid_kinds)}"
        )

    scene_ids: list[str] = []
    for col, (cube, iso) in enumerate(zip(cubes, isovalues), start=1):
        # Pre-build each kind so trace_order can route insertion freely.
        kind_traces: dict[str, list] = {k: [] for k in valid_kinds}

        if show_cell_box and cube.axes is not None:
            kind_traces["cell"].append(
                cell_box_trace(cube.lattice, origin=cube.origin,
                               color=cell_box_color, width=cell_box_width)
            )

        if use_mesh:
            kind_traces["orbital"] = orbital_mesh_traces(
                cube,
                isovalue=iso,
                percentile=percentile,
                stride=stride,
                positive_color=positive_color,
                negative_color=negative_color,
                opacity=opacity,
                min_volume_voxels=min_volume_voxels,
                atom_mask_radius=atom_mask_radius,
                extra_atom_positions=extra_atom_positions,
            )
        else:
            kind_traces["orbital"] = orbital_isosurface_traces(
                cube,
                isovalue=iso,
                percentile=percentile,
                stride=stride,
                positive_color=positive_color,
                negative_color=negative_color,
                opacity=opacity,
            )

        if show_bonds and cube.atoms:
            kind_traces["bonds"] = list(
                bond_traces(cube, tolerance=bond_tolerance, radius=bond_radius)
            )
        if show_atoms and cube.atoms:
            if use_atom_spheres:
                kind_traces["atoms"] = list(
                    atom_sphere_traces(cube, radius_scale=atom_radius_scale)
                )
            else:
                size = atom_marker_scale if atom_marker_scale is not None else 5.0
                kind_traces["atoms"] = [cube_atom_trace(cube, atom_scale=size)]

        for kind in order:
            for trace in kind_traces[kind]:
                fig.add_trace(trace, row=1, col=col)

        scene_id = "scene" if col == 1 else f"scene{col}"
        scene_ids.append(scene_id)
        ranges = _scene_ranges(cube)
        fig.update_layout(
            **{
                scene_id: dict(
                    xaxis=dict(visible=False, range=ranges[0]),
                    yaxis=dict(visible=False, range=ranges[1]),
                    zaxis=dict(visible=False, range=ranges[2]),
                    aspectmode="cube",
                    bgcolor="white",
                    camera=cam,
                )
            }
        )

    domains: list[tuple[float, float]] = []
    available = 1.0 - horizontal_spacing * (n - 1) if n > 1 else 1.0
    width_each = available / n
    for col in range(n):
        x0 = col * (width_each + horizontal_spacing)
        domains.append((x0, x0 + width_each))
    for col, (x0, x1) in enumerate(domains, start=1):
        scene_id = "scene" if col == 1 else f"scene{col}"
        fig.layout[scene_id].domain = dict(x=[x0, x1], y=[0.0, scene_y_top])
        if col - 1 < len(fig.layout.annotations):
            fig.layout.annotations[col - 1].update(
                x=0.5 * (x0 + x1),
                y=1.0,
                xref="paper", yref="paper",
                xanchor="center", yanchor="bottom",
            )

    fig.update_layout(
        paper_bgcolor="white",
        margin=dict(l=0, r=0, t=int(title_fontsize * 1.6), b=0),
        showlegend=False,
        font=dict(family="Arial, Helvetica, sans-serif", size=title_fontsize),
    )
    for ann in fig.layout.annotations:
        ann.font = dict(family="Arial, Helvetica, sans-serif",
                        size=title_fontsize, color="#000000")
    return fig


def export_static(
    fig: go.Figure,
    path: str | Path,
    *,
    width: int = 1600,
    height: int = 800,
    scale: float = 2.0,
) -> Path:
    """Export a Plotly figure to PNG or PDF using kaleido.

    PDF will rasterize 3D scenes (Plotly limitation); PNG remains crisp.
    """
    out = Path(path)
    fig.write_image(str(out), width=width, height=height, scale=scale)
    return out


def cell_box_trace(
    lattice: np.ndarray,
    origin: np.ndarray | None = None,
    *,
    color: str = "#444444",
    width: float = 3.0,
) -> go.Scatter3d:
    """Return a single Scatter3d trace tracing the 12 edges of a parallelepiped."""
    if origin is None:
        origin = np.zeros(3)
    a, b, c = lattice[0], lattice[1], lattice[2]
    o = np.asarray(origin, dtype=float)
    corners = np.array([o, o + a, o + a + b, o + b,
                        o + c, o + a + c, o + a + b + c, o + b + c])
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for i, j in edges:
        xs.extend([corners[i, 0], corners[j, 0], None])
        ys.extend([corners[i, 1], corners[j, 1], None])
        zs.extend([corners[i, 2], corners[j, 2], None])
    return go.Scatter3d(
        x=xs, y=ys, z=zs, mode="lines",
        line=dict(color=color, width=width),
        hoverinfo="skip", showlegend=False, name="cell",
    )


def sign_legend_annotations(
    *,
    positive_color: str = "#D55E00",
    negative_color: str = "#0072B2",
    x: float = 0.99,
    y: float = 0.99,
    fontsize: int = 14,
    spacing: float = 0.035,
) -> list[dict]:
    """Two-line +/- sign legend for orbital phase (paper-coord annotations)."""
    return [
        dict(
            xref="paper", yref="paper", x=x, y=y, xanchor="right", yanchor="top",
            text=f"<b><span style='color:{positive_color}'>\u25A0</span> +</b>",
            showarrow=False,
            font=dict(family="Arial, Helvetica, sans-serif", size=fontsize, color="#000"),
        ),
        dict(
            xref="paper", yref="paper", x=x, y=y - spacing,
            xanchor="right", yanchor="top",
            text=f"<b><span style='color:{negative_color}'>\u25A0</span> \u2212</b>",
            showarrow=False,
            font=dict(family="Arial, Helvetica, sans-serif", size=fontsize, color="#000"),
        ),
    ]


def axis_indicator_traces(
    lattice: np.ndarray,
    origin: np.ndarray | None = None,
    *,
    length: float = 2.5,
    width: float = 6.0,
    label_offset: float = 0.6,
    colors: tuple[str, str, str] = ("#D62728", "#2CA02C", "#1F77B4"),
    labels: tuple[str, str, str] = ("a", "b", "c"),
) -> list[go.Scatter3d]:
    """Three short ``a/b/c`` arrows + labels anchored at ``origin``.

    Lengths are normalised so the indicator is the same physical size in
    Angstrom along each axis, regardless of the lattice anisotropy.
    """
    if origin is None:
        origin = np.zeros(3)
    o = np.asarray(origin, dtype=float)
    traces: list[go.Scatter3d] = []
    for vec, color, label in zip(lattice, colors, labels):
        n = float(np.linalg.norm(vec))
        if n < 1e-9:
            continue
        end = o + (vec / n) * length
        traces.append(
            go.Scatter3d(
                x=[o[0], end[0]], y=[o[1], end[1]], z=[o[2], end[2]],
                mode="lines",
                line=dict(color=color, width=width),
                hoverinfo="skip", showlegend=False, name=f"axis-{label}",
            )
        )
        tip = o + (vec / n) * (length + label_offset)
        traces.append(
            go.Scatter3d(
                x=[tip[0]], y=[tip[1]], z=[tip[2]],
                mode="text",
                text=[f"<b>{label}</b>"],
                textfont=dict(color=color, size=18,
                              family="Arial, Helvetica, sans-serif"),
                hoverinfo="skip", showlegend=False, name=f"label-{label}",
            )
        )
    return traces
