#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Linear Map Fuzzer (CLI Version)
=====================================
Генерирует вертикальный столбец горизонтальных линий.
Динамически подтягивает значения fclass из road_codes.csv 
для формирования корректных тегов <tag k="highway" v="..." />.
"""

import argparse
import csv
import os
import xml.etree.ElementTree as ET
from xml.dom import minidom

LAT_CENTER = 53.7135
LON_CENTER = 28.4194

LAT_STEP = 0.0005
LON_LENGTH = 0.015

def load_road_codes(csv_path="road_codes.csv"):
    """
    Парсит словарь кодов Geofabrik.
    Возвращает словарь: {5111: 'motorway', 5112: 'trunk', ...}
    """
    codes_map = {}
    if not os.path.exists(csv_path):
        print(f"[-] Предупреждение: Файл {csv_path} не найден. Используются fallback-значения.")
        return codes_map

    with open(csv_path, mode='r', encoding='utf-8') as f:
        # Используем DictReader для привязки к заголовкам столбцов
        reader = csv.DictReader(f)
        for row in reader:
            code_str = row.get('Code', '').strip()
            fclass = row.get('fclass', '').strip()
            
            # Пропускаем группирующие строки (например, "511x") и пустые
            if code_str.isdigit() and fclass:
                codes_map[int(code_str)] = fclass
                
    print(f"[>] Загружено {len(codes_map)} уникальных кодов из {csv_path}")
    return codes_map

def generate_linear_fuzzer(start_code, count, out_file, codes_map):
    print(f"[>] Генерация столбца из {count} линий, начиная с кода {start_code}...")
    osm = ET.Element('osm', version='0.6', generator='dtg1_cli_fuzzer')
    node_id = -1 
    
    current_lat = LAT_CENTER + ((count / 2) * LAT_STEP)
    lon_start = LON_CENTER - (LON_LENGTH / 2)
    lon_end = LON_CENTER + (LON_LENGTH / 2)

    for i in range(count):
        current_code = start_code + i
        
        # Получаем легитимный тег из CSV. 
        # Если кода нет в базе (при жестком фаззинге), помечаем как unknown.
        actual_fclass = codes_map.get(current_code, "unknown")
        
        n1_id = str(node_id); node_id -= 1
        n2_id = str(node_id); node_id -= 1
        
        ET.SubElement(osm, 'node', id=n1_id, lat=str(current_lat), lon=str(lon_start), version="1")
        ET.SubElement(osm, 'node', id=n2_id, lat=str(current_lat), lon=str(lon_end), version="1")
        
        way = ET.SubElement(osm, 'way', id=str(current_code), version="1")
        ET.SubElement(way, 'nd', ref=n1_id)
        ET.SubElement(way, 'nd', ref=n2_id)
        
        # Записываем правильный тег highway на основе словаря road_codes.csv
        ET.SubElement(way, 'tag', k='highway', v=actual_fclass)
        ET.SubElement(way, 'tag', k='name', v=f"{actual_fclass} ({current_code})")
        
        current_lat -= LAT_STEP

    print("[>] Форматирование XML...")
    xmlstr = minidom.parseString(ET.tostring(osm)).toprettyxml(indent="  ")
    
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(xmlstr)
        
    print(f"[+] Успешно! Файл {out_file} сохранен.")

def main():
    parser = argparse.ArgumentParser(description="Генератор тестовых OSM карт для DT G1")
    parser.add_argument("-s", "--start", type=int, required=True, 
                        help="Стартовый код дороги (например, 5110)")
    parser.add_argument("-c", "--count", type=int, required=True, 
                        help="Количество генерируемых линий")
    parser.add_argument("-o", "--out", type=str, default="map.osm", 
                        help="Имя выходного файла (по умолчанию map.osm)")
    parser.add_argument("--csv", type=str, default="road_codes.csv", 
                        help="Путь к файлу словаря кодов Geofabrik")
    
    args = parser.parse_args()
    
    # Загружаем словарь перед генерацией
    codes_map = load_road_codes(args.csv)
    generate_linear_fuzzer(args.start, args.count, args.out, codes_map)

if __name__ == "__main__":
    main()