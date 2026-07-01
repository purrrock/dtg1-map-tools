#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Geometry helpers for DTG1 map compiler.
Optimized with pure Python DP and array.array structures.
"""

import math
import array

EARTH_RADIUS = 6378137.0
PERSPECTIVE_Y_MULTIPLIER = 1.5
R = 4.1

class POIGeometryFactory:
    EARTH_RADIUS = EARTH_RADIUS
    R = R
    PERSPECTIVE_Y_MULTIPLIER = PERSPECTIVE_Y_MULTIPLIER

    @classmethod
    def generate_polygon(cls, shape_type: str, center_lon_scaled: int, center_lat_scaled: int) -> array.array:
        """Convert metric shapes into spherical polygons (WGS 84). Returns scaled array.array."""
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

        rel_coords = shapes.get(shape_type, shapes["rhombus"])
        lon, lat = center_lon_scaled * 1e-6, center_lat_scaled * 1e-6
        earth_rad_cos = cls.EARTH_RADIUS * max(abs(math.cos(math.radians(lat))), 1e-10)
        
        arr = array.array('i')
        for x, y in rel_coords:
            arr.extend((
                int((lon + (x / earth_rad_cos) * (180.0 / math.pi)) * 1e6),
                int((lat + ((y * cls.PERSPECTIVE_Y_MULTIPLIER) / cls.EARTH_RADIUS) * (180.0 / math.pi)) * 1e6)
            ))
        return arr

def is_clockwise(arr: array.array) -> bool:
    """Check ring orientation using signed area."""
    if not arr or len(arr) < 6:
        return False

    area = 0.0
    x1, y1 = arr[0], arr[1]
    for i in range(2, len(arr), 2):
        x2, y2 = arr[i], arr[i+1]
        area += (x1 * y2 - x2 * y1)
        x1, y1 = x2, y2
        
    return area < 0.0

def reverse_array_inplace(arr: array.array) -> None:
    """Reverse point pairs in-place."""
    n = len(arr)
    for i in range(0, n // 2, 2):
        j = n - 2 - i
        arr[i], arr[j], arr[i+1], arr[j+1] = arr[j], arr[i], arr[j+1], arr[i+1]

def douglas_peucker_gpx(arr: array.array, epsilon: float) -> array.array:
    """Pure Python DP for user provided raw GPX tracks."""
    n = len(arr) // 2
    if n <= 2: return arr
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    eps_sq = float(epsilon * epsilon)
    
    while stack:
        start, end = stack.pop()
        p1x, p1y = float(arr[start*2]), float(arr[start*2+1])
        p2x, p2y = float(arr[end*2]), float(arr[end*2+1])
        dx, dy = p2x - p1x, p2y - p1y
        max_dist_sq, max_idx = -1.0, -1
        
        if dx == 0.0 and dy == 0.0:
            for i in range(start + 1, end):
                vx, vy = float(arr[i*2]) - p1x, float(arr[i*2+1]) - p1y
                dist_sq = vx*vx + vy*vy
                if dist_sq > max_dist_sq: max_dist_sq, max_idx = dist_sq, i
            if max_dist_sq > eps_sq:
                keep[max_idx] = True
                stack.extend(((start, max_idx), (max_idx, end)))
        else:
            eps_sq_len = eps_sq * (dx*dx + dy*dy)
            for i in range(start + 1, end):
                vx, vy = float(arr[i*2]) - p1x, float(arr[i*2+1]) - p1y
                cross = dy * vx - dx * vy
                cross_sq = cross * cross
                if cross_sq > max_dist_sq: max_dist_sq, max_idx = cross_sq, i
            if max_dist_sq > eps_sq_len:
                keep[max_idx] = True
                stack.extend(((start, max_idx), (max_idx, end)))
            
    out_arr = array.array('i')
    for i in range(n):
        if keep[i]: out_arr.extend((arr[i*2], arr[i*2+1]))
    return out_arr