#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Landuse Layer Calibration Grid Generator
==============================================
Генерирует тестовую матрицу замкнутых полигонов OSM для проверки 
аппаратной поддержки рендеринга типов землепользования (цвета заливки, 
паттерны, z-index полигонов) графическим конвейером ATS3085S.

Архитектура геометрии:
- Формируется замкнутый контур (Closed Way) из 5 узлов (последний = первый).
- Обход вершин (Winding) выполняется по часовой стрелке (CW), что является 
  стандартом для внешних (Outer) полигонов в целевой архитектуре.
"""

import csv
import math
import sys
import os

# Базовые координаты (центр стартового полигона)
LAT_CENTER = 53.7135
LON_CENTER = 28.4194

# Физические параметры сетки
POLYGON_SIZE_METERS = 5  # Размер грани квадрата
GAP_METERS = 5       # Зазор между соседними полигонами

def get_landuse_features(csv_path="features_factory.csv"):
    landuse = []
    if not os.path.exists(csv_path):
        print(f"[-] Ошибка: Файл {csv_path} не найден.")
        sys.exit(1)
        
    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter=';')
        next(reader, None) # Пропуск заголовка
        
        for row in reader:
            if len(row) < 6:
                continue
                
            layer = row[4].strip()
            # Фильтруем строго слой landuse
            if layer == 'landuse':
                fclass = row[1].strip()
                osm_tags_raw = row[5].strip()
                
                tags = {}
                # Парсинг композитных тегов (разделитель - запятая)
                for tag_pair in osm_tags_raw.split(','):
                    if '=' in tag_pair:
                        k, v = tag_pair.split('=', 1)
                        tags[k.strip()] = v.strip()
                
                if tags:
                    landuse.append({'fclass': fclass, 'tags': tags})
                    
    return landuse

def generate_landuse_grid(filename="landuse_calibration.osm", csv_path="features_factory.csv"):
    features = get_landuse_features(csv_path)
    total_features = len(features)
    
    if total_features == 0:
        print("[-] Ошибка: Не найдено объектов слоя 'landuse' в конфигурации LUT.")
        return

    # Вычисление размерности квадратной матрицы
    grid_size = math.ceil(math.sqrt(total_features))
    
    # Шаг ячейки сетки (полигон + зазор)
    step_total_meters = POLYGON_SIZE_METERS + GAP_METERS

    # Аппроксимация проекции (R_earth ~ 6378 км)
    lat_factor = 111320.0
    lon_factor = 111320.0 * math.cos(math.radians(LAT_CENTER))
    
    step_lat_deg = step_total_meters / lat_factor
    step_lon_deg = step_total_meters / lon_factor
    
    poly_lat_deg = POLYGON_SIZE_METERS / lat_factor
    poly_lon_deg = POLYGON_SIZE_METERS / lon_factor

    osm_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="DTG1_Landuse_Fuzzer">',
        f'  <bounds minlat="{LAT_CENTER}" minlon="{LON_CENTER}" maxlat="{LAT_CENTER + step_lat_deg * grid_size}" maxlon="{LON_CENTER + step_lon_deg * grid_size}"/>'
    ]

    # Отрицательные ID для обхода конфликтов
    node_id = -1
    way_id = -1
    
    for idx, feature in enumerate(features):
        row = idx // grid_size
        col = idx % grid_size
        
        # Вычисляем координаты нижнего левого (Bottom-Left) угла полигона
        base_lat = LAT_CENTER + (row * step_lat_deg)
        base_lon = LON_CENTER + (col * step_lon_deg)
        
        # Вершины квадрата (обход по часовой стрелке)
        nodes_coords = [
            (base_lat, base_lon),                                # N1: Bottom-Left
            (base_lat + poly_lat_deg, base_lon),                 # N2: Top-Left
            (base_lat + poly_lat_deg, base_lon + poly_lon_deg),  # N3: Top-Right
            (base_lat, base_lon + poly_lon_deg)                  # N4: Bottom-Right
        ]
        
        current_node_ids = []
        
        # Генерация XML-узлов
        for lat, lon in nodes_coords:
            osm_lines.append(f'  <node id="{node_id}" lat="{lat:.7f}" lon="{lon:.7f}" version="1"/>')
            current_node_ids.append(node_id)
            node_id -= 1
            
        # Сборка замкнутого контура (Closed Way)
        osm_lines.append(f'  <way id="{way_id}" version="1">')
        for nid in current_node_ids:
            osm_lines.append(f'    <nd ref="{nid}"/>')
        # Замыкание на первый узел
        osm_lines.append(f'    <nd ref="{current_node_ids[0]}"/>')
        
        # Инъекция атрибутов из LUT
        for k, v in feature['tags'].items():
            osm_lines.append(f'    <tag k="{k}" v="{v}"/>')
            
        # Инъекция текстовой метки для визуальной идентификации заливки
        osm_lines.append(f'    <tag k="name" v="{feature["fclass"]}"/>')
        
        osm_lines.append('  </way>')
        way_id -= 1

    osm_lines.append('</osm>')

    with open(filename, 'w', encoding='utf-8') as f:
        f.write("\n".join(osm_lines))
        
    print(f"[+] Сгенерирована калибровочная матрица землепользования {grid_size}x{grid_size}.")
    print(f"[+] Всего сгенерировано полигонов: {total_features}.")
    print(f"[+] Параметры: размер {POLYGON_SIZE_METERS}x{POLYGON_SIZE_METERS}м, зазор {GAP_METERS}м.")
    print(f"[+] Файл успешно сохранен как: {filename}")

if __name__ == "__main__":
    generate_landuse_grid()