#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import argparse
import mmap
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from concurrent.futures import ThreadPoolExecutor

def parse_mlp_pointers(mlp_path: str):
    if not os.path.exists(mlp_path):
        return [], None, None
        
    with open(mlp_path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            size = mm.size()

            aligned_size = size - (size % 4)
            data_i32 = np.frombuffer(mm[:aligned_size], dtype=np.int32)
            data_u32 = data_i32.view(np.uint32)

            p = 32 // 4
            end_p = aligned_size // 4
            
            line_segments = []
            point_list = []
            global_bbox = [float('inf'), float('inf'), float('-inf'), float('-inf')]
            
            while p < end_p:

                content_len_bytes = data_u32[p + 1]
                if content_len_bytes == 0: 
                    break

                record_len_32 = (8 + content_len_bytes) // 4
                
                if p + record_len_32 > end_p or content_len_bytes < 24:
                    p += record_len_32
                    continue

                minx = data_i32[p + 2] / 1_000_000.0
                miny = data_i32[p + 3] / 1_000_000.0
                maxx = data_i32[p + 4] / 1_000_000.0
                maxy = data_i32[p + 5] / 1_000_000.0

                if minx < global_bbox[0]: global_bbox[0] = minx
                if miny < global_bbox[1]: global_bbox[1] = miny
                if maxx > global_bbox[2]: global_bbox[2] = maxx
                if maxy > global_bbox[3]: global_bbox[3] = maxy
                
                num_parts = data_i32[p + 6]
                num_points = data_i32[p + 7]

                pts_start = p + 8 + num_parts
                pts_end = pts_start + (num_points * 2)
                
                if pts_end <= p + record_len_32:

                    points = data_i32[pts_start:pts_end].reshape(-1, 2) / 1_000_000.0
                    
                    if num_points >= 2:
                        if num_parts > 1:
                            parts = data_u32[p + 8 : p + 8 + num_parts]
                            line_segments.extend(np.split(points, parts[1:]))
                        else:
                            line_segments.append(points)
                    elif num_points == 1:
                        point_list.append(points[0])

                p += record_len_32
                
    return line_segments, np.array(point_list, dtype=np.float32) if point_list else None, global_bbox

def parse_idx_vectorized(idx_path: str):
    if not os.path.exists(idx_path):
        return None, None, None, None
        
    dt_nav = np.dtype([('v3', '<u4'), ('minx', '<f4'), ('miny', '<f4'), ('maxx', '<f4'), ('maxy', '<f4'), ('v1', '<u4'), ('cnt', '<u4')])
    dt_data = np.dtype([('minx', '<f4'), ('miny', '<f4'), ('maxx', '<f4'), ('maxy', '<f4'), ('code', '<u4'), ('v1', '<u4'), ('v2', '<u4')])

    nav_nodes, data_nodes = [], []

    with open(idx_path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            size = mm.size()
            aligned_size = size - (size % 4)
            data_u32 = np.frombuffer(mm[:aligned_size], dtype=np.uint32)

            sqt_pointers = np.where(data_u32 == 22303059)[0]
            
            for ptr in sqt_pointers:

                mode = data_u32[ptr + 2]
                count = data_u32[ptr + 3]
                
                if count == 0: continue

                byte_offset = (ptr + 4) * 4 
                
                if mode == 1: # Clustered
                    for _ in range(count):
                        if byte_offset + 28 > size: break
                        nav = np.frombuffer(mm[byte_offset:byte_offset+28], dtype=dt_nav, count=1)
                        nav_nodes.append(nav)
                        byte_offset += 28
                        
                        d_count = nav[0]['cnt']
                        if d_count > 0 and byte_offset + (28 * d_count) <= size:
                            data_nodes.append(np.frombuffer(mm[byte_offset:byte_offset+28*d_count], dtype=dt_data))
                            byte_offset += 28 * d_count
                            
                elif mode == 0: # Flat
                    if byte_offset + (28 * count) <= size:
                        data_nodes.append(np.frombuffer(mm[byte_offset:byte_offset+28*count], dtype=dt_data))

    def build_nan_separated_lines(nodes_list):
        if not nodes_list: return None, None
        arr = np.concatenate(nodes_list)
        valid = (arr['maxx'] > arr['minx']) & (arr['maxy'] > arr['miny'])
        arr = arr[valid]
        N = len(arr)
        if N == 0: return None, None
        
        X, Y = np.empty(N * 6, dtype=np.float32), np.empty(N * 6, dtype=np.float32)
        X[0::6] = arr['minx']; Y[0::6] = arr['miny']
        X[1::6] = arr['maxx']; Y[1::6] = arr['miny']
        X[2::6] = arr['maxx']; Y[2::6] = arr['maxy']
        X[3::6] = arr['minx']; Y[3::6] = arr['maxy']
        X[4::6] = arr['minx']; Y[4::6] = arr['miny']
        X[5::6] = np.nan;      Y[5::6] = np.nan      
        return X, Y

    nav_X, nav_Y = build_nan_separated_lines(nav_nodes)
    data_X, data_Y = build_nan_separated_lines(data_nodes)
                
    return nav_X, nav_Y, data_X, data_Y

def visualize(idx_path=None, mlp_path=None):
    plt.style.use('fast')
    fig, ax = plt.subplots(figsize=(14, 14))
    global_minx, global_miny, global_maxx, global_maxy = float('inf'), float('inf'), float('-inf'), float('-inf')

    print("[*] Launching Multi-threaded Parsers...")
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_idx = executor.submit(parse_idx_vectorized, idx_path) if idx_path else None
        future_mlp = executor.submit(parse_mlp_pointers, mlp_path) if mlp_path else None

        if future_idx:
            nav_X, nav_Y, data_X, data_Y = future_idx.result()
            if data_X is not None:
                ax.plot(data_X, data_Y, color='blue', linewidth=0.8, alpha=0.4, rasterized=True)
                print(f"[+] IDX Data Nodes loaded: {len(data_X)//6}")
                global_minx, global_miny = np.nanmin(data_X), np.nanmin(data_Y)
                global_maxx, global_maxy = np.nanmax(data_X), np.nanmax(data_Y)
            if nav_X is not None:
                ax.plot(nav_X, nav_Y, color='red', linewidth=1.5, alpha=0.5, rasterized=True)
                print(f"[+] IDX Nav Nodes loaded: {len(nav_X)//6}")
                
        if future_mlp:
            mlp_segments, mlp_points, bbox = future_mlp.result()
            if mlp_segments:
                lc = LineCollection(mlp_segments, colors='black', linewidths=1.0, alpha=0.8, rasterized=True)
                ax.add_collection(lc)
                print(f"[+] MLP Lines loaded: {len(mlp_segments)}")
                if bbox[0] != float('inf'):
                    global_minx, global_miny = min(global_minx, bbox[0]), min(global_miny, bbox[1])
                    global_maxx, global_maxy = max(global_maxx, bbox[2]), max(global_maxy, bbox[3])
            if mlp_points is not None:
                ax.scatter(mlp_points[:, 0], mlp_points[:, 1], c='green', s=3, rasterized=True)

    if global_minx != float('inf'):
        ax.set_xlim(global_minx, global_maxx)
        ax.set_ylim(global_miny, global_maxy)

    ax.set_aspect('equal', adjustable='datalim')
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("DT G1 Absolute Limit Visualizer (Pointers + Async Threads)")
    ax.grid(True, linestyle='--', alpha=0.3)
    
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color='black', lw=2, label='MLP Geometry'),
        Line2D([0], [0], color='red', lw=2, label='IDX Nav Cluster'),
        Line2D([0], [0], color='blue', lw=2, label='IDX Data Object')
    ]
    ax.legend(handles=legend_elements, loc='upper right')
    
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DT G1 Ultimate Visualizer")
    parser.add_argument("--idx", default=None, help="Path to the .idx file")
    parser.add_argument("--mlp", default=None, help="Path to the .mlp file")
    args = parser.parse_args()
    
    if not args.idx and not args.mlp:
        print("[-] Error: Please provide at least one file using --idx or --mlp")
    else:
        visualize(args.idx, args.mlp)