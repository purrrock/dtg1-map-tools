#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 -> GeoJSON exporter (Roads, Landuse, Water ONLY)
======================================================
Архитектура: Flat List State Machine (Плоский список).
Парсер строго следует аппаратному алгоритму чтения прошивки DT G1.
"""

import os
import sys
import json
import struct
import argparse
from pathlib import Path

YZL_SIZE = 32
SQT_HEADER_SIZE = 8
NODE_SIZE = 28

# =========================================================
# UTILS
# =========================================================

def decode_str(b):
    return b.split(b"\x00")[0].decode("utf-8", errors="ignore").strip()

# =========================================================
# DB PARSER
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

        osm_id = decode_str(rec[1:13])
        code = decode_str(rec[13:17])
        fclass = decode_str(rec[17:45])
        name = decode_str(rec[45:])

        records.append({
            "osm_id": osm_id,
            "code": code,
            "fclass": fclass,
            "name": name,
        })

    return records

# =========================================================
# RIGID STATE MACHINE IDX PARSER (REVERSE-ENGINEERED)
# =========================================================

def parse_idx(path):
    """
    Сканирует .idx файл и возвращает ТОЛЬКО валидные узлы данных.
    Использует строгий автомат состояний плоского списка.
    """
    nodes = []
    if not path.exists():
        return nodes

    with open(path, "rb") as f:
        data = f.read()

    size = len(data)
    offset = YZL_SIZE

    while offset < size:
        sqt_idx = data.find(b"SQT\x01", offset)
        if sqt_idx == -1:
            break

        offset = sqt_idx + SQT_HEADER_SIZE
        next_sqt = data.find(b"SQT\x01", offset)
        limit = next_sqt if next_sqt != -1 else size

        # Инициализация автомата состояний для текущего LOD-уровня
        data_nodes_left = 0

        while offset + NODE_SIZE <= limit:
            raw = data[offset:offset+NODE_SIZE]
            
            # Пропуск нулевого терминатора/выравнивания
            if raw == b'\x00' * NODE_SIZE:
                offset += NODE_SIZE
                continue

            v1, v2 = struct.unpack("<II", raw[:8])

            if data_nodes_left > 0:
                # СОСТОЯНИЕ 3: Чтение целевых Данных (Data Node)
                code = struct.unpack("<I", raw[24:28])[0]
                nodes.append({
                    "offset": offset,
                    "v1": v1,
                    "v2": v2,
                    "code": code
                })
                data_nodes_left -= 1
            else:
                if v1 == 0:
                    # СОСТОЯНИЕ 2: Заголовок кластера (Cluster Header)
                    # Извлекаем v2 (Кол-во объектов + 1) для настройки счетчика
                    data_nodes_left = v2 - 1 if v2 > 0 else 0
                else:
                    # СОСТОЯНИЕ 1: Узел перехода (Navigation Node)
                    # Мы парсим файл целиком, поэтому прыжки (v3) нам не нужны. Просто пропускаем.
                    pass

            offset += NODE_SIZE
            
        offset = limit

    return nodes

# =========================================================
# FAST MLP PARSER
# =========================================================

def read_mlp_geometry_fast(f_mlp, v1, size):
    abs_offset = YZL_SIZE + v1 - 8
    if abs_offset < 0 or abs_offset >= size:
        return None

    try:
        f_mlp.seek(abs_offset)
        
        header_data = f_mlp.read(32)
        if len(header_data) < 32:
            return None
            
        record_number = struct.unpack(">I", header_data[:4])[0]
        _, _, _, _, _, num_parts, num_points = struct.unpack("<IiiiiII", header_data[4:32])

        if num_parts > 50000 or num_points > 500000:
            return None

        parts_data = f_mlp.read(num_parts * 4)
        parts = struct.unpack(f"<{num_parts}I", parts_data)

        points_data = f_mlp.read(num_points * 8)
        points_raw = struct.unpack(f"<{num_points * 2}i", points_data)
        
        points = [[points_raw[i] / 1_000_000.0, points_raw[i+1] / 1_000_000.0] for i in range(0, len(points_raw), 2)]

        return {
            "record_number": record_number,
            "parts": list(parts),
            "points": points,
        }
    except Exception:
        return None

# =========================================================
# GEOMETRY BUILDERS
# =========================================================

def build_linestring(parts, points):
    if not points: return None
    if len(parts) <= 1:
        return {"type": "LineString", "coordinates": points}

    lines = []
    for i in range(len(parts)):
        start = parts[i]
        end = parts[i + 1] if i + 1 < len(parts) else len(points)
        lines.append(points[start:end])

    return {"type": "MultiLineString", "coordinates": lines}

def build_polygon(parts, points):
    if not points: return None
    rings = []
    for i in range(len(parts)):
        start = parts[i]
        end = parts[i + 1] if i + 1 < len(parts) else len(points)
        ring = points[start:end]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        rings.append(ring)

    return {"type": "Polygon", "coordinates": rings}

# =========================================================
# EXPORT CORE
# =========================================================

def export_layer(base_dir, layer, debug=False):
    idx_path = base_dir / f"{layer}.idx"
    db_path = base_dir / f"{layer}.db"
    mlp_path = base_dir / f"{layer}.mlp"

    print(f"\n[+] Layer: {layer}")

    if not idx_path.exists() or not mlp_path.exists():
        print("    [!] Missing .idx or .mlp files. Skipping.")
        return None

    sys.stdout.write("    [>] Parsing DB... ")
    sys.stdout.flush()
    db_records = parse_db(db_path)
    print("Done.")

    sys.stdout.write("    [>] Parsing IDX (Strict State Machine)... ")
    sys.stdout.flush()
    # Теперь parse_idx возвращает только 100% валидные Data Nodes
    nodes = parse_idx(idx_path)
    print("Done.")

    print(f"    [i] DB records : {len(db_records)}")
    print(f"    [i] Target nodes: {len(nodes)}")
    print("    [>] Extracting Geometry & Building GeoJSON...")

    features = []
    named_count = 0
    unnamed_count = 0
    geometry_missing = 0

    mlp_size = os.path.getsize(mlp_path)
    total_nodes = len(nodes)

    with open(mlp_path, "rb") as f_mlp:
        for i, node in enumerate(nodes):
            
            if i % 1000 == 0 or i == total_nodes - 1:
                progress = (i + 1) / total_nodes * 100
                sys.stdout.write(f"\r        Progress: {i+1}/{total_nodes} ({progress:.1f}%)")
                sys.stdout.flush()

            props = {
                "osm_id": "",
                "code": str(node["code"]) if node["code"] != 0 else "",
                "fclass": "",
                "name": "",
            }

            # Привязка базы данных
            if node["v2"] > 0:
                db_index = node["v2"] - 1
                if db_index < len(db_records) and db_records[db_index]:
                    props.update(db_records[db_index])

            if props["name"]:
                named_count += 1
            else:
                unnamed_count += 1

            # Привязка геометрии
            g = read_mlp_geometry_fast(f_mlp, node["v1"], mlp_size)

            if not g:
                geometry_missing += 1
                continue

            if layer == "roads":
                geom = build_linestring(g["parts"], g["points"])
            else:
                geom = build_polygon(g["parts"], g["points"])

            if not geom:
                continue

            feature = {
                "type": "Feature",
                "geometry": geom,
                "properties": props
            }

            if debug:
                feature["properties"]["_v1"] = node["v1"]
                feature["properties"]["_v2"] = node["v2"]
                feature["properties"]["_idx_offset"] = hex(node["offset"])

            features.append(feature)

    print() 

    geojson = {
        "type": "FeatureCollection",
        "features": features
    }

    out_path = base_dir / f"{layer}.geojson"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print(f"    [*] Exported     : {len(features)}")
    print(f"    [*] Named        : {named_count}")
    print(f"    [*] Unnamed      : {unnamed_count}")
    print(f"    [*] No geometry  : {geometry_missing}")
    print(f"    [*] Saved        : {out_path.name}")

    return {
        "layer": layer,
        "features": len(features),
        "named": named_count,
        "unnamed": unnamed_count,
        "db_records": len(db_records),
        "idx_nodes": total_nodes,
        "geometry_missing": geometry_missing,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default=".", help="map directory")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    base_dir = Path(args.directory)
    layers = ["roads", "landuse", "water"]
    stats = []

    for layer in layers:
        idx_file = base_dir / f"{layer}.idx"
        if not idx_file.exists():
            continue

        s = export_layer(base_dir, layer, debug=args.debug)
        if s:
            stats.append(s)

    print("\n=================================================")
    print("TOTAL STATISTICS")
    print("=================================================")

    total = 0
    for s in stats:
        total += s["features"]
        print(
            f"{s['layer']:10} "
            f"features={s['features']:6} "
            f"named={s['named']:6} "
            f"unnamed={s['unnamed']:6}"
        )

    print("-------------------------------------------------")
    print(f"TOTAL FEATURES: {total}")
    print("=================================================")

if __name__ == "__main__":
    main()