"""Domain-neutral polygon and primitive mesh helpers.

The rendering layer consumes meshes as a list of Cartesian vertices and
triangular faces.  Keeping the construction and validation here makes the
same geometry usable by Plotly, exporters, and callers that only need the
coordinates.  No chemistry or Plotly objects belong in this module.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np


def _vector3(value: Iterable[float], *, name: str) -> np.ndarray:
    """Coerce *value* to one finite 3-vector."""

    try:
        out = np.asarray(list(value), dtype=float)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"{name} must be an iterable of three numbers") from exc
    if out.shape != (3,) or not np.all(np.isfinite(out)):
        raise ValueError(f"{name} must be an iterable of three finite numbers")
    return out


def triangulate_faces(faces: Iterable[Iterable[int]]) -> np.ndarray:
    """Triangulate polygon index lists with a fan from the first vertex.

    A triangle list is returned unchanged (apart from integer coercion), while
    polygons with more than three vertices are split into ``(0, i, i+1)``
    triangles.  The input order is preserved, which means callers control the
    surface winding and therefore the lighting/normal direction.
    """

    if faces is None:
        raise ValueError("faces must be a non-empty iterable")
    try:
        raw_faces = list(faces)
    except TypeError as exc:  # pragma: no cover - defensive
        raise ValueError("faces must be a non-empty iterable") from exc
    if not raw_faces:
        raise ValueError("faces must contain at least one face")
    # Be forgiving for the common one-triangle shorthand ``faces=[0, 1, 2]``
    # while keeping the documented list-of-lists form unambiguous.
    if len(raw_faces) == 3 and all(np.isscalar(value) for value in raw_faces):
        raw_faces = [raw_faces]

    triangles: list[tuple[int, int, int]] = []
    for face_index, raw_face in enumerate(raw_faces):
        try:
            values = list(raw_face)
        except TypeError as exc:
            raise ValueError(f"face {face_index} is not an index iterable") from exc
        if len(values) < 3:
            raise ValueError(f"face {face_index} must contain at least three indices")
        indices: list[int] = []
        for value in values:
            # Do not silently round a floating-point index: a malformed mesh
            # should fail at construction rather than produce a wrong face.
            if isinstance(value, (bool, np.bool_)):
                raise ValueError(f"face {face_index} contains a non-integer index")
            try:
                integer = int(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"face {face_index} contains a non-integer index"
                ) from exc
            if isinstance(value, (float, np.floating)) and float(value) != integer:
                raise ValueError(f"face {face_index} contains a non-integer index")
            indices.append(integer)
        anchor = indices[0]
        for offset in range(1, len(indices) - 1):
            triangles.append((anchor, indices[offset], indices[offset + 1]))

    return np.asarray(triangles, dtype=int).reshape(-1, 3)


def validate_mesh(
    vertices: Iterable[Iterable[float]],
    faces: Iterable[Iterable[int]],
) -> tuple[np.ndarray, np.ndarray]:
    """Validate and normalise a Cartesian triangle mesh.

    Returns independent ``float64``/``int64`` arrays.  Indices are checked,
    coordinates must be finite, and zero-area triangles are rejected because
    they make depth and normal calculations undefined in WebGL.
    """

    try:
        vertex_array = np.asarray(vertices, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("vertices must be an N×3 numeric array") from exc
    if vertex_array.ndim != 2 or vertex_array.shape[1] != 3 or len(vertex_array) == 0:
        raise ValueError("vertices must be a non-empty N×3 numeric array")
    if not np.all(np.isfinite(vertex_array)):
        raise ValueError("vertices must contain only finite coordinates")

    # ``faces`` may be a rectangular numpy array or a ragged list of polygons.
    triangle_array = triangulate_faces(faces)
    if np.any(triangle_array < 0) or np.any(triangle_array >= len(vertex_array)):
        raise ValueError("faces contain an index outside the vertices array")

    p0 = vertex_array[triangle_array[:, 0]]
    p1 = vertex_array[triangle_array[:, 1]]
    p2 = vertex_array[triangle_array[:, 2]]
    twice_area = np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)
    if np.any(twice_area <= 1e-12):
        raise ValueError("faces contain a zero-area triangle")

    return np.array(vertex_array, dtype=float, copy=True), np.array(
        triangle_array, dtype=int, copy=True
    )


def cylinder_vertices_faces(
    center: Iterable[float],
    axis: Iterable[float],
    radius: float,
    length: float,
    *,
    segments: int = 32,
    caps: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a closed (or open) triangulated cylinder in Cartesian space.

    ``center`` is the midpoint of the cylinder and ``axis`` supplies its
    direction; the axis vector need not be normalised.  ``length`` is the
    physical end-to-end length.  With ``caps=False`` only the lateral surface
    is emitted, which is useful for showing an open through-channel without
    inventing a face across either opening.
    """

    center_array = _vector3(center, name="center")
    axis_array = _vector3(axis, name="axis")
    try:
        radius_value = float(radius)
        length_value = float(length)
    except (TypeError, ValueError) as exc:
        raise ValueError("radius and length must be finite positive numbers") from exc
    if not np.isfinite(radius_value) or radius_value <= 0.0:
        raise ValueError("radius must be a finite positive number")
    if not np.isfinite(length_value) or length_value <= 0.0:
        raise ValueError("length must be a finite positive number")
    try:
        segment_count = int(segments)
    except (TypeError, ValueError) as exc:
        raise ValueError("segments must be an integer >= 3") from exc
    if segment_count < 3 or segment_count != segments:
        raise ValueError("segments must be an integer >= 3")

    axis_norm = float(np.linalg.norm(axis_array))
    if axis_norm <= 1e-12:
        raise ValueError("axis must have non-zero length")
    axis_unit = axis_array / axis_norm

    # Pick a reference that is not parallel to the requested axis, then build
    # a right-handed orthonormal frame around it.  The exact azimuth is not a
    # visual contract; deterministic selection keeps snapshots reproducible.
    reference = np.array([1.0, 0.0, 0.0], dtype=float)
    if abs(float(np.dot(axis_unit, reference))) > 0.92:
        reference = np.array([0.0, 1.0, 0.0], dtype=float)
    basis_u = np.cross(axis_unit, reference)
    basis_u /= float(np.linalg.norm(basis_u))
    basis_v = np.cross(axis_unit, basis_u)

    angles = np.linspace(0.0, 2.0 * np.pi, segment_count, endpoint=False)
    offsets = radius_value * (
        np.cos(angles)[:, None] * basis_u[None, :]
        + np.sin(angles)[:, None] * basis_v[None, :]
    )
    half = 0.5 * length_value
    ring_minus = center_array[None, :] - half * axis_unit[None, :] + offsets
    ring_plus = center_array[None, :] + half * axis_unit[None, :] + offsets

    vertices = [*ring_minus, *ring_plus]
    faces: list[tuple[int, int, int]] = []
    for index in range(segment_count):
        nxt = (index + 1) % segment_count
        # Outward-facing lateral winding for the right-handed frame above.
        faces.append((index, nxt, segment_count + index))
        faces.append((nxt, segment_count + nxt, segment_count + index))

    if caps:
        minus_center = len(vertices)
        plus_center = minus_center + 1
        vertices.extend(
            [
                center_array - half * axis_unit,
                center_array + half * axis_unit,
            ]
        )
        for index in range(segment_count):
            nxt = (index + 1) % segment_count
            # Bottom normal points along -axis; top normal along +axis.
            faces.append((minus_center, nxt, index))
            faces.append((plus_center, segment_count + index, segment_count + nxt))

    return validate_mesh(np.asarray(vertices, dtype=float), faces)


__all__ = ["cylinder_vertices_faces", "triangulate_faces", "validate_mesh"]
