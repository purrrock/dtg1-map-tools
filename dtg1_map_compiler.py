#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Monolithic Map Compiler (Platform C175C1)
===============================================
Финальная версия компилятора векторных данных OpenStreetMap (OSM) 
в закрытые бинарные форматы смарт-часов DT NO.1 G1 (.mlp, .idx, .db).

Архитектурные особенности движка:
  1. Flat List State Machine: SQT-индекс генерируется плоским списком,
     а не деревом (обход ограничения аппаратного стека часов v2=0 Underflow).
  2. Non-Zero Winding Rule: Внутренние контуры (дырки) полигонов обходятся
     против часовой стрелки, а внешние - по часовой, для корректной аппаратной
     триангуляции и отрисовки островов на озерах.
  3. Z-Culling (LOD): Объекты аппаратно скрываются по 3 уровням детализации 
     в зависимости от их физических размеров.
  4. System Dummies: Для неподдерживаемых слоев генерируются Hex-пустышки
     строго с Payload Size = 0, чтобы обойти ошибку EOF при системной валидации.
"""

import os
import struct
import xml.etree.ElementTree as ET
import json

# ==============================================================================
# БИНАРНЫЕ КОНСТАНТЫ ПЛАТФОРМЫ C175C1
# ==============================================================================
YZL_SIZE = 32           # Размер глобального заголовка YZL (включая скрытые флаги)
SQT_HEADER_SIZE = 8     # Размер заголовка секции LOD (SQT\x01...)
NODE_SIZE = 28          # Размер одного узла данных в SQT
CHUNK_SIZE = 15         # Лимит объектов в одном плоском кластере (размер буфера часов)

DBF_HEADER_LEN = 161    # Фиксированный размер заголовка dBase III 
RECORD_LEN = 145        # Фиксированная длина одной записи атрибутов

# ==============================================================================
# ЛУКАП ТАБЛИЦЫ (LOOK-UP TABLES)
# ==============================================================================

# Словари разделены во избежание коллизий (например, тег 'residential' есть и там, и там)

HIGHWAY_CODES = {
    "motorway": 5111, "trunk": 5112, "primary": 5113, "secondary": 5114, "tertiary": 5115,
    "unclassified": 5121, "residential": 5122, "living_street": 5123, "pedestrian": 5124, "busway": 5125,
    "motorway_link": 5111, # ХАК: 5131 аппаратно не поддерживается, заменен на 5111
    "trunk_link": 5132, "primary_link": 5133, "secondary_link": 5134, "tertiary_link": 5135,
    "service": 5141, "track": 5142, "track_grade1": 5143, "track_grade2": 5144, "track_grade3": 5145, 
    "track_grade4": 5146, "track_grade5": 5147, "bridleway": 5151, "cycleway": 5152, "footway": 5153,
    "path": 5154, "steps": 5155, "road": 5199, "unknown": 5199
}

POLYGON_CODES = {
    "forest": 7201, "park": 7202, "residential": 7203, "industrial": 7204,
    "cemetery": 7206, "allotments": 7207, "meadow": 7208, "commercial": 7209,
    "nature_reserve": 7210, "recreation_ground": 7211, "retail": 7212,
    "military": 7213, "quarry": 7214, "orchard": 7215, "vineyard": 7216, "scrub": 7217,
    # Новые агро/эко зоны:
    "grass": 7218, "heath": 7219, "farmland": 7228, "farmyard": 7229, "landfill": 7233,
    # Водоемы:
    "water": 8200
}

# Пороги аппаратного Z-Culling (Масштаб экрана в метрах, на котором объект ПОЯВЛЯЕТСЯ)
DISPLAY_SCALES = {
    # Линии
    5111: 1000, 5112: 1000, 5113: 1000, 5114: 1000,
    5115: 500,  5131: 500,  5132: 500,  5133: 500,  5134: 500,  5135: 500,
    5121: 100,  5122: 100,  5123: 100,  5124: 100,  5125: 100,
    5141: 50,   5142: 50,   5143: 50,   5144: 50,   5145: 50,   5146: 50,   5147: 50,
    5151: 20,   5152: 20,   5153: 20,   5154: 20,   5155: 20,   5199: 20,
    
    # Полигоны (Landuse) - скрываем на обзорных масштабах для экономии GPU
    7201: 500, 7202: 500, 7203: 500, 7204: 500, 7206: 500, 7207: 500, 7208: 500, 7209: 500,
    7210: 500, 7211: 500, 7212: 500, 7213: 500, 7214: 500, 7215: 500, 7216: 500, 7217: 500,
    7218: 500, 7219: 500, 7228: 500, 7229: 500, 7233: 500,
    
    # Полигоны (Water)
    8200: 500
}

# ==============================================================================
# ФАЗА 1: ПАРСЕР ГЕОМЕТРИИ (Топология и Winding Rules)
# ==============================================================================

def is_clockwise(points):
    """
    Математическое вычисление направления обхода контура (Шнуровка Гаусса).
    Адаптировано для гео-координат (Lat/Lon).
    Возвращает True, если полигон закручен ПО часовой стрелке.
    """
    sum_area = 0.0
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        sum_area += (x1 * y2 - x2 * y1)
    
    # В классической декартовой системе отрицательная сумма означает Clockwise
    return sum_area < 0

def parse_osm_geometry(osm_file):
    """Двухпроходный потоковый парсер (защита от переполнения ОЗУ)."""
    print("[>] Проход 1: Кэширование узлов (nodes)...")
    nodes = {}
    for event, elem in ET.iterparse(osm_file, events=('start', 'end')):
        if event == 'end' and elem.tag == 'node':
            nodes[elem.attrib['id']] = (float(elem.attrib['lon']), float(elem.attrib['lat']))
            elem.clear()
            
    print(f"    Загружено узлов: {len(nodes)}")
    print("[>] Проход 2: Нормализация геометрии и мультиполигонов...")
    
    ways_cache = {}
    roads, landuse = [], []
    
    context = ET.iterparse(osm_file, events=('end',))
    for event, elem in context:
        
        # --- ОБРАБОТКА ЛИНИЙ И ПРОСТЫХ КОНТУРОВ ---
        if elem.tag == 'way':
            tags = {child.attrib['k']: child.attrib['v'] for child in elem.findall('tag')}
            points = [nodes[nd.attrib['ref']] for nd in elem.findall('nd') if nd.attrib['ref'] in nodes]
            
            if points:
                ways_cache[elem.attrib['id']] = points
                name = tags.get('int_name', '').strip() or tags.get('name', '').strip()
                osm_id = elem.attrib['id']

                # Дороги (Направление вектора неважно)
                if 'highway' in tags and len(points) >= 2:
                    fclass = tags['highway']
                    roads.append({
                        "osm_id": osm_id, "fclass": fclass, 
                        "code": HIGHWAY_CODES.get(fclass, 5142), 
                        "name": name, "points": points, "parts": [0]
                    })
                    
                # Простые полигоны (Одиночное кольцо обязано быть Outer -> По часовой)
                elif ('landuse' in tags or 'leisure' in tags or 'natural' in tags) and len(points) >= 4:
                    if points[0] == points[-1]: 
                        fclass = tags.get('landuse', tags.get('leisure', tags.get('natural', 'unknown')))
                        if not is_clockwise(points):
                            points.reverse() # Нормализация направления
                            
                        landuse.append({
                            "osm_id": osm_id, "fclass": fclass, 
                            "code": POLYGON_CODES.get(fclass, 7208), 
                            "name": name, "points": points, "parts": [0]
                        })
            elem.clear()

        # --- ОБРАБОТКА МУЛЬТИПОЛИГОНОВ (Дырки и Острова) ---
        elif elem.tag == 'relation':
            tags = {child.attrib['k']: child.attrib['v'] for child in elem.findall('tag')}
            
            if tags.get('type') == 'multipolygon':
                fclass = tags.get('landuse', tags.get('leisure', tags.get('natural', None)))
                
                if fclass:
                    name = tags.get('int_name', '').strip() or tags.get('name', '').strip()
                    combined_points, parts, current_index = [], [], 0
                    
                    for member in elem.findall('member'):
                        if member.attrib.get('type') == 'way':
                            ref = member.attrib['ref']
                            role = member.attrib.get('role', 'outer')
                            
                            if ref in ways_cache:
                                ring_points = list(ways_cache[ref])
                                
                                if len(ring_points) >= 4 and ring_points[0] == ring_points[-1]:
                                    is_cw = is_clockwise(ring_points)
                                    
                                    # Аппаратное правило триангуляции (Non-Zero Winding)
                                    if role == 'outer' and not is_cw:
                                        ring_points.reverse()
                                    elif role == 'inner' and is_cw:
                                        ring_points.reverse()
                                        
                                    parts.append(current_index)
                                    combined_points.extend(ring_points)
                                    current_index += len(ring_points)
                    
                    if combined_points and parts:
                        landuse.append({
                            "osm_id": elem.attrib['id'], "fclass": fclass, 
                            "code": POLYGON_CODES.get(fclass, 7208), 
                            "name": name, "points": combined_points, "parts": parts
                        })
            elem.clear()

    print(f"    Собрано: {len(roads)} дорог, {len(landuse)} полигонов.")
    return roads, landuse

# ==============================================================================
# ФАЗА 2: КОМПИЛЯЦИЯ БИНАРНЫХ СТРУКТУР
# ==============================================================================

def compile_mlp(features, mlp_out):
    """Компилятор бинарной геометрии (ESRI Shapefile-подобная структура)."""
    print(f"[>] Компиляция геометрии: {mlp_out}...")
    bin_records = bytearray()
    abs_offset = YZL_SIZE
    meta_records = []
    record_number = 1

    for feature in features:
        points = feature["points"]
        parts = feature.get("parts", [0])
        
        minx_f, miny_f = min(p[0] for p in points), min(p[1] for p in points)
        maxx_f, maxy_f = max(p[0] for p in points), max(p[1] for p in points)
        
        # Float * 1,000,000 -> Int32 (Аппаратный стандарт)
        body = bytearray(struct.pack("<iiii", int(minx_f * 1e6), int(miny_f * 1e6), int(maxx_f * 1e6), int(maxy_f * 1e6)))
        body += struct.pack("<II", len(parts), len(points))
        
        # Динамический массив индексов частей (Parts Array)
        for part_idx in parts: body += struct.pack("<I", part_idx)
        for p in points: body += struct.pack("<ii", int(p[0] * 1e6), int(p[1] * 1e6))
            
        header = struct.pack(">I", record_number) + struct.pack("<I", len(body))
        record_bin = header + body
        
        # v1: Системное смещение (от начала файла до тела геометрии)
        meta_records.append({
            "osm_id": feature["osm_id"], "code": feature["code"],
            "fclass": feature["fclass"], "name": feature["name"],
            "v1": abs_offset - 24, "bbox": [minx_f, miny_f, maxx_f, maxy_f]
        })
        
        bin_records += record_bin
        abs_offset += len(record_bin)
        record_number += 1

    payload_size = len(bin_records)
    with open(mlp_out, 'wb') as f:
        # Внимание: Скрытый системный байт выравнивания \x00\x00\x00\x04
        f.write(b'YZL\x00' + struct.pack("<I", payload_size) + b'\x00\x00\x00\x04' + b'\x00' * 20)
        f.write(bin_records)
        
    return meta_records

def compile_db(meta_records, db_out):
    """Генератор dBase III атрибутов и линковочных указателей v2."""
    print(f"[>] Компиляция атрибутов: {db_out}...")
    db_records, db_counter = [], 2 
    
    for item in meta_records:
        if item.get("name"):
            item["v2"] = db_counter
            db_counter += 1
            db_records.append(item)
        else: 
            item["v2"] = 1 # Ссылка на пустую 'Record 0' для безымянных
            
    bin_records = b'\x00' * RECORD_LEN 
    pad = lambda text, length: str(text).encode('utf-8')[:length].ljust(length, b'\x00')
    desc = lambda name, length: name.encode('ascii').ljust(11, b'\x00') + b'C' + b'\x00'*4 + bytes([length]) + b'\x00'*15
    
    for rec in db_records:
        r_bytes = bytearray(b'\x20')
        r_bytes += pad(rec["osm_id"], 12) + pad(rec["code"], 4) + pad(rec["fclass"], 28) + pad(rec["name"], 100)
        bin_records += r_bytes

    total_records = len(db_records) + 1
    dbf_header = bytearray(b'\x03\x00\x00\x00') + struct.pack('<I', total_records)
    dbf_header += struct.pack('<H', DBF_HEADER_LEN) + struct.pack('<H', RECORD_LEN) + b'\x00' * 20
    dbf_header += desc("osm_id", 12) + desc("code", 4) + desc("fclass", 28) + desc("name", 100) + b'\x0D'
    
    with open(db_out, 'wb') as f:
        f.write(b'YZL\x00' + struct.pack('<I', DBF_HEADER_LEN + len(bin_records)) + b'\x00\x00\x00\x04' + b'\x00' * 20)
        f.write(dbf_header)
        f.write(bin_records)

class ClusterBlock:
    def __init__(self, data_nodes):
        self.data_nodes = data_nodes
        self.bbox = [
            min(n["bbox"][0] for n in data_nodes), min(n["bbox"][1] for n in data_nodes),
            max(n["bbox"][2] for n in data_nodes), max(n["bbox"][3] for n in data_nodes)
        ]

def compile_idx(meta_records, idx_out):
    """Многоуровневый компилятор SQT-индекса (Плоский список)."""
    print(f"[>] Компиляция индекса SQT: {idx_out}...")
    idx_buffer = bytearray()
    lod_filters = [
        lambda c: True,
        lambda c: DISPLAY_SCALES.get(c, 20) >= 100,
        lambda c: DISPLAY_SCALES.get(c, 20) >= 1000
    ]

    for lod_index, condition in enumerate(lod_filters):
        lod_records = [r for r in meta_records if condition(r["code"])]
        idx_buffer.extend(b'SQT\x01' + struct.pack("<I", 1))
        
        blocks = [ClusterBlock(lod_records[i:i+CHUNK_SIZE]) for i in range(0, len(lod_records), CHUNK_SIZE)]
        for block in blocks:
            if not block.data_nodes: continue
            
            # Навигационный прыжок (v3) к следующему кластеру
            first = block.data_nodes[0]
            cluster_len = len(block.data_nodes) + 1
            jump_v3 = (cluster_len * NODE_SIZE) + 8 
            
            idx_buffer.extend(struct.pack("<IIIffff", first["v1"], first["v2"], jump_v3, *block.bbox))
            idx_buffer.extend(struct.pack("<IIffffI", 0, cluster_len, *block.bbox, int(first["code"])))
            
            for d in block.data_nodes:
                idx_buffer.extend(struct.pack("<IIffffI", d["v1"], d["v2"], *d["bbox"], int(d["code"])))
                
        idx_buffer.extend(b'\x00' * 8)
    
    with open(idx_out, "wb") as f:
        f.write(b'YZL\x00' + struct.pack("<I", len(idx_buffer)) + b'\x00\x00\x00\x04' + b'\x00' * 20)
        f.write(idx_buffer)

# ==============================================================================
# ФАЗА 3: ВСПОМОГАТЕЛЬНЫЕ ГЕНЕРАТОРЫ И DUMMIES
# ==============================================================================

def create_map_name(name, meta_records, out_file="map.name"):
    """Центрирует камеру приложения карт по координатам скомпилированного массива."""
    if not meta_records: return
    center_lat = (min(r["bbox"][1] for r in meta_records) + max(r["bbox"][3] for r in meta_records)) / 2.0
    center_lon = (min(r["bbox"][0] for r in meta_records) + max(r["bbox"][2] for r in meta_records)) / 2.0

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"centerLat": center_lat, "centerLon": center_lon, "mapName": name}, f, separators=(',', ':'))

def create_empty_layer(layer_prefix):
    """Hex-дампы оригинальных пустых файлов C175C1 для обхода EOF-защиты прошивки."""
    print(f"[>] Создание системной Hex-заглушки: {layer_prefix}...")
    mlp_hex = "595A4C00000000000000000400000000D41D8CD98F00B204E9800998ECF8427EA0B861411B1259427BD96D41FCD45A42000000000000000000000000000000008BDDE3424F40B4418BDDE3424F40B441"
    idx_hex = "595A4C10300000000000000400000010E5F9D2228804251B5F9E3EAB298C30E5535154010100000000000000000000005351540101000000000000000000000053515401010000000000000000000000"
    db_hex = "595A4C00320100000000000400000000D65E1C742D95963F147A4468DD25F93F035F071A01000000A100910000000000000000000000000000000000000000006F736D5F6964000000000043000000000C000000000000000000000000000000636F6465000000000000004E000000000400000000000000000000000000000066636C617373000000000043000000001C0000000000000000000000000000006E616D65000000000000004300000000640000000000000000000000000000000D" + "00" * 145
    with open(f"{layer_prefix}.mlp", "wb") as f: f.write(bytearray.fromhex(mlp_hex))
    with open(f"{layer_prefix}.idx", "wb") as f: f.write(bytearray.fromhex(idx_hex))
    with open(f"{layer_prefix}.db",  "wb") as f: f.write(bytearray.fromhex(db_hex))

# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main():
    if not os.path.exists("map.osm"):
        print("[-] Ошибка: Файл map.osm не найден.")
        return
        
    print("=========================================")
    print("DT G1 MAP COMPILER (Release v1.0)")
    print("=========================================")
    
    roads_data, landuse_data = parse_osm_geometry("map.osm")
    meta_all = []

    # 1. Слой Дорог
    if roads_data:
        meta_roads = compile_mlp(roads_data, "roads.mlp")
        compile_db(meta_roads, "roads.db")
        compile_idx(meta_roads, "roads.idx")
        meta_all.extend(meta_roads)

    # 2. Слой Землепользования (Разделяем Landuse и Water)
    landuse_only = [f for f in landuse_data if f['code'] != 8200]
    water_only = [f for f in landuse_data if f['code'] == 8200]

    if landuse_only:
        meta_landuse = compile_mlp(landuse_only, "landuse.mlp")
        compile_db(meta_landuse, "landuse.db")
        compile_idx(meta_landuse, "landuse.idx")
        meta_all.extend(meta_landuse)
    else:
        create_empty_layer("landuse")

    if water_only:
        meta_water = compile_mlp(water_only, "water.mlp")
        compile_db(meta_water, "water.db")
        compile_idx(meta_water, "water.idx")
        meta_all.extend(meta_water)
    else:
        create_empty_layer("water")

    # 3. Общая центровка камеры
    if meta_all:
        create_map_name("DTG1_Map", meta_all, "map.name")
    
    # 4. Неподдерживаемые слои глушим пустышками
    create_empty_layer("pois")
    
    print("\n[УСПЕХ] Пакет карт готов к записи на часы!")

if __name__ == "__main__":
    main()