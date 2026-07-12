import pytest
from dtg1_geometry import is_clockwise

def test_is_clockwise_empty():
    """Test with an empty list."""
    assert is_clockwise([]) is False

def test_is_clockwise_single_point():
    """Test with a single point."""
    assert is_clockwise([(0, 0)]) is False

def test_is_clockwise_two_points():
    """Test with two points (collinear)."""
    assert is_clockwise([(0, 0), (1, 1)]) is False

def test_is_clockwise_cw_open():
    """Test with a simple clockwise open ring (unit square)."""
    points = [(0, 1), (1, 1), (1, 0), (0, 0)]
    assert is_clockwise(points) is True

def test_is_clockwise_cw_closed():
    """Test with a simple clockwise closed ring (unit square)."""
    points = [(0, 1), (1, 1), (1, 0), (0, 0), (0, 1)]
    assert is_clockwise(points) is True

def test_is_clockwise_ccw_open():
    """Test with a simple counter-clockwise open ring (unit square)."""
    points = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert is_clockwise(points) is False

def test_is_clockwise_ccw_closed():
    """Test with a simple counter-clockwise closed ring (unit square)."""
    points = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
    assert is_clockwise(points) is False

def test_is_clockwise_collinear_points():
    """Test with collinear points that do not form a valid area."""
    points = [(0, 0), (1, 1), (2, 2), (3, 3)]
    assert is_clockwise(points) is False

def test_is_clockwise_triangle_cw():
    """Test with a clockwise triangle."""
    points = [(0, 5), (5, -5), (-5, -5)]
    assert is_clockwise(points) is True

def test_is_clockwise_triangle_ccw():
    """Test with a counter-clockwise triangle."""
    points = [(0, 5), (-5, -5), (5, -5)]
    assert is_clockwise(points) is False
