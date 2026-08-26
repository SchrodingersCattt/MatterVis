"""Array-based CPU sphere rasterization for large atomistic frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ...config import atom_radius as configured_atom_radius
from ...loader.frame_batch import FrameBatch, frame_box_corners
from ..camera import CameraTransform
from ..contracts import CameraSpec
from ..planning import _ELEMENT_COLORS

try:
    from numba import njit
except ImportError:  # pragma: no cover - exercised by the legacy fallback path
    njit = None


NUMBA_AVAILABLE = njit is not None
_LIGHT = np.asarray([-0.32, 0.42, 1.0], dtype=np.float32)
_LIGHT /= np.linalg.norm(_LIGHT)


@dataclass(frozen=True, slots=True)
class BatchRenderResult:
    """One rendered frame and numeric buffers used by equivalence tests."""

    rgba: np.ndarray
    depth: np.ndarray
    camera_positions: np.ndarray


@dataclass(frozen=True, slots=True)
class SphereBatch:
    """Contiguous projected centers and style lookup tables for spheres."""

    camera_positions: np.ndarray
    atomic_numbers: np.ndarray
    colors: np.ndarray
    radii: np.ndarray

    def __post_init__(self) -> None:
        positions = np.ascontiguousarray(self.camera_positions, dtype=np.float32)
        numbers = np.ascontiguousarray(self.atomic_numbers, dtype=np.uint8)
        colors = np.ascontiguousarray(self.colors, dtype=np.uint8)
        radii = np.ascontiguousarray(self.radii, dtype=np.float32)
        if positions.ndim != 2 or positions.shape[1:] != (3,):
            raise ValueError("camera_positions must have shape (N, 3)")
        if numbers.shape != (len(positions),):
            raise ValueError("atomic_numbers must have shape (N,)")
        if colors.ndim != 2 or colors.shape[1:] != (3,):
            raise ValueError("colors must have shape (E, 3)")
        if radii.shape != (len(colors),):
            raise ValueError("radii must have shape (E,)")
        object.__setattr__(self, "camera_positions", positions)
        object.__setattr__(self, "atomic_numbers", numbers)
        object.__setattr__(self, "colors", colors)
        object.__setattr__(self, "radii", radii)


def _compile(*args: Any, **kwargs: Any):
    if njit is None:

        def decorator(function):
            return function

        return decorator
    return njit(*args, **kwargs)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    text = str(value).strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"expected #RRGGBB colour, got {value!r}")
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))


def element_style_tables(atom_scale: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Build atomic-number indexed colour and radius arrays."""
    if not np.isfinite(atom_scale) or atom_scale <= 0.0:
        raise ValueError("atom_scale must be finite and positive")
    from ase.data import chemical_symbols

    colors = np.full((len(chemical_symbols), 3), 128, dtype=np.uint8)
    radii = np.full(len(chemical_symbols), 0.5, dtype=np.float32)
    for atomic_number, symbol in enumerate(chemical_symbols):
        if atomic_number == 0 or not symbol:
            continue
        colors[atomic_number] = _hex_rgb(_ELEMENT_COLORS.get(symbol, "#808080"))
        radii[atomic_number] = max(configured_atom_radius(symbol), 0.2) * atom_scale
    return np.ascontiguousarray(colors), np.ascontiguousarray(radii)


def build_sphere_batch(
    frame: FrameBatch,
    camera_positions: np.ndarray,
    *,
    atom_scale: float = 1.0,
) -> SphereBatch:
    """Build the renderer-facing sphere arrays without per-atom objects."""
    colors, radii = element_style_tables(atom_scale)
    return SphereBatch(
        camera_positions=camera_positions,
        atomic_numbers=frame.atomic_numbers,
        colors=colors,
        radii=radii,
    )


