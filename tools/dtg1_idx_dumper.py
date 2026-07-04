#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 IDX Dumper (CSV Export)
=============================
Utility to create readable CSV dumps from binary spatial indices (.idx).
Refactoring for Specification v4.0 (C-Union Node Architecture & Mode Switch).
"""

import os
import struct
import argparse

def dump_idx(file_path: str, out_csv: str) -> None:
    if not os.path.exists(file_path):
        print(f"[-] File {file_path} not found.")
        return

    with open(file_path, "rb") as f:
        data = f.read()

    size = len(data)
    with open(out_csv, "w", encoding="utf-8") as out:
        # Unified CSV header. Code_or_v3Jump column changes meaning depending on node type (C-Union)
        out.write("Offset_Hex,LOD_Level,Mode,NodeType,v1,v2,Code_or_v3Jump,MinX,MinY,MaxX,MaxY,Raw_Hex\n")
        
        # Global YZL container always takes the first 32 bytes of memory
        if size < 32:
            print("[-] Error: File is too small for YZL container.")
            return
            
        out.write(f"0x00,-,-,YZL Header,,,,,,,,{data[:32].hex()}\n")

        offset = 32
        lod_level = 0

        while offset < size:
            # 1. Search for Level of Detail signature (LOD SQT)
            if data[offset:offset+4] == b"SQT\x01":
                # SQT header takes strictly 16 bytes:
                # [Magic 4b] [Padding 4b] [Mode 4b] [Count 4b]
                header_raw = data[offset:offset+16]
                if len(header_raw) < 16:
                    break
                    
                mode, count = struct.unpack("<II", header_raw[8:16])
                mode_str = "Clustered" if mode == 1 else "Flat List"
                
                out.write(f"{hex(offset)},{lod_level},{mode_str},SQT Header,,,{count},,,,,{header_raw.hex()}\n")
                offset += 16
                
                # 2. Processing empty layer (System Dummy / EOF Panic protection)
                if count == 0:
                    lod_level += 1
                    continue
                    
                # 3. Parsing state machine nodes depending on Mode
                if mode == 1:
                    # Mode 0x01 (Clustered): First Nav Node is read, then nested Data Nodes
                    clusters_processed = 0
                    while clusters_processed < count and offset < size:
                        nav_raw = data[offset:offset+28]
                        if len(nav_raw) < 28:
                            break
                            
                        # Unpacking Nav Node (C-Union pattern: <IffffII)
                        # v3_jump: hardware jump pointer (cluster size + 8 bytes of prefetch compensation)
                        # v1: reserved (usually 0)
                        # v2: number of Data Nodes in this cluster
                        v3_jump, minx, miny, maxx, maxy, v1, v2 = struct.unpack("<IffffII", nav_raw)
                        out.write(f"{hex(offset)},{lod_level},{mode_str},Nav Node,{v1},{v2},{v3_jump},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{nav_raw.hex()}\n")
                        offset += 28
                        
                        data_nodes_count = v2
                        
                        for _ in range(data_nodes_count):
                            if offset >= size: break
                            data_raw = data[offset:offset+28]
                            
                            # Unpacking Data Node (C-Union pattern: <ffffIII)
                            # code: system alias of object style (LUT)
                            # d_v1: absolute geometry offset in .mlp
                            # d_v2: row index in attribute DB .db
                            d_minx, d_miny, d_maxx, d_maxy, code, d_v1, d_v2 = struct.unpack("<ffffIII", data_raw)
                            out.write(f"{hex(offset)},{lod_level},{mode_str},Data Node,{d_v1},{d_v2},{code},{d_minx:.6f},{d_miny:.6f},{d_maxx:.6f},{d_maxy:.6f},{data_raw.hex()}\n")
                            offset += 28
                            
                        clusters_processed += 1
                        
                elif mode == 0:
                    # Mode 0x00 (Flat List): Only Data Nodes (without clustering and jumps)
                    nodes_processed = 0
                    while nodes_processed < count and offset < size:
                        data_raw = data[offset:offset+28]
                        if len(data_raw) < 28:
                            break
                            
                        d_minx, d_miny, d_maxx, d_maxy, code, d_v1, d_v2 = struct.unpack("<ffffIII", data_raw)
                        out.write(f"{hex(offset)},{lod_level},{mode_str},Data Node,{d_v1},{d_v2},{code},{d_minx:.6f},{d_miny:.6f},{d_maxx:.6f},{d_maxy:.6f},{data_raw.hex()}\n")
                        offset += 28
                        nodes_processed += 1
                        
                lod_level += 1
                
            else:
                # 4. Fallback for broken or unknown memory blocks (search for next SQT signature)
                next_sqt = data.find(b"SQT\x01", offset)
                if next_sqt == -1: 
                    next_sqt = size
                rem = next_sqt - offset
                chunk_hex = data[offset:offset+rem].hex()
                out.write(f"{hex(offset)},{lod_level},Unknown,Unknown Chunk,,,,,,,,{chunk_hex}\n")
                offset += rem
                
    print(f"[+] Dump successfully saved in {out_csv}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spatial index decompiler DT G1 (.idx)")
    parser.add_argument("input", help="Path to .idx file")
    parser.add_argument("output", help="Path to save .csv dump")
    args = parser.parse_args()
    
    dump_idx(args.input, args.output)