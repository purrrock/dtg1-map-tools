#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
import time
import argparse
import gc
import math
import re
import platform

def format_time(seconds):
    return f"{int(seconds // 60)}m {seconds % 60:.2f}s"

# Pre-compiled C-level regex patterns for maximum Pass 1 topology parsing speed
RE_ND = re.compile(b'<nd[^>]*ref="(\d+)"')
RE_MEMBER = re.compile(b'<member[^>]*type="way"[^>]*ref="(\d+)"')

class UltraTileManager:
    def __init__(self, output_dir):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.leaves = []          
        self.leaf_to_group = {}   
        self.tiles = []           
        self.grid_index = {}      
        self.node_files = {}
        self.way_files = {}
        self.rel_files = {}

    def build_spatial_index(self):
        self.grid_index.clear()
        for leaf_id, (minlat, maxlat, minlon, maxlon) in enumerate(self.leaves):
            min_y, max_y = math.floor(minlat * 10), math.floor(maxlat * 10)
            min_x, max_x = math.floor(minlon * 10), math.floor(maxlon * 10)
            for y in range(min_y, max_y + 1):
                for x in range(min_x, max_x + 1):
                    k = (y, x)
                    if k not in self.grid_index: self.grid_index[k] = []
                    self.grid_index[k].append(leaf_id)

    def get_tile_id(self, lat, lon):
        y, x = math.floor(lat * 10), math.floor(lon * 10)
        candidates = self.grid_index.get((y, x))
        if candidates:
            for leaf_id in candidates:
                minlat, maxlat, minlon, maxlon = self.leaves[leaf_id]
                if minlat <= lat <= maxlat and minlon <= lon <= maxlon:
                    return self.leaf_to_group[leaf_id]
        return -1

    def open_files(self):
        total_files = len(self.tiles) * 3
        
        # 🌟 System optimization 1: bypass Windows' default 512 simultaneous open-file limit to prevent crashes
        if platform.system() == 'Windows':
            try:
                import ctypes
                ctypes.cdll.msvcrt._setmaxstdio(8192)
            except Exception:
                pass
                
        max_total_buffer = 1024 * 1024 * 1024 
        buff_size = max_total_buffer // max(1, total_files)
        buff_size = max(512 * 1024, min(8 * 1024 * 1024, buff_size))
        
        if total_files > 150:
            print(f"⚠️ About to open {total_files} temp files simultaneously. Dynamically adjusted buffer to {buff_size//1024} KB/file.")
            
        for tid in range(len(self.tiles)):
            t_name = f"tile_{tid:03d}"
            self.node_files[tid] = open(os.path.join(self.output_dir, f"{t_name}_n.tmp"), 'wb', buffering=buff_size)
            self.way_files[tid] = open(os.path.join(self.output_dir, f"{t_name}_w.tmp"), 'wb', buffering=buff_size)
            self.rel_files[tid] = open(os.path.join(self.output_dir, f"{t_name}_r.tmp"), 'wb', buffering=buff_size)

    def write_to_tiles(self, mask, data, element_type):
        # Direct memory write; loop iterations equal only the bit length — maximum performance
        target_dict = self.node_files if element_type == 1 else (self.way_files if element_type == 2 else self.rel_files)
        
        if not (mask & (mask - 1)):
            target_dict[mask.bit_length() - 1].write(data)
        else:
            t = 0
            while mask > 0:
                if mask & 1: target_dict[t].write(data)
                mask >>= 1; t += 1

    def close_all(self):
        for f in self.node_files.values(): f.close()
        for f in self.way_files.values(): f.close()
        for f in self.rel_files.values(): f.close()

def build_kd_tree(box, heatmap_subset, max_weight, depth=0):
    minlat, maxlat, minlon, maxlon = box
    weight = sum(heatmap_subset.values())
    
    if weight <= max_weight or depth > 15 or (maxlat - minlat <= 0.051 and maxlon - minlon <= 0.051):
        return [box]

    hm1, hm2 = {}, {}
    if (maxlon - minlon) > (maxlat - minlat):
        mid = (minlon + maxlon) / 2.0
        box1, box2 = (minlat, maxlat, minlon, mid), (minlat, maxlat, mid, maxlon)
        for (y_idx, x_idx), w in heatmap_subset.items():
            (hm1 if (x_idx / 20.0) <= mid else hm2)[(y_idx, x_idx)] = w
    else:
        mid = (minlat + maxlat) / 2.0
        box1, box2 = (minlat, mid, minlon, maxlon), (mid, maxlat, minlon, maxlon)
        for (y_idx, x_idx), w in heatmap_subset.items():
            (hm1 if (y_idx / 20.0) <= mid else hm2)[(y_idx, x_idx)] = w

    return build_kd_tree(box1, hm1, max_weight, depth + 1) + build_kd_tree(box2, hm2, max_weight, depth + 1)


