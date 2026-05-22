#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler: Roads IDX & DB Generator
============================================
Версия 3.0 (BVH Cluster Architecture)
Идеально повторяет заводскую логику: Branch (v1=1) -> Leaf Header (v1=0) -> Data (v1>=8).
"""

import os
import json
import struct

YZL_SIZE = 32
SQT_HEADER_SIZE = 8
NODE_SIZE = 28
CHUNK_SIZE = 15  # Дорог в одном кластере

DBF_HEADER_LEN = 161
RECORD_LEN = 145

# =========================================================
# АРХИТЕКТУРА BVH ДЕРЕВА
# =========================================================

class LeafBlock:
    """Листовой кластер: содержит заголовок (v1=0) и сами дороги (v1>=8)"""
    def __init__(self, data_nodes):
        self.data_nodes = data_nodes
        minx = min(n["bbox"][0] for n in data_nodes)
        miny = min(n["bbox"][1] for n in data_nodes)
        maxx = max(n["bbox"][2] for n in data_nodes)
        maxy = max(n["bbox"][3] for n in data_nodes)
        self.bbox = [minx, miny, maxx, maxy]
        self.center = ((minx + maxx) / 2.0, (miny + maxy) / 2.0)
        # Размер = Заголовок (28) + Все дороги (N * 28)
        self.size = NODE_SIZE + len(data_nodes) * NODE_SIZE
        self.offset = 0

class BranchNode:
    """Узел ветвления (Навигатор): содержит флаг v1=1 и прыжок v3"""
    def __init__(self, left, right):
        self.left = left
        self.right = right
        self.bbox = [
            min(left.bbox[0], right.bbox[0]),
            min(left.bbox[1], right.bbox[1]),
            max(left.bbox[2], right.bbox[2]),
            max(left.bbox[3], right.bbox[3])
        ]
        self.center = (
            (self.bbox[0] + self.bbox[2]) / 2.0,
            (self.bbox[1] + self.bbox[3]) / 2.0
        )
        self.v3 = 0
        self.offset = 0
        self.size = NODE_SIZE

def build_bvh(blocks):
    """Рекурсивно строит BVH дерево над кластерами"""
    if len(blocks) == 1:
        return blocks[0]
        
    cx_range = max(b.center[0] for b in blocks) - min(b.center[0] for b in blocks)
    cy_range = max(b.center[1] for b in blocks) - min(b.center[1] for b in blocks)
    
    if cx_range > cy_range:
        blocks.sort(key=lambda b: b.center[0])
    else:
        blocks.sort(key=lambda b: b.center[1])
        
    mid = len(blocks) // 2
    return BranchNode(build_bvh(blocks[:mid]), build_bvh(blocks[mid:]))

def assign_offsets(node, current_offset):
    """Вычисляет абсолютные смещения и v3 прыжки"""
    node.offset = current_offset
    if isinstance(node, BranchNode):
        current_offset += NODE_SIZE
        current_offset = assign_offsets(node.left, current_offset)
        # Указатель на правого ребенка
        node.v3 = current_offset - YZL_SIZE
        current_offset = assign_offsets(node.right, current_offset)
    else:
        current_offset += node.size
    return current_offset

def serialize_tree(node, buffer):
    """Превращает дерево в бинарный код"""
    if isinstance(node, BranchNode):
        # Branch Node: v1=1 (Флаг ветвления), v2=0, v3=прыжок
        buffer.extend(struct.pack("<IIIffff", 1, 0, node.v3, *node.bbox))
        serialize_tree(node.left, buffer)
        serialize_tree(node.right, buffer)
    else:
        # Cluster Header: v1=0 (Флаг кластера), v2=кол-во дорог
        code = int(node.data_nodes[0]["code"])
        buffer.extend(struct.pack("<IIffffI", 0, len(node.data_nodes), *node.bbox, code))
        # Data Nodes: v1=Смещение MLP (>=8), v2=Индекс БД
        for d in node.data_nodes:
            buffer.extend(struct.pack("<IIffffI", d["v1"], d["v2"], *d["bbox"], int(d["code"])))

# =========================================================
# УТИЛИТЫ БД И ГЛАВНАЯ ЛОГИКА
# =========================================================

def make_string_field(text, length):
    return str(text).encode('utf-8')[:length].ljust(length, b'\x00')

def make_dbf_descriptor(name, length):
    desc = name.encode('ascii').ljust(11, b'\x00')
    desc += b'C' + b'\x00' * 4 + bytes([length]) + b'\x00' * 15
    return desc

def compile_db(db_records, out_file):
    bin_records = bytearray()
    bin_records += b'\x00' * RECORD_LEN  # Пустая Record 0
    
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
    dbf_header += b'\x03\x00\x00\x00' + struct.pack('<I', total_records)
    dbf_header += struct.pack('<H', DBF_HEADER_LEN) + struct.pack('<H', RECORD_LEN) + b'\x00' * 20
    dbf_header += make_dbf_descriptor("osm_id", 12) + make_dbf_descriptor("code", 4)
    dbf_header += make_dbf_descriptor("fclass", 28) + make_dbf_descriptor("name", 100) + b'\x0D'
    
    total_size = YZL_SIZE + DBF_HEADER_LEN + len(bin_records)
    with open(out_file, 'wb') as f:
        f.write(b'YZL\x00' + struct.pack('<I', total_size) + b'\x00' * 24)
        f.write(dbf_header)
        f.write(bin_records)

def create_map_name(name, items, out_file):
    if not items: return
    minx = min(i["bbox"][0] for i in items)
    miny = min(i["bbox"][1] for i in items)
    maxx = max(i["bbox"][2] for i in items)
    maxy = max(i["bbox"][3] for i in items)
    
    map_info = {"centerLat": (miny+maxy)/2.0, "centerLon": (minx+maxx)/2.0, "mapName": name}
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(map_info, f, separators=(',', ':'))

def main():
    if not os.path.exists("roads_meta.json"): return
    with open("roads_meta.json", 'r', encoding='utf-8') as f:
        items = json.load(f)

    create_map_name("DT_Map", items, "map.name")

    db_records = []
    db_counter = 2 
    for item in items:
        if item.get("name"):
            item["v2"] = db_counter
            db_counter += 1
            db_records.append(item)
        else:
            item["v2"] = 1 

    print("[>] Создание листовых кластеров...")
    blocks = []
    for i in range(0, len(items), CHUNK_SIZE):
        blocks.append(LeafBlock(items[i:i+CHUNK_SIZE]))

    print(f"[>] Построение BVH дерева из {len(blocks)} кластеров...")
    bvh_root = build_bvh(blocks)

    idx_buffer = bytearray()
    
    # --- TREE 0 ---
    idx_buffer.extend(b'SQT\x01' + struct.pack("<I", 1))
    assign_offsets(bvh_root, YZL_SIZE + SQT_HEADER_SIZE)
    serialize_tree(bvh_root, idx_buffer)
    idx_buffer.extend(b'\x00' * 8)
    
    # --- TREE 1 & 2 ---
    for _ in range(2):
        idx_buffer.extend(b'SQT\x01' + struct.pack("<I", 1) + b'\x00' * 8)
    
    with open("roads.idx", "wb") as f:
        f.write(b'YZL\x00' + struct.pack("<I", YZL_SIZE + len(idx_buffer)) + b'\x00' * 24)
        f.write(idx_buffer)

    compile_db(db_records, "roads.db")
    print("\n[УСПЕХ] Скомпилировано идеальное BVH дерево! Загружайте мульти-карту!")

if __name__ == "__main__":
    import sys
    sys.setrecursionlimit(200000)
    main()