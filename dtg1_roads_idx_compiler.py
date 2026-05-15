#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler: Roads IDX & DB Generator + Map.Name
=======================================================
ОБНОВЛЕНИЕ: KD-Tree (Branch с геометрией), v2=1 для безымянных, SQT-чанки.
"""

import os
import json
import struct

YZL_SIZE = 32
SQT_HEADER_SIZE = 8
NODE_SIZE = 28
CHUNK_SIZE = 300  # Безопасный размер дерева для часов DT G1

DBF_HEADER_LEN = 161
RECORD_LEN = 145

class Node:
    def __init__(self):
        self.is_leaf = False
        self.bbox = [0.0, 0.0, 0.0, 0.0]
        self.v1 = 0
        self.v2 = 0
        self.v3 = 0
        self.code = 0
        self.offset = 0
        self.left = None
        self.right = None

def build_kd_tree(items):
    """Строит строгое KD-дерево, где каждый узел содержит реальную геометрию"""
    if not items:
        return None
        
    if len(items) == 1:
        item = items[0]
        node = Node()
        node.is_leaf = True
        node.bbox = item["bbox"]
        node.v1 = item["v1"]
        node.v2 = item["v2"]
        node.code = int(item["code"])
        return node

    # BBox всего поддерева (для маршрутизации)
    minx = min(i["bbox"][0] for i in items)
    miny = min(i["bbox"][1] for i in items)
    maxx = max(i["bbox"][2] for i in items)
    maxy = max(i["bbox"][3] for i in items)
    
    # Сортируем по самой длинной оси центроидов
    c_minx = min(i["center"][0] for i in items)
    c_maxx = max(i["center"][0] for i in items)
    c_miny = min(i["center"][1] for i in items)
    c_maxy = max(i["center"][1] for i in items)
    
    if (c_maxx - c_minx) > (c_maxy - c_miny):
        items.sort(key=lambda i: i["center"][0])
    else:
        items.sort(key=lambda i: i["center"][1])
        
    # Смещаем медиану влево, чтобы правый потомок (v3) всегда существовал
    mid = (len(items) - 1) // 2
    median_item = items[mid]
    
    node = Node()
    node.is_leaf = False
    node.bbox = [minx, miny, maxx, maxy]
    # На Branch-узле висит реальная дорога!
    node.v1 = median_item["v1"]
    node.v2 = median_item["v2"]
    node.code = int(median_item["code"])
    
    node.left = build_kd_tree(items[:mid])
    node.right = build_kd_tree(items[mid+1:])
    
    return node

def assign_offsets(node, current_offset):
    if not node:
        return current_offset
        
    node.offset = current_offset
    current_offset += NODE_SIZE
    
    if not node.is_leaf:
        if node.left:
            current_offset = assign_offsets(node.left, current_offset)
            
        # Указатель на правое поддерево (абсолютное смещение минус YZL)
        if node.right:
            node.v3 = current_offset - YZL_SIZE
            current_offset = assign_offsets(node.right, current_offset)
            
    return current_offset

def serialize_tree(node, buffer):
    if not node:
        return
        
    if node.is_leaf:
        # Для Leaf пишем MinX..MaxY вместо v3 и Code в конце
        raw = struct.pack("<IIffffI", node.v1, node.v2, *node.bbox, node.code)
        buffer.extend(raw)
    else:
        # Для Branch пишем v3 на место MinX, а Code отбрасывается
        raw = struct.pack("<IIIffff", node.v1, node.v2, node.v3, *node.bbox)
        buffer.extend(raw)
        serialize_tree(node.left, buffer)
        serialize_tree(node.right, buffer)

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
    
    center_lon = (minx + maxx) / 2.0
    center_lat = (miny + maxy) / 2.0
    
    map_info = {
        "centerLat": center_lat,
        "centerLon": center_lon,
        "mapName": name
    }
    
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(map_info, f, separators=(',', ':'))

def main():
    if not os.path.exists("roads_meta.json"):
        return
        
    with open("roads_meta.json", 'r', encoding='utf-8') as f:
        items = json.load(f)

    # Вычисляем центроиды
    for item in items:
        item["center"] = (
            (item["bbox"][0] + item["bbox"][2]) / 2.0,
            (item["bbox"][1] + item["bbox"][3]) / 2.0
        )

    create_map_name("osm", items, "map.name")

    db_records = []
    db_counter = 2 
    
    for item in items:
        if item.get("name"):
            item["v2"] = db_counter
            db_counter += 1
            db_records.append(item)
        else:
            # КРИТИЧНОЕ ИСПРАВЛЕНИЕ: v2 = 1 указывает на пустую запись
            item["v2"] = 1 

    print(f"[>] Нарезка деревьев (по {CHUNK_SIZE} узлов)...")
    trees = []
    for i in range(0, len(items), CHUNK_SIZE):
        chunk = items[i:i+CHUNK_SIZE]
        trees.append(build_kd_tree(chunk))
    
    idx_buffer = bytearray()
    current_offset = YZL_SIZE
    
    for tree in trees:
        idx_buffer.extend(b'SQT\x01')
        idx_buffer.extend(struct.pack("<I", 1))
        current_offset += SQT_HEADER_SIZE
        
        current_offset = assign_offsets(tree, current_offset)
        serialize_tree(tree, idx_buffer)
    
    total_size = YZL_SIZE + len(idx_buffer)
    yzl = b'YZL\x00' + struct.pack("<I", total_size) + b'\x00' * 24
    
    with open("roads.idx", "wb") as f:
        f.write(yzl)
        f.write(idx_buffer)

    compile_db(db_records, "roads.db")
    print(f"\n[УСПЕХ] Сгенерировано {len(trees)} SQT деревьев. Слой готов к загрузке!")

if __name__ == "__main__":
    import sys
    sys.setrecursionlimit(2000000)
    main()