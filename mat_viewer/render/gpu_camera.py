"""Clip-space camera matrices shared by the GPU mesh and instance shaders.

WebGPU's window depth is 0 at the near plane and 1 at the far plane. The
projection below matches that range. Lighting uses the same camera-space
direction as the CPU mesh rasterizer, transformed into world space so an
orbiting camera keeps the highlight stable on screen.
"""

from __future__ import annotations

from typing import Any

import numpy as np


# Camera-space light shared with ``render.cpu.raster``. The vector is
# normalised once; shaders still normalise the uploaded world-space copy.
_CAMERA_LIGHT = np.asarray((-0.32, 0.42, 1.0), dtype=np.float64)
_CAMERA_LIGHT /= np.linalg.norm(_CAMERA_LIGHT)
_UNIFORM_BYTES = 256


def _camera_basis(camera: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    position = np.asarray(camera.position, dtype=np.float32)
    target = np.asarray(camera.target, dtype=np.float32)
    up = np.asarray(camera.up, dtype=np.float32)
    forward = target - position
    forward /= max(float(np.linalg.norm(forward)), 1e-12)
    right = np.cross(forward, up)
    right /= max(float(np.linalg.norm(right)), 1e-12)
    true_up = np.cross(right, forward)
    return position, right, true_up, forward


def world_light(camera: Any) -> np.ndarray:
    """Return the CPU camera-space light expressed in world space."""

    _position, right, true_up, forward = _camera_basis(camera)
    rotation_transpose = np.column_stack((right, true_up, -forward))
    return (rotation_transpose @ _CAMERA_LIGHT).astype(np.float32)


def _camera_matrix(camera: Any, *, aspect: float) -> np.ndarray:
    """Build a column-major world-to-clip matrix for the MVP shader."""

    position, right, true_up, forward = _camera_basis(camera)
    view = np.eye(4, dtype=np.float32)
    view[0, :3] = right
    view[1, :3] = true_up
    view[2, :3] = -forward
    view[:3, 3] = -np.array(
        [
            np.dot(right, position),
            np.dot(true_up, position),
            -np.dot(forward, position),
        ],
        dtype=np.float32,
    )
    if str(camera.projection) == "perspective":
        f = 1.0 / np.tan(np.deg2rad(float(camera.fov_y_deg)) / 2.0)
        near, far = float(camera.near), float(camera.far)
        projection = np.array(
            [
                [f / max(aspect, 1e-12), 0, 0, 0],
                [0, f, 0, 0],
                [0, 0, far / (near - far), (near * far) / (near - far)],
                [0, 0, -1, 0],
            ],
            dtype=np.float32,
        )
    else:
        # CameraSpec.ortho_scale is the half-height of the orthographic
        # view volume (matching render.camera._projection_matrix), not the
        # full viewport height.
        half_y = float(camera.ortho_scale)
        half_x = half_y * max(aspect, 1e-12)
        near, far = float(camera.near), float(camera.far)
        projection = np.array(
            [
                [1 / half_x, 0, 0, 0],
                [0, 1 / half_y, 0, 0],
                [0, 0, 1 / (near - far), near / (near - far)],
                [0, 0, 0, 1],
            ],
            dtype=np.float32,
        )
    # WGSL mat4x4 is column-major; numpy's transpose gives bytes in the
    # expected layout for the shader's matrix multiply.
    return (projection @ view).T.astype(np.float32, copy=False)


def uniform_bytes(camera: Any, *, aspect: float) -> bytes:
    """Pack the view-projection matrix and world light into one uniform buffer.

    ``view_proj`` occupies bytes 0–63. ``light`` is a ``vec3`` at byte 64,
    which is the next 16-byte slot after a ``mat4x4``.
    """

    matrix = _camera_matrix(camera, aspect=aspect).astype("<f4", copy=False).tobytes()
    light = np.asarray(world_light(camera), dtype="<f4").tobytes()
    blob = bytearray(_UNIFORM_BYTES)
    blob[: len(matrix)] = matrix
    blob[64 : 64 + len(light)] = light
    return bytes(blob)


def _ndc_to_camera_depth(depth_ndc: np.ndarray, camera: Any) -> np.ndarray:
    """Convert WebGPU window depth to the CPU composite's positive camera depth."""

    near = float(camera.near)
    far = float(camera.far)
    span = near - far
    if str(camera.projection) == "perspective":
        p22 = far / span
        p23 = near * far / span
        z_view = p23 / (-depth_ndc - p22)
    else:
        z_view = (depth_ndc - near / span) * span
    return np.asarray(-z_view, dtype=np.float32)
