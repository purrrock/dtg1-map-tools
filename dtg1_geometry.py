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
        cls, shape_type: str, center_lon: int, center_lat: int
    ) -> List[Tuple[int, int]]:
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
            # T-образная форма (Футболка). 11 вершин (с замыкающей).
            "tshirt": [
                (-1.5, R),                # 1. Левый край воротника
                (1.5, R),                 # 2. Правый край воротника
                (R, R - 1.5),             # 3. Правое плечо
                (R - 1.0, R - 4.0),       # 4. Низ правого рукава
                (2.0, R - 3.0),           # 5. Правая подмышка
                (2.0, -R),                # 6. Правый нижний край
                (-2.0, -R),               # 7. Левый нижний край
                (-2.0, R - 3.0),          # 8. Левая подмышка
                (-R + 1.0, R - 4.0),      # 9. Низ левого рукава
                (-R, R - 1.5),            # 10. Левое плечо
                (-1.5, R)                 # 11. Замыкание (возврат к 1)
            ],
            # Асимметричный молоток.
            # 10 вершин (с замыкающей).
            "hammer": [
                (-1.0, R),              # 1. Верхний левый край бойка
                (3.0, R),               # 2. Верхний правый край бойка
                (3.0, R - 3.0),         # 3. Нижний правый край бойка
                (1.0, R - 3.0),         # 4. Внутренний правый угол
                (1.0, -R),              # 5. Правый низ рукоятки
                (-1.0, -R),             # 6. Левый низ рукоятки
                (-1.0, R - 3.0),        # 7. Внутренний левый угол
                (-R, R - 3.0),          # 8. Низ левого выступа (гвоздодер)
                (-R, R - 1.0),          # 9. Излом гвоздодера
                (-1.0, R)               # 1. Замыкание (диагональный срез)
            ],

            # Асимметричная горная гряда (Outdoor / Горы). Правый пик ниже левого.
            # 6 вершин (с замыкающей).
            "mountain": [
                (-R, -R), (-R / 2, R), (0, 0), (R / 2, R - 1.5),
                (R, -R), (-R, -R)
            ],
            # Единый полигон монитора и подставки. 10 вершин.
            "computer": [
                (-R, R), (R, R), (R, -R + 3.0), (1.0, -R + 3.0),
                (2.0, -R + 1.0), (2.0, -R), (-2.0, -R), (-2.0, -R + 1.0),
                (-1.0, -R + 3.0), (-R, -R + 3.0), (-R, R)
            ],
            # Самолет (Аэропорт). 13 вершин (с замыкающей).
            "airplane": [
                (0, R),               # 1. Нос
                (1.0, 1.0),           # 2. Правый борт (перед крылом)
                (R, 0.0),             # 3. Верхний правый конец крыла
                (R, -1.0),            # 4. Нижний правый конец крыла (прямой срез)
                (1.0, -1.0),          # 5. Правый борт (за крылом, горизонтальная кромка)
                (2.0, -R),            # 6. Правый край хвостового оперения
                (0, -R + 0.5),        # 7. Вырез в хвосте (центр)
                (-2.0, -R),           # 8. Левый край хвостового оперения
                (-1.0, -1.0),         # 9. Левый борт (за крылом)
                (-R, -1.0),           # 10. Нижний левый конец крыла
                (-R, 0.0),            # 11. Верхний левый конец крыла (прямой срез)
                (-1.0, 1.0),          # 12. Левый борт (перед крылом)
                (0, R)                # 1. Нос (замыкание)
            ],
            # Стилизованная бензоколонка со шлангом (АЗС). 10 вершин.
            "fuel": [
                (-1.5, R), (1.5, R), (1.5, 1.0), (R, 1.0),
                (R, -1.0), (2.5, -1.0), (2.5, 0.0), (1.5, 0.0),
                (1.5, -R), (-1.5, -R), (-1.5, R)
            ],
            # Стилизованный знак доллара "S" с вертикальным штрихом (Банк / Банкомат).
            # Тонкие линии. Нижняя линия S опущена (-2.5) для визуального баланса.
            # 21 вершина (с замыкающей).
            "dollar": [
                (-0.5, R),            # 1. Верхний левый край верхнего штриха
                (0.5, R),             # 2. Верхний правый край верхнего штриха
                (0.5, 3.0),           # 3. Внутренний угол перехода к верхней перекладине
                (2.0, 3.0),           # 4. Верхний правый край буквы S
                (2.0, 2.0),           # 5. Нижний правый край верхней перекладины
                (-1.0, 2.0),          # 6. Внутренний верхний вырез (вдоль верхней перекладины влево)
                (-1.0, 0.5),          # 7. Внутренний верхний вырез (вниз к средней перекладине)
                (2.0, 0.5),           # 8. Верхний правый край средней перекладины
                (2.0, -3.0),          # 9. Правый нижний край буквы S (линия опущена)
                (0.5, -3.0),          # 10. Внутренний правый угол перехода к нижнему штриху
                (0.5, -R),            # 11. Правый нижний край нижнего штриха
                (-0.5, -R),           # 12. Левый нижний край нижнего штриха
                (-0.5, -3.0),         # 13. Внутренний левый угол перехода от штриха к S
                (-2.0, -3.0),         # 14. Левый нижний край буквы S (линия опущена)
                (-2.0, -2.0),         # 15. Верхний левый край нижней перекладины
                (1.0, -2.0),          # 16. Внутренний нижний вырез (вдоль нижней перекладины вправо)
                (1.0, -0.5),           # 17. Внутренний нижний вырез (вверх к средней перекладине)
                (-2.0, -0.5),          # 18. Нижний левый край средней перекладины
                (-2.0, 3.0),          # 19. Левый верхний край буквы S
                (-0.5, 3.0),          # 20. Внутренний левый угол перехода к верхнему штриху
                (-0.5, R)             # 1. Возврат к началу (замыкание контура)
            ],
            # Щит (Полиция, Пожарная часть). 7 вершин.
            "shield": [
                (0.0, R), (R, R - 1.0), (R, -1.0), (0.0, -R),
                (-R, -1.0), (-R, R - 1.0), (0.0, R)
            ],
        }

        rel_coords = shapes.get(shape_type, shapes["rhombus"])
        points: List[Tuple[int, int]] = []

        # Convert fixed-point int coordinates back to float degrees for math
        center_lat_float = center_lat / 1000000.0
        center_lon_float = center_lon / 1000000.0

        lat_rad = radians(center_lat_float)
        cos_lat = cos(lat_rad)

        for x_offset, y_offset in rel_coords:
            y_offset_stretched = y_offset * cls.PERSPECTIVE_Y_MULTIPLIER
            d_lat = (y_offset_stretched / cls.EARTH_RADIUS) * (180.0 / pi)
            d_lon = (x_offset / (cls.EARTH_RADIUS * cos_lat)) * (180.0 / pi)

            # Scale back to fixed-point int
            points.append((int((center_lon_float + d_lon) * 1000000), int((center_lat_float + d_lat) * 1000000)))

        return points


def is_clockwise(points: List[Tuple[int, int]]) -> bool:
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
