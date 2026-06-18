#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Geometry helpers for DTG1 map compiler.

Contains:
- POIGeometryFactory: generator of low-polygon POI primitives (triangle, rhombus, cross, octagon, etc.)
- PERSPECTIVE_Y_MULTIPLIER constant (1.5) for ATS3085S compensation
- is_clockwise(points): CW winding rule checker

API mirrors the original usage in dtg1_map_compiler.py: POIGeometryFactory.generate_polygon(shape_type, center_lon, center_lat)
and is_clockwise(points) which accepts a sequence of (lon, lat) tuples (closed or open) and returns True if ring is clockwise.
"""

from typing import List, Tuple
import math

EARTH_RADIUS = 6378137.0
PERSPECTIVE_Y_MULTIPLIER = 1.5
R = 4.1

class POIGeometryFactory:
    """Generator of low-polygon primitives for the POI layer.

    Usage: POIGeometryFactory.generate_polygon(shape_type, center_lon, center_lat)
    center_lon/center_lat expected in degrees (WGS84).
    Returns list of (lon, lat) tuples.
    """
    EARTH_RADIUS = EARTH_RADIUS
    R = R
    PERSPECTIVE_Y_MULTIPLIER = PERSPECTIVE_Y_MULTIPLIER

    @classmethod
    def generate_polygon(cls, shape_type: str, center_lon: float, center_lat: float) -> List[Tuple[float, float]]:
        """Convert metric shapes into spherical polygons (WGS 84)."""
        R = cls.R

        # Basic low-poly templates (x: meters east, y: meters north)
        shapes = {
            "rhombus": [(0, R * 1.4), (R, 0), (0, -R * 1.4), (-R, 0), (0, R * 1.4)],
            "triangle": [(0, R), (R, -R), (-R, -R), (0, R)],
            "house": [(0, R + 1), (R, R - 3), (R, -R), (-R, -R), (-R, R - 3), (0, R + 1)],
            "cup": [(-R, R), (R, R), (R, -R + 2.5), (R - 2.5, -R), (-R + 2.5, -R), (-R, -R + 2.5), (-R, R)],
            "cross": [(-2, R), (2, R), (2, 2), (R, 2), (R, -2), (2, -2), (2, -R), (-2, -R), (-2, -2), (-R, -2), (-R, 2), (-2, 2), (-2, R)],
            "toilet": [(-R, R), (R, R), (0.5, 0), (R, -R), (-R, -R), (-0.5, 0), (-R, R)],
            "transport": [(-R, R - 1), (R - 3, R - 1), (R, R - 3.0), (R, -R), (R - 1.0, -R), (R - 1.0, -R + 1.5), (R - 3.0, -R + 1.5), (R - 3.0, -R), (-R + 3.0, -R), (-R + 3.0, -R + 1.5), (-R + 1.0, -R + 1.5), (-R + 1.0, -R), (-R, -R), (-R, R - 1)],
            "shop": [(-R, R), (R, R), (R - 2.5, -R), (-R, -R), (-R, R)],
            "attraction": [(-R, R), (-2.5, R - 2.0), (0.0, R), (2.5, R - 2.0), (R, R), (R, -R), (-R, -R), (-R, R)],
            "bicycle": [(-7.5, 1.5), (-5.25, 4.0), (-1.5, 4.0), (0.0, 1.5), (1.5, 4.0), (5.25, 4.0), (7.5, 1.5), (7.5, -1.5), (5.25, -4.0), (1.5, -4.0), (0.0, -1.5), (-1.5, -4.0), (-5.25, -4.0), (-7.5, -1.5), (-7.5, 1.5)],
            "shower": [(0.0, R), (5, 1.5), (-0.75, 1.5), (-0.75, -R), (-5, -R), (-5, 1.5), (0.0, R)],
            "barrier": [(0.0, 1.5), (R - 1.5, R), (R, R - 1.5), (1.5, 0.0), (R, -R + 1.5), (R - 1.5, -R), (0.0, -1.5), (-R + 1.5, -R), (-R, -R + 1.5), (-1.5, 0.0), (-R, R - 1.5), (-R + 1.5, R), (0.0, 1.5)],
        }

        # Add octagon template (regular octagon approx. radius R)
        if "octagon" not in shapes:
            # Generate regular octagon vertices in meters
            octagon = []
            for i in range(8):
                ang = math.pi * 2 * i / 8.0
                x = R * math.cos(ang)
                y = R * math.sin(ang)
                octagon.append((x, y))
            octagon.append(octagon[0])
            shapes["octagon"] = octagon

        rel_coords = shapes.get(shape_type, shapes["rhombus"])
        points: List[Tuple[float, float]] = []
        lat_rad = math.radians(center_lat)
        cos_lat = math.cos(lat_rad)

        for x_offset, y_offset in rel_coords:
            y_offset_stretched = y_offset * cls.PERSPECTIVE_Y_MULTIPLIER
            d_lat = (y_offset_stretched / cls.EARTH_RADIUS) * (180.0 / math.pi)
            d_lon = (x_offset / (cls.EARTH_RADIUS * cos_lat)) * (180.0 / math.pi)
            points.append((center_lon + d_lon, center_lat + d_lat))

        return points


def is_clockwise(points: List[Tuple[float, float]]) -> bool:
    """Check ring orientation using signed area.

    Accepts sequence of (lon, lat) tuples. Works with closed rings (first == last) or open rings.
    Returns True if ring is clockwise (CW winding), False otherwise.
    """
    if not points:
        return False

    total = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += (x1 * y2 - x2 * y1)

    # Negative signed area indicates clockwise in the original implementation
    return total < 0.0
