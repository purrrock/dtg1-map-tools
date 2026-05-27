#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Monolithic Map Compiler (C175C1 Platform)
===============================================
Однопроходный компилятор для создания файлов .mlp, .idx и .db из map.osm.
Сохраняет строгую бинарную совместимость с аппаратным рендерером часов.
Основано на Flat List State Machine Specification v3.0.
"""

import os
import struct
import xml.etree.ElementTree as ET

# Константы формата YZL / SQT
YZL_SIZE = 32
SQT_HEADER_SIZE = 8
NODE_SIZE = 28
CHUNK_SIZE = 15

DBF_HEADER_LEN = 161
RECORD_LEN = 145

# Полный словарь типов дорог согласно спецификации Geofabrik
HIGHWAY_CODES = {
    "motorway": 5111,
    "trunk": 5112,
    "primary": 5113,
    "secondary": 5114,
    "tertiary": 5115,
    "unclassified": 5121,
    "residential": 5122,
    "living_street": 5123,
    "pedestrian": 5124,
    "busway": 5125,
    "motorway_link": 5131,
    "trunk_link": 5132,
    "primary_link": 5133,
    "secondary_link": 5134,
    "tertiary_link": 5135,
    "service": 5141,
    "track": 5142,
    "track_grade1": 5143,
    "track_grade2": 5144,
    "track_grade3": 5145,
    "track_grade4": 5146,
    "track_grade5": 5147,
    "bridleway": 5151,
    "cycleway": 5152,
    "footway": 5153,
    "path": 5154,
    "steps": 5155,
    "road": 5199,
    "unknown": 5199
}

# Строгий резервный код, который точно переваривается часами
DEFAULT_CODE = 5142

# ==============================================================================
# ФАЗА 1: ПАРСИНГ ГЕОМЕТРИИ OSM
# ==============================================================================

def parse_osm_geometry(osm_file):
    print("[>] Проход 1: Загрузка узлов (nodes) в память...")
    nodes = {}
    context = ET.iterparse(osm_file, events=('start', 'end'))
    
    for event, elem in context:
        if event == 'end' and elem.tag == 'node':
            nodes[elem.attrib['id']] = (float(elem.attrib['lon']), float(elem.attrib['lat']))
            elem.clear()
            
    print(f"    Загружено узлов: {len(nodes)}")
    
    print("[>] Проход 2: Сборка объектов (ways)...")
    ways = []
    context = ET.iterparse(osm_file, events=('end',))
    
    for event, elem in context:
        if elem.tag == 'way':
            tags = {child.attrib['k']: child.attrib['v'] for child in elem.findall('tag')}
            
            if 'highway' in tags:
                points = []
                for nd in elem.findall('nd'):
                    ref = nd.attrib['ref']
                    if ref in nodes:
                        points.append(nodes[ref])
                
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

    print(f"    Собрано объектов: {len(ways)}")
    return ways

# ==============================================================================
# ФАЗА 2: КОМПИЛЯЦИЯ .MLP (Геометрия)
# ==============================================================================

def compile_mlp(ways, mlp_out):
    print(f"[>] Компиляция {mlp_out}...")
    
    bin_records = bytearray()
    abs_offset = YZL_SIZE
    meta_records = []
    record_number = 1

    for way in ways:
        points = way["points"]
        
        # Расчет Bounding Box
        minx_f, miny_f = min(p[0] for p in points), min(p[1] for p in points)
        maxx_f, maxy_f = max(p[0] for p in points), max(p[1] for p in points)
        
        # Конвертация координат (* 1,000,000) для аппаратного чтения
        minx, miny = int(minx_f * 1_000_000), int(miny_f * 1_000_000)
        maxx, maxy = int(maxx_f * 1_000_000), int(maxy_f * 1_000_000)
        
        num_parts, num_points = 1, len(points)
        
        body = bytearray()
        body += struct.pack("<iiii", minx, miny, maxx, maxy)
        body += struct.pack("<II", num_parts, num_points)
        body += struct.pack("<I", 0)
        
        for p in points:
            body += struct.pack("<ii", int(p[0] * 1_000_000), int(p[1] * 1_000_000))
            
        header = struct.pack(">I", record_number) + struct.pack("<I", len(body))
        record_bin = header + body
        
        # Вычисляем v1 (Абсолютное смещение - 24 байта)
        v1 = abs_offset - 24
        
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

    total_size = YZL_SIZE + len(bin_records)
    yzl_header = b'YZL\x00' + struct.pack("<I", total_size) + b'\x00' * 24
    
    with open(mlp_out, 'wb') as f:
        f.write(yzl_header)
        f.write(bin_records)
        
    print(f"    Успешно сохранен {mlp_out} ({total_size} байт)")
    return meta_records

# ==============================================================================
# ФАЗА 3: КОМПИЛЯЦИЯ .DB (Атрибуты dBase III)
# ==============================================================================

def make_string_field(text, length):
    return str(text).encode('utf-8')[:length].ljust(length, b'\x00')

def make_dbf_descriptor(name, length):
    desc = name.encode('ascii').ljust(11, b'\x00')
    desc += b'C' + b'\x00' * 4 + bytes([length]) + b'\x00' * 15
    return desc

def compile_db(meta_records, db_out):
    print(f"[>] Компиляция {db_out}...")
    
    db_records = []
    db_counter = 2 
    
    for item in meta_records:
        if item.get("name"):
            item["v2"] = db_counter
            db_counter += 1
            db_records.append(item)
        else: 
            item["v2"] = 1 # Ссылка на пустую Record 0
            
    # Запись 0 обязана быть пустой для корректного чтения часов
    bin_records = b'\x00' * RECORD_LEN  
    
    for rec in db_records:
        record_bytes = bytearray(b'\x20')
        record_bytes += make_string_field(rec["osm_id"], 12)
        record_bytes += make_string_field(rec["code"], 4)
        record_bytes += make_string_field(rec["fclass"], 28)
        record_bytes += make_string_field(rec["name"], 100)
        bin_records += record_bytes

    total_records = len(db_records) + 1
    dbf_header = bytearray(b'\x03\x00\x00\x00') + struct.pack('<I', total_records)
    dbf_header += struct.pack('<H', DBF_HEADER_LEN) + struct.pack('<H', RECORD_LEN) + b'\x00' * 20
    dbf_header += make_dbf_descriptor("osm_id", 12) + make_dbf_descriptor("code", 4)
    dbf_header += make_dbf_descriptor("fclass", 28) + make_dbf_descriptor("name", 100) + b'\x0D'
    
    total_size = YZL_SIZE + DBF_HEADER_LEN + len(bin_records)
    
    with open(db_out, 'wb') as f:
        f.write(b'YZL\x00' + struct.pack('<I', total_size) + b'\x00' * 24)
        f.write(dbf_header)
        f.write(bin_records)
        
    print(f"    Успешно сохранен {db_out} ({total_size} байт)")

# ==============================================================================
# ФАЗА 4: КОМПИЛЯЦИЯ .IDX (Пространственный индекс SQT)
# ==============================================================================

class ClusterBlock:
    def __init__(self, data_nodes):
        self.data_nodes = data_nodes
        self.bbox = [
            min(n["bbox"][0] for n in data_nodes),
            min(n["bbox"][1] for n in data_nodes),
            max(n["bbox"][2] for n in data_nodes),
            max(n["bbox"][3] for n in data_nodes)
        ]

def compile_idx(meta_records, idx_out):
    print(f"[>] Компиляция {idx_out}...")
    
    # Нарезаем плоские блоки
    blocks = [ClusterBlock(meta_records[i:i+CHUNK_SIZE]) for i in range(0, len(meta_records), CHUNK_SIZE)]

    idx_buffer = bytearray(b'SQT\x01' + struct.pack("<I", 1))
    
    for block in blocks:
        first_data = block.data_nodes[0]
        v1_safe, v2_safe = first_data["v1"], first_data["v2"]
        cluster_elements_count = len(block.data_nodes) + 1
        jump_v3 = (cluster_elements_count * NODE_SIZE) + 8
        
        # Навигационный узел
        idx_buffer.extend(struct.pack("<IIIffff", v1_safe, v2_safe, jump_v3, *block.bbox))
        # Заголовок кластера
        idx_buffer.extend(struct.pack("<IIffffI", 0, cluster_elements_count, *block.bbox, int(first_data["code"])))
        # Узлы данных
        for d in block.data_nodes:
            idx_buffer.extend(struct.pack("<IIffffI", d["v1"], d["v2"], *d["bbox"], int(d["code"])))
            
    idx_buffer.extend(b'\x00' * 8)
    
    # TODO: Реализовать фильтрацию по LOD 1 и LOD 2
    for _ in range(2): 
        idx_buffer.extend(b'SQT\x01' + struct.pack("<I", 1) + b'\x00' * 8)
    
    total_size = YZL_SIZE + len(idx_buffer)
    
    with open(idx_out, "wb") as f:
        f.write(b'YZL\x00' + struct.pack("<I", total_size) + b'\x00' * 24)
        f.write(idx_buffer)
        
    print(f"    Успешно сохранен {idx_out} ({total_size} байт)")

# ==============================================================================
# MAIN
# ==============================================================================

def main():
    if not os.path.exists("map.osm"):
        print("[-] Ошибка: Файл map.osm не найден в текущей папке.")
        return
        
    print("=========================================")
    print("DT G1 MAP COMPILER (Monolithic)")
    print("=========================================")
    
    ways = parse_osm_geometry("map.osm")
    
    if not ways:
        print("[-] Ошибка: Не найдено объектов для компиляции.")
        return
        
    meta_records = compile_mlp(ways, "roads.mlp")
    compile_db(meta_records, "roads.db")
    compile_idx(meta_records, "roads.idx")
    
    print("\n[УСПЕХ] Картографический слой roads собран без ошибок!")

if __name__ == "__main__":
    main()