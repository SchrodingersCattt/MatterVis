"""Unit tests for crystal_viewer.depth_sort utilities."""

from __future__ import annotations

import numpy as np
import pytest

from crystal_viewer.depth_sort import (
    assign_zorder_by_depth,
    camera_view_vector,
    depth_along_view,
)


def test_camera_view_vector_axis_aligned():
    # azim=0, elev=0 -> camera on +x looking back; view vector points to +x
    v = camera_view_vector(0.0, 0.0)
    np.testing.assert_allclose(v, [1.0, 0.0, 0.0], atol=1e-12)

    # azim=90, elev=0 -> camera on +y
    v = camera_view_vector(0.0, 90.0)
    np.testing.assert_allclose(v, [0.0, 1.0, 0.0], atol=1e-12)

    # azim=0, elev=90 -> camera straight above
    v = camera_view_vector(90.0, 0.0)
    np.testing.assert_allclose(v, [0.0, 0.0, 1.0], atol=1e-12)


def test_depth_along_view_orders_correctly():
    # Camera on +x; +x point is closer than -x point
    near = np.array([1.0, 0.0, 0.0])
    far = np.array([-1.0, 0.0, 0.0])
    d_near = depth_along_view(near, 0.0, 0.0)
    d_far = depth_along_view(far, 0.0, 0.0)
    assert d_near > d_far


def test_depth_along_view_batched():
    pts = np.array([[1, 0, 0], [0, 0, 0], [-1, 0, 0]], dtype=float)
    d = depth_along_view(pts, 0.0, 0.0)
    assert d.shape == (3,)
    assert d[0] > d[1] > d[2]


def test_assign_zorder_back_to_front():
    prims = [
        {"name": "near", "point": [1.0, 0.0, 0.0]},
        {"name": "mid",  "point": [0.0, 0.0, 0.0]},
        {"name": "far",  "point": [-1.0, 0.0, 0.0]},
    ]
    sorted_prims = assign_zorder_by_depth(prims, 0.0, 0.0, base_zorder=10)
    # back-to-front: far first, near last
    assert [p["name"] for p in sorted_prims] == ["far", "mid", "near"]
    # zorders increase
    assert [p["zorder"] for p in sorted_prims] == [10, 11, 12]


def test_assign_zorder_uses_centroid_for_points():
    prims = [
        {"name": "spans_far",  "points": [[-2, 0, 0], [-1, 0, 0]]},
        {"name": "spans_near", "points": [[ 1, 0, 0], [ 2, 0, 0]]},
    ]
    sorted_prims = assign_zorder_by_depth(prims, 0.0, 0.0)
    assert [p["name"] for p in sorted_prims] == ["spans_far", "spans_near"]


def test_assign_zorder_empty():
    assert assign_zorder_by_depth([], 0.0, 0.0) == []


def test_assign_zorder_missing_point_raises():
    with pytest.raises(KeyError):
        assign_zorder_by_depth([{"name": "x"}], 0.0, 0.0)
