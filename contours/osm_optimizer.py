#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import osmium as o
import sys
import os
import tempfile
import shutil
import time

try:
    import numpy as np
    from numba import njit
    HAS_JIT = True
except ImportError:
    HAS_JIT = False
    # Dummy decorator so the code runs with plain NumPy even without Numba
    def njit(f): return f 

# 🌟 Smartwatch map tag whitelist
WATCH_ALLOWED_TAGS = {
    'highway', 'natural', 'waterway', 'landuse', 'leisure', 
    'amenity', 'tourism', 'place', 'bridge', 'tunnel', 'historic', 
    'railway', 'aeroway', 'boundary'
}

VALID_POI_VALUES = {
    'peak', 'saddle', 'volcano', 'cave_entrance', 'waterfall', 'spring', 
    'toilets', 'drinking_water', 'hospital', 'police', 'shelter',        
    'convenience', 'supermarket', 'fuel', 'camp_site', 'viewpoint', 
    'information', 'alpine_hut', 'guest_house', 'parking', 'station', 
    'bus_stop', 'guidepost', 'milestone', 'ruins', 'monument', 'historic',                                     
    'city', 'town', 'village', 'hamlet', 'park', 'school', 'kindergarten', 'university'
}

ESCAPE_RULES = {'"': "&quot;", "'": "&apos;", "&": "&amp;", "<": "&lt;", ">": "&gt;"}
def escape_xml(s):
    if not s: return ""
    for k, v in ESCAPE_RULES.items():
        if k in s: s = s.replace(k, v)
    return s

def format_time(seconds):
    return f"{int(seconds // 60)}m {seconds % 60:.2f}s"

# ==========================================
# Core algorithm: RDP line simplification
# ==========================================
@njit
def douglas_peucker_indices_fast(pts, epsilon):
    n = len(pts)
    if n < 3:
        arr = np.empty(n, dtype=np.int64)
        for i in range(n): arr[i] = i
        return arr
    epsilon_sq = epsilon * epsilon
    stack_start, stack_end = np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64)
    stack_ptr = 0
    stack_start[0], stack_end[0] = 0, n - 1
    stack_ptr += 1
    keep_indices = np.zeros(n, dtype=np.bool_)
    keep_indices[0], keep_indices[n - 1] = True, True
    
    while stack_ptr > 0:
        stack_ptr -= 1
        start, end = stack_start[stack_ptr], stack_end[stack_ptr]
        if end - start <= 1: continue
        p1_x, p1_y, p2_x, p2_y = pts[start, 0], pts[start, 1], pts[end, 0], pts[end, 1]
        dx, dy = p2_x - p1_x, p2_y - p1_y
        l2 = dx*dx + dy*dy 
        dmax_sq, index = 0.0, start
        
        for i in range(start + 1, end):
            px, py = pts[i, 0], pts[i, 1]
            if l2 == 0.0:
                vx, vy = px - p1_x, py - p1_y
                d_sq = vx*vx + vy*vy
            else:
                cross = dy * px - dx * py + p2_x * p1_y - p2_y * p1_x
                d_sq = (cross * cross) / l2
            if d_sq > dmax_sq:
                dmax_sq, index = d_sq, i
                
    if dmax_sq > epsilon_sq:
        keep_indices[index] = True
        stack_start[stack_ptr], stack_end[stack_ptr] = start, index
        stack_ptr += 1
        stack_start[stack_ptr], stack_end[stack_ptr] = index, end
        stack_ptr += 1
    return np.nonzero(keep_indices)[0]

