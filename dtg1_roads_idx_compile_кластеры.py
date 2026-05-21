#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler: Roads IDX & DB Generator
============================================
ФИНАЛЬНАЯ ВЕРСИЯ: Использование плоских кластеров (Chunks) 
с узлами-заголовками (v1=0, v2=N) вместо рекурсивных деревьев.
"""

import os
import json
import struct

YZL_SIZE = 32
SQT_HEADER_SIZE = 8
NODE_SIZE = 28
CHUNK_SIZE = 15  # В заводской карте кластеры небольшие (11-20 объектов)

DBF_HEADER_LEN = 161
RECORD_LEN = 145

# ========== УТИЛИТЫ DB ==========

def make_string_field(text, length):
    return str(text).encode('utf-8')[:length].ljust(length, b'\x00')

def make_dbf_descriptor(name, length):
    desc = name.encode('ascii').ljust(11, b'\x00')
    desc += b'C' + b'\x00' * 4 + bytes([length]) + b'\x00' * 15
    return desc

def compile_db(db_records, out_file):
    bin_records = bytearray()
    bin_records += b'\x00' * RECORD_LEN  # Пустая запись 0
    
    for rec in db_records:
        record_bytes = bytearray()
        record_bytes += b'\x20'
        record_bytes += make_string_field(rec["osm_id"], 12)
        record_bytes += make_string_field(rec["code"], 4)
        record_bytes += make_string_field(rec["fclass"], 28)
        record_bytes += make_string_field(rec["name"], 100)
        bin_records += record_bytes

    total_records = len(db_records) + 1
    
    dbf_header = bytearray()
    dbf_header += b'\x03\x00\x00\x00'
    dbf_header += struct.pack('<I', total_records)
    dbf_header += struct.pack('<H', DBF_HEADER_LEN)
    dbf_header += struct.pack('<H', RECORD_LEN)
    dbf_header += b'\x00' * 20
    dbf_header += make_dbf_descriptor("osm_id", 12)
    dbf_header += make_dbf_descriptor("code", 4)
    dbf_header += make_dbf_descriptor("fclass", 28)
    dbf_header += make_dbf_descriptor("name", 100)
    dbf_header += b'\x0D'
    
    total_file_size = YZL_SIZE + DBF_HEADER_LEN + len(bin_records)
    yzl_header = b'YZL\x00' + struct.pack('<I', total_file_size) + b'\x00' * 24
    
    with open(out_file, 'wb') as f:
        f.write(yzl_header)
        f.write(dbf_header)
        f.write(bin_records)

def create_map_name(name, items, out_file):
    minx = min(i["bbox"][0] for i in items)
    miny = min(i["bbox"][1] for i in items)
    maxx = max(i["bbox"][2] for i in items)
    maxy = max(i["bbox"][3] for i in items)
    
    map_info = {
        "centerLat": (miny + maxy) / 2.0,
        "centerLon": (minx + maxx) / 2.0,
        "mapName": name
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(map_info, f, separators=(',', ':'))

# ========== ГЛАВНАЯ ЛОГИКА ==========

def main():
    if not os.path.exists("roads_meta.json"):
        print("Не найден roads_meta.json")
        return
        
    with open("roads_meta.json", 'r', encoding='utf-8') as f:
        items = json.load(f)

    create_map_name("osm", items, "map.name")

    # Синхронизация DB
    db_records = []
    db_counter = 2 
    
    for item in items:
        if item.get("name"):
            item["v2"] = db_counter
            db_counter += 1
            db_records.append(item)
        else:
            item["v2"] = 1 

    print("[>] Упаковка дорог в плоские кластеры (Chunks)...")
    
    idx_buffer = bytearray()
    
    # --- TREE 0 (Основная геометрия) ---
    idx_buffer.extend(b'SQT\x01')
    idx_buffer.extend(struct.pack("<I", 1))
    
    # Разбиваем на чанки (группы)
    for i in range(0, len(items), CHUNK_SIZE):
        chunk = items[i:i+CHUNK_SIZE]
        
        # Вычисляем общий BBox для заголовка кластера
        minx = min(item["bbox"][0] for item in chunk)
        miny = min(item["bbox"][1] for item in chunk)
        maxx = max(item["bbox"][2] for item in chunk)
        maxy = max(item["bbox"][3] for item in chunk)
        
        # Код берем у первого элемента (часам он нужен для стиля)
        group_code = int(chunk[0]["code"])
        
        # 1. ЗАПИСЬ УЗЛА-ЗАГОЛОВКА КЛАСТЕРА
        # v1 = 0, v2 = количество элементов в кластере
        idx_buffer.extend(struct.pack("<IIffffI", 0, len(chunk), minx, miny, maxx, maxy, group_code))
        
        # 2. ЗАПИСЬ САМИХ ЭЛЕМЕНТОВ (Листьев)
        for item in chunk:
            idx_buffer.extend(struct.pack("<IIffffI", item["v1"], item["v2"], *item["bbox"], int(item["code"])))
            
    # Завершающий паддинг для первого дерева
    idx_buffer.extend(b'\x00' * 8)
    
    # --- TREE 1 (Пустое, для масштаба) ---
    idx_buffer.extend(b'SQT\x01')
    idx_buffer.extend(struct.pack("<I", 1))
    idx_buffer.extend(b'\x00' * 8)
    
    # --- TREE 2 (Пустое, для масштаба) ---
    idx_buffer.extend(b'SQT\x01')
    idx_buffer.extend(struct.pack("<I", 1))
    idx_buffer.extend(b'\x00' * 8)
    
    # Сборка финального файла
    total_size = YZL_SIZE + len(idx_buffer)
    yzl = b'YZL\x00' + struct.pack("<I", total_size) + b'\x00' * 24
    
    with open("roads.idx", "wb") as f:
        f.write(yzl)
        f.write(idx_buffer)

    compile_db(db_records, "roads.db")
    print(f"\n[УСПЕХ] Индекс собран по заводской логике кластеров! Слой готов к загрузке.")

if __name__ == "__main__":
    main()