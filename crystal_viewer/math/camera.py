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

from .rotation import view_rotation

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
        Horizontal rotation angle in degrees (around world Y).
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

    def __post_init__(self):
        if self.target is None:
            self.target = np.zeros(3)
        self.target = np.asarray(self.target, dtype=float)

    @property
    def view_direction(self) -> np.ndarray:
        """Unit vector FROM camera TOWARD target (into the scene)."""
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
        R = view_rotation(self.view_direction)
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
        """Return a new camera rotated by the given increments (degrees)."""
        new_elev = np.clip(self.elevation + d_elev, -89.0, 89.0)
        new_azim = (self.azimuth + d_azim) % 360.0
        new_roll = (self.roll + d_roll) % 360.0
        return replace(self, azimuth=new_azim, elevation=new_elev, roll=new_roll)

    def pan(self, dx: float = 0.0, dy: float = 0.0) -> "Camera":
        """Pan the camera in the screen plane.

        dx/dy are in world-coordinate units along screen-right/up.
        """
        R = self.rotation_matrix
        right = R[0]  # screen-right in world space
        up = R[1]     # screen-up in world space
        offset = right * dx + up * dy
        return replace(self, target=self.target + offset)

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
        # Perspective divide: x/z, y/z
        # z_depth here = distance along view direction from target
        # We offset by camera distance to avoid divide-by-zero
        z = cam_space[:, 2] + camera.distance
        z = np.where(np.abs(z) < 0.01, 0.01, z)  # clamp
        fov_scale = np.tan(np.radians(camera.fov_deg / 2))
        xy_2d = cam_space[:, :2] / (z[:, np.newaxis] * fov_scale)
        depth = cam_space[:, 2]
    else:
        raise ValueError(f"Unknown projection: {camera.projection}")

    return xy_2d, depth


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
