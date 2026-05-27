#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Advanced Grid Fuzzer
==========================
Генератор калибровочной сетки (9 горизонт / 9 вертик / 10 диагоналей) 
для точной верификации аппаратных стилей и Z-Culling на платформе C175C1.
"""

import math

# Точные координаты центра полигона по запросу архитектона
LAT_CENTER = 53.7135
LON_CENTER = 28.4194

# Шаг сетки в метрах (20 метров — оптимум против визуального слияния на LCD/AMOLED)
STEP_METERS = 5.0
# Длина базовой линии (ограничиваем 300 метрами, чтобы сетка локализовалась на экране)
LINE_LENGTH_METERS = 300.0

# Строгое распределение 28 fclass из features.csv (9 + 9 + 10)
FCLASSES_HORIZONTAL = [
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "living_street", "pedestrian"
]

FCLASSES_VERTICAL = [
    "busway", "motorway_link", "trunk_link", "primary_link",
    "secondary_link", "tertiary_link", "service", "track", "track_grade1"
]

FCLASSES_DIAGONAL = [
    "track_grade2", "track_grade3", "track_grade4", "track_grade5",
    "bridleway", "cycleway", "footway", "path", "steps", "unknown"
]

def main():
    # --- ГИС МАТЕМАТИКА И ПЕРЕСЧЕТ МЕТРОВ В ГРАДУСЫ ---
    # Длина дуги 1 градуса широты на эллипсоиде WGS-84 (~111.32 км)
    METER_PER_LAT_DEGREE = 111320.0
    # Длина дуги 1 градуса долготы зависит от текущей широты (сужается к полюсу)
    METER_PER_LON_DEGREE = 111320.0 * math.cos(math.radians(LAT_CENTER))

    # Перевод метров в шаг изменения координат
    lat_step_deg = STEP_METERS / METER_PER_LAT_DEGREE
    lon_step_deg = STEP_METERS / METER_PER_LON_DEGREE

    # Полуширина охвата для длин линий
    half_span_lat = (LINE_LENGTH_METERS / 2.0) / METER_PER_LAT_DEGREE
    half_span_lon = (LINE_LENGTH_METERS / 2.0) / METER_PER_LON_DEGREE

    osm_output = []
    osm_output.append('<?xml version="1.0" encoding="UTF-8"?>')
    osm_output.append('<osm version="0.6" generator="dtg1_advanced_grid_fuzzer">')

    node_id = 1
    way_id = 2000

    print("[>] Сборка геометрического шаблона матрицы...")

    # ==========================================================================
    # 1. ГЕНЕРАЦИЯ ГОРИЗОНТАЛЬНЫХ ЛИНИЙ (9 штук)
    # ==========================================================================
    # Распределяем симметрично: 4 линии выше центра, 1 по центру, 4 ниже
    for idx, fclass in enumerate(FCLASSES_HORIZONTAL):
        offset_multiplier = idx - 4  # Диапазон от -4 до +4
        line_lat = LAT_CENTER + (offset_multiplier * lat_step_deg)
        
        lon_start = LON_CENTER - half_span_lon
        lon_end = LON_CENTER + half_span_lon

        # Западная точка
        osm_output.append(f'  <node id="{node_id}" lat="{line_lat:.7f}" lon="{lon_start:.7f}" version="1"/>')
        n_start = node_id
        node_id += 1

        # Восточная точка
        osm_output.append(f'  <node id="{node_id}" lat="{line_lat:.7f}" lon="{lon_end:.7f}" version="1"/>')
        n_end = node_id
        node_id += 1

        # Сборка линии
        osm_output.append(f'  <way id="{way_id}" version="1">')
        osm_output.append(f'    <nd ref="{n_start}"/>')
        osm_output.append(f'    <nd ref="{n_end}"/>')
        osm_output.append(f'    <tag k="highway" v="{fclass}"/>')
        osm_output.append(f'    <tag k="name" v="{fclass}"/>')
        osm_output.append('  </way>')
        way_id += 1

    # ==========================================================================
    # 2. ГЕНЕРАЦИЯ ВЕРТИКАЛЬНЫХ ЛИНИЙ (9 штук)
    # ==========================================================================
    # Распределяем симметрично: 4 левее центра, 1 по центру, 4 правее
    for idx, fclass in enumerate(FCLASSES_VERTICAL):
        offset_multiplier = idx - 4  # Диапазон от -4 до +4
        line_lon = LON_CENTER + (offset_multiplier * lon_step_deg)
        
        lat_start = LAT_CENTER - half_span_lat
        lat_end = LAT_CENTER + half_span_lat

        # Южная точка
        osm_output.append(f'  <node id="{node_id}" lat="{lat_start:.7f}" lon="{line_lon:.7f}" version="1"/>')
        n_start = node_id
        node_id += 1

        # Северная точка
        osm_output.append(f'  <node id="{node_id}" lat="{lat_end:.7f}" lon="{line_lon:.7f}" version="1"/>')
        n_end = node_id
        node_id += 1

        # Сборка линии
        osm_output.append(f'  <way id="{way_id}" version="1">')
        osm_output.append(f'    <nd ref="{n_start}"/>')
        osm_output.append(f'    <nd ref="{n_end}"/>')
        osm_output.append(f'    <tag k="highway" v="{fclass}"/>')
        osm_output.append(f'    <tag k="name" v="{fclass}"/>')
        osm_output.append('  </way>')
        way_id += 1

    # ==========================================================================
    # 3. ГЕНЕРАЦИЯ ДИАГОНАЛЬНЫХ ЛИНИЙ (10 штук)
    # ==========================================================================
    # Сдвигаем каждую параллельную диагональ по оси долготы для четкого разделения.
    # Направление: Юго-Запад -> Северо-Восток.
    for idx, fclass in enumerate(FCLASSES_DIAGONAL):
        offset_multiplier = idx - 5  # Диапазон от -5 до +4 (ровно 10 линий)
        
        # Смещение начальной и конечной точек по X для параллельности диагоналей
        diag_lon_shift = offset_multiplier * lon_step_deg

        lat_start = LAT_CENTER - half_span_lat
        lon_start = LON_CENTER - half_span_lon + diag_lon_shift

        lat_end = LAT_CENTER + half_span_lat
        lon_end = LON_CENTER + half_span_lon + diag_lon_shift

        # Точка Юго-Запад
        osm_output.append(f'  <node id="{node_id}" lat="{lat_start:.7f}" lon="{lon_start:.7f}" version="1"/>')
        n_start = node_id
        node_id += 1

        # Point Северо-Восток
        osm_output.append(f'  <node id="{node_id}" lat="{lat_end:.7f}" lon="{lon_end:.7f}" version="1"/>')
        n_end = node_id
        node_id += 1

        # Сборка линии
        osm_output.append(f'  <way id="{way_id}" version="1">')
        osm_output.append(f'    <nd ref="{n_start}"/>')
        osm_output.append(f'    <nd ref="{n_end}"/>')
        osm_output.append(f'    <tag k="highway" v="{fclass}"/>')
        osm_output.append(f'    <tag k="name" v="{fclass}"/>')
        osm_output.append('  </way>')
        way_id += 1

    osm_output.append('</osm>')

    # Запись выходного файла
    with open("map.osm", "w", encoding="utf-8") as f:
        f.write("\n".join(osm_output))

    print(f"[+] Успех! Создан файл map.osm.")
    print(f"    - Всего точек (nodes): {node_id - 1}")
    print(f"    - Всего линий (ways):  {way_id - 2000} (9 горизонтальных, 9 вертикальных, 10 диагональных)")

if __name__ == "__main__":
    main()