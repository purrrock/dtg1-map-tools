import pytest
from dtg1_geometry import POIGeometryFactory, is_clockwise

def test_generate_polygon_rhombus():
    """Test generating a rhombus polygon."""
    center_lon = 37000000  # 37.0 degrees
    center_lat = 55000000  # 55.0 degrees

    polygon = POIGeometryFactory.generate_polygon("rhombus", center_lon, center_lat)

    assert isinstance(polygon, list)
    assert len(polygon) == 5  # rhombus has 5 points (closed loop)
    for point in polygon:
        assert isinstance(point, tuple)
        assert len(point) == 2
        assert isinstance(point[0], int)
        assert isinstance(point[1], int)

def test_generate_polygon_unknown_shape():
    """Test generating a polygon with an unknown shape falls back to rhombus."""
    center_lon = 37000000
    center_lat = 55000000

    rhombus_polygon = POIGeometryFactory.generate_polygon("rhombus", center_lon, center_lat)
    unknown_polygon = POIGeometryFactory.generate_polygon("unknown_shape_123", center_lon, center_lat)

    # Should fallback to rhombus
    assert unknown_polygon == rhombus_polygon

def test_generate_polygon_equator():
    """Test generating a polygon at the equator."""
    center_lon = 0
    center_lat = 0

    polygon = POIGeometryFactory.generate_polygon("triangle", center_lon, center_lat)

    assert isinstance(polygon, list)
    assert len(polygon) == 4

def test_generate_polygon_all_shapes():
    """Test that all defined shapes return valid geometries."""
    # List of all shapes defined in the factory
    shapes = [
        "rhombus", "triangle", "house", "cup", "cross", "toilet",
        "transport", "shop", "attraction", "bicycle", "shower",
        "barrier", "tshirt", "hammer", "mountain", "computer",
        "airplane", "fuel", "dollar", "shield"
    ]

    center_lon = 10000000
    center_lat = 20000000

    for shape in shapes:
        polygon = POIGeometryFactory.generate_polygon(shape, center_lon, center_lat)
        assert len(polygon) > 0, f"Shape '{shape}' generated empty polygon"
        assert polygon[0] == polygon[-1], f"Shape '{shape}' is not a closed polygon"
        for point in polygon:
            assert isinstance(point[0], int)
            assert isinstance(point[1], int)

def test_is_clockwise():
    """Test the is_clockwise function."""
    # Clockwise square
    cw_square = [(0, 1), (1, 1), (1, 0), (0, 0), (0, 1)]
    assert is_clockwise(cw_square) is True

    # Counter-clockwise square
    ccw_square = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
    assert is_clockwise(ccw_square) is False

    # Empty list
    assert is_clockwise([]) is False

    # Triangle (Clockwise)
    cw_triangle = [(0, 1), (1, -1), (-1, -1), (0, 1)]
    assert is_clockwise(cw_triangle) is True
