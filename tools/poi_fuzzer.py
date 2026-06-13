#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import datetime

# ==========================================
# КОНСТАНТЫ ГЕОМЕТРИИ И КООРДИНАТ
# ==========================================
# Базовые координаты (левый верхний угол стартовой сетки)
LAT_CENTER = 53.70509
LON_CENTER = 28.419233

# Радиус Земли (модель WGS 84, экваториальный радиус)
EARTH_RADIUS = 6378137.0

# Расстояние между центрами точек в матрице
SPACING_METERS = 20.0

OUTPUT_FILENAME = "points_test_grid_v1.osm"

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def meters_to_lat_delta(meters):
    # Преобразование линейного смещения (метры) в угловое (градусы широты)
    return (meters / EARTH_RADIUS) * (180.0 / math.pi)

def meters_to_lon_delta(meters, latitude):
    # Преобразование линейного смещения (метры) в угловое (градусы долготы).
    # Используется косинус текущей широты для компенсации схождения меридианов к полюсам.
    lat_rad = math.radians(latitude)
    return (meters / (EARTH_RADIUS * math.cos(lat_rad))) * (180.0 / math.pi)

# ==========================================
# ОПРЕДЕЛЕНИЕ ТЕГОВ ТОЧЕК (POI)
# ==========================================

def get_poi_definitions():
    # Возвращает список словарей. Каждый словарь соответствует одному узлу (Node).
    # Ключи и значения словаря напрямую транслируются в <tag k="..." v="..."/>.
    return [
        {"natural": "saddle"},
        {"tourism": "camp_site"},
        {"tourism": "hotel"},
        {"amenity": "restaurant"},
        {"amenity": "pharmacy"},
        {"amenity": "toilets"},
        {"railway": "station"},
        {"shop": "supermarket"},
        {"tourism": "attraction"},
        {"shop": "bicycle"},
        {"barrier": "gate", "access": "private"}, # Мультитегирование для одного узла
        {"amenity": "shower"}                     # Точка Shower (замыкает матрицу 3x4)
    ]

# ==========================================
# ГЕНЕРАЦИЯ OSM XML 
# ==========================================

def generate_points_osm():
    # Генерация временной метки по стандарту ISO 8601 для соответствия спецификации OSM
    timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    poi_defs = get_poi_definitions()
    
    node_id = 1
    
    xml_header = f"""<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="POIFuzzer_v1" timestamp="{timestamp}">
  <bounds minlat="{LAT_CENTER - 0.1}" minlon="{LON_CENTER - 0.1}" maxlat="{LAT_CENTER + 0.1}" maxlon="{LON_CENTER + 0.1}"/>
"""
    nodes_xml = ""
    
    for i, tags in enumerate(poi_defs):
        # Матричное проецирование: сетка по 4 точки в ряду
        row = i // 4  
        col = i % 4   
        
        # Расчет смещения в метрах.
        # Ось Y инвертирована (-row) для корректной отрисовки сверху-вниз на стандартной карте.
        north_offset_meters = -row * SPACING_METERS 
        east_offset_meters = col * SPACING_METERS
        
        node_lat = LAT_CENTER + meters_to_lat_delta(north_offset_meters)
        node_lon = LON_CENTER + meters_to_lon_delta(east_offset_meters, LAT_CENTER)
        
        # Формирование блока <node>
        nodes_xml += f'  <node id="{node_id}" lat="{node_lat:.7f}" lon="{node_lon:.7f}" timestamp="{timestamp}" version="1">\n'
        
        # Внедрение тегов внутрь элемента узла
        for k, v in tags.items():
            nodes_xml += f'    <tag k="{k}" v="{v}"/>\n'
            
        nodes_xml += '  </node>\n'
        
        node_id += 1

    xml_footer = "</osm>\n"
    
    # Запись дампа памяти структур в файл
    with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
        f.write(xml_header)
        f.write(nodes_xml)
        f.write(xml_footer)

if __name__ == "__main__":
    generate_points_osm()