def split_osm_extreme(input_file, output_dir="osm", max_mb=30, min_mb=None):
    if min_mb is None: min_mb = max_mb / 4.0 
        
    print(f"🚀 [V6 Ultimate Compiler-Linked Edition] Starting! Target: {max_mb} MB per tile")
    if not os.path.exists(input_file): return print(f"❌ File not found: {input_file}")

    tm = UltraTileManager(output_dir)
    gc.disable() 
    
    B_NODE, B_WAY, B_REL = b'<node', b'<way', b'<relation'
    B_END_WAY, B_END_REL = b'</way>', b'</relation>'
    B_LAT, B_LON, B_ID = b' lat="', b' lon="', b' id="'
    
    # ---------------------------------------------------------
    # Pass 0: Spatial heatmap and automatic fragment merging
    # ---------------------------------------------------------
    print("⏳ [Pass 0] High-speed scan and magnet fragment reassembly...")
    t0 = time.time()
    heatmap = {}
    global_minlat, global_maxlat, global_minlon, global_maxlon = 90.0, -90.0, 180.0, -180.0
    
    with open(input_file, 'rb', buffering=64*1024*1024) as f:
        for line in f:
            if B_NODE in line:
                lat_idx, lon_idx = line.find(B_LAT), line.find(B_LON)
                if lat_idx > 0 and lon_idx > 0:
                    lat = float(line[lat_idx+6 : line.find(b'"', lat_idx+6)])
                    lon = float(line[lon_idx+6 : line.find(b'"', lon_idx+6)])
                    
                    grid_k = (math.floor(lat * 20), math.floor(lon * 20))
                    heatmap[grid_k] = heatmap.get(grid_k, 0) + 1
                    
                    if lat < global_minlat: global_minlat = lat
                    if lat > global_maxlat: global_maxlat = lat
                    if lon < global_minlon: global_minlon = lon
                    if lon > global_maxlon: global_maxlon = lon

    max_nodes = int(max_mb * 15000)
    min_nodes = int(min_mb * 15000)
    global_box = (global_minlat - 0.1, global_maxlat + 0.1, global_minlon - 0.1, global_maxlon + 0.1)
    raw_leaves = build_kd_tree(global_box, heatmap, max_nodes)
    
    leaf_weights = [0] * len(raw_leaves)
    leaf_centers = [((b[0]+b[1])/2, (b[2]+b[3])/2) for b in raw_leaves]
    
    for (y_idx, x_idx), count in heatmap.items():
        h_lat, h_lon = y_idx / 20.0, x_idx / 20.0
        for i, (minlat, maxlat, minlon, maxlon) in enumerate(raw_leaves):
            if minlat <= h_lat <= maxlat and minlon <= h_lon <= maxlon:
                leaf_weights[i] += count
                break

    group_leaves = {i: [i] for i in range(len(raw_leaves))}
    group_weights = {i: leaf_weights[i] for i in range(len(raw_leaves))}
    
    def get_group_center(gid):
        lvs = group_leaves[gid]
        return (sum(leaf_centers[l][0] for l in lvs) / len(lvs), sum(leaf_centers[l][1] for l in lvs) / len(lvs))

    while len(group_weights) > 1:
        smallest_gid = min(group_weights, key=group_weights.get)
        if group_weights[smallest_gid] >= min_nodes: break 
            
        cy1, cx1 = get_group_center(smallest_gid)
        best_neighbor, min_dist = None, float('inf')
        for gid in group_weights:
            if gid == smallest_gid: continue
            cy2, cx2 = get_group_center(gid)
            d = (cy1 - cy2)**2 + (cx1 - cx2)**2
            if d < min_dist: min_dist, best_neighbor = d, gid
                
        group_leaves[best_neighbor].extend(group_leaves[smallest_gid])
        group_weights[best_neighbor] += group_weights[smallest_gid]
        del group_leaves[smallest_gid]; del group_weights[smallest_gid]

    tm.leaves = raw_leaves
    for final_id, gid in enumerate(group_weights.keys()):
        lvs = group_leaves[gid]
        for l in lvs: tm.leaf_to_group[l] = final_id
        
        minlat = min(raw_leaves[l][0] for l in lvs)
        maxlat = max(raw_leaves[l][1] for l in lvs)
        minlon = min(raw_leaves[l][2] for l in lvs)
        maxlon = max(raw_leaves[l][3] for l in lvs)
        tm.tiles.append((minlat, maxlat, minlon, maxlon))

    tm.build_spatial_index() 
    print(f"   => Auto-merged down to {len(tm.tiles)} stable output files. Time: {format_time(time.time() - t0)}")

    del heatmap, raw_leaves, group_leaves, group_weights, leaf_weights, leaf_centers
    gc.collect()

    # ---------------------------------------------------------
    # Pass 1: Build spatial topology network
    # ---------------------------------------------------------
    print("⏳ [Pass 1] Regex-accelerated topology parsing (clean handoff to compiler safe-cut)...")
    t1 = time.time()
    node_tiles, way_tiles, rel_tiles = {}, {}, {}
    in_element, current_id, current_mask = 0, -1, 0

    # 🌟 Performance optimization 2: use the regex C engine to replace Python string-loop operations
    def extract_mask_way(data_line):
        mask = 0
        for m in RE_ND.finditer(data_line):
            mask |= node_tiles.get(int(m.group(1)), 0)
        return mask

    def extract_mask_rel(data_line):
        mask = 0
        for m in RE_MEMBER.finditer(data_line):
            mask |= way_tiles.get(int(m.group(1)), 0)
        return mask

    with open(input_file, 'rb', buffering=64*1024*1024) as f:
        for line in f:
            if in_element == 0:
                if B_NODE in line:
                    lat_idx, lon_idx, id_idx = line.find(B_LAT), line.find(B_LON), line.find(B_ID)
                    if lat_idx > 0 and lon_idx > 0 and id_idx > 0:
                        lat = float(line[lat_idx+6 : line.find(b'"', lat_idx+6)])
                        lon = float(line[lon_idx+6 : line.find(b'"', lon_idx+6)])
                        tid = tm.get_tile_id(lat, lon)
                        if tid >= 0: 
                            nid = int(line[id_idx+5 : line.find(b'"', id_idx+5)])
                            node_tiles[nid] = (1 << tid) 
                        
                elif B_WAY in line:
                    if b'/>' in line and b'<nd' not in line: continue
                    id_idx = line.find(B_ID)
                    if id_idx > 0: current_id = int(line[id_idx+5 : line.find(b'"', id_idx+5)])
                    
                    current_mask = extract_mask_way(line)
                    if B_END_WAY in line or b'/>' in line:
                        if current_mask: way_tiles[current_id] = current_mask
                    else:
                        in_element = 1
                    
                elif B_REL in line:
                    if b'/>' in line and b'<member' not in line: continue
                    id_idx = line.find(B_ID)
                    if id_idx > 0: current_id = int(line[id_idx+5 : line.find(b'"', id_idx+5)])
                    
                    current_mask = extract_mask_rel(line)
                    if B_END_REL in line or b'/>' in line:
                        if current_mask: rel_tiles[current_id] = current_mask
                    else:
                        in_element = 2

            elif in_element == 1:
                current_mask |= extract_mask_way(line)
                if B_END_WAY in line:
                    if current_mask: way_tiles[current_id] = current_mask
                    in_element = 0

            elif in_element == 2:
                current_mask |= extract_mask_rel(line)
                if B_END_REL in line:
                    if current_mask: rel_tiles[current_id] = current_mask
                    in_element = 0

    print(f"   => Topology built! Time: {format_time(time.time() - t1)}")

    # ---------------------------------------------------------
    # Pass 2: File output
    # ---------------------------------------------------------
    print("⏳ [Pass 2] Memory-copy write (ultra-fast export)...")
    t2 = time.time()
    tm.open_files()
    in_element, current_mask = 0, 0
    current_lines = []

    with open(input_file, 'rb', buffering=64*1024*1024) as f:
        for line in f:
            if in_element == 0:
                if B_NODE in line:
                    id_idx = line.find(B_ID)
                    if id_idx > 0:
                        nid = int(line[id_idx+5 : line.find(b'"', id_idx+5)])
                        mask = node_tiles.get(nid, 0)
                        if mask:
                            if b'/>' in line or b'</node>' in line: 
                                tm.write_to_tiles(mask, line, 1)
                            else: in_element = 1; current_mask = mask; current_lines = [line]

                elif B_WAY in line:
                    if b'/>' in line and b'<nd' not in line: continue
                    id_idx = line.find(B_ID)
                    if id_idx > 0:
                        wid = int(line[id_idx+5 : line.find(b'"', id_idx+5)])
                        mask = way_tiles.get(wid, 0)
                        if mask:
                            if B_END_WAY in line or b'/>' in line:
                                tm.write_to_tiles(mask, line, 2)
                            else: in_element = 2; current_mask = mask; current_lines = [line]

                elif B_REL in line:
                    if b'/>' in line and b'<member' not in line: continue
                    id_idx = line.find(B_ID)
                    if id_idx > 0:
                        rid = int(line[id_idx+5 : line.find(b'"', id_idx+5)])
                        mask = rel_tiles.get(rid, 0)
                        if mask:
                            if B_END_REL in line or b'/>' in line:
                                tm.write_to_tiles(mask, line, 3)
                            else: in_element = 3; current_mask = mask; current_lines = [line]
            else:
                current_lines.append(line)
                if (in_element == 1 and b'</node' in line) or \
                   (in_element == 2 and B_END_WAY in line) or \
                   (in_element == 3 and B_END_REL in line):
                    
                    data = b''.join(current_lines)
                    tm.write_to_tiles(current_mask, data, in_element)
                    in_element = 0

    tm.close_all()
    
    # 🌟 Performance optimization 3: fully purge the massive dictionaries (hundreds of millions of entries) before disk merge
    del node_tiles, way_tiles, rel_tiles
    gc.enable()
    gc.collect()
    
    print(f"   => File output complete! Time: {format_time(time.time() - t2)}")

    # ---------------------------------------------------------
    # Phase 3: Merge OSM temp files
    # ---------------------------------------------------------
    print("💾 Phase 3: Merging temp files into final OSM tiles...")
    for tid, bounds in enumerate(tm.tiles):
        minlat_t, maxlat_t, minlon_t, maxlon_t = bounds
        t_name = f"tile_{tid:03d}"
        final_osm = os.path.join(output_dir, f"{t_name}.osm")
        
        has_data = False
        with open(final_osm, 'wb') as fout:
            fout.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="hyper_extreme_splitter">\n')
            fout.write(f'  <bounds minlat="{minlat_t:.5f}" minlon="{minlon_t:.5f}" maxlat="{maxlat_t:.5f}" maxlon="{maxlon_t:.5f}"/>\n'.encode('utf-8'))
            
            for ext in ['_n', '_w', '_r']:
                tmp_file = os.path.join(output_dir, f"{t_name}{ext}.tmp")
                if os.path.exists(tmp_file):
                    if os.path.getsize(tmp_file) > 0: has_data = True
                    with open(tmp_file, 'rb') as fin:
                        shutil.copyfileobj(fin, fout, length=16*1024*1024)
                    os.remove(tmp_file)
            fout.write(b'</osm>\n')
            
        if not has_data: os.remove(final_osm)
        else: print(f"   ✅ {t_name}.osm ({os.path.getsize(final_osm)/(1024*1024):.2f} MB)")
            
    print(f"🎉 All processing complete! Total time: {format_time(time.time() - t0)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OSM splitter with micro-tile magnet auto-merge technology V6 (C-engine accelerated)")
    parser.add_argument("input", nargs="?", default="map.osm")
    parser.add_argument("--max-mb", type=float, default=30.0, help="Maximum size per split tile (default: 30 MB)")
    parser.add_argument("--min-mb", type=float, default=None, help="Minimum size threshold to trigger magnet merge (default: 1/4 of max)")
    parser.add_argument("--out", type=str, default="osm", help="Output directory")
    args = parser.parse_args()

    split_osm_extreme(args.input, args.out, args.max_mb, args.min_mb)
