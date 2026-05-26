#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler: Roads MLP Generator
=======================================
Извлекает геометрию дорог из map.osm, генерирует roads.mlp 
и сохраняет метаданные в roads_meta.json для сборки IDX/DB.
"""

import os
import json
import struct
import xml.etree.ElementTree as ET

# ==============================================================================
# ЭТАЛОННЫЙ МАППИНГ ТИПОВ ДОРОГ СОГЛАСНО road_codes.csv (Geofabrik GIS Schema)
# ==============================================================================
# Каждый целочисленный код (uint32) соответствует определенному стилю линии
# в аппаратной таблице Look-Up Table (LUT) графического процессора часов C175C1.
HIGHWAY_CODES = {
    # Крупные магистрали и шоссе (Major roads - 511x)
    "motorway": 5111,
    "trunk": 5112,
    "primary": 5113,
    "secondary": 5114,
    "tertiary": 5115,
    
    # Улицы и дороги местного значения (Minor Roads - 512x)
    "unclassified": 5121,
    "residential": 5122,
    "living_street": 5123,
    "pedestrian": 5124,
    "busway": 5125,
    
    # Соединительные съезды и развязки (Highway links / sliproads / ramps - 513x)
    "motorway_link": 5131,
    "trunk_link": 5132,
    "primary_link": 5133,
    "secondary_link": 5134,
    "tertiary_link": 5135,
    
    # Внутридворовые и технологические проезды (Very small roads - 514x)
    "service": 5141,
    "track": 5142,          # Без явного указания качества покрытия (tracktype)
    "track_grade1": 5143,   # Твердое покрытие (асфальт/бетон)
    "track_grade2": 5144,   # Спрессованный грунт/гравий
    "track_grade3": 5145,   # Неустойчивый сухой грунт
    "track_grade4": 5146,   # Грунтовая дорога с колейностью
    "track_grade5": 5147,   # Труднопроходимая тропа / просека
    
    # Пешеходные и велосипедные пути (Paths unsuitable for cars - 515x)
    "bridleway": 5151,      # Дорожки для верховой езды
    "cycleway": 5152,       # Выделенные велодорожки (аппаратно скрываются на G1)
    "footway": 5153,        # Пешеходные тротуары
    "path": 5154,           # Неспецифицированные грунтовые тропы
    "steps": 5155,          # Лестницы
    
    # Резервные и неопознанные классы (Unknown / Fallback - 519x)
    "road": 5199,
    "unknown": 5199
}

# В качестве фолбека используем код 5199 (unknown), чтобы не путать 
# нераспознанные теги с легитимными сельскохозяйственными дорогами (5142).
DEFAULT_CODE = 5199
# Длина заголовка YZL (в байтах) картографических слоев платформы C175C1
YZL_SIZE = 32

def parse_osm_geometry(osm_file):
    print("[>] Проход 1: Загрузка узлов (nodes) в память...")
    nodes = {}
    context = ET.iterparse(osm_file, events=('start', 'end'))
    
    # Чтобы не съесть всю оперативную память на гигантских OSM
    for event, elem in context:
        if event == 'end' and elem.tag == 'node':
            nodes[elem.attrib['id']] = (float(elem.attrib['lon']), float(elem.attrib['lat']))
            elem.clear()
            
    print(f"    Загружено узлов: {len(nodes)}")
    
    print("[>] Проход 2: Сборка дорог (ways)...")
    ways = []
    context = ET.iterparse(osm_file, events=('end',))
    
    for event, elem in context:
        if elem.tag == 'way':
            tags = {child.attrib['k']: child.attrib['v'] for child in elem.findall('tag')}
            
            if 'highway' in tags:
                # Извлекаем геометрию
                points = []
                for nd in elem.findall('nd'):
                    ref = nd.attrib['ref']
                    if ref in nodes:
                        points.append(nodes[ref])
                
                # Дорога должна состоять хотя бы из 2 точек
                if len(points) >= 2:
                    name = tags.get('int_name', '').strip()
                    if not name:
                        name = tags.get('name', '').strip()
                        
                    code = HIGHWAY_CODES.get(tags['highway'], DEFAULT_CODE)
                    
                    ways.append({
                        "osm_id": elem.attrib['id'],
                        "fclass": tags['highway'],
                        "code": code,
                        "name": name,
                        "points": points
                    })
            elem.clear()

    print(f"    Собрано дорог: {len(ways)}")
    return ways

def compile_mlp(ways, mlp_out, meta_out):
    print(f"[>] Компиляция {mlp_out}...")
    
    bin_records = bytearray()
    abs_offset = YZL_SIZE  # Стартуем сразу после YZL
    
    meta_records = []
    record_number = 1

    for way in ways:
        points = way["points"]
        
        # Расчет Bounding Box
        minx_f = min(p[0] for p in points)
        miny_f = min(p[1] for p in points)
        maxx_f = max(p[0] for p in points)
        maxy_f = max(p[1] for p in points)
        
        # Конвертация координат (* 1,000,000)
        minx = int(minx_f * 1_000_000)
        miny = int(miny_f * 1_000_000)
        maxx = int(maxx_f * 1_000_000)
        maxy = int(maxy_f * 1_000_000)
        
        num_parts = 1
        num_points = len(points)
        
        # Собираем Body (тело геометрии)
        body = bytearray()
        body += struct.pack("<iiii", minx, miny, maxx, maxy)
        body += struct.pack("<II", num_parts, num_points)
        body += struct.pack("<I", 0) # parts array (у нас одна часть, начало с индекса 0)
        
        for p in points:
            body += struct.pack("<ii", int(p[0] * 1_000_000), int(p[1] * 1_000_000))
            
        content_len = len(body)
        
        # Собираем Header (8 байт: Record(BE) + Length(LE))
        header = struct.pack(">I", record_number) + struct.pack("<I", content_len)
        
        record_bin = header + body
        
        # Вычисляем v1 для дерева SQT
        v1 = abs_offset - 24
        
        # Сохраняем метаданные
        meta_records.append({
            "osm_id": way["osm_id"],
            "code": way["code"],
            "fclass": way["fclass"],
            "name": way["name"],
            "v1": v1,
            "bbox": [minx_f, miny_f, maxx_f, maxy_f]
        })
        
        bin_records += record_bin
        abs_offset += len(record_bin)
        record_number += 1

    # Формируем глобальный заголовок YZL
    total_size = YZL_SIZE + len(bin_records)
    yzl_header = b'YZL\x00' + struct.pack("<I", total_size) + b'\x00' * 24
    
    with open(mlp_out, 'wb') as f:
        f.write(yzl_header)
        f.write(bin_records)
        
    print(f"    Успешно сохранен {mlp_out} ({total_size} байт)")

    # Сохраняем промежуточный JSON для сборки DB и IDX
    with open(meta_out, 'w', encoding='utf-8') as f:
        json.dump(meta_records, f, ensure_ascii=False, indent=2)
        
    print(f"    Метаданные сохранены в {meta_out}")

if __name__ == "__main__":
    if not os.path.exists("map.osm"):
        print("Ошибка: Файл map.osm не найден в текущей папке.")
    else:
        ways = parse_osm_geometry("map.osm")
        compile_mlp(ways, "roads.mlp", "roads_meta.json")