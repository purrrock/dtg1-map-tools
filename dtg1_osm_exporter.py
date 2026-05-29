#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 -> OSM XML Exporter (Hardware LUT Revision)
======================================================
Инструмент декомпиляции бинарных карт C175C1 обратно в векторный формат OpenStreetMap.

Исправления версии 2.0:
  1. Hardware LUT: Полный отказ от строкового поля fclass для классификации. 
     Теги восстанавливаются строго по аппаратному полю code (uint32) из SQT-индекса.
  2. Demultiplexing: Точное восстановление составных тегов (tracktype).
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

g_node_id = -1
g_way_id = -1
g_relation_id = -1

g_node_cache = {}
g_ways = []
g_relations = []

# =========================================================
# REVERSE LOOK-UP TABLE (На основе features.csv)
# Единственный достоверный источник типизации объектов
# =========================================================

REVERSE_LUT = {
    # Highway
    5111: {"highway": "motorway"}, 5112: {"highway": "trunk"},
    5113: {"highway": "primary"}, 5114: {"highway": "secondary"},
    5115: {"highway": "tertiary"}, 5121: {"highway": "unclassified"},
    5122: {"highway": "residential"}, 5123: {"highway": "living_street"},
    5124: {"highway": "pedestrian"}, 5125: {"highway": "busway"},
    5131: {"highway": "motorway_link"}, 5132: {"highway": "trunk_link"},
    5133: {"highway": "primary_link"}, 5134: {"highway": "secondary_link"},
    5135: {"highway": "tertiary_link"}, 5141: {"highway": "service"},
    5142: {"highway": "track"}, 
    # Составные теги (Demultiplexing)
    5143: {"highway": "track", "tracktype": "grade1"},
    5144: {"highway": "track", "tracktype": "grade2"},
    5145: {"highway": "track", "tracktype": "grade3"},
    5146: {"highway": "track", "tracktype": "grade4"},
    5147: {"highway": "track", "tracktype": "grade5"},
    5151: {"highway": "bridleway"}, 5152: {"highway": "cycleway"},
    5153: {"highway": "footway"}, 5154: {"highway": "path"},
    5155: {"highway": "steps"}, 5199: {"highway": "road"},
    
    # Полигоны (Landuse, Leisure, Natural)
    7201: {"landuse": "forest"}, 7202: {"leisure": "park"},
    7203: {"landuse": "residential"}, 7204: {"landuse": "industrial"},
    7206: {"landuse": "cemetery"}, 7207: {"landuse": "allotments"},
    7208: {"landuse": "meadow"}, 7209: {"landuse": "commercial"},
    7210: {"leisure": "nature_reserve"}, 7211: {"leisure": "recreation_ground"},
    7212: {"landuse": "retail"}, 7213: {"landuse": "military"},
    7214: {"landuse": "quarry"}, 7215: {"landuse": "orchard"},
    7216: {"landuse": "vineyard"}, 7217: {"landuse": "scrub"},
    7218: {"landuse": "grass"}, 7219: {"natural": "heath"},
    7228: {"landuse": "farmland"}, 7229: {"landuse": "farmyard"},
    7233: {"landuse": "landfill"},
    
    # Вода
    8200: {"natural": "water"}
}

# =========================================================
# УТИЛИТЫ И ГЕОМЕТРИЯ
# =========================================================

def decode_str(b):
    return b.split(b"\x00")[0].decode("utf-8", errors="ignore").strip()

def is_clockwise(points):
    """Шнуровка Гаусса (Отрицательная площадь == Clockwise)."""
    sum_area = 0.0
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        sum_area += (x1 * y2 - x2 * y1)
    return sum_area < 0

def get_or_create_node(lon, lat):
    global g_node_id
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
    if not path.exists(): return records
    with open(path, "rb") as f: data = f.read()

    header_len = struct.unpack("<H", data[0x28:0x2A])[0]
    record_len = struct.unpack("<H", data[0x2A:0x2C])[0]
    if record_len <= 1: return records

    record_count = (len(data) - YZL_SIZE - header_len) // record_len
    base = YZL_SIZE + header_len

    for i in range(record_count):
        rec = data[base + i * record_len : base + (i + 1) * record_len]
        if len(rec) != record_len or rec[0] == 0x2A:
            records.append(None)
            continue
        records.append({
            "osm_id": decode_str(rec[1:13]),
            "name": decode_str(rec[45:])
        })
    return records

def parse_idx(path):
    nodes = []
    if not path.exists(): return nodes
    with open(path, "rb") as f: data = f.read()
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

