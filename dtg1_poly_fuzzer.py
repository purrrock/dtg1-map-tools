#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Polygon Fuzzer (Landuse Matrix)
=====================================
Генерирует регулярную сетку квадратов 4x4. Каждый квадрат является 
замкнутым контуром (Closed Ring) с уникальным тегом землепользования.
"""

import math

LAT_CENTER = 53.7135
LON_CENTER = 28.4194

# Извлеченные полигоны из features.csv (16 штук для ровной матрицы 4x4)
LANDUSE_CLASSES = [
    "forest", "park", "residential", "industrial", 
    "cemetery", "allotments", "meadow", "commercial", 
    "nature_reserve", "recreation_ground", "retail", "military", 
    "quarry", "orchard", "vineyard", "scrub"
]

def main():
    METER_PER_LAT = 111320.0
    METER_PER_LON = 111320.0 * math.cos(math.radians(LAT_CENTER))

    SQUARE_SIZE = 30.0  # Размер каждого полигона 30x30 метров
    STEP = 50.0         # Шаг сетки 50 метров (между центрами)

    lat_step_deg = STEP / METER_PER_LAT
    lon_step_deg = STEP / METER_PER_LON

    half_sq_lat = (SQUARE_SIZE / 2.0) / METER_PER_LAT
    half_sq_lon = (SQUARE_SIZE / 2.0) / METER_PER_LON

    osm_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="dtg1_poly_fuzzer">']
    
    node_id = 1
    way_id = 3000

    print("[>] Сборка полигональной матрицы (4x4)...")

    for i, fclass in enumerate(LANDUSE_CLASSES):
        row = i // 4
        col = i % 4

        # Вычисляем центр текущего квадрата
        cy = LAT_CENTER + ((row - 1.5) * lat_step_deg)
        cx = LON_CENTER + ((col - 1.5) * lon_step_deg)

        # Координаты вершин
        min_lat, max_lat = cy - half_sq_lat, cy + half_sq_lat
        min_lon, max_lon = cx - half_sq_lon, cx + half_sq_lon

        # 1. Юго-Запад
        osm_lines.append(f'  <node id="{node_id}" lat="{min_lat:.7f}" lon="{min_lon:.7f}" version="1"/>')
        n1 = node_id; node_id += 1
        # 2. Северо-Запад
        osm_lines.append(f'  <node id="{node_id}" lat="{max_lat:.7f}" lon="{min_lon:.7f}" version="1"/>')
        n2 = node_id; node_id += 1
        # 3. Северо-Восток
        osm_lines.append(f'  <node id="{node_id}" lat="{max_lat:.7f}" lon="{max_lon:.7f}" version="1"/>')
        n3 = node_id; node_id += 1
        # 4. Юго-Восток
        osm_lines.append(f'  <node id="{node_id}" lat="{min_lat:.7f}" lon="{max_lon:.7f}" version="1"/>')
        n4 = node_id; node_id += 1

        # Формирование замкнутого полигона (последний узел равен первому)
        osm_lines.append(f'  <way id="{way_id}" version="1">')
        osm_lines.append(f'    <nd ref="{n1}"/>')
        osm_lines.append(f'    <nd ref="{n2}"/>')
        osm_lines.append(f'    <nd ref="{n3}"/>')
        osm_lines.append(f'    <nd ref="{n4}"/>')
        osm_lines.append(f'    <nd ref="{n1}"/>') # ЗАМЫКАНИЕ
        osm_lines.append(f'    <tag k="landuse" v="{fclass}"/>')
        osm_lines.append(f'    <tag k="name" v="{fclass}"/>')
        osm_lines.append('  </way>')
        way_id += 1

    osm_lines.append('</osm>')

    with open("map.osm", "w", encoding="utf-8") as f:
        f.write("\n".join(osm_lines))

    print(f"[+] Сгенерирован map.osm ({len(LANDUSE_CLASSES)} полигонов).")

if __name__ == "__main__":
    main()