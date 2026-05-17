"""Camera-aware depth sorting for matplotlib 3D scenes.

Matplotlib's ``Axes3D`` only sorts polygons inside a single
``Poly3DCollection`` against each other.  Separate ``ax.plot`` /
``ax.scatter`` calls do **not** get ordered against each other by depth
— matplotlib renders them in submission order, with ``zorder`` as a
secondary key.  When a single panel mixes wireframe bonds, polyhedron
faces, scatter points, etc., this produces visibly wrong front/back
occlusion (e.g. a "back" bond drawn on top of a "front" face).

This module provides a tiny, reusable helper to fix that:

1. ``camera_view_vector(elev_deg, azim_deg)`` — unit vector pointing
   *from* the scene origin *toward* the matplotlib camera.  The dot
   product of any 3D point with this vector gives a scalar depth: the
   larger the value, the closer to the viewer.

2. ``depth_along_view(points, elev_deg, azim_deg)`` — vectorised depth
   for an array of points.

3. ``assign_zorder_by_depth(primitives, elev_deg, azim_deg, ...)`` —
   the workhorse: takes a list of "primitive" dicts, each carrying one
   representative 3D point (or a list of points whose centroid is used),
   and writes a per-primitive integer ``zorder`` so that when callers
   subsequently draw with ``zorder=p['zorder']``, matplotlib will layer
   them back-to-front correctly.  Primitives still need to be drawn in
   ascending-zorder order to be safe (matplotlib falls back to draw
   order on equal zorder).

These helpers carry no matplotlib dependency themselves; they can be
imported in any module that does its own ``ax.plot``/``ax.scatter``
calls and wants consistent depth ordering across primitive types.
"""

from __future__ import annotations

from typing import Iterable, List, Mapping, MutableMapping, Sequence, Union

import numpy as np

PointsLike = Union[Sequence[float], np.ndarray]


def camera_view_vector(elev_deg: float, azim_deg: float) -> np.ndarray:
    """Unit vector pointing from the scene origin toward the camera.

    Mirrors matplotlib's ``Axes3D`` camera convention:

    - ``elev`` is the angle above the xy-plane, in degrees.
    - ``azim`` is the angle in the xy-plane measured from the +x axis,
      in degrees, counter-clockwise.

    The returned vector has the property that, for any world-space
    point ``p``, ``np.dot(p, view) > np.dot(q, view)`` iff ``p`` is
    closer to the camera than ``q``.
    """
    elev = np.radians(float(elev_deg))
    azim = np.radians(float(azim_deg))
    return np.array(
        [
            np.cos(elev) * np.cos(azim),
            np.cos(elev) * np.sin(azim),
            np.sin(elev),
        ],
        dtype=float,
    )


def depth_along_view(
    points: PointsLike,
    elev_deg: float,
    azim_deg: float,
) -> np.ndarray:
    """Depth (signed scalar) of each point along the camera view vector.

    Larger values are closer to the viewer.  Works for a single point
    (returns a 0-D array), a list of points (returns a 1-D array), or
    a 2-D ``(N, 3)`` array.
    """
    pts = np.asarray(points, dtype=float)
    view = camera_view_vector(elev_deg, azim_deg)
    if pts.ndim == 1:
        return pts @ view
    return pts @ view


def _representative_point(prim: Mapping, point_key: str, points_key: str) -> np.ndarray:
    if point_key in prim:
        return np.asarray(prim[point_key], dtype=float)
    if points_key in prim:
        arr = np.asarray(prim[points_key], dtype=float)
        if arr.ndim == 1:
            return arr
        return arr.mean(axis=0)
    raise KeyError(
        f"Primitive missing both {point_key!r} and {points_key!r}: {prim!r}"
    )


def assign_zorder_by_depth(
    primitives: Iterable[MutableMapping],
    elev_deg: float,
    azim_deg: float,
    *,
    base_zorder: int = 1,
    point_key: str = "point",
    points_key: str = "points",
) -> List[MutableMapping]:
    """Assign per-primitive integer ``zorder`` based on camera depth.

    Each primitive in ``primitives`` should carry either:

    - ``point_key`` (default ``'point'``): a single 3D point, or
    - ``points_key`` (default ``'points'``): a list/array of 3D points
      whose centroid is used as the depth proxy.

    After this call, every primitive will have an integer ``'zorder'``
    field set such that **back-most** primitives get the **smallest**
    zorder and **front-most** get the largest.  Callers should then
    pass ``zorder=prim['zorder']`` to their matplotlib draw calls,
    and (optionally) iterate the returned list which has been sorted
    back-to-front so callers can also rely on submission order as a
    matplotlib tie-breaker.

    Parameters
    ----------
    primitives:
        Iterable of mutable mappings (typically dicts).  Mutated in
        place to gain a ``'zorder'`` key.
    elev_deg, azim_deg:
        Camera angles in degrees, matching ``ax.view_init`` arguments.
    base_zorder:
        The smallest ``zorder`` to assign (i.e. the back-most primitive
        gets ``base_zorder``, the next gets ``base_zorder+1``, etc.).
        Use this to layer the depth-sorted block above or below other
        elements drawn with hard-coded ``zorder``.
    point_key, points_key:
        Field names to read the representative point from.

    Returns
    -------
    list
        The primitives sorted back-to-front (smallest depth first).
    """
    prims = list(primitives)
    if not prims:
        return prims
    view = camera_view_vector(elev_deg, azim_deg)
    depths = np.array(
        [
            float(np.dot(_representative_point(p, point_key, points_key), view))
            for p in prims
        ],
        dtype=float,
    )
    order = np.argsort(depths)  # ascending: back first
    sorted_prims = [prims[i] for i in order]
    for k, p in enumerate(sorted_prims):
        p["zorder"] = int(base_zorder) + k
    return sorted_prims