def build_osm_tags(layer, name, code, osm_id):
    """
    Построение OSM-тегов ИСКЛЮЧИТЕЛЬНО на базе аппаратного кода.
    """
    tags = {}
    
    # 1. Применяем Reverse LUT
    if code in REVERSE_LUT:
        tags.update(REVERSE_LUT[code])
    else:
        # Fallback для неизвестных кодов
        if layer == "roads": tags["highway"] = "road"
        elif layer == "water": tags["natural"] = "water"
        else: tags["landuse"] = "unknown"

    # 2. Добавляем имя, если оно было в БД
    if name: tags["name"] = name
    
    # 3. Инженерные метаданные (для отладки в JOSM)
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
        
        # Аппаратное замыкание колец
        if layer != "roads" and ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        if ring: rings.append(ring)

    if not rings: return

    if layer == "roads":
        for ring in rings:
            node_ids = [get_or_create_node(lon, lat) for lon, lat in ring]
            g_ways.append({"id": g_way_id, "nodes": node_ids, "tags": tags.copy()})
            g_way_id -= 1
        return

    # Обработка полигонов
    if len(rings) == 1:
        node_ids = [get_or_create_node(lon, lat) for lon, lat in rings[0]]
        g_ways.append({"id": g_way_id, "nodes": node_ids, "tags": tags})
        g_way_id -= 1
    else:
        # Мультиполигон с восстановлением ролей (inner/outer)
        members = []
        for ring in rings:
            node_ids = [get_or_create_node(lon, lat) for lon, lat in ring]
            is_cw = is_clockwise(ring)
            role = "outer" if is_cw else "inner"
            
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

    print(f"\n[+] Декомпиляция слоя: {layer}")
    db_records = parse_db(db_path)
    nodes = parse_idx(idx_path)
    mlp_size = os.path.getsize(mlp_path)
    
    with open(mlp_path, "rb") as f_mlp:
        for i, node in enumerate(nodes):
            if i % 1000 == 0 or i == len(nodes) - 1:
                sys.stdout.write(f"\r    Прогресс: {i+1}/{len(nodes)}")
                sys.stdout.flush()

            name, osm_id = "", ""
            if node["v2"] > 0:
                db_idx = node["v2"] - 1
                if db_idx < len(db_records) and db_records[db_idx]:
                    rec = db_records[db_idx]
                    name, osm_id = rec["name"], rec["osm_id"]

            geom = read_mlp_geometry_fast(f_mlp, node["v1"], mlp_size)
            if not geom: continue

            # Вызываем исправленный строитель тегов
            tags = build_osm_tags(layer, name, node["code"], osm_id)
            process_geometry(layer, geom, tags)
            
    print(" - Готово.")
    return True

def write_osm_xml(out_path):
    print(f"\n[>] Сохранение векторных данных в {out_path}...")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<osm version="0.6" generator="DT_G1_Reverse_Compiler_v2">\n')
        
        for (lon, lat), nid in g_node_cache.items():
            f.write(f'  <node id="{nid}" lat="{lat:.7f}" lon="{lon:.7f}" />\n')

        for way in g_ways:
            f.write(f'  <way id="{way["id"]}">\n')
            for nid in way["nodes"]: f.write(f'    <nd ref="{nid}" />\n')
            for k, v in way["tags"].items(): f.write(f'    <tag k="{escape(k)}" v="{escape(str(v))}" />\n')
            f.write('  </way>\n')

        for rel in g_relations:
            f.write(f'  <relation id="{rel["id"]}">\n')
            for mem in rel["members"]: f.write(f'    <member type="{mem["type"]}" ref="{mem["ref"]}" role="{mem["role"]}" />\n')
            for k, v in rel["tags"].items(): f.write(f'    <tag k="{escape(k)}" v="{escape(str(v))}" />\n')
            f.write('  </relation>\n')

        f.write('</osm>\n')
    print("[*] Экспорт успешно завершен. Можете проверить данные в JOSM.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default=".", help="Папка с бинарниками карт (.idx, .mlp, .db)")
    args = parser.parse_args()
    base_dir = Path(args.directory)

    print("=================================================")
    print("DT G1 -> OSM XML ROUND-TRIP EXPORTER (v2.0)")
    print("=================================================")

    processed = 0
    for layer in ["roads", "landuse", "water"]:
        if export_to_memory(base_dir, layer): processed += 1

    if processed > 0: write_osm_xml(base_dir / "map.osm")
    else: print("\n[-] Ошибка: Файлы карт не найдены в указанной директории.")

if __name__ == "__main__":
    main()