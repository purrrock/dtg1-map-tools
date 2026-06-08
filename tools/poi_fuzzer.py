#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 POI Layer Calibration Grid Generator
==========================================
Генерирует регулярную сетку точек OSM (node) для проверки 
аппаратной поддержки рендеринга POI-объектов платформой ATS3085S.
Данные извлекаются динамически из таблицы features_factory.csv.
"""

import csv
import math
import sys
import os

# Целевые координаты (центр сетки тестирования)
LAT_CENTER = 53.7135
LON_CENTER = 28.4194

# Шаг сетки
STEP_METERS = 10

def get_poi_features(csv_path="features_factory.csv"):
    pois = []
    if not os.path.exists(csv_path):
        print(f"[-] Файл {csv_path} не найден.")
        sys.exit(1)
        
    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter=';')
        next(reader, None)
        
        for row in reader:
            if len(row) < 6:
                continue
                
            layer = row[4].strip()
            if layer == 'pois':
                fclass = row[1].strip()
                osm_tags = row[5].strip()
                
                if '=' in osm_tags:
                    k, v = osm_tags.split('=', 1)
                    pois.append({'fclass': fclass, 'k': k.strip(), 'v': v.strip()})
                    
    return pois

def generate_poi_grid(filename="poi_calibration.osm", csv_path="features_factory.csv"):
    features = get_poi_features(csv_path)
    total_features = len(features)
    
    if total_features == 0:
        print("[-] Ошибка: Не найдено объектов слоя POI в конфигурации.")
        return

    # Вычисление размера квадратной матрицы
    grid_size = math.ceil(math.sqrt(total_features))
    
    # Аппроксимация перевода метров в градусы (Earth radius ~ 6378 km)
    # 1 градус широты ~ 111320 метров
    lat_step_deg = STEP_METERS / 111320.0
    lon_step_deg = STEP_METERS / (111320.0 * math.cos(math.radians(LAT_CENTER)))

    osm_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="DTG1_POI_Fuzzer">',
        f'  <bounds minlat="{LAT_CENTER - lat_step_deg}" minlon="{LON_CENTER - lon_step_deg}" maxlat="{LAT_CENTER + lat_step_deg * grid_size}" maxlon="{LON_CENTER + lon_step_deg * grid_size}"/>'
    ]

    # Генерация отрицательных ID для валидности XML (стандарт для новых объектов в OSM)
    node_id = -1
    
    for idx, feature in enumerate(features):
        row = idx // grid_size
        col = idx % grid_size
        
        # Смещение координаты для текущего узла сетки
        current_lat = LAT_CENTER + (row * lat_step_deg)
        current_lon = LON_CENTER + (col * lon_step_deg)
        
        osm_lines.append(f'  <node id="{node_id}" lat="{current_lat:.7f}" lon="{current_lon:.7f}" version="1">')
        osm_lines.append(f'    <tag k="{feature["k"]}" v="{feature["v"]}"/>')
        osm_lines.append(f'    <tag k="name" v="{feature["fclass"]}"/>')
        osm_lines.append('  </node>')
        
        node_id -= 1

    osm_lines.append('</osm>')

    with open(filename, 'w', encoding='utf-8') as f:
        f.write("\n".join(osm_lines))
        
    print(f"[+] Сгенерирована матрица POI {grid_size}x{grid_size}.")
    print(f"[+] Всего размещено тестовых маркеров: {total_features}.")
    print(f"[+] Файл успешно сохранен как: {filename}")

if __name__ == "__main__":
    generate_poi_grid()