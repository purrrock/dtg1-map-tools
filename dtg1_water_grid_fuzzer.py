#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Water Layer Calibration Grid Generator
============================================
Генерирует регулярную сетку полигонов OSM для проверки 
аппаратных стилей (Feature Codes) гидрологических объектов (серия 82xx).
"""

import math

# Целевые координаты (центр полигона тестирования)
LAT_CENTER = 53.7135
LON_CENTER = 28.4194

# Перечень объектов слоя "water" (на базе Geofabrik Section 6.5 / 6.6)
# Эти теги транслируются в серию 82xx (8211, 8212, 8221 и т.д.)
WATER_FEATURES = [
    {"natural": "water"},               # Базовая вода (8211)
    {"landuse": "reservoir"},           # Водохранилище (8212)
    {"waterway": "riverbank"},          # Русло реки (8213)
    {"natural": "glacier"},             # Ледник (8214)
    {"natural": "wetland"},             # Болото/заболоченность (8221)
    {"natural": "bay"},                 # Залив
    {"natural": "water", "water": "lake"},  # Озеро (уточнение)
    {"natural": "water", "water": "river"}, # Река (уточнение)
    {"landuse": "basin"}                # Бассейн
]

def generate_calibration_grid(filename="water_calibration.osm"):
    # Перевод градусов в метры для проекции Меркатора
    METER_PER_LAT = 111320.0
    METER_PER_LON = 111320.0 * math.cos(math.radians(LAT_CENTER))
    
    # Геометрия тестового полигона
    SIZE_M = 50.0  # Размер квадрата 100х100 метров
    GAP_M = 25.0    # Отступ между квадратами 50 метров
    COLS = 3        # Количество колонок в матрице
    
    osm_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="dtg1_water_grid_fuzzer">'
    ]
    
    node_id = 1000
    way_id = 4000
    
    for idx, tags in enumerate(WATER_FEATURES):
        row = idx // COLS
        col = idx % COLS
        
        # Вычисление смещения от центра
        offset_lat_m = -row * (SIZE_M + GAP_M)
        offset_lon_m = col * (SIZE_M + GAP_M)
        
        base_lat = LAT_CENTER + (offset_lat_m / METER_PER_LAT)
        base_lon = LON_CENTER + (offset_lon_m / METER_PER_LON)
        
        d_lat = SIZE_M / METER_PER_LAT
        d_lon = SIZE_M / METER_PER_LON
        
        # Вершины квадрата (обход по часовой стрелке)
        nodes_coords = [
            (base_lat, base_lon),                   # Нижний левый
            (base_lat + d_lat, base_lon),           # Верхний левый
            (base_lat + d_lat, base_lon + d_lon),   # Верхний правый
            (base_lat, base_lon + d_lon)            # Нижний правый
        ]
        
        current_node_ids = []
        
        # Генерация XML <node>
        for lat, lon in nodes_coords:
            osm_lines.append(f'  <node id="{node_id}" lat="{lat:.7f}" lon="{lon:.7f}" version="1"/>')
            current_node_ids.append(node_id)
            node_id += 1
            
        # Генерация XML <way>
        osm_lines.append(f'  <way id="{way_id}" version="1">')
        
        # Привязка вершин
        for nid in current_node_ids:
            osm_lines.append(f'    <nd ref="{nid}"/>')
        # Замыкание контура
        osm_lines.append(f'    <nd ref="{current_node_ids[0]}"/>') 
        
        # Внедрение тегов
        for k, v in tags.items():
            osm_lines.append(f'    <tag k="{k}" v="{v}"/>')
        
        # Генерация имени для отладки
        tag_desc = " ".join([f"{k}={v}" for k, v in tags.items()])
        osm_lines.append(f'    <tag k="name" v="Style Test: {tag_desc}"/>')
        osm_lines.append('  </way>')
        
        way_id += 1
        
    osm_lines.append('</osm>')
    
    # Физическая запись файла
    with open(filename, 'w', encoding='utf-8') as f:
        f.write('\n'.join(osm_lines))
        
    print(f"[*] Сгенерирована калибровочная сетка: {filename}")
    print(f"[*] Количество тестовых полигонов: {len(WATER_FEATURES)}")

if __name__ == '__main__':
    generate_calibration_grid()