# ==========================================
# Phase 1: Protect natural polygon relations (fast scan)
# ==========================================
class RelationScanner(o.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.relation_ways = set()

    def relation(self, r):
        if r.tags.get('type') == 'multipolygon' and not r.tags.get('building'): 
            for member in r.members:
                if member.type == 'w':
                    self.relation_ways.add(member.ref)

# ==========================================
# Phase 2: Way optimizer (Douglas-Peucker acceleration + whitelist filtering)
# ==========================================
class WayOptimizer(o.SimpleHandler):
    def __init__(self, temp_ways_file, max_nodes_per_way, epsilon_deg, relation_ways):
        super().__init__()
        self.tmp_f = open(temp_ways_file, 'wb', buffering=16*1024*1024)
        self.max_nodes = max_nodes_per_way
        self.base_epsilon = epsilon_deg
        self.relation_ways = relation_ways 
        self.used_node_ids = set()
        self.split_id_counter = -1000000000 
        self.ignore_highway_types = {'corridor', 'elevator', 'proposed', 'construction', 'abandoned', 'raceway'}
        self.polygon_safe_limit = 500 

    def way(self, w):
        if w.tags.get('building'): return 
        
        for tag in w.tags:
            if tag.k == 'highway' and tag.v in self.ignore_highway_types: return

        is_in_relation = w.id in self.relation_ways
        is_polygon = w.is_closed() and any(tag.k in WATCH_ALLOWED_TAGS or tag.k in {'area', 'landcover', 'surface'} for tag in w.tags)

        if not is_in_relation and len(w.tags) == 0: return
        if len(w.nodes) < 2: return

        parsed_tags = {}
        zh_name = None
        for tag in w.tags:
            if tag.k in {'name:zh-Hant', 'name:zh_tw', 'name:zh', 'name:zh_TW'}:
                if not zh_name or tag.k == 'name:zh-Hant': zh_name = tag.v
            elif tag.k in WATCH_ALLOWED_TAGS:
                parsed_tags[tag.k] = tag.v

        if zh_name: parsed_tags['name'] = zh_name
        elif 'name' in w.tags: parsed_tags['name'] = w.tags['name']

        if not parsed_tags and not is_in_relation: return

        pts, valid_nds = [], []
        for n in w.nodes:
            try:
                pts.append((n.location.lon, n.location.lat))
                valid_nds.append(n.ref)
            except o.InvalidLocationError: pass
                
        if len(pts) < 2: return
        
        if is_polygon:
            lons, lats = [p[0] for p in pts], [p[1] for p in pts]
            if (max(lats) - min(lats) < 0.00002) and (max(lons) - min(lons) < 0.00002): return
            
        pts_array = np.array(pts, dtype=np.float64)
        current_epsilon = self.base_epsilon
        kept_indices = douglas_peucker_indices_fast(pts_array, current_epsilon)
        
        if is_polygon or is_in_relation:
            while len(kept_indices) > self.polygon_safe_limit and current_epsilon < 0.005:
                current_epsilon *= 2.0
                kept_indices = douglas_peucker_indices_fast(pts_array, current_epsilon)
            
            if len(kept_indices) > self.polygon_safe_limit:
                kept_indices = kept_indices[:self.polygon_safe_limit]
                kept_indices[-1] = len(pts) - 1 
                
            chunks = [[valid_nds[i] for i in kept_indices]]
        else:
            simplified_nds = [valid_nds[i] for i in kept_indices]
            chunks = [simplified_nds[i:i + self.max_nodes] for i in range(0, len(simplified_nds), max(1, self.max_nodes - 1))]
        
        for chunk in chunks:
            if len(chunk) < 2: continue
            
            wid = self.split_id_counter if len(chunks) > 1 else w.id
            if len(chunks) > 1: self.split_id_counter -= 1
            
            lines = [f'<way id="{wid}">\n']
            for nd_ref in chunk:
                lines.append(f'<nd ref="{nd_ref}"/>\n')
                self.used_node_ids.add(nd_ref) 
                
            for k, v in parsed_tags.items():
                lines.append(f'<tag k="{escape_xml(k)}" v="{escape_xml(v)}"/>\n')
            lines.append('</way>\n')
            
            self.tmp_f.write("".join(lines).encode('utf-8'))

    def close(self): self.tmp_f.close()

# ==========================================
# Phase 3: Final node and relation builder (pure-string ultra-fast write)
# ==========================================
class FinalBuilder(o.SimpleHandler):
    def __init__(self, output_file, temp_ways_file, used_node_ids):
        super().__init__()
        self.f = open(output_file, 'wb', buffering=16*1024*1024)
        self.temp_ways_file = temp_ways_file
        self.used_node_ids = used_node_ids
        self.ways_written = False
        
        self.minlat, self.maxlat = 90.0, -90.0
        self.minlon, self.maxlon = 180.0, -180.0
        self.poi_count = 0
        self.rel_count = 0
        
        # Write XML header and reserve a perfectly padded 120-byte bounds placeholder
        self.f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n')
        self.bounds_pos = self.f.tell()
        self.f.write(b' ' * 120 + b'\n') 

    def node(self, n):
        is_in_way = n.id in self.used_node_ids
        is_valid_poi = False
        
        if not is_in_way:
            for tag in n.tags:
                if tag.v in VALID_POI_VALUES or tag.k in ('amenity', 'tourism'):
                    is_valid_poi = True; break
            if not is_valid_poi: return

        lat, lon = n.location.lat, n.location.lon
        if lat < self.minlat: self.minlat = lat
        if lat > self.maxlat: self.maxlat = lat
        if lon < self.minlon: self.minlon = lon
        if lon > self.maxlon: self.maxlon = lon
        
        lat_s, lon_s = f"{lat:.5f}", f"{lon:.5f}"

        # Plain road nodes are written as tag-less elements to minimize file size
        if is_in_way and not is_valid_poi:
            self.f.write(f'<node id="{n.id}" lat="{lat_s}" lon="{lon_s}"/>\n'.encode('utf-8'))
            return

        self.poi_count += 1
        best_name = None
        for k in ('name:zh-Hant', 'name:zh_tw', 'name:zh', 'name'):
            if k in n.tags:
                best_name = n.tags[k]; break

        lines = [f'<node id="{n.id}" lat="{lat_s}" lon="{lon_s}">\n']
        has_name = False
        for tag in n.tags:
            k, v = tag.k, tag.v
            if k == 'name' and best_name:
                lines.append(f'<tag k="name" v="{escape_xml(best_name)}"/>\n')
                has_name = True
            elif k in WATCH_ALLOWED_TAGS and k != 'name':
                lines.append(f'<tag k="{escape_xml(k)}" v="{escape_xml(v)}"/>\n')
                
        if best_name and not has_name:
            lines.append(f'<tag k="name" v="{escape_xml(best_name)}"/>\n')
            
        lines.append('</node>\n')
        self.f.write("".join(lines).encode('utf-8'))

    def write_ways_cache(self):
        if not self.ways_written:
            with open(self.temp_ways_file, 'rb') as tw: 
                shutil.copyfileobj(tw, self.f, length=16*1024*1024)
            self.ways_written = True

    def relation(self, r):
        self.write_ways_cache() # Flush all ways before writing relations
            
        if r.tags.get('type') == 'multipolygon' and not r.tags.get('building'):
            self.rel_count += 1
            best_name = None
            for k in ('name:zh-Hant', 'name:zh_tw', 'name:zh', 'name'):
                if k in r.tags:
                    best_name = r.tags[k]; break
                    
            lines = [f'<relation id="{r.id}">\n']
            has_name = False
            for tag in r.tags:
                k, v = tag.k, tag.v
                if k == 'type':
                    lines.append(f'<tag k="type" v="{escape_xml(v)}"/>\n')
                elif k == 'name':
                    if best_name: lines.append(f'<tag k="name" v="{escape_xml(best_name)}"/>\n')
                    has_name = True
                elif k in WATCH_ALLOWED_TAGS:
                    lines.append(f'<tag k="{escape_xml(k)}" v="{escape_xml(v)}"/>\n')
                    
            if best_name and not has_name:
                lines.append(f'<tag k="name" v="{escape_xml(best_name)}"/>\n')
                
            for m in r.members:
                lines.append(f'<member type="{m.type}" ref="{m.ref}" role="{escape_xml(m.role)}"/>\n')
                
            lines.append('</relation>\n')
            self.f.write("".join(lines).encode('utf-8'))

    def close(self):
        self.write_ways_cache()
        self.f.write(b'</osm>\n')
        
        # Seek back to the start and overwrite the bounds element with real values
        bounds_str = f'  <bounds minlat="{self.minlat:.5f}" minlon="{self.minlon:.5f}" maxlat="{self.maxlat:.5f}" maxlon="{self.maxlon:.5f}"/>'
        padded = bounds_str.ljust(120, ' ').encode('utf-8') + b'\n'
        self.f.seek(self.bounds_pos)
        self.f.write(padded)
        self.f.close()

# ==========================================
# Main control flow
# ==========================================
def optimize_osm_pyosmium(input_file, output_file, max_nodes_per_way=40, epsilon_deg=0.00003):
    t0 = time.time()
    
    print(f"🚀 [Extreme Optimization Engine] Starting! Input file: {input_file}")
    if input_file.endswith(".pbf"):
        print("   💡 PBF format detected — parsing directly in memory, no XML conversion needed!")

    print(f"⏳ [Pass 1] Scanning natural polygon relations...")
    rel_scanner = RelationScanner()
    rel_scanner.apply_file(input_file, locations=False)

    temp_ways_name = tempfile.mktemp(suffix=".tmp")
    
    print(f"⏳ [Pass 2] Way RDP smart slimming and filtering (JIT engine)...")
    way_opt = WayOptimizer(temp_ways_name, max_nodes_per_way, epsilon_deg, rel_scanner.relation_ways)
    way_opt.apply_file(input_file, locations=True, idx='flex_mem') # Automatically caches node coordinates
    way_opt.close()
    
    used_node_ids = way_opt.used_node_ids
    print(f"   => Extracted {len(used_node_ids):,} required coordinate points.")
    
    print(f"⏳ [Pass 3] High-speed merge and POI extraction (C-level string builder)...")
    final_builder = FinalBuilder(output_file, temp_ways_name, used_node_ids)
    final_builder.apply_file(input_file, locations=False)
    final_builder.close()
    
    os.remove(temp_ways_name)
    
    print(f"🎉 Map core optimization complete! Total time: {format_time(time.time() - t0)}")
    print(f"   📊 Stats: Nodes kept {len(used_node_ids):,} | POI landmarks {final_builder.poi_count:,} | Relation polygons {final_builder.rel_count:,}")
    print(f"   📁 Output file: {output_file} (Size: {os.path.getsize(output_file)/(1024*1024):.2f} MB)")

if __name__ == "__main__":
    # Accepts .osm.pbf input directly and outputs a clean .osm file
    input_osm = sys.argv[1] if len(sys.argv) > 1 else "base.osm.pbf"
    output_osm = sys.argv[2] if len(sys.argv) > 2 else "base_map.osm"
    
    if not os.path.exists(input_osm) and input_osm == "base.osm.pbf":
        if os.path.exists("base.osm"):
            input_osm = "base.osm" # Fallback: use .osm if .pbf is not found
            
    optimize_osm_pyosmium(input_osm, output_osm)