def project_frame(frame: FrameBatch, camera: CameraSpec) -> np.ndarray:
    """Transform a frame into one fixed camera without object allocation."""
    view = CameraTransform(camera, 1, 1).view_matrix.astype(np.float32)
    positions = frame.positions
    projected = np.empty_like(positions, dtype=np.float32)
    projected[:, 0] = (
        positions[:, 0] * view[0, 0]
        + positions[:, 1] * view[0, 1]
        + positions[:, 2] * view[0, 2]
        + view[0, 3]
    )
    projected[:, 1] = (
        positions[:, 0] * view[1, 0]
        + positions[:, 1] * view[1, 1]
        + positions[:, 2] * view[1, 2]
        + view[1, 3]
    )
    projected[:, 2] = (
        positions[:, 0] * view[2, 0]
        + positions[:, 1] * view[2, 1]
        + positions[:, 2] * view[2, 2]
        + view[2, 3]
    )
    return projected


@_compile(cache=True, nogil=True)
def _fill_background(
    height: int,
    width: int,
    background: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    depth = np.empty((height, width), dtype=np.float32)
    for y in range(height):
        for x in range(width):
            rgba[y, x, 0] = background[0]
            rgba[y, x, 1] = background[1]
            rgba[y, x, 2] = background[2]
            rgba[y, x, 3] = background[3]
            depth[y, x] = np.inf
    return rgba, depth


@_compile(cache=True, nogil=True)
def _project_point(
    point: np.ndarray,
    width: int,
    height: int,
    projection: int,
    ortho_scale: float,
    fov_y_deg: float,
) -> tuple[float, float, float, bool]:
    camera_depth = -point[2]
    if projection == 0:
        aspect = width / height
        x = (point[0] / (ortho_scale * aspect) + 1.0) * 0.5 * width
        y = (1.0 - point[1] / ortho_scale) * 0.5 * height
        return x, y, camera_depth, True
    if camera_depth <= 1.0e-12:
        return 0.0, 0.0, camera_depth, False
    focal = 1.0 / np.tan(np.deg2rad(fov_y_deg) * 0.5)
    ndc_x = point[0] * focal / (camera_depth * (width / height))
    ndc_y = point[1] * focal / camera_depth
    x = (ndc_x + 1.0) * 0.5 * width
    y = (1.0 - ndc_y) * 0.5 * height
    return x, y, camera_depth, True


@_compile(cache=True, nogil=True)
def _draw_cell_segments(
    rgba: np.ndarray,
    depth: np.ndarray,
    camera_corners: np.ndarray,
    projection: int,
    ortho_scale: float,
    fov_y_deg: float,
    near: float,
    far: float,
    color: np.ndarray,
    width_px: float,
) -> None:
    edges = (
        (0, 1),
        (0, 2),
        (0, 3),
        (1, 4),
        (1, 5),
        (2, 4),
        (2, 6),
        (3, 5),
        (3, 6),
        (4, 7),
        (5, 7),
        (6, 7),
    )
    height, width = depth.shape
    half_width = max(0, int(np.ceil(width_px * 0.5)))
    for edge_index in range(12):
        first_index, second_index = edges[edge_index]
        first = camera_corners[first_index]
        second = camera_corners[second_index]
        x0, y0, d0, valid0 = _project_point(
            first, width, height, projection, ortho_scale, fov_y_deg
        )
        x1, y1, d1, valid1 = _project_point(
            second, width, height, projection, ortho_scale, fov_y_deg
        )
        if not valid0 or not valid1:
            continue
        steps = max(1, int(np.ceil(max(abs(x1 - x0), abs(y1 - y0)) * 2.0)))
        for step in range(steps + 1):
            fraction = step / steps
            pixel_x = int(np.floor(x0 + (x1 - x0) * fraction))
            pixel_y = int(np.floor(y0 + (y1 - y0) * fraction))
            pixel_depth = d0 + (d1 - d0) * fraction
            if pixel_depth < near or pixel_depth > far:
                continue
            for offset_y in range(-half_width, half_width + 1):
                y = pixel_y + offset_y
                if y < 0 or y >= height:
                    continue
                for offset_x in range(-half_width, half_width + 1):
                    x = pixel_x + offset_x
                    if x < 0 or x >= width:
                        continue
                    if pixel_depth < depth[y, x]:
                        depth[y, x] = pixel_depth
                        rgba[y, x, 0] = color[0]
                        rgba[y, x, 1] = color[1]
                        rgba[y, x, 2] = color[2]
                        rgba[y, x, 3] = 255


@_compile(cache=True, nogil=True)
def _rasterize_spheres(
    camera_positions: np.ndarray,
    atomic_numbers: np.ndarray,
    colors: np.ndarray,
    radii: np.ndarray,
    rgba: np.ndarray,
    depth_buffer: np.ndarray,
    projection: int,
    ortho_scale: float,
    fov_y_deg: float,
    near: float,
    far: float,
    show_hydrogen: bool,
) -> None:
    height, width = depth_buffer.shape
    aspect = width / height
    light_x, light_y, light_z = _LIGHT[0], _LIGHT[1], _LIGHT[2]
    world_per_pixel = 2.0 * ortho_scale / height
    focal = 1.0 / np.tan(np.deg2rad(fov_y_deg) * 0.5)
    for atom_index in range(camera_positions.shape[0]):
        atomic_number = int(atomic_numbers[atom_index])
        if atomic_number <= 0 or atomic_number >= radii.shape[0]:
            continue
        if not show_hydrogen and atomic_number == 1:
            continue
        center_x = camera_positions[atom_index, 0]
        center_y = camera_positions[atom_index, 1]
        center_z = camera_positions[atom_index, 2]
        center_depth = -center_z
        radius = radii[atomic_number]
        if center_depth + radius < near or center_depth - radius > far:
            continue
        if projection == 0:
            screen_x = (center_x / (ortho_scale * aspect) + 1.0) * 0.5 * width
            screen_y = (1.0 - center_y / ortho_scale) * 0.5 * height
            radius_px = radius / world_per_pixel
        else:
            if center_depth <= radius + 1.0e-12:
                continue
            screen_x = (center_x * focal / (center_depth * aspect) + 1.0) * 0.5 * width
            screen_y = (1.0 - center_y * focal / center_depth) * 0.5 * height
            radius_px = (
                0.5
                * height
                * focal
                * radius
                / np.sqrt(center_depth * center_depth - radius * radius)
            )
        minimum_x = max(0, int(np.floor(screen_x - radius_px - 1.0)))
        maximum_x = min(width - 1, int(np.ceil(screen_x + radius_px + 1.0)))
        minimum_y = max(0, int(np.floor(screen_y - radius_px - 1.0)))
        maximum_y = min(height - 1, int(np.ceil(screen_y + radius_px + 1.0)))
        if minimum_x > maximum_x or minimum_y > maximum_y:
            continue
        for pixel_y in range(minimum_y, maximum_y + 1):
            for pixel_x in range(minimum_x, maximum_x + 1):
                if projection == 0:
                    delta_x = (pixel_x + 0.5 - screen_x) * world_per_pixel
                    delta_y = -(pixel_y + 0.5 - screen_y) * world_per_pixel
                    radial_squared = delta_x * delta_x + delta_y * delta_y
                    if radial_squared > radius * radius:
                        continue
                    front = np.sqrt(max(radius * radius - radial_squared, 0.0))
                    pixel_depth = center_depth - front
                    normal_x = delta_x / radius
                    normal_y = delta_y / radius
                    normal_z = front / radius
                else:
                    ndc_x = 2.0 * (pixel_x + 0.5) / width - 1.0
                    ndc_y = 1.0 - 2.0 * (pixel_y + 0.5) / height
                    ray_x = ndc_x * aspect / focal
                    ray_y = ndc_y / focal
                    ray_z = -1.0
                    norm = np.sqrt(ray_x * ray_x + ray_y * ray_y + 1.0)
                    ray_x /= norm
                    ray_y /= norm
                    ray_z /= norm
                    along = ray_x * center_x + ray_y * center_y + ray_z * center_z
                    discriminant = along * along - (
                        center_x * center_x
                        + center_y * center_y
                        + center_z * center_z
                        - radius * radius
                    )
                    if discriminant < 0.0:
                        continue
                    distance = along - np.sqrt(max(discriminant, 0.0))
                    hit_x = ray_x * distance
                    hit_y = ray_y * distance
                    hit_z = ray_z * distance
                    pixel_depth = -hit_z
                    normal_x = (hit_x - center_x) / radius
                    normal_y = (hit_y - center_y) / radius
                    normal_z = (hit_z - center_z) / radius
                if pixel_depth < near or pixel_depth > far:
                    continue
                if pixel_depth >= depth_buffer[pixel_y, pixel_x] - 1.0e-9:
                    continue
                illumination = 0.68 + 0.32 * abs(
                    normal_x * light_x + normal_y * light_y + normal_z * light_z
                )
                depth_buffer[pixel_y, pixel_x] = pixel_depth
                rgba[pixel_y, pixel_x, 0] = min(
                    255, int(colors[atomic_number, 0] * illumination + 0.5)
                )
                rgba[pixel_y, pixel_x, 1] = min(
                    255, int(colors[atomic_number, 1] * illumination + 0.5)
                )
                rgba[pixel_y, pixel_x, 2] = min(
                    255, int(colors[atomic_number, 2] * illumination + 0.5)
                )
                rgba[pixel_y, pixel_x, 3] = 255


@_compile(cache=True, nogil=True)
def _rasterize_bonds(
    camera_positions: np.ndarray,
    atomic_numbers: np.ndarray,
    pairs: np.ndarray,
    colors: np.ndarray,
    rgba: np.ndarray,
    depth_buffer: np.ndarray,
    projection: int,
    ortho_scale: float,
    fov_y_deg: float,
    near: float,
    far: float,
    bond_radius: float,
    show_hydrogen: bool,
) -> None:
    height, width = depth_buffer.shape
    aspect = width / height
    focal = 1.0 / np.tan(np.deg2rad(fov_y_deg) * 0.5)
    for bond_index in range(pairs.shape[0]):
        first_index = pairs[bond_index, 0]
        second_index = pairs[bond_index, 1]
        first_number = int(atomic_numbers[first_index])
        second_number = int(atomic_numbers[second_index])
        if not show_hydrogen and (first_number == 1 or second_number == 1):
            continue
        first = camera_positions[first_index]
        second = camera_positions[second_index]
        depth0 = -first[2]
        depth1 = -second[2]
        if depth0 < near or depth0 > far or depth1 < near or depth1 > far:
            continue
        if projection == 0:
            x0 = (first[0] / (ortho_scale * aspect) + 1.0) * 0.5 * width
            y0 = (1.0 - first[1] / ortho_scale) * 0.5 * height
            x1 = (second[0] / (ortho_scale * aspect) + 1.0) * 0.5 * width
            y1 = (1.0 - second[1] / ortho_scale) * 0.5 * height
            half_width = bond_radius * height / (2.0 * ortho_scale)
        else:
            x0 = (first[0] * focal / (depth0 * aspect) + 1.0) * 0.5 * width
            y0 = (1.0 - first[1] * focal / depth0) * 0.5 * height
            x1 = (second[0] * focal / (depth1 * aspect) + 1.0) * 0.5 * width
            y1 = (1.0 - second[1] * focal / depth1) * 0.5 * height
            half_width = 0.5 * height * focal * bond_radius / min(depth0, depth1)
        half_width = max(0.5, half_width)
        vector_x = x1 - x0
        vector_y = y1 - y0
        length_squared = vector_x * vector_x + vector_y * vector_y
        if length_squared < 1.0e-16:
            continue
        minimum_x = max(0, int(np.floor(min(x0, x1) - half_width - 1.0)))
        maximum_x = min(width - 1, int(np.ceil(max(x0, x1) + half_width + 1.0)))
        minimum_y = max(0, int(np.floor(min(y0, y1) - half_width - 1.0)))
        maximum_y = min(height - 1, int(np.ceil(max(y0, y1) + half_width + 1.0)))
        for pixel_y in range(minimum_y, maximum_y + 1):
            for pixel_x in range(minimum_x, maximum_x + 1):
                parameter = (
                    (pixel_x + 0.5 - x0) * vector_x + (pixel_y + 0.5 - y0) * vector_y
                ) / length_squared
                parameter = min(1.0, max(0.0, parameter))
                closest_x = x0 + parameter * vector_x
                closest_y = y0 + parameter * vector_y
                delta_x = pixel_x + 0.5 - closest_x
                delta_y = pixel_y + 0.5 - closest_y
                distance_squared = delta_x * delta_x + delta_y * delta_y
                if distance_squared > half_width * half_width:
                    continue
                if projection == 0:
                    pixel_depth = (1.0 - parameter) * depth0 + parameter * depth1
                else:
                    reciprocal = (1.0 - parameter) / depth0 + parameter / depth1
                    pixel_depth = 1.0 / max(reciprocal, 1.0e-15)
                radial = np.sqrt(
                    max(
                        1.0 - distance_squared / (half_width * half_width),
                        0.0,
                    )
                )
                pixel_depth -= bond_radius * radial
                if pixel_depth < near or pixel_depth > far:
                    continue
                if pixel_depth >= depth_buffer[pixel_y, pixel_x] - 1.0e-9:
                    continue
                atom_index = first_index if parameter <= 0.5 else second_index
                atomic_number = int(atomic_numbers[atom_index])
                illumination = 0.72 + 0.28 * radial
                depth_buffer[pixel_y, pixel_x] = pixel_depth
                rgba[pixel_y, pixel_x, 0] = min(
                    255, int(colors[atomic_number, 0] * illumination + 0.5)
                )
                rgba[pixel_y, pixel_x, 1] = min(
                    255, int(colors[atomic_number, 1] * illumination + 0.5)
                )
                rgba[pixel_y, pixel_x, 2] = min(
                    255, int(colors[atomic_number, 2] * illumination + 0.5)
                )
                rgba[pixel_y, pixel_x, 3] = 255


def _camera_points(points: np.ndarray, camera: CameraSpec) -> np.ndarray:
    view = CameraTransform(camera, 1, 1).view_matrix.astype(np.float32)
    result = np.empty_like(points, dtype=np.float32)
    result[:, 0] = (
        points[:, 0] * view[0, 0]
        + points[:, 1] * view[0, 1]
        + points[:, 2] * view[0, 2]
        + view[0, 3]
    )
    result[:, 1] = (
        points[:, 0] * view[1, 0]
        + points[:, 1] * view[1, 1]
        + points[:, 2] * view[1, 2]
        + view[1, 3]
    )
    result[:, 2] = (
        points[:, 0] * view[2, 0]
        + points[:, 1] * view[2, 1]
        + points[:, 2] * view[2, 2]
        + view[2, 3]
    )
    return result


def render_frame_batch(
    frame: FrameBatch,
    camera: CameraSpec,
    *,
    width: int,
    height: int,
    atom_scale: float = 1.0,
    background: tuple[int, int, int, int] = (255, 255, 255, 255),
    show_hydrogen: bool = True,
    show_cell: bool = True,
    cell_color: tuple[int, int, int] = (51, 51, 51),
    cell_width_px: float = 2.0,
    bonds: Any = None,
    bond_radius: float = 0.15,
) -> BatchRenderResult:
    """Render analytic spheres from contiguous arrays into one fixed viewport."""
    camera_positions = project_frame(frame, camera)
    return render_projected_frame(
        frame,
        camera_positions,
        camera,
        width=width,
        height=height,
        atom_scale=atom_scale,
        background=background,
        show_hydrogen=show_hydrogen,
        show_cell=show_cell,
        cell_color=cell_color,
        cell_width_px=cell_width_px,
        bonds=bonds,
        bond_radius=bond_radius,
    )


def render_projected_frame(
    frame: FrameBatch,
    camera_positions: np.ndarray,
    camera: CameraSpec,
    *,
    width: int,
    height: int,
    atom_scale: float = 1.0,
    background: tuple[int, int, int, int] = (255, 255, 255, 255),
    show_hydrogen: bool = True,
    show_cell: bool = True,
    cell_color: tuple[int, int, int] = (51, 51, 51),
    cell_width_px: float = 2.0,
    bonds: Any = None,
    bond_radius: float = 0.15,
) -> BatchRenderResult:
    """Rasterize a frame that has already been transformed by one camera."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if camera_positions.shape != frame.positions.shape:
        raise ValueError("camera_positions must match frame.positions")
    spheres = build_sphere_batch(frame, camera_positions, atom_scale=atom_scale)
    rgba, depth = _fill_background(
        int(height), int(width), np.asarray(background, dtype=np.uint8)
    )
    projection = 0 if camera.projection == "orthographic" else 1
    if show_cell:
        corners = _camera_points(frame_box_corners(frame), camera)
        _draw_cell_segments(
            rgba,
            depth,
            corners,
            projection,
            float(camera.ortho_scale),
            float(camera.fov_y_deg),
            float(camera.near),
            float(camera.far),
            np.asarray(cell_color, dtype=np.uint8),
            float(cell_width_px),
        )
    if bonds is not None and len(bonds.pairs):
        if not np.isfinite(bond_radius) or bond_radius <= 0.0:
            raise ValueError("bond_radius must be finite and positive")
        _rasterize_bonds(
            spheres.camera_positions,
            spheres.atomic_numbers,
            bonds.pairs,
            spheres.colors,
            rgba,
            depth,
            projection,
            float(camera.ortho_scale),
            float(camera.fov_y_deg),
            float(camera.near),
            float(camera.far),
            float(bond_radius),
            bool(show_hydrogen),
        )
    _rasterize_spheres(
        spheres.camera_positions,
        spheres.atomic_numbers,
        spheres.colors,
        spheres.radii,
        rgba,
        depth,
        projection,
        float(camera.ortho_scale),
        float(camera.fov_y_deg),
        float(camera.near),
        float(camera.far),
        bool(show_hydrogen),
    )
    return BatchRenderResult(
        rgba=np.asarray(rgba),
        depth=np.asarray(depth),
        camera_positions=camera_positions,
    )


@_compile(cache=True, nogil=True)
def _quantize_global_palette(rgb: np.ndarray) -> np.ndarray:
    height, width, _ = rgb.shape
    indices = np.empty((height, width), dtype=np.uint8)
    for y in range(height):
        for x in range(width):
            red = (int(rgb[y, x, 0]) * 5 + 127) // 255
            green = (int(rgb[y, x, 1]) * 6 + 127) // 255
            blue = (int(rgb[y, x, 2]) * 5 + 127) // 255
            indices[y, x] = (red * 7 + green) * 6 + blue
    return indices


def quantize_global_palette(rgb: np.ndarray) -> np.ndarray:
    """Map RGB pixels to the deterministic global 6x7x6 GIF palette."""
    values = np.ascontiguousarray(rgb, dtype=np.uint8)
    if values.ndim != 3 or values.shape[2] != 3:
        raise ValueError("RGB input must have shape (height, width, 3)")
    return _quantize_global_palette(values)


def warm_batch_renderer() -> None:
    """Compile the numeric kernels once before forking frame workers."""
    frame = FrameBatch(
        positions=np.asarray([[0.0, 0.0, 0.0]], dtype=np.float32),
        atomic_numbers=np.asarray([6], dtype=np.uint8),
        atom_ids=None,
        origin=np.zeros(3),
        cell=np.eye(3),
        pbc=np.ones(3, dtype=bool),
        timestep=0,
        source_index=0,
    )
    camera = CameraSpec.looking_along(
        (0.0, 0.0, 1.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        distance=5.0,
        projection="orthographic",
        ortho_scale=2.0,
    )
    render_frame_batch(frame, camera, width=8, height=8, show_cell=False)
    quantize_global_palette(np.zeros((2, 2, 3), dtype=np.uint8))


__all__ = [
    "BatchRenderResult",
    "SphereBatch",
    "build_sphere_batch",
    "NUMBA_AVAILABLE",
    "element_style_tables",
    "project_frame",
    "quantize_global_palette",
    "render_frame_batch",
    "render_projected_frame",
    "warm_batch_renderer",
]
