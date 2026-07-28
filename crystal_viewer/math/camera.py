"""Camera abstraction for the TUI renderer.

This module is ISOLATED from the existing render/ and compass/ pipeline.
It must NOT be imported by any existing module. It provides orthographic
projection (with a perspective stub for future work).

Reuses: crystal_viewer.math.rotation.view_rotation() for rotation matrices.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np

from .rotation import (
    axis_camera_basis,
    orthonormal_camera_basis,
    rotate_vector,
    view_rotation,
    view_vec_to_elev_azim,
)

if TYPE_CHECKING:
    from ..tui.crystal_ir import CrystalIR


class ProjectionMode(Enum):
    ORTHOGRAPHIC = "orthographic"
    PERSPECTIVE = "perspective"


@dataclass
class Camera:
    """Minimal camera state for terminal rendering.

    Attributes
    ----------
    azimuth : float
        Horizontal rotation angle in degrees around world +Z.
    elevation : float
        Vertical rotation angle in degrees (above XY plane).
    distance : float
        Camera distance from target (affects ortho scale).
    target : np.ndarray
        3D point the camera looks at (pan offset).
    projection : ProjectionMode
        Current projection mode.
    fov_deg : float
        Field of view for perspective mode (unused in ortho).
    """

    azimuth: float = 30.0
    elevation: float = 20.0
    roll: float = 0.0  # rotation around view axis (degrees)
    distance: float = 1.0
    target: np.ndarray = None  # type: ignore[assignment]
    projection: ProjectionMode = ProjectionMode.ORTHOGRAPHIC
    fov_deg: float = 50.0
    viewport_zoom: float = 1.0  # >1 crops viewport (zoom into center)
    pan_x: float = 0.0  # 2D viewport pan offset (in projected data units)
    pan_y: float = 0.0
    basis: np.ndarray | None = None  # [right; up; forward], if view-relative state is retained
    perspective_near_is_larger: bool = False

    def __post_init__(self):
        if self.target is None:
            self.target = np.zeros(3)
        self.target = np.asarray(self.target, dtype=float)
        if self.target.shape != (3,) or not np.all(np.isfinite(self.target)):
            raise ValueError("target must be a finite three-dimensional vector")
        if self.basis is not None:
            basis = np.asarray(self.basis, dtype=float)
            if basis.shape != (3, 3) or not np.all(np.isfinite(basis)):
                raise ValueError("basis must be a finite 3x3 matrix")
            self.basis = orthonormal_camera_basis(basis[2], basis[1])

    @property
    def view_direction(self) -> np.ndarray:
        """Unit vector from the target toward the camera (larger depth is closer)."""
        if self.basis is not None:
            return self.basis[2].copy()
        elev = np.radians(self.elevation)
        azim = np.radians(self.azimuth)
        # Spherical to Cartesian: camera position relative to target
        return np.array([
            np.cos(elev) * np.cos(azim),
            np.cos(elev) * np.sin(azim),
            np.sin(elev),
        ])

    @property
    def rotation_matrix(self) -> np.ndarray:
        """3×3 rotation matrix [right; up; forward] with roll applied."""
        if self.basis is not None:
            return self.basis.copy()
        # The turntable's world-up convention is +Z. Use it for first-frame
        # Euler cameras too, so the first orbit continues the rendered basis
        # instead of changing screen roll or making pitch orbit around +Z.
        R = orthonormal_camera_basis(
            self.view_direction,
            np.array([0.0, 0.0, 1.0]),
        )
        if abs(self.roll) > 0.01:
            # Apply roll: rotate the right and up vectors around forward
            angle = np.radians(self.roll)
            c, s = np.cos(angle), np.sin(angle)
            right = R[0] * c + R[1] * s
            up = -R[0] * s + R[1] * c
            R = np.array([right, up, R[2]])
        return R

    # ── Factory ─────────────────────────────────────────────────────────

    @classmethod
    def from_view_name(cls, name: str, crystal: "CrystalIR") -> "Camera":
        """Create a Camera from a named view direction.

        Parameters
        ----------
        name : str
            One of: "auto", "a", "b", "c", "diagonal", "ab", "ac", "bc"
        crystal : CrystalIR
            Used to compute center and appropriate distance.
        """
        target = crystal.center_of_mass

        # Compute a reasonable distance from the extent of the structure
        coords = crystal.cart_coords
        if len(coords) > 0:
            spread = np.linalg.norm(coords - target, axis=1).max()
            distance = max(spread * 1.5, 1.0)
        else:
            distance = 5.0

        presets = {
            "a": (0.0, 0.0),      # Looking along +a
            "b": (90.0, 0.0),     # Looking along +b
            "c": (0.0, 90.0),     # Looking along +c (top-down)
            "diagonal": (30.0, 20.0),
            "ab": (45.0, 0.0),
            "ac": (0.0, 45.0),
            "bc": (90.0, 45.0),
        }

        if name == "auto":
            name = "diagonal"

        azim, elev = presets.get(name, (30.0, 20.0))

        return cls(
            azimuth=azim,
            elevation=elev,
            distance=distance,
            target=target,
            projection=ProjectionMode.ORTHOGRAPHIC,
        )

    # ── Transforms ──────────────────────────────────────────────────────

    def rotate(self, d_azim: float = 0.0, d_elev: float = 0.0, d_roll: float = 0.0) -> "Camera":
        """Return a legacy absolute-Euler camera rotation.

        New interactive TUI code uses :meth:`orbit_turntable`, which composes
        rotations in camera/world space. This method remains stable for
        existing callers that expect scalar Euler increments.
        """
        new_elev = np.clip(self.elevation + d_elev, -89.0, 89.0)
        new_azim = (self.azimuth + d_azim) % 360.0
        new_roll = (self.roll + d_roll) % 360.0
        return replace(self, azimuth=new_azim, elevation=new_elev, roll=new_roll, basis=None)

    def set_orientation(
        self,
        *,
        azimuth: float | None = None,
        elevation: float | None = None,
        roll: float | None = None,
    ) -> "Camera":
        """Set absolute Euler orientation values and rebuild the camera basis."""
        new_azimuth = self.azimuth if azimuth is None else float(azimuth) % 360.0
        new_elevation = self.elevation if elevation is None else float(np.clip(float(elevation), -89.0, 89.0))
        new_roll = self.roll if roll is None else float(roll) % 360.0
        if not all(np.isfinite(value) for value in (new_azimuth, new_elevation, new_roll)):
            raise ValueError("camera orientation values must be finite")
        return replace(
            self,
            azimuth=new_azimuth,
            elevation=new_elevation,
            roll=new_roll,
            basis=None,
        )

    def orbit_turntable(
        self,
        *,
        yaw_deg: float = 0.0,
        pitch_deg: float = 0.0,
        roll_deg: float = 0.0,
        max_pitch_deg: float = 89.0,
    ) -> "Camera":
        """Compose a world-up turntable orbit without moving scene geometry.

        Yaw rotates around Cartesian world ``+Z``. Pitch then rotates around
        the updated screen-right axis. Roll rotates around the view axis. The
        final basis is retained so future pitch/roll updates stay continuous.
        """
        values = (yaw_deg, pitch_deg, roll_deg, max_pitch_deg)
        if not all(np.isfinite(value) for value in values):
            raise ValueError("orbit angles must be finite")
        if not 0.0 < max_pitch_deg < 90.0:
            raise ValueError("max_pitch_deg must be between 0 and 90")

        world_up = np.array([0.0, 0.0, 1.0])
        # Preserve the exact basis that rendered the preceding frame. In
        # particular, an Euler camera initially uses ``view_rotation()``'s
        # established up-vector policy; rebuilding it with world +Z here
        # would introduce a visible roll before the requested orbit begins.
        right, up, forward = self.rotation_matrix
        if abs(yaw_deg) > 1e-12:
            forward = rotate_vector(forward, world_up, yaw_deg)
            up = rotate_vector(up, world_up, yaw_deg)
            right = rotate_vector(right, world_up, yaw_deg)
        basis = orthonormal_camera_basis(forward, up)
        right, up, forward = basis

        if abs(pitch_deg) > 1e-12:
            # ``forward`` points from scene target to camera. A positive
            # pitch should therefore raise the camera toward world +Z, which
            # is a right-hand rotation about *negative* screen-right.
            pitch_axis = -right
            applied_pitch = _clamp_turntable_pitch(
                forward,
                pitch_axis,
                pitch_deg,
                max_pitch_deg,
            )
            forward = rotate_vector(forward, pitch_axis, applied_pitch)
            up = rotate_vector(up, pitch_axis, applied_pitch)
            basis = orthonormal_camera_basis(forward, up)
            right, up, forward = basis

        if abs(roll_deg) > 1e-12:
            right = rotate_vector(right, forward, roll_deg)
            up = rotate_vector(up, forward, roll_deg)
        basis = orthonormal_camera_basis(forward, up)
        elevation, azimuth = view_vec_to_elev_azim(basis[2])
        base_basis = view_rotation(basis[2])
        roll = np.degrees(np.arctan2(
            float(np.dot(basis[0], base_basis[1])),
            float(np.dot(basis[0], base_basis[0])),
        )) % 360.0
        return replace(
            self,
            azimuth=float(azimuth % 360.0),
            elevation=float(elevation),
            roll=float(roll),
            basis=basis,
        )

    def align_lattice_axis(self, matrix: np.ndarray, axis: str) -> "Camera":
        """Orient this camera along a real or reciprocal lattice axis."""
        basis = axis_camera_basis(matrix, axis)
        elevation, azimuth = view_vec_to_elev_azim(basis[2])
        base_basis = view_rotation(basis[2])
        roll = np.degrees(np.arctan2(
            float(np.dot(basis[0], base_basis[1])),
            float(np.dot(basis[0], base_basis[0])),
        )) % 360.0
        return replace(
            self,
            azimuth=float(azimuth % 360.0),
            elevation=float(elevation),
            roll=float(roll),
            basis=basis,
        )

    def pan(self, dx: float = 0.0, dy: float = 0.0) -> "Camera":
        """Pan the viewport in 2D screen space.

        dx/dy shift the viewport window over the projected scene.
        Positive dx = scene moves left (viewport moves right).
        """
        return replace(self, pan_x=self.pan_x + dx, pan_y=self.pan_y + dy)

    def zoom(self, factor: float) -> "Camera":
        """Zoom by scaling viewport_zoom. factor > 1 zooms in."""
        new_zoom = max(self.viewport_zoom * factor, 0.5)
        new_zoom = min(new_zoom, 20.0)
        return replace(self, viewport_zoom=new_zoom)

    def reset_zoom(self) -> "Camera":
        """Reset zoom to 1.0."""
        return replace(self, viewport_zoom=1.0)

    def toggle_projection(self) -> "Camera":
        """Toggle between orthographic and perspective."""
        if self.projection == ProjectionMode.ORTHOGRAPHIC:
            return replace(self, projection=ProjectionMode.PERSPECTIVE)
        return replace(self, projection=ProjectionMode.ORTHOGRAPHIC)


# ── Projection ──────────────────────────────────────────────────────────────


def project_points(
    camera: Camera,
    points_3d: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Project 3D points to 2D screen coordinates.

    Parameters
    ----------
    camera : Camera
        Camera state.
    points_3d : np.ndarray
        (N, 3) array of Cartesian world positions.

    Returns
    -------
    xy_2d : np.ndarray
        (N, 2) screen-plane coordinates (right, up).
    depth : np.ndarray
        (N,) depth values (larger = closer to camera).
    """
    if len(points_3d) == 0:
        return np.empty((0, 2)), np.empty(0)

    pts = np.asarray(points_3d, dtype=float)

    # Center on target
    centered = pts - camera.target

    # Rotate into camera space
    R = camera.rotation_matrix  # [right; up; forward]
    cam_space = centered @ R.T  # (N, 3): [x_screen, y_screen, z_depth]

    if camera.projection == ProjectionMode.ORTHOGRAPHIC:
        xy_2d = cam_space[:, :2] / camera.distance
        depth = cam_space[:, 2]
    elif camera.projection == ProjectionMode.PERSPECTIVE:
        # Legacy static callers retain their historical convention. The
        # semantic controller opts into physical camera-distance projection,
        # where depth grows toward the camera and therefore reduces distance.
        z = (
            camera.distance - cam_space[:, 2]
            if camera.perspective_near_is_larger
            else camera.distance + cam_space[:, 2]
        )
        z = np.maximum(z, 0.01)
        fov_scale = np.tan(np.radians(camera.fov_deg / 2))
        xy_2d = cam_space[:, :2] / (z[:, np.newaxis] * fov_scale)
        depth = cam_space[:, 2]
    else:
        raise ValueError(f"Unknown projection: {camera.projection}")

    return xy_2d, depth


