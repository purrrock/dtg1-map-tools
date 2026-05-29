#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 -> OSM XML Exporter (Round-Trip Validation Tool)
======================================================
Инструмент декомпиляции бинарных карт C175C1 обратно в векторный формат OpenStreetMap.
Предназначен для побайтовой валидации (Round-Trip Test) алгоритмов компилятора.

Особенности архитектуры:
  1. Synthetic IDs: Генерирует отрицательные ID для всех нодов, веев и релейшенов.
  2. Inverse Winding Rules: Анализирует закрученность полигонов Гауссом для 
     автоматического восстановления ролей (outer/inner) в мультиполигонах.
  3. Tag Demultiplexing: Восстанавливает составные теги (например, tracktype).
"""

import os
import sys
import struct
import argparse
from pathlib import Path
from xml.sax.saxutils import escape

YZL_SIZE = 32
SQT_HEADER_SIZE = 8
NODE_SIZE = 28

# Глобальные счетчики для синтетических OSM ID
# В OSM кастомные/несохраненные элементы традиционно имеют отрицательные ID
g_node_id = -1
g_way_id = -1
g_relation_id = -1

# Глобальные хранилища для потоковой сборки XML
g_node_cache = {}  # (lon, lat) -> node_id
g_ways = []        # {"id": int, "nodes": [node_id...], "tags": {}}
g_relations = []   # {"id": int, "members": [{"type": "way", "ref": id, "role": str}...], "tags": {}}

# =========================================================
# УТИЛИТЫ И ГЕОМЕТРИЯ
# =========================================================

def decode_str(b):
    return b.split(b"\x00")[0].decode("utf-8", errors="ignore").strip()

def is_clockwise(points):
    """
    Обратная шнуровка Гаусса для восстановления топологии.
    Отрицательная площадь в декартовой системе (где Y - Latitude) означает CW (Outer).
    """
    sum_area = 0.0
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        sum_area += (x1 * y2 - x2 * y1)
    return sum_area < 0

def get_or_create_node(lon, lat):
    global g_node_id
    # Округление защищает от микро-погрешностей float32 -> float64
    coord = (round(lon, 7), round(lat, 7))
    if coord not in g_node_cache:
        g_node_cache[coord] = g_node_id
        g_node_id -= 1
    return g_node_cache[coord]

# =========================================================
# БИНАРНЫЕ ПАРСЕРЫ
# =========================================================

def parse_db(path):
    records = []
    if not path.exists():
        return records

    with open(path, "rb") as f:
        data = f.read()

    header_len = struct.unpack("<H", data[0x28:0x2A])[0]
    record_len = struct.unpack("<H", data[0x2A:0x2C])[0]

    if record_len <= 1:
        return records

    record_count = (len(data) - YZL_SIZE - header_len) // record_len
    base = YZL_SIZE + header_len

    for i in range(record_count):
        start = base + i * record_len
        end = start + record_len
        rec = data[start:end]

        if len(rec) != record_len or rec[0] == 0x2A:
            records.append(None)
            continue

        records.append({
            "osm_id": decode_str(rec[1:13]),
            "code": decode_str(rec[13:17]),
            "fclass": decode_str(rec[17:45]),
            "name": decode_str(rec[45:]),
        })

    return records

def parse_idx(path):
    """Строгий Flat List парсер узлов данных (Data Nodes)."""
    nodes = []
    if not path.exists():
        return nodes

    with open(path, "rb") as f:
        data = f.read()

    size, offset = len(data), YZL_SIZE

    while offset < size:
        sqt_idx = data.find(b"SQT\x01", offset)
        if sqt_idx == -1: break

        offset = sqt_idx + SQT_HEADER_SIZE
        next_sqt = data.find(b"SQT\x01", offset)
        limit = next_sqt if next_sqt != -1 else size
        data_nodes_left = 0

        while offset + NODE_SIZE <= limit:
            raw = data[offset:offset+NODE_SIZE]
            if raw == b'\x00' * NODE_SIZE:
                offset += NODE_SIZE
                continue

            v1, v2 = struct.unpack("<II", raw[:8])

            if data_nodes_left > 0:
                nodes.append({
                    "v1": v1, "v2": v2,
                    "code": struct.unpack("<I", raw[24:28])[0]
                })
                data_nodes_left -= 1
            else:
                if v1 == 0:
                    data_nodes_left = v2 - 1 if v2 > 0 else 0

            offset += NODE_SIZE
        offset = limit

    return nodes

def read_mlp_geometry_fast(f_mlp, v1, size):
    abs_offset = YZL_SIZE + v1 - 8
    if abs_offset < 0 or abs_offset >= size: return None

    try:
        f_mlp.seek(abs_offset)
        header_data = f_mlp.read(32)
        if len(header_data) < 32: return None
            
        _, _, _, _, _, num_parts, num_points = struct.unpack("<IiiiiII", header_data[4:32])
        if num_parts > 50000 or num_points > 500000: return None

        parts = struct.unpack(f"<{num_parts}I", f_mlp.read(num_parts * 4))
        points_raw = struct.unpack(f"<{num_points * 2}i", f_mlp.read(num_points * 8))
        
        points = [[points_raw[i] / 1e6, points_raw[i+1] / 1e6] for i in range(0, len(points_raw), 2)]
        return {"parts": list(parts), "points": points}
    except Exception:
        return None

# =========================================================
# ГЕНЕРАТОРЫ OSM СУЩНОСТЕЙ
# =========================================================

def build_osm_tags(layer, fclass, name, code, osm_id):
    """Демультиплексор атрибутов обратно в формат тегов OSM."""
    tags = {}
    if layer == "roads":
        if fclass.startswith("track_grade"):
            tags["highway"] = "track"
            tags["tracktype"] = fclass.split("_")[1]
        else:
            tags["highway"] = fclass
    elif layer == "water":
        tags["natural"] = "water"
    else:  # landuse
        tags["landuse"] = fclass

    if name: tags["name"] = name
    if osm_id: tags["dtg_osm_id"] = osm_id
    tags["dtg_code"] = str(code)
    return tags

def process_geometry(layer, geom_data, tags):
    global g_way_id, g_relation_id
    parts = geom_data["parts"]
    points = geom_data["points"]
    if not points: return

    rings = []
    for i in range(len(parts)):
        start = parts[i]
        end = parts[i + 1] if i + 1 < len(parts) else len(points)
        ring = points[start:end]
        
        # Замыкаем контур для полигонов, если железо его разомкнуло
        if layer != "roads" and ring and ring[0] != ring[-1]:
            ring.append(ring[0])
            
        if ring: rings.append(ring)

    if not rings: return

    # Обработка линий (Дороги)
    if layer == "roads":
        for ring in rings:
            node_ids = [get_or_create_node(lon, lat) for lon, lat in ring]
            g_ways.append({"id": g_way_id, "nodes": node_ids, "tags": tags.copy()})
            g_way_id -= 1
        return

    # Обработка полигонов (Landuse, Water)
    if len(rings) == 1:
        # Простой полигон (Single Way)
        node_ids = [get_or_create_node(lon, lat) for lon, lat in rings[0]]
        g_ways.append({"id": g_way_id, "nodes": node_ids, "tags": tags})
        g_way_id -= 1
    else:
        # Мультиполигон (Relation)
        members = []
        for ring in rings:
            node_ids = [get_or_create_node(lon, lat) for lon, lat in ring]
            
            # Winding Rule Inference
            is_cw = is_clockwise(ring)
            role = "outer" if is_cw else "inner"
            
            # Записываем кольцо как Way без тегов
            current_way_id = g_way_id
            g_ways.append({"id": current_way_id, "nodes": node_ids, "tags": {}})
            g_way_id -= 1
            
            members.append({"type": "way", "ref": current_way_id, "role": role})
        
        tags["type"] = "multipolygon"
        g_relations.append({"id": g_relation_id, "members": members, "tags": tags})
        g_relation_id -= 1

# =========================================================
# ЯДРО ЭКСПОРТА
# =========================================================

def export_to_memory(base_dir, layer):
    idx_path = base_dir / f"{layer}.idx"
    db_path = base_dir / f"{layer}.db"
    mlp_path = base_dir / f"{layer}.mlp"

    if not idx_path.exists() or not mlp_path.exists():
        return False

    print(f"\n[+] Чтение слоя: {layer}")
    db_records = parse_db(db_path)
    nodes = parse_idx(idx_path)
    mlp_size = os.path.getsize(mlp_path)
    
    with open(mlp_path, "rb") as f_mlp:
        for i, node in enumerate(nodes):
            if i % 1000 == 0 or i == len(nodes) - 1:
                sys.stdout.write(f"\r    Прогресс декомпиляции: {i+1}/{len(nodes)}")
                sys.stdout.flush()

            # Извлекаем атрибуты
            fclass, name, osm_id = "unknown", "", ""
            if node["v2"] > 0:
                db_idx = node["v2"] - 1
                if db_idx < len(db_records) and db_records[db_idx]:
                    rec = db_records[db_idx]
                    fclass, name, osm_id = rec["fclass"], rec["name"], rec["osm_id"]

            geom = read_mlp_geometry_fast(f_mlp, node["v1"], mlp_size)
            if not geom: continue

            tags = build_osm_tags(layer, fclass, name, node["code"], osm_id)
            process_geometry(layer, geom, tags)
            
    print(" - Готово.")
    return True

def write_osm_xml(out_path):
    print(f"\n[>] Запись векторных данных в {out_path}...")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<osm version="0.6" generator="DT_G1_Reverse_Compiler">\n')
        
        # 1. Запись Nodes (Кэш перегоняем в файл)
        print(f"    Запись {len(g_node_cache)} узлов (nodes)...")
        for (lon, lat), nid in g_node_cache.items():
            f.write(f'  <node id="{nid}" lat="{lat:.7f}" lon="{lon:.7f}" />\n')

        # 2. Запись Ways
        print(f"    Запись {len(g_ways)} линий/контуров (ways)...")
        for way in g_ways:
            f.write(f'  <way id="{way["id"]}">\n')
            for nid in way["nodes"]:
                f.write(f'    <nd ref="{nid}" />\n')
            for k, v in way["tags"].items():
                f.write(f'    <tag k="{escape(k)}" v="{escape(str(v))}" />\n')
            f.write('  </way>\n')

        # 3. Запись Relations (Multipolygons)
        print(f"    Запись {len(g_relations)} мультиполигонов (relations)...")
        for rel in g_relations:
            f.write(f'  <relation id="{rel["id"]}">\n')
            for mem in rel["members"]:
                f.write(f'    <member type="{mem["type"]}" ref="{mem["ref"]}" role="{mem["role"]}" />\n')
            for k, v in rel["tags"].items():
                f.write(f'    <tag k="{escape(k)}" v="{escape(str(v))}" />\n')
            f.write('  </relation>\n')

        f.write('</osm>\n')
    print("[*] Экспорт успешно завершен.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default=".", help="Папка с бинарниками карт (.idx, .mlp, .db)")
    args = parser.parse_args()
    base_dir = Path(args.directory)

    print("=================================================")
    print("DT G1 -> OSM XML ROUND-TRIP EXPORTER")
    print("=================================================")

    layers = ["roads", "landuse", "water"]
    processed = 0

    for layer in layers:
        if export_to_memory(base_dir, layer):
            processed += 1

    if processed > 0:
        write_osm_xml(base_dir / "map.osm")
    else:
        print("\n[-] Ошибка: Файлы карт не найдены в указанной директории.")

if __name__ == "__main__":
    main()