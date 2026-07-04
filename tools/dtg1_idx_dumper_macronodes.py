#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 IDX Dumper (CSV Export)
=============================
Utility to create readable CSV dumps from binary spatial indices (.idx).
Full support for Specification v4.0 (R-Tree Hierarchical SQT & C-Union Node).
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
        # Unified CSV header
        out.write("Offset_Hex,LOD_Level,Mode,NodeType,v1,v2,Code_or_v3Jump,MinX,MinY,MaxX,MaxY,Raw_Hex\n")
        
        if size < 32:
            print("[-] Error: File is too small for YZL container.")
            return
            
        out.write(f"0x00,-,-,YZL Header,,,,,,,,{data[:32].hex()}\n")

        offset = 32
        lod_level = 0

        while offset < size:
            # 1. Search for Level of Detail signature (LOD SQT)
            if data[offset:offset+4] == b"SQT\x01":
                header_raw = data[offset:offset+16]
                if len(header_raw) < 16:
                    break
                    
                mode, count = struct.unpack("<II", header_raw[8:16])
                mode_str = f"R-Tree (Depth {mode})" if mode > 1 else ("Clustered" if mode == 1 else "Flat List")
                
                out.write(f"{hex(offset)},{lod_level},{mode_str},SQT Header,,,{count},,,,,{header_raw.hex()}\n")
                offset += 16
                
                # 2. Processing empty layer (System Dummy)
                if count == 0:
                    lod_level += 1
                    continue
                
                # 3. Parsing state machine
                if mode == 0:
                    # Mode 0x00: Only Data Nodes (without Nav Nodes)
                    nodes_processed = 0
                    while nodes_processed < count and offset < size:
                        data_raw = data[offset:offset+28]
                        if len(data_raw) < 28: break
                            
                        d_minx, d_miny, d_maxx, d_maxy, code, d_v1, d_v2 = struct.unpack("<ffffIII", data_raw)
                        out.write(f"{hex(offset)},{lod_level},{mode_str},Data Node,{d_v1},{d_v2},{code},{d_minx:.6f},{d_miny:.6f},{d_maxx:.6f},{d_maxy:.6f},{data_raw.hex()}\n")
                        offset += 28
                        nodes_processed += 1
                
                elif mode >= 1:
                    # Mode >= 0x01: Hierarchical R-Tree
                    # Recursive traversal function (DFS)
                    def parse_r_tree(curr_offset: int, is_nav: bool) -> int:
                        if curr_offset + 28 > size: 
                            return curr_offset
                            
                        raw_data = data[curr_offset:curr_offset+28]
                        
                        if is_nav:
                            v3_jump, minx, miny, maxx, maxy, v1, v2 = struct.unpack("<IffffII", raw_data)
                            node_type = f"Nav Node (Lvl {v1})"
                            
                            out.write(f"{hex(curr_offset)},{lod_level},{mode_str},{node_type},{v1},{v2},{v3_jump},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{raw_data.hex()}\n")
                            curr_offset += 28
                            
                            # If v1 > 0, children are child Nav Nodes.
                            # If v1 == 0, children are leaf Data Nodes.
                            child_is_nav = (v1 > 0)
                            
                            for _ in range(v2):
                                curr_offset = parse_r_tree(curr_offset, child_is_nav)
                        else:
                            d_minx, d_miny, d_maxx, d_maxy, code, d_v1, d_v2 = struct.unpack("<ffffIII", raw_data)
                            out.write(f"{hex(curr_offset)},{lod_level},{mode_str},Data Node,{d_v1},{d_v2},{code},{d_minx:.6f},{d_miny:.6f},{d_maxx:.6f},{d_maxy:.6f},{raw_data.hex()}\n")
                            curr_offset += 28
                            
                        return curr_offset

                    # Count in SQT header for Mode >= 1 means the number of ROOT Nav nodes
                    for _ in range(count):
                        offset = parse_r_tree(offset, is_nav=True)
                        
                lod_level += 1
                
            else:
                # 4. Fallback (search for signature of next SQT)
                next_sqt = data.find(b"SQT\x01", offset)
                if next_sqt == -1: 
                    next_sqt = size
                rem = next_sqt - offset
                chunk_hex = data[offset:offset+rem].hex()
                out.write(f"{hex(offset)},{lod_level},Unknown,Unknown Chunk,,,,,,,,{chunk_hex}\n")
                offset += rem
                
    print(f"[+] Dump successfully saved in {out_csv}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spatial index decompiler DT G1 (.idx) with R-Tree support")
    parser.add_argument("input", help="Path to .idx file")
    parser.add_argument("output", help="Path to save .csv dump")
    args = parser.parse_args()
    
    dump_idx(args.input, args.output)