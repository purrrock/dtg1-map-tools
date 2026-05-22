#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import struct
import argparse
from pathlib import Path

def dump_idx(file_path, out_csv):
    if not os.path.exists(file_path):
        print(f"[-] Файл {file_path} не найден.")
        return

    with open(file_path, "rb") as f:
        data = f.read()

    size = len(data)
    with open(out_csv, "w", encoding="utf-8") as out:
        out.write("Offset_Hex,LOD_Level,NodeType,v1,v2,v3_or_Code,MinX,MinY,MaxX,MaxY,Raw_Hex\n")
        out.write(f"0x00,-,YZL Header,,,,,,,,{data[:32].hex()}\n")

        offset = 32
        lod_level = 0

        while offset < size:
            if data[offset:offset+4] == b"SQT\x01":
                param = struct.unpack("<I", data[offset+4:offset+8])[0]
                out.write(f"{hex(offset)},{lod_level},SQT Header,,,Param:{param},,,,,{data[offset:offset+8].hex()}\n")
                offset += 8
                
                data_nodes_left = 0
                next_sqt = data.find(b"SQT\x01", offset)
                if next_sqt == -1: next_sqt = size

                while offset < next_sqt:
                    rem = next_sqt - offset
                    if rem < 28:
                        pad_raw = data[offset:next_sqt]
                        node_type = f"Padding ({rem} bytes)" if pad_raw == b'\x00' * rem else "Unknown Tail"
                        out.write(f"{hex(offset)},{lod_level},{node_type},,,,,,,,,{pad_raw.hex()}\n")
                        offset = next_sqt
                        break

                    node_raw = data[offset:offset+28]
                    v1, v2 = struct.unpack("<II", node_raw[:8])
                    
                    if data_nodes_left > 0:
                        v1, v2, minx, miny, maxx, maxy, code = struct.unpack("<IIffffI", node_raw)
                        out.write(f"{hex(offset)},{lod_level},Data Node,{v1},{v2},{code},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{node_raw.hex()}\n")
                        data_nodes_left -= 1
                    else:
                        if v1 == 0:
                            v1, v2, minx, miny, maxx, maxy, code = struct.unpack("<IIffffI", node_raw)
                            out.write(f"{hex(offset)},{lod_level},Cluster Header,{v1},{v2},{code},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{node_raw.hex()}\n")
                            data_nodes_left = v2 - 1 if v2 > 0 else 0
                        else:
                            v1, v2, v3, minx, miny, maxx, maxy = struct.unpack("<IIIffff", node_raw)
                            out.write(f"{hex(offset)},{lod_level},Branch Node,{v1},{v2},{v3},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{node_raw.hex()}\n")

                    offset += 28
                lod_level += 1
            else:
                next_sqt = data.find(b"SQT\x01", offset)
                if next_sqt == -1: next_sqt = size
                rem = next_sqt - offset
                out.write(f"{hex(offset)},{lod_level},Unknown Chunk,,,,,,,,, {data[offset:offset+rem].hex()}\n")
                offset += rem
    print(f"[+] Дамп сохранен в {out_csv}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("idx_file")
    args = parser.parse_args()
    dump_idx(Path(args.idx_file), Path(args.idx_file).with_name(f"{Path(args.idx_file).stem}_dump.csv"))