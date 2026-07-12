import pytest
import numpy as np

from osm_optimizer import douglas_peucker_indices_fast

def test_dp_less_than_3_points():
    pts_0 = np.array([], dtype=np.float64).reshape(0, 2)
    assert np.array_equal(douglas_peucker_indices_fast(pts_0, 1.0), np.array([], dtype=np.int64))

    pts_1 = np.array([[0.0, 0.0]])
    assert np.array_equal(douglas_peucker_indices_fast(pts_1, 1.0), np.array([0], dtype=np.int64))

    pts_2 = np.array([[0.0, 0.0], [1.0, 1.0]])
    assert np.array_equal(douglas_peucker_indices_fast(pts_2, 1.0), np.array([0, 1], dtype=np.int64))

def test_dp_collinear_points():
    # Points on a straight line should only retain the start and end points
    pts = np.array([
        [0.0, 0.0],
        [1.0, 1.0],
        [2.0, 2.0],
        [3.0, 3.0],
        [4.0, 4.0]
    ])
    indices = douglas_peucker_indices_fast(pts, 0.1)
    assert np.array_equal(indices, np.array([0, 4], dtype=np.int64))

def test_dp_epsilon_filtering():
    # Points forming a triangle
    pts = np.array([
        [0.0, 0.0],
        [5.0, 1.0],  # Distance from the line (0,0)-(10,0) is 1.0
        [10.0, 0.0]
    ])

    # If epsilon > 1.0, the middle point should be removed
    indices_large_eps = douglas_peucker_indices_fast(pts, 1.1)
    assert np.array_equal(indices_large_eps, np.array([0, 2], dtype=np.int64))

    # If epsilon < 1.0, the middle point should be kept
    indices_small_eps = douglas_peucker_indices_fast(pts, 0.9)
    assert np.array_equal(indices_small_eps, np.array([0, 1, 2], dtype=np.int64))

def test_dp_point_cluster():
    # All points are in the exact same spot (l2 == 0)
    pts = np.array([
        [0.0, 0.0],
        [0.0, 0.0],
        [0.0, 0.0],
        [0.0, 0.0]
    ])
    indices = douglas_peucker_indices_fast(pts, 0.1)
    # The start and end are kept. Others have distance 0 to the start/end, so they are dropped.
    assert np.array_equal(indices, np.array([0, 3], dtype=np.int64))
