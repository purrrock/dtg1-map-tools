#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
import time
import argparse
import gc
import platform

def format_time(seconds):
    return f"{int(seconds // 60)}m {seconds % 60:.2f}s"

class UltraTileManager:
    __slots__ = ['output_dir', 'tiles', 'node_files', 'way_files', 'rel_files', 'grid_mask']

    def __init__(self, output_dir):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.tiles = []           
        self.node_files = []
        self.way_files = []
        self.rel_files = []
        # Fix matrix size to exact 3601 * 7201
        self.grid_mask = [0] * 25930801 

    def open_files(self):
        num_tiles = len(self.tiles)
        total_files = num_tiles * 3
        
        # Handle file open limits for different OS
        if platform.system() == 'Windows':
            try:
                import ctypes
                ctypes.cdll.msvcrt._setmaxstdio(8192)
            except Exception: pass
        else:
            try:
                import resource
                soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
                if soft < total_files + 50:
                    resource.setrlimit(resource.RLIMIT_NOFILE, (max(hard, total_files + 100), hard))
            except Exception:
                if total_files > 800:
                    print(f"⚠️ Warning: Need to open {total_files} files. If it crashes, run `ulimit -n 4096` in terminal")
                
        max_total_buffer = 1024 * 1024 * 1024 
        buff_size = max_total_buffer // max(1, total_files)
        buff_size = max(512 * 1024, min(16 * 1024 * 1024, buff_size))
            
        self.node_files = [None] * num_tiles
        self.way_files = [None] * num_tiles
        self.rel_files = [None] * num_tiles

        for tid in range(num_tiles):
            t_name = f"tile_{tid:03d}"
            self.node_files[tid] = open(os.path.join(self.output_dir, f"{t_name}_n.tmp"), 'wb', buffering=buff_size)
            self.way_files[tid] = open(os.path.join(self.output_dir, f"{t_name}_w.tmp"), 'wb', buffering=buff_size)
            self.rel_files[tid] = open(os.path.join(self.output_dir, f"{t_name}_r.tmp"), 'wb', buffering=buff_size)

    def close_all(self):
        for f in self.node_files: 
            if f: f.close()
        for f in self.way_files: 
            if f: f.close()
        for f in self.rel_files: 
            if f: f.close()

