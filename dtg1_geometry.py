#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Geometry helpers for DTG1 map compiler.

Contains:
- POIGeometryFactory: generator of low-polygon POI primitives
  (triangle, rhombus, cross, etc.)
- PERSPECTIVE_Y_MULTIPLIER constant (1.5) for ATS3085S compensation
- is_clockwise(points): CW winding rule checker

API mirrors original usage in dtg1_map_compiler.py:
POIGeometryFactory.generate_polygon(shape_type, center_lon, center_lat)
and is_clockwise(points) which accepts a sequence of (lon, lat) tuples
(closed or open) and returns True if ring is clockwise.
"""

from typing import List, Tuple
from math import radians, cos, pi

EARTH_RADIUS = 6378137.0
PERSPECTIVE_Y_MULTIPLIER = 1.5
R = 4.1


class POIGeometryFactory:
    """Generator of low-polygon primitives for the POI layer.

    Usage: POIGeometryFactory.generate_polygon(
        shape_type, center_lon, center_lat
    )
    center_lon/center_lat expected in degrees (WGS84).
    Returns list of (lon, lat) tuples.
    """
    EARTH_RADIUS = EARTH_RADIUS
    R = R
    PERSPECTIVE_Y_MULTIPLIER = PERSPECTIVE_Y_MULTIPLIER

    @classmethod
    def generate_polygon(
        cls, shape_type: str, center_lon: float, center_lat: float
    ) -> List[Tuple[float, float]]:
        """Convert metric shapes into spherical polygons (WGS 84)."""
        R = cls.R

        # Basic low-poly templates (x: meters east, y: meters north)
        shapes = {
            "rhombus": [
                (0, R * 1.4), (R, 0), (0, -R * 1.4), (-R, 0), (0, R * 1.4)
            ],
            "triangle": [(0, R), (R, -R), (-R, -R), (0, R)],
            "house": [
                (0, R + 1), (R, R - 3), (R, -R), (-R, -R), (-R, R - 3),
                (0, R + 1)
            ],
            "cup": [
                (-R, R), (R, R), (R, -R + 2.5), (R - 2.5, -R),
                (-R + 2.5, -R), (-R, -R + 2.5), (-R, R)
            ],
            "cross": [
                (-2, R), (2, R), (2, 2), (R, 2), (R, -2), (2, -2),
                (2, -R), (-2, -R), (-2, -2), (-R, -2), (-R, 2),
                (-2, 2), (-2, R)
            ],
            "toilet": [
                (-R, R), (R, R), (0.5, 0), (R, -R), (-R, -R), (-0.5, 0),
                (-R, R)
            ],
            "transport": [
                (-R, R - 1), (R - 3, R - 1), (R, R - 3.0), (R, -R),
                (R - 1.0, -R), (R - 1.0, -R + 1.5), (R - 3.0, -R + 1.5),
                (R - 3.0, -R), (-R + 3.0, -R), (-R + 3.0, -R + 1.5),
                (-R + 1.0, -R + 1.5), (-R + 1.0, -R), (-R, -R), (-R, R - 1)
            ],
            "shop": [(-R, R), (R, R), (R - 2.5, -R), (-R, -R), (-R, R)],
            "attraction": [
                (-R, R), (-2.5, R - 2.0), (0.0, R), (2.5, R - 2.0),
                (R, R), (R, -R), (-R, -R), (-R, R)
            ],
            "bicycle": [
                (-7.5, 1.5), (-5.25, 4.0), (-1.5, 4.0), (0.0, 1.5),
                (1.5, 4.0), (5.25, 4.0), (7.5, 1.5), (7.5, -1.5),
                (5.25, -4.0), (1.5, -4.0), (0.0, -1.5), (-1.5, -4.0),
                (-5.25, -4.0), (-7.5, -1.5), (-7.5, 1.5)
            ],
            "shower": [
                (0.0, R), (5, 1.5), (-0.75, 1.5), (-0.75, -R), (-5, -R),
                (-5, 1.5), (0.0, R)
            ],
            "barrier": [
                (0.0, 1.5), (R - 1.5, R), (R, R - 1.5), (1.5, 0.0),
                (R, -R + 1.5), (R - 1.5, -R), (0.0, -1.5), (-R + 1.5, -R),
                (-R, -R + 1.5), (-1.5, 0.0), (-R, R - 1.5), (-R + 1.5, R),
                (0.0, 1.5)
            ],
            # T-образная форма (Футболка). 8 вершин.
            "tshirt": [
                (-R, R), (R, R), (R, R - 1.5), (R - 1.5, R - 1.5), 
                (R - 1.5, -R), (-R + 1.5, -R), (-R + 1.5, R - 1.5), 
                (-R, R - 1.5), (-R, R)
            ],
            
            # Асимметричная T-образная форма (Молоток). 8 вершин.
            "hammer": [
                (-R, R), (R - 1.0, R), (R - 1.0, R - 2.0), (1.0, R - 2.0), 
                (1.0, -R), (-1.0, -R), (-1.0, R - 2.0), (-R, R - 2.0), (-R, R)
            ],
            
            # Единый контур горной гряды. 5 вершин.
            "mountain": [
                (-R, -R), (-R / 2, R), (0, 0), (R / 2, R), 
                (R, -R), (-R, -R)
            ],
            
            # Единый полигон монитора и подставки. 10 вершин.
            "computer": [
                (-R, R), (R, R), (R, -R + 3.0), (1.0, -R + 3.0), 
                (2.0, -R + 1.0), (2.0, -R), (-2.0, -R), (-2.0, -R + 1.0), 
                (-1.0, -R + 3.0), (-R, -R + 3.0), (-R, R)
            ],  
            # Контур самолета (Аэропорт). 11 вершин.
            "airplane": [
                (0, R), (0.8, 1.0), (R, -1.0), (0.8, -1.0), 
                (1.5, -R), (0, -R + 0.5), (-1.5, -R), (-0.8, -1.0), 
                (-R, -1.0), (-0.8, 1.0), (0, R)
            ],
            # Стилизованная бензоколонка со шлангом (АЗС). 10 вершин.
            "fuel": [
                (-1.5, R), (1.5, R), (1.5, 1.0), (R, 1.0), 
                (R, -1.0), (2.5, -1.0), (2.5, 0.0), (1.5, 0.0), 
                (1.5, -R), (-1.5, -R), (-1.5, R)
            ],
            # Стилизованный знак доллара "S" (Банк / Банкомат). 13 вершин (с замыкающей).
            "dollar": [
                (-2.0, R), (2.0, R), (2.0, 1.5), (-0.5, 1.5), 
                (-0.5, 0.5), (2.0, 0.5), (2.0, -R), (-2.0, -R), 
                (-2.0, -1.5), (0.5, -1.5), (0.5, -0.5), (-2.0, -0.5), 
                (-2.0, R)
            ],
            # Щит (Полиция, Пожарная часть). 7 вершин.
            "shield": [
                (0.0, R), (R, R - 1.0), (R, -1.0), (0.0, -R),
                (-R, -1.0), (-R, R - 1.0), (0.0, R)
            ],
        }

        rel_coords = shapes.get(shape_type, shapes["rhombus"])
        points: List[Tuple[float, float]] = []
        lat_rad = radians(center_lat)
        cos_lat = cos(lat_rad)

        for x_offset, y_offset in rel_coords:
            y_offset_stretched = y_offset * cls.PERSPECTIVE_Y_MULTIPLIER
            d_lat = (y_offset_stretched / cls.EARTH_RADIUS) * (180.0 / pi)
            d_lon = (x_offset / (cls.EARTH_RADIUS * cos_lat)) * (180.0 / pi)
            points.append((center_lon + d_lon, center_lat + d_lat))

        return points


def is_clockwise(points: List[Tuple[float, float]]) -> bool:
    """Check ring orientation using signed area.

    Accepts sequence of (lon, lat) tuples. Works with closed rings
    (first == last) or open rings.
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
