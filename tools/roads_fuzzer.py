#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Roads Layer Calibration Grid Generator (v2.0)
===================================================
Генерирует тестовую сетку OSM для проверки аппаратной поддержки рендеринга 
типов дорог (толщина линий, цвета) графическим конвейером ATS3085S.

Изменения архитектуры:
- Шаг сетки (STEP_METERS) снижен до 5 метров для оптимизации под площадь экрана.
- Массив векторов разделен на горизонтальный и вертикальный блоки для 
  формирования решетки (Lattice). Это позволяет протестировать аппаратный 
  Anti-Aliasing на перекрестках.
"""

import csv
import math
import sys
import os

# Базовые координаты (центр тестового полигона)
LAT_CENTER = 53.714055
LON_CENTER = 28.420172

# Физические параметры сетки
STEP_METERS = 5          # Шаг между параллельными векторами
LINE_LENGTH_METERS = 200 # Длина каждого отрезка дороги

def get_road_features(csv_path="features_factory.csv"):
    roads = []
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
            if layer == 'roads':
                fclass = row[1].strip()
                osm_tags_raw = row[5].strip()
                
                tags = {}
                for tag_pair in osm_tags_raw.split(','):
                    if '=' in tag_pair:
                        k, v = tag_pair.split('=', 1)
                        tags[k.strip()] = v.strip()
                
                if tags:
                    roads.append({'fclass': fclass, 'tags': tags})
                    
    return roads

def generate_roads_grid(filename="roads_calibration.osm", csv_path="features_factory.csv"):
    features = get_road_features(csv_path)
    total_features = len(features)
    
    if total_features == 0:
        print("[-] Ошибка: Не найдено объектов слоя 'roads'.")
        return

    # Разделение массива: половина по оси Y (горизонтальные векторы), половина по оси X (вертикальные)
    half_features = total_features // 2

    # Аппроксимация проекции (R_earth ~ 6378 км, 1 градус ~ 111320 м)
    lat_step_deg = STEP_METERS / 111320.0
    lon_step_deg = STEP_METERS / (111320.0 * math.cos(math.radians(LAT_CENTER)))
    
    lat_length_deg = LINE_LENGTH_METERS / 111320.0
    lon_length_deg = LINE_LENGTH_METERS / (111320.0 * math.cos(math.radians(LAT_CENTER)))

    # Вычисление BBox для XML-заголовка
    max_lat = LAT_CENTER + max(half_features * lat_step_deg, lat_length_deg)
    max_lon = LON_CENTER + max((total_features - half_features) * lon_step_deg, lon_length_deg)

    osm_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="DTG1_Roads_Fuzzer_v2">',
        f'  <bounds minlat="{LAT_CENTER}" minlon="{LON_CENTER}" maxlat="{max_lat}" maxlon="{max_lon}"/>'
    ]

    # Отрицательные ID для исключения конфликтов в компиляторе
    node_id = -1
    way_id = -1
    
    for idx, feature in enumerate(features):
        if idx < half_features:
            # Горизонтальные линии: фиксированный Y (с шагом), X меняется от 0 до Length
            start_lat = LAT_CENTER + (idx * lat_step_deg)
            end_lat = start_lat
            start_lon = LON_CENTER
            end_lon = LON_CENTER + lon_length_deg
        else:
            # Вертикальные линии: фиксированный X (с шагом), Y меняется от 0 до Length
            vert_idx = idx - half_features
            start_lat = LAT_CENTER
            end_lat = LAT_CENTER + lat_length_deg
            start_lon = LON_CENTER + (vert_idx * lon_step_deg)
            end_lon = start_lon
        
        # Генерация узлов
        osm_lines.append(f'  <node id="{node_id}" lat="{start_lat:.7f}" lon="{start_lon:.7f}" version="1"/>')
        start_node_id = node_id
        node_id -= 1
        
        osm_lines.append(f'  <node id="{node_id}" lat="{end_lat:.7f}" lon="{end_lon:.7f}" version="1"/>')
        end_node_id = node_id
        node_id -= 1
        
        # Сборка геометрии Way
        osm_lines.append(f'  <way id="{way_id}" version="1">')
        osm_lines.append(f'    <nd ref="{start_node_id}"/>')
        osm_lines.append(f'    <nd ref="{end_node_id}"/>')
        
        # Инъекция атрибутов из конфигурации
        for k, v in feature['tags'].items():
            osm_lines.append(f'    <tag k="{k}" v="{v}"/>')
            
        osm_lines.append(f'    <tag k="name" v="{feature["fclass"]}"/>')
        osm_lines.append('  </way>')
        way_id -= 1

    osm_lines.append('</osm>')

    with open(filename, 'w', encoding='utf-8') as f:
        f.write("\n".join(osm_lines))
        
    print(f"[+] Сгенерирована калибровочная решетка дорог: {total_features} типов.")
    print(f"    - Горизонтальных векторов: {half_features}")
    print(f"    - Вертикальных векторов: {total_features - half_features}")
    print(f"    - Параметры: шаг {STEP_METERS}м, длина {LINE_LENGTH_METERS}м.")
    print(f"[+] Файл успешно сохранен как: {filename}")

if __name__ == "__main__":
    generate_roads_grid()