def split_osm_extreme(input_file, output_dir="osm", max_mb=300, min_mb=None):
    if min_mb is None: min_mb = max_mb / 10.0 
        
    print(f"🚀 [V12.0 Robust Speed Edition] Started! Target: {max_mb} MB per tile")
    if not os.path.exists(input_file): return print(f"❌ File not found: {input_file}")

    tm = UltraTileManager(output_dir)
    gc.disable() 
    
    B_NODE, B_WAY, B_REL = b'<node', b'<way', b'<relation'
    B_END_NODE, B_END_WAY, B_END_REL = b'</node>', b'</way>', b'</relation>'
    B_LAT, B_LON, B_ID = b' lat="', b' lon="', b' id="'
    _int, _float = int, float 
    
    # ---------------------------------------------------------
    # Pass 0: Build spatial matrix
    # ---------------------------------------------------------
    print("⏳ [Pass 0] Building spatial heatmap & O(1) absolute positioning matrix...")
    t0 = time.time()
    global_minlat, global_maxlat, global_minlon, global_maxlon = 90.0, -90.0, 180.0, -180.0
    g_mask = tm.grid_mask
    
    with open(input_file, 'rb', buffering=64*1024*1024) as f:
        for line in f:
            if B_NODE in line:
                lat_idx = line.find(B_LAT)
                if lat_idx > -1:
                    # 🔧 Fix 1: Remove dangerous +10 offset, use safe range search
                    lon_idx = line.find(B_LON, lat_idx) 
                    if lon_idx > -1:
                        lat = _float(line[lat_idx+6 : line.find(b'"', lat_idx+6)])
                        lon = _float(line[lon_idx+6 : line.find(b'"', lon_idx+6)])
                        
                        # 🔧 Fix 2: Clamp coordinates to prevent matrix out-of-bounds crash due to abnormal data
                        c_lat = max(-90.0, min(90.0, lat))
                        c_lon = max(-180.0, min(180.0, lon))
                        
                        g_mask[(_int(c_lat // 0.05) + 1800) * 7201 + (_int(c_lon // 0.05) + 3600)] += 1
                        
                        if lat < global_minlat: global_minlat = lat
                        if lat > global_maxlat: global_maxlat = lat
                        if lon < global_minlon: global_minlon = lon
                        if lon > global_maxlon: global_maxlon = lon

    heatmap = {}
    for idx, count in enumerate(g_mask):
        if count > 0:
            heatmap[((idx // 7201) - 1800, (idx % 7201) - 3600)] = count
            g_mask[idx] = 0  
            
    if not heatmap: return print("❌ No node data found!")

    def build_kd_tree(box, heatmap_subset, max_weight, depth=0):
        minlat, maxlat, minlon, maxlon = box
        weight = sum(heatmap_subset.values())
        if weight <= max_weight or depth > 15 or (maxlat - minlat <= 0.051 and maxlon - minlon <= 0.051): return [box]
        hm1, hm2 = {}, {}
        if (maxlon - minlon) > (maxlat - minlat):
            mid = (minlon + maxlon) / 2.0
            box1, box2 = (minlat, maxlat, minlon, mid), (minlat, maxlat, mid, maxlon)
            for (y, x), w in heatmap_subset.items(): (hm1 if (x * 0.05) <= mid else hm2)[(y, x)] = w
        else:
            mid = (minlat + maxlat) / 2.0
            box1, box2 = (minlat, mid, minlon, maxlon), (mid, maxlat, minlon, maxlon)
            for (y, x), w in heatmap_subset.items(): (hm1 if (y * 0.05) <= mid else hm2)[(y, x)] = w
        return build_kd_tree(box1, hm1, max_weight, depth + 1) + build_kd_tree(box2, hm2, max_weight, depth + 1)

    max_nodes, min_nodes = _int(max_mb * 15000), _int(min_mb * 15000)
    raw_leaves = build_kd_tree((global_minlat-0.1, global_maxlat+0.1, global_minlon-0.1, global_maxlon+0.1), heatmap, max_nodes)
    
    leaf_weights = [0] * len(raw_leaves)
    leaf_centers = [((b[0]+b[1])/2, (b[2]+b[3])/2) for b in raw_leaves]
    
    for (y, x), count in heatmap.items():
        h_lat, h_lon = y * 0.05, x * 0.05
        for i, (minlat, maxlat, minlon, maxlon) in enumerate(raw_leaves):
            if minlat <= h_lat <= maxlat and minlon <= h_lon <= maxlon:
                leaf_weights[i] += count; break

    group_leaves = {i: [i] for i in range(len(raw_leaves))}
    group_weights = {i: leaf_weights[i] for i in range(len(raw_leaves))}

    while len(group_weights) > 1:
        smallest_gid = min(group_weights, key=group_weights.get)
        if group_weights[smallest_gid] >= min_nodes: break 
            
        lvs1 = group_leaves[smallest_gid]; len1 = len(lvs1)
        cy1, cx1 = sum(leaf_centers[l][0] for l in lvs1)/len1, sum(leaf_centers[l][1] for l in lvs1)/len1
        
        best_neighbor, min_dist = None, float('inf')
        for gid in group_weights:
            if gid == smallest_gid: continue
            lvs2 = group_leaves[gid]; len2 = len(lvs2)
            cy2, cx2 = sum(leaf_centers[l][0] for l in lvs2)/len2, sum(leaf_centers[l][1] for l in lvs2)/len2
            d = (cy1 - cy2)**2 + (cx1 - cx2)**2
            if d < min_dist: min_dist, best_neighbor = d, gid
                
        group_leaves[best_neighbor].extend(group_leaves[smallest_gid])
        group_weights[best_neighbor] += group_weights[smallest_gid]
        del group_leaves[smallest_gid], group_weights[smallest_gid]

    leaf_to_group = {}
    for final_id, gid in enumerate(group_weights.keys()):
        lvs = group_leaves[gid]
        for l in lvs: leaf_to_group[l] = final_id
        tm.tiles.append((
            min(raw_leaves[l][0] for l in lvs), max(raw_leaves[l][1] for l in lvs),
            min(raw_leaves[l][2] for l in lvs), max(raw_leaves[l][3] for l in lvs)
        ))

    for (y, x) in heatmap.keys():
        h_lat, h_lon = y * 0.05, x * 0.05
        for i, (minlat, maxlat, minlon, maxlon) in enumerate(raw_leaves):
            if minlat <= h_lat <= maxlat and minlon <= h_lon <= maxlon:
                g_mask[(y + 1800) * 7201 + (x + 3600)] = 1 << leaf_to_group[i]; break

    del heatmap, raw_leaves, group_leaves, group_weights, leaf_weights, leaf_centers, leaf_to_group
    gc.collect()

    # ---------------------------------------------------------
    # Pass 1: Topology Scan
    # ---------------------------------------------------------
    print("⏳ [Pass 1] Sliding cursor topology scan (Fix dead loops & relation links)...")
    t1 = time.time()
    node_tiles, way_tiles, rel_tiles = {}, {}, {}
    in_element, current_id, current_mask = 0, -1, 0
    nt_get, wt_get = node_tiles.get, way_tiles.get

    with open(input_file, 'rb', buffering=64*1024*1024) as f:
        for line in f:
            if in_element == 0:
                if B_NODE in line:
                    lat_idx = line.find(B_LAT)
                    if lat_idx > -1:
                        lon_idx, id_idx = line.find(B_LON, lat_idx), line.find(B_ID)
                        if lon_idx > -1 and id_idx > -1:
                            lat = _float(line[lat_idx+6 : line.find(b'"', lat_idx+6)])
                            lon = _float(line[lon_idx+6 : line.find(b'"', lon_idx+6)])
                            
                            c_lat, c_lon = max(-90.0, min(90.0, lat)), max(-180.0, min(180.0, lon))
                            mask = g_mask[(_int(c_lat // 0.05) + 1800) * 7201 + (_int(c_lon // 0.05) + 3600)]
                            
                            if not mask:
                                for tid, (minlat, maxlat, minlon, maxlon) in enumerate(tm.tiles):
                                    if minlat <= lat <= maxlat and minlon <= lon <= maxlon:
                                        mask = 1 << tid; break
                            if mask: node_tiles[_int(line[id_idx+5 : line.find(b'"', id_idx+5)])] = mask 
                        
                elif B_WAY in line:
                    id_idx = line.find(B_ID)
                    if id_idx > -1: current_id = _int(line[id_idx+5 : line.find(b'"', id_idx+5)])
                    current_mask = 0
                    
                    idx = line.find(b'<nd ')
                    while idx > -1:
                        end_tag = line.find(b'>', idx)
                        if end_tag == -1: break
                        r_idx = line.find(b'ref="', idx, end_tag)
                        if r_idx > -1: current_mask |= nt_get(_int(line[r_idx+5 : line.find(b'"', r_idx+5)]), 0)
                        # 🔧 Fix 3: Ensure forward movement to prevent dead loop when r_idx = -1
                        idx = line.find(b'<nd ', end_tag) 
                        
                    # 🔧 Fix 4: Safe self-closing check
                    if B_END_WAY in line or line.rstrip().endswith(b'/>'):
                        if current_mask: way_tiles[current_id] = current_mask
                    else: in_element = 1
                    
                elif B_REL in line:
                    id_idx = line.find(B_ID)
                    if id_idx > -1: current_id = _int(line[id_idx+5 : line.find(b'"', id_idx+5)])
                    current_mask = 0
                    
                    idx = line.find(b'<member ')
                    while idx > -1:
                        end_tag = line.find(b'>', idx)
                        if end_tag == -1: break
                        # 🔧 Fix 5: Fix issue where Relation misses Nodes
                        r_idx = line.find(b'ref="', idx, end_tag)
                        if r_idx > -1:
                            ref_id = _int(line[r_idx+5 : line.find(b'"', r_idx+5)])
                            if line.find(b'type="w', idx, end_tag) > -1:
                                current_mask |= wt_get(ref_id, 0)
                            elif line.find(b'type="n', idx, end_tag) > -1:
                                current_mask |= nt_get(ref_id, 0)
                        idx = line.find(b'<member ', end_tag)

                    if B_END_REL in line or line.rstrip().endswith(b'/>'):
                        if current_mask: rel_tiles[current_id] = current_mask
                    else: in_element = 2

            elif in_element == 1:
                idx = line.find(b'<nd ')
                while idx > -1:
                    end_tag = line.find(b'>', idx)
                    if end_tag == -1: break
                    r_idx = line.find(b'ref="', idx, end_tag)
                    if r_idx > -1: current_mask |= nt_get(_int(line[r_idx+5 : line.find(b'"', r_idx+5)]), 0)
                    idx = line.find(b'<nd ', end_tag)
                    
                if B_END_WAY in line:
                    if current_mask: way_tiles[current_id] = current_mask
                    in_element = 0

            elif in_element == 2:
                idx = line.find(b'<member ')
                while idx > -1:
                    end_tag = line.find(b'>', idx)
                    if end_tag == -1: break
                    r_idx = line.find(b'ref="', idx, end_tag)
                    if r_idx > -1:
                        ref_id = _int(line[r_idx+5 : line.find(b'"', r_idx+5)])
                        if line.find(b'type="w', idx, end_tag) > -1:
                            current_mask |= wt_get(ref_id, 0)
                        elif line.find(b'type="n', idx, end_tag) > -1:
                            current_mask |= nt_get(ref_id, 0)
                    idx = line.find(b'<member ', end_tag)

                if B_END_REL in line:
                    if current_mask: rel_tiles[current_id] = current_mask
                    in_element = 0

    print(f"   => Topology built! Time elapsed: {format_time(time.time() - t1)}")

    # ---------------------------------------------------------
    # Pass 2: State separation and lossless direct write stream
    # ---------------------------------------------------------
    print("⏳ [Pass 2] State separation lossless direct write stream...")
    t2 = time.time()
    tm.open_files()
    
    in_element = 0
    active_mask = 0
    active_files = None
    
    rt_get = rel_tiles.get
    n_files, w_files, r_files = tm.node_files, tm.way_files, tm.rel_files
    
    # 🔧 Fix 6: Completely remove early memory_stage release to ensure no data loss when processing unsorted OSM

    with open(input_file, 'rb', buffering=64*1024*1024) as f:
        for line in f:
            line_mask = 0
            line_files = None
            
            if in_element == 0:
                if B_NODE in line:
                    id_idx = line.find(B_ID)
                    if id_idx > -1:
                        mask = nt_get(_int(line[id_idx+5 : line.find(b'"', id_idx+5)]), 0)
                        if mask:
                            line_mask, line_files = mask, n_files
                            # 🔧 Fix 7: Precisely determine if it is self-closing
                            if not (line.rstrip().endswith(b'/>') or B_END_NODE in line):
                                in_element, active_mask, active_files = 1, mask, n_files
                
                elif B_WAY in line:
                    id_idx = line.find(B_ID)
                    if id_idx > -1:
                        mask = wt_get(_int(line[id_idx+5 : line.find(b'"', id_idx+5)]), 0)
                        if mask:
                            line_mask, line_files = mask, w_files
                            if not (line.rstrip().endswith(b'/>') or B_END_WAY in line):
                                in_element, active_mask, active_files = 2, mask, w_files
                                
                elif B_REL in line:
                    id_idx = line.find(B_ID)
                    if id_idx > -1:
                        mask = rt_get(_int(line[id_idx+5 : line.find(b'"', id_idx+5)]), 0)
                        if mask:
                            line_mask, line_files = mask, r_files
                            if not (line.rstrip().endswith(b'/>') or B_END_REL in line):
                                in_element, active_mask, active_files = 3, mask, r_files
            else:
                # Inside element, fully use mask, completely ignore /> traps embedded in tag attributes
                line_mask, line_files = active_mask, active_files
                
                if (in_element == 1 and B_END_NODE in line) or \
                   (in_element == 2 and B_END_WAY in line) or \
                   (in_element == 3 and B_END_REL in line):
                    in_element = 0

            if line_mask:
                m = line_mask
                if not (m & (m - 1)): 
                    line_files[m.bit_length() - 1].write(line)
                else:
                    while m:
                        lsb = m & -m
                        line_files[lsb.bit_length() - 1].write(line)
                        m ^= lsb

    tm.close_all()
    del tm.grid_mask, node_tiles, way_tiles, rel_tiles
    gc.enable(); gc.collect()
    
    print(f"   => File output complete! Time elapsed: {format_time(time.time() - t2)}")

    # ---------------------------------------------------------
    # Phase 3: Merging temp files into final OSM
    # ---------------------------------------------------------
    print("💾 Phase 3: Merging temp files into final OSM...")
    for tid, bounds in enumerate(tm.tiles):
        minlat_t, maxlat_t, minlon_t, maxlon_t = bounds
        t_name = f"tile_{tid:03d}"
        final_osm = os.path.join(output_dir, f"{t_name}.osm")
        
        has_data = False
        with open(final_osm, 'wb') as fout:
            fout.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="v12.0_stable_extreme">\n')
            fout.write(f'  <bounds minlat="{minlat_t:.5f}" minlon="{minlon_t:.5f}" maxlat="{maxlat_t:.5f}" maxlon="{maxlon_t:.5f}"/>\n'.encode('utf-8'))
            
            for ext in ['_n', '_w', '_r']:
                tmp_file = os.path.join(output_dir, f"{t_name}{ext}.tmp")
                if os.path.exists(tmp_file):
                    if os.path.getsize(tmp_file) > 0: has_data = True
                    with open(tmp_file, 'rb') as fin: shutil.copyfileobj(fin, fout, length=16*1024*1024)
                    os.remove(tmp_file)
            fout.write(b'</osm>\n')
            
        if not has_data: os.remove(final_osm)
        else: print(f"   ✅ {t_name}.osm ({os.path.getsize(final_osm)/(1024*1024):.2f} MB)")
            
    print(f"🎉 All processing complete! Total time: {format_time(time.time() - t0)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V12.0 Robust Speed Edition (Fix matrix crash/dead loop/attribute traps)")
    parser.add_argument("input", nargs="?", default="map.osm")
    parser.add_argument("--max-mb", type=float, default=300.0)
    parser.add_argument("--min-mb", type=float, default=None)
    parser.add_argument("--out", type=str, default="osm")
    args = parser.parse_args()
    split_osm_extreme(args.input, args.out, args.max_mb, args.min_mb)