def _clamp_turntable_pitch(
    forward: np.ndarray,
    axis: np.ndarray,
    requested_deg: float,
    max_pitch_deg: float,
) -> float:
    """Stop pitch at the first world-up pole boundary along its arc."""
    if abs(requested_deg) < 1e-12:
        return 0.0
    direction = float(np.copysign(1.0, requested_deg))
    # A turntable never needs more than a half turn to reach its first pole.
    # Canonicalising bounds work for arbitrary finite agent input while
    # retaining the first-pole semantics for ordinary interactions.
    total = min(abs(float(requested_deg)), 180.0)

    def elevation_at(magnitude: float) -> float:
        candidate = rotate_vector(forward, axis, direction * magnitude)
        return abs(float(np.degrees(np.arcsin(np.clip(candidate[2], -1.0, 1.0)))))

    low = 0.0
    high: float | None = None
    # Search a fixed number of intervals so a 180° request cannot tunnel
    # through the first pole and return to low elevation on the far side.
    for step in range(1, 65):
        candidate = total * step / 64.0
        if elevation_at(candidate) > max_pitch_deg:
            high = candidate
            break
        low = candidate
    if high is None:
        return direction * total

    # A fixed number of bisection iterations avoids work proportional to the
    # original user input while resolving the first pole precisely.
    for _ in range(48):
        middle = (low + high) / 2.0
        if elevation_at(middle) <= max_pitch_deg:
            low = middle
        else:
            high = middle
    return direction * low


def project_segments(
    camera: Camera,
    segments: list[tuple[np.ndarray, np.ndarray]],
) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """Project line segments (pairs of 3D points) to 2D.

    Returns list of (start_2d, end_2d, avg_depth).
    """
    if not segments:
        return []

    starts = np.array([s[0] for s in segments])
    ends = np.array([s[1] for s in segments])

    s_2d, s_depth = project_points(camera, starts)
    e_2d, e_depth = project_points(camera, ends)

    result = []
    for i in range(len(segments)):
        avg_depth = (s_depth[i] + e_depth[i]) / 2.0
        result.append((s_2d[i], e_2d[i], avg_depth))
    return result
