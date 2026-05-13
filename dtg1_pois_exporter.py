#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 POIS Exporter (Standalone Debug Version)
==============================================
Изолированный скрипт для экспорта слоя POI.
Содержит защиту от аппаратных багов компилятора:
1. Wrap-around bug (смещение последнего индекса в 0)
2. Scrambled v2 bug (поврежденные указатели и фальшивые владельцы)
"""

import os
import json
import struct
import argparse
from pathlib import Path

YZL_SIZE = 32
SQT_HEADER_SIZE = 8
NODE_SIZE = 28

def decode_str(b):
    return b.split(b"\x00")[0].decode("utf-8", errors="ignore").strip()

def safe_int(val):
    try:
        return int(val)
    except (ValueError, TypeError):
        return -1

def parse_db(path):
    records = []
    if not path.exists():
        return records

    with open(path, "rb") as f:
        f.seek(0x28)
        header_len = struct.unpack("<H", f.read(2))[0]
        f.seek(0x2A)
        record_len = struct.unpack("<H", f.read(2))[0]

        if record_len <= 1:
            return records

        record_count = (os.path.getsize(path) - YZL_SIZE - header_len) // record_len
        base = YZL_SIZE + header_len

        for i in range(record_count):
            f.seek(base + i * record_len)
            rec = f.read(record_len)

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

def parse_pois_idx(path):
    leaves = []
    if not path.exists():
        return leaves

    with open(path, "rb") as f:
        data = f.read()

    size = len(data)
    offset = YZL_SIZE

    while offset < size:
        sqt_idx = data.find(b"SQT\x01", offset)
        if sqt_idx == -1:
            break

        offset = sqt_idx + SQT_HEADER_SIZE

        while offset + NODE_SIZE <= size:
            next_sqt = data.find(b"SQT\x01", offset)
            if next_sqt != -1 and (next_sqt - offset) < NODE_SIZE:
                break

            raw = data[offset:offset+NODE_SIZE]
            v1, v2, val_0x08 = struct.unpack("<III", raw[:12])

            # Для POI нам нужны только Leaf узлы (где на 0x08 лежит float MinX)
            if val_0x08 >= 1000000:
                minx, miny, maxx, maxy, code = struct.unpack("<ffffI", raw[8:28])
                
                # Убеждаемся, что геометрия схлопнута в точку
                if abs(minx - maxx) < 1e-9 and abs(miny - maxy) < 1e-9 and minx != 0.0:
                    leaves.append({
                        "offset": offset,
                        "v1": v1,
                        "v2": v2,
                        "code": code,
                        "bbox": [minx, miny],
                        "db_rec": None
                    })

            offset += NODE_SIZE

    return leaves

def export_pois(base_dir, debug=False):
    idx_path = base_dir / "pois.idx"
    db_path = base_dir / "pois.db"

    print("\n[+] Exporting POIS layer...")
    
    db_records = parse_db(db_path)
    leaves = parse_pois_idx(idx_path)

    print(f"    DB records : {len(db_records)}")
    print(f"    IDX points : {len(leaves)}")

    # ==========================================
    # ИНТЕЛЛЕКТУАЛЬНЫЙ 2-PASS MATCHING
    # ==========================================
    available_db = {i: rec for i, rec in enumerate(db_records) if rec is not None}
    unassigned_leaves = []

    # Pass 1: Строгое совпадение с проверкой Истинного Владельца
    for leaf in leaves:
        v2 = leaf["v2"]
        db_index = 0 if v2 == len(db_records) else v2
        
        # Магическая формула: v1 == v2 * 16 - 8
        is_true_owner = (leaf["v1"] == v2 * 16 - 8)
        
        if is_true_owner and db_index in available_db and safe_int(available_db[db_index]["code"]) == leaf["code"]:
            leaf["db_rec"] = available_db[db_index]
            del available_db[db_index]
            if debug: print(f"    [Pass 1] Linked {leaf['db_rec']['name']} to offset {hex(leaf['offset'])}")
        else:
            unassigned_leaves.append(leaf)

    # Pass 2: Восстановление поврежденных узлов по Code
    pass_2_count = 0
    for leaf in unassigned_leaves:
        for idx, rec in list(available_db.items()):
            if safe_int(rec["code"]) == leaf["code"]:
                leaf["db_rec"] = rec
                del available_db[idx]
                pass_2_count += 1
                if debug: print(f"    [Pass 2] Salvaged {rec['name']} at offset {hex(leaf['offset'])}")
                break

    # ==========================================
    # ГЕНЕРАЦИЯ GEOJSON
    # ==========================================
    features = []
    named_count = 0

    for leaf in leaves:
        props = {
            "osm_id": "",
            "code": str(leaf["code"]) if leaf["code"] != 0 else "",
            "fclass": "",
            "name": "",
        }

        if leaf["db_rec"]:
            props.update(leaf["db_rec"])
            
        if props["name"]:
            named_count += 1

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": leaf["bbox"]
            },
            "properties": props
        }

        if debug:
            feature["properties"]["_v1"] = leaf["v1"]
            feature["properties"]["_v2"] = leaf["v2"]
            feature["properties"]["_idx_offset"] = hex(leaf["offset"])

        features.append(feature)

    out_path = base_dir / "pois.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "type": "FeatureCollection",
            "features": features
        }, f, ensure_ascii=False, indent=2)

    print(f"    Exported   : {len(features)} features")
    print(f"    Named      : {named_count}")
    print(f"    Pass 2 Fix : {pass_2_count} items salvaged")
    print(f"    Saved as   : {out_path.name}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default=".", help="map directory")
    parser.add_argument("--debug", action="store_true", help="show verbose matching info")
    args = parser.parse_args()

    export_pois(Path(args.directory), debug=args.debug)

if __name__ == "__main__":
    main()