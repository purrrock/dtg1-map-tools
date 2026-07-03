#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OSM Optimizer (V30 Ultimate Stable Universal Edition + POI Dual Display Magic)
===========================================================
- 🌍 Perfect language correction: Integrates V20.5's Traditional Chinese fault tolerance (prevents parks from disappearing) + OSM international standards (`name` > `name:en` prevents local roads from becoming English).
- 🪟 100% Windows PowerShell friendly: Execute directly, globally applicable.
- 🌲 Landmark return: Restores the retention weight of key POIs like `park`, `school`, `historic`.
- 🎯 POI Dual Display: Automatically extracts the centroid of polygons with landmark attributes to generate icons, while "retaining the original polygon area".
- 🗑️ Junk name filtering: Automatically removes "unnamed", "nameless", "industrial roads", etc.
- ⚡ Core restoration: Retains V20.5's clean logic and Numba AVX high-speed engine.
"""

import osmium as o
import sys
import os
import tempfile
import shutil
import time
import gc
import functools

try:
    import numpy as np
    from numba import njit
    HAS_JIT = True
except ImportError:
    HAS_JIT = False

# ==========================================
# 🌍 Perfect Language Weight Engine (Automatically compatible with Taiwan and global map data)
# The smaller the number, the higher the priority
# ==========================================
NAME_PRIORITIES = {
    'name:zh-Hant': 1,
    'name:zh-TW': 2,
    'name:zh_TW': 3,
    'name:zh_tw': 4,
    'name:zh-Hans': 5,
    'name:zh-CN': 6,
    'name:zh_CN': 7,
    'name:zh': 8,
    'name': 9,       # OSM global standard local name (prioritized over English, ensures local names don't become English fallback)
    'name:en': 10    # English as the final fallback
}

# Junk names to filter out ("Unnamed", "Nameless", "Industrial Road", "Path", "Trail", "Forest Road")
# Kept in original Chinese as they target specific map data tags.
IGNORE_NAMES = frozenset({
    "未命名", "無名", "產業道路", "小徑", "步道", "林道"
})

# ==========================================
# Constants & Tag Configurations
# ==========================================
WATCH_ALLOWED_TAGS = frozenset({
    'highway', 'natural', 'waterway', 'landuse', 'leisure', 
    'amenity', 'tourism', 'place', 'historic', 'railway', 
    'tracktype', 'shop', 'man_made'
})

IGNORE_BUILDING_VALUES = frozenset({
    'yes', 'residential', 'house', 'apartments', 'garage', 'garages', 
    'hut', 'shed', 'roof', 'terrace', 'greenhouse', 'cabin', 'detached'
})

POI_TRIGGER_KEYS = frozenset({
    'amenity', 'shop', 'leisure', 'tourism', 'sport', 'historic', 'craft', 'office', 'healthcare'
})

# 🌲 Ensure landmarks like park, school perfectly return
VALID_POI_VALUES = frozenset({
    'peak', 'saddle', 'volcano', 'cave_entrance', 'waterfall', 'spring', 
    'toilets', 'drinking_water', 'hospital', 'police', 'shelter',        
    'convenience', 'supermarket', 'fuel', 'camp_site', 'viewpoint', 
    'information', 'alpine_hut', 'guest_house', 'parking', 'station', 
    'bus_stop', 'guidepost', 'milestone', 'ruins', 'monument', 'historic',                                     
    'city', 'town', 'village', 'hamlet', 'park', 'school', 'kindergarten', 'university',
    'survey_point'
})

DROP_TAG_KEYS = frozenset({
    'wikidata', 'wikipedia', 'phone', 'website', 'url', 'opening_hours', 
    'email', 'fax', 'note', 'source', 'fixme', 'operator', 'start_date', 'created_by',
    'building:levels', 'height', 'roof:shape', 'description', 'brand'
})

DROP_TAG_PREFIXES = ('addr:', 'contact:', 'payment:', 'source:', 'generator:', 'building:ruin')

@functools.lru_cache(maxsize=8192)
def escape_xml_bytes(s: str) -> bytes:
    if not s: return b""
    if '&' in s or '<' in s or '>' in s or '"' in s or "'" in s:
        s = s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&apos;')
    return s.encode('utf-8')

def format_time(seconds):
    return f"{int(seconds // 60)}m {seconds % 60:.2f}s"

if HAS_JIT:
    @njit(fastmath=True, cache=True, nogil=True, boundscheck=False)
    def is_too_small(pts, threshold):
        if len(pts) < 3: return True
        min_x, min_y = pts[0, 0], pts[0, 1]
        max_x, max_y = pts[0, 0], pts[0, 1]
        for i in range(1, len(pts)):
            x, y = pts[i, 0], pts[i, 1]
            if x < min_x: min_x = x
            elif x > max_x: max_x = x
            if y < min_y: min_y = y
            elif y > max_y: max_y = y
        return (max_x - min_x < threshold) and (max_y - min_y < threshold)

    @njit(fastmath=True, cache=True, nogil=True, boundscheck=False)
    def douglas_peucker_fast(pts, epsilon, stack_start, stack_end, keep_indices):
        n = len(pts)
        if n < 3:
            for i in range(n): keep_indices[i] = True
            return keep_indices[:n]
            
        epsilon_sq = epsilon * epsilon
        stack_ptr = 1
        stack_start[0] = 0
        stack_end[0] = n - 1
        
        for i in range(n): keep_indices[i] = False
        keep_indices[0], keep_indices[n - 1] = True, True
        
        while stack_ptr > 0:
            stack_ptr -= 1
            start, end = stack_start[stack_ptr], stack_end[stack_ptr]
            if end - start <= 1: continue
                
            p1_x, p1_y = pts[start, 0], pts[start, 1]
            p2_x, p2_y = pts[end, 0], pts[end, 1]
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
                
        return keep_indices[:n]

def douglas_peucker_fallback(pts, epsilon):
    n = len(pts)
    if n < 3: return list(range(n))
    epsilon_sq = epsilon * epsilon
    stack = [(0, n - 1)]
    keep = [False] * n
    keep[0] = keep[-1] = True
    while stack:
        start, end = stack.pop()
        if end - start <= 1: continue
        p1x, p1y = pts[start] 
        p2x, p2y = pts[end]
        dx, dy = p2x - p1x, p2y - p1y
        l2 = dx*dx + dy*dy
        dmax_sq, index = 0.0, start
        for i in range(start + 1, end):
            px, py = pts[i]
            if l2 == 0.0:
                vx, vy = px - p1x, py - p1y
                d_sq = vx*vx + vy*vy
            else:
                cross = dy * px - dx * py + p2x * p1y - p2y * p1x
                d_sq = (cross * cross) / l2
            if d_sq > dmax_sq: dmax_sq, index = d_sq, i
        if dmax_sq > epsilon_sq:
            keep[index] = True
            stack.extend([(start, index), (index, end)])
    return [i for i, k in enumerate(keep) if k]


class RelationScanner(o.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.relation_ways = set()

    def relation(self, r):
        tags = r.tags
        if tags and tags.get('type') == 'multipolygon':
            b_val = tags.get('building')
            if b_val and b_val in IGNORE_BUILDING_VALUES:
                return 
            add_way = self.relation_ways.add
            for m in r.members:
                if m.type == 'w': add_way(m.ref)


class WayOptimizer(o.SimpleHandler):
    def __init__(self, temp_ways_file, temp_nodes_file, max_nodes_per_way, epsilon_deg, relation_ways):
        super().__init__()
        self.tmp_f = open(temp_ways_file, 'wb', buffering=16*1024*1024)
        self.tmp_nodes_f = open(temp_nodes_file, 'wb', buffering=16*1024*1024)
        self.max_nodes = max_nodes_per_way
        self.base_epsilon = epsilon_deg
        self.relation_ways = relation_ways 
        self.used_node_ids = set()
        self.split_id_counter = -1000000000 
        self.ignore_highway_types = frozenset({'corridor', 'elevator', 'proposed', 'construction', 'abandoned', 'raceway'})
        
        self.io_buffer = bytearray()
        self.nodes_io_buffer = bytearray()
        self.FLUSH_LIMIT = 16 * 1024 * 1024 
        self.extracted_pois = 0
        
        if HAS_JIT:
            self.max_pts_capacity = 40000
            self.s_pts = np.empty((self.max_pts_capacity, 2), dtype=np.float64)
            self.s_ref = np.empty(self.max_pts_capacity, dtype=np.int64)
            self.s_stack_start = np.empty(self.max_pts_capacity, dtype=np.int64)
            self.s_stack_end = np.empty(self.max_pts_capacity, dtype=np.int64)
            self.s_keep = np.empty(self.max_pts_capacity, dtype=np.bool_)

    def way(self, w):
        tags = w.tags
        if not len(tags): return 
        
        parsed_tag_lines = []
        poi_tag_lines = []
        app_tag = parsed_tag_lines.append
        app_poi = poi_tag_lines.append
        
        best_name = None
        best_name_prio = 999
        has_allowed_tag = is_area = is_linear = has_poi = False
        
        loc_drop_keys = DROP_TAG_KEYS
        loc_drop_prefixes = DROP_TAG_PREFIXES
        loc_ignore_hwy = self.ignore_highway_types
        loc_poi_triggers = POI_TRIGGER_KEYS
        loc_valid_pois = VALID_POI_VALUES
        loc_watch_tags = WATCH_ALLOWED_TAGS
        loc_name_prios = NAME_PRIORITIES
        loc_ignore_names = IGNORE_NAMES
        loc_escape = escape_xml_bytes
        loc_ignore_bldgs = IGNORE_BUILDING_VALUES
        
        for tag in tags:
            k, v = tag.k, tag.v
            if k in loc_drop_keys or k.startswith(loc_drop_prefixes): continue
            
            if k == 'highway':
                if v in loc_ignore_hwy: return
                is_linear = True
            elif k == 'waterway':
                is_linear = True
                
            if k in {'area', 'landcover', 'surface'}: 
                is_area = True
                
            if k in loc_poi_triggers or v in loc_valid_pois:
                has_poi = True
                
            if k == 'building':
                is_area = True
                if v not in loc_ignore_bldgs:
                    has_allowed_tag = True
                    app_tag(b'  <tag k="%b" v="%b"/>\n' % (loc_escape(k), loc_escape(v)))
                continue
            
            if k in loc_name_prios:
                prio = loc_name_prios[k]
                if prio < best_name_prio:
                    best_name_prio, best_name = prio, v
            
            elif k in loc_watch_tags or k in loc_poi_triggers:
                has_allowed_tag = True
                line = b'  <tag k="%b" v="%b"/>\n' % (loc_escape(k), loc_escape(v))
                app_tag(line)
                if k != 'area':  
                    app_poi(line)

        # Perfectly resolve junk names and prioritize retention
        if best_name and best_name not in loc_ignore_names:
            name_line = b'  <tag k="name" v="%b"/>\n' % loc_escape(best_name)
            app_tag(name_line)
            app_poi(name_line)

        is_in_relation = w.id in self.relation_ways
        is_polygon = w.is_closed() and (has_allowed_tag or is_area)
        
        if not is_in_relation and not parsed_tag_lines: return
        
        num_nodes = len(w.nodes)
        if num_nodes < 2: return

        if HAS_JIT:
            if num_nodes > self.max_pts_capacity:
                self.max_pts_capacity = int(num_nodes * 1.5)
                self.s_pts = np.empty((self.max_pts_capacity, 2), dtype=np.float64)
                self.s_ref = np.empty(self.max_pts_capacity, dtype=np.int64)
                self.s_stack_start = np.empty(self.max_pts_capacity, dtype=np.int64)
                self.s_stack_end = np.empty(self.max_pts_capacity, dtype=np.int64)
                self.s_keep = np.empty(self.max_pts_capacity, dtype=np.bool_)

            idx = 0
            for n in w.nodes:
                loc = n.location
                if loc.valid():
                    self.s_pts[idx, 0], self.s_pts[idx, 1] = loc.lon, loc.lat
                    self.s_ref[idx] = n.ref
                    idx += 1
            if idx < 2: return
            pts_array, valid_nds = self.s_pts[:idx], self.s_ref[:idx]
        else:
            pts, valid_nds = [], []
            for n in w.nodes:
                loc = n.location
                if loc.valid():
                    pts.append((loc.lon, loc.lat))
                    valid_nds.append(n.ref)
            if len(pts) < 2: return
            
        # 🎯 POI Centroid Extraction: Generate independent icon nodes
        if is_polygon and has_poi and not is_in_relation:
            if HAS_JIT:
                center_lon = np.mean(pts_array[:, 0])
                center_lat = np.mean(pts_array[:, 1])
            else:
                center_lon = sum(p[0] for p in pts) / len(pts)
                center_lat = sum(p[1] for p in pts) / len(pts)
                
            node_id = 20000000000 + w.id
            centroid_xml = [b'<node id="%d" visible="true" version="1" lat="%.5f" lon="%.5f">\n' % (node_id, center_lat, center_lon)]
            if poi_tag_lines: centroid_xml.extend(poi_tag_lines)
            centroid_xml.append(b'</node>\n')
            
            self.nodes_io_buffer.extend(b"".join(centroid_xml))
            self.extracted_pois += 1
            if len(self.nodes_io_buffer) > self.FLUSH_LIMIT:
                self.tmp_nodes_f.write(self.nodes_io_buffer)
                self.nodes_io_buffer.clear()

        # Filter tiny polygons (this check is placed after centroid extraction to ensure extremely small landmarks still retain POI icons)
        if is_polygon and not is_in_relation:
            if HAS_JIT:
                if is_too_small(pts_array, 0.00002): return
            else:
                lons, lats = [p[0] for p in pts], [p[1] for p in pts]
                if (max(lats) - min(lats) < 0.00002) and (max(lons) - min(lons) < 0.00002): return

        current_epsilon = self.base_epsilon
        
        if HAS_JIT:
            keep_arr = douglas_peucker_fast(pts_array, current_epsilon, self.s_stack_start, self.s_stack_end, self.s_keep)
            kept_indices = np.nonzero(keep_arr)[0]
        else:
            kept_indices = douglas_peucker_fallback(pts, current_epsilon)

        kept_refs = valid_nds[kept_indices].tolist() if HAS_JIT else [valid_nds[i] for i in kept_indices]
        chunks = [kept_refs] if (is_polygon or is_in_relation) else [kept_refs[i:i + self.max_nodes] for i in range(0, len(kept_refs), max(1, self.max_nodes - 1))]
        
        tag_blob = b"".join(parsed_tag_lines) if parsed_tag_lines else b""
        if not tag_blob and not is_in_relation: return
        
        _extend = self.io_buffer.extend
        _update_nodes = self.used_node_ids.update  
        
        for chunk in chunks:
            if len(chunk) < 2: continue
            
            wid = self.split_id_counter if len(chunks) > 1 else w.id
            if len(chunks) > 1: self.split_id_counter -= 1
            
            chunk_parts = [b'<way id="%d" visible="true" version="1">\n' % wid]
            _app = chunk_parts.append
            
            for nd_ref in chunk:
                _app(b'  <nd ref="%d"/>\n' % nd_ref)
                
            _update_nodes(chunk)
                
            if tag_blob: _app(tag_blob)
            _app(b'</way>\n')
            
            _extend(b"".join(chunk_parts))

        if len(self.io_buffer) > self.FLUSH_LIMIT:
            self.tmp_f.write(self.io_buffer)
            self.io_buffer.clear()

    def close(self):
        if self.io_buffer: self.tmp_f.write(self.io_buffer)
        if self.nodes_io_buffer: self.tmp_nodes_f.write(self.nodes_io_buffer)
        self.tmp_f.close()
        self.tmp_nodes_f.close()


class FinalBuilder(o.SimpleHandler):
    def __init__(self, output_file, temp_ways_file, temp_nodes_file, used_node_ids):
        super().__init__()
        self.f = open(output_file, 'wb', buffering=32*1024*1024)
        self.temp_ways_file = temp_ways_file
        self.temp_nodes_file = temp_nodes_file
        self.used_node_ids = used_node_ids
        self.ways_written = False
        self.minlat, self.maxlat, self.minlon, self.maxlon = 90.0, -90.0, 180.0, -180.0
        self.poi_count, self.rel_count = 0, 0
        
        self.io_buffer = bytearray()
        self.FLUSH_LIMIT = 32 * 1024 * 1024
        
        self._extend = self.io_buffer.extend
        self.f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="OSM_Optimizer_V30_Duo">\n')
        self.bounds_pos = self.f.tell()
        self.f.write(b' ' * 120 + b'\n')

    def node(self, n):
        n_id = n.id
        is_in_way = n_id in self.used_node_ids
        tags = n.tags
        
        if not is_in_way and not tags: 
            return

        is_valid_poi = False
        best_name = None
        best_name_prio = 999
        parsed_tag_lines = []
        _app = parsed_tag_lines.append
        
        loc_drop_keys = DROP_TAG_KEYS
        loc_drop_prefixes = DROP_TAG_PREFIXES
        loc_valid_pois = VALID_POI_VALUES
        loc_poi_triggers = POI_TRIGGER_KEYS
        loc_name_prios = NAME_PRIORITIES
        loc_ignore_names = IGNORE_NAMES
        loc_watch_tags = WATCH_ALLOWED_TAGS
        loc_escape = escape_xml_bytes
        
        if tags:
            for tag in tags:
                k, v = tag.k, tag.v
                if k in loc_drop_keys or k.startswith(loc_drop_prefixes): continue
                
                if not is_valid_poi and (v in loc_valid_pois or k in loc_poi_triggers):
                    is_valid_poi = True
                
                if k in loc_name_prios:
                    prio = loc_name_prios[k]
                    if prio < best_name_prio:
                        best_name_prio, best_name = prio, v
                        
                elif k in loc_watch_tags or k in loc_poi_triggers:
                    _app(b'  <tag k="%b" v="%b"/>\n' % (loc_escape(k), loc_escape(v)))
                    
        if not is_in_way and not is_valid_poi: return

        loc = n.location
        lat, lon = loc.lat, loc.lon
        
        if lat < self.minlat: self.minlat = lat
        elif lat > self.maxlat: self.maxlat = lat
        if lon < self.minlon: self.minlon = lon
        elif lon > self.maxlon: self.maxlon = lon

        if is_in_way and not is_valid_poi:
            self._extend(b'<node id="%d" visible="true" version="1" lat="%.5f" lon="%.5f"/>\n' % (n_id, lat, lon))
        else:
            self.poi_count += 1
            node_parts = [b'<node id="%d" visible="true" version="1" lat="%.5f" lon="%.5f">\n' % (n_id, lat, lon)]
            if best_name and best_name not in loc_ignore_names: 
                node_parts.append(b'  <tag k="name" v="%b"/>\n' % loc_escape(best_name))
            if parsed_tag_lines: node_parts.extend(parsed_tag_lines)
            node_parts.append(b'</node>\n')
            self._extend(b"".join(node_parts))
            
        if len(self.io_buffer) > self.FLUSH_LIMIT:
            self.f.write(self.io_buffer)
            self.io_buffer.clear()

    def write_ways_cache(self):
        if not self.ways_written:
            if self.io_buffer:
                self.f.write(self.io_buffer)
                self.io_buffer.clear()
            with open(self.temp_nodes_file, 'rb') as tn: 
                shutil.copyfileobj(tn, self.f, length=32*1024*1024)
            with open(self.temp_ways_file, 'rb') as tw: 
                shutil.copyfileobj(tw, self.f, length=32*1024*1024)
            self.ways_written = True

    def relation(self, r):
        self.write_ways_cache() 
        tags = r.tags
        
        if not tags or tags.get('type') != 'multipolygon': return
        
        b_val = tags.get('building')
        if b_val and b_val in IGNORE_BUILDING_VALUES:
            return 
            
        self.rel_count += 1
        best_name = None
        best_name_prio = 999
        rel_parts = [b'<relation id="%d" visible="true" version="1">\n' % r.id]
        _app = rel_parts.append
        
        loc_drop_keys = DROP_TAG_KEYS
        loc_drop_prefixes = DROP_TAG_PREFIXES
        loc_name_prios = NAME_PRIORITIES
        loc_ignore_names = IGNORE_NAMES
        loc_watch_tags = WATCH_ALLOWED_TAGS
        loc_poi_triggers = POI_TRIGGER_KEYS
        loc_ignore_bldgs = IGNORE_BUILDING_VALUES
        loc_escape = escape_xml_bytes
        
        for tag in tags:
            k, v = tag.k, tag.v
            if k in loc_drop_keys or k.startswith(loc_drop_prefixes): continue
            
            if k == 'type':
                _app(b'  <tag k="type" v="%b"/>\n' % loc_escape(v))
                
            elif k == 'building':
                if v not in loc_ignore_bldgs:
                    _app(b'  <tag k="%b" v="%b"/>\n' % (loc_escape(k), loc_escape(v)))
                
            elif k in loc_name_prios:
                prio = loc_name_prios[k]
                if prio < best_name_prio:
                    best_name_prio, best_name = prio, v
                    
            elif k in loc_watch_tags or k in loc_poi_triggers:
                _app(b'  <tag k="%b" v="%b"/>\n' % (loc_escape(k), loc_escape(v)))
                
        if best_name and best_name not in loc_ignore_names: 
            _app(b'  <tag k="name" v="%b"/>\n' % loc_escape(best_name))
            
        for m in r.members:
            if m.type == 'w': _app(b'  <member type="way" ref="%d" role="%b"/>\n' % (m.ref, loc_escape(m.role)))
        _app(b'</relation>\n')
        self._extend(b"".join(rel_parts))
        
        if len(self.io_buffer) > self.FLUSH_LIMIT:
            self.f.write(self.io_buffer)
            self.io_buffer.clear()

    def close(self):
        self.write_ways_cache()
        if self.io_buffer:
            self.f.write(self.io_buffer)
            self.io_buffer.clear()
        self.f.write(b'</osm>\n')
        
        bounds_str = b'  <bounds minlat="%.5f" minlon="%.5f" maxlat="%.5f" maxlon="%.5f"/>' % (self.minlat, self.minlon, self.maxlat, self.maxlon)
        self.f.seek(self.bounds_pos)
        self.f.write(bounds_str.ljust(120, b' ') + b'\n')
        self.f.close()

def optimize_osm_pyosmium(input_file, output_file, max_nodes_per_way=50, epsilon_deg=0.00003):
    t0 = time.time()
    
    print(f"🚀 [OSM Optimizer V30 Duo - POI Area & Icon Dual Retention Edition] Started! Input: {input_file}")
    if HAS_JIT: print("   ⚡ Numba AVX hardware acceleration engine activated!")
    
    print(f"⏳ [Pass 1] Scanning relation multi-polygon data...")
    rel_scanner = RelationScanner()
    rel_scanner.apply_file(input_file, locations=False)
    gc.collect()

    temp_ways_name = tempfile.mktemp(suffix=".w.tmp", dir=os.getcwd())
    temp_nodes_name = tempfile.mktemp(suffix=".n.tmp", dir=os.getcwd())
    
    print(f"⏳ [Pass 2] Triggering Bytes streaming write and geometry optimization engine...")
    way_opt = WayOptimizer(temp_ways_name, temp_nodes_name, max_nodes_per_way, epsilon_deg, rel_scanner.relation_ways)
    
    # 🔥 Core fix: Prioritize RAM cache, prevent HDD SWAP thrashing!
    try:
        way_opt.apply_file(input_file, locations=True, idx='flex_mem')
    except RuntimeError:
        print("⚠️ Insufficient memory, downgraded to HDD cache (speed will decrease)...")
        way_opt.apply_file(input_file, locations=True, idx='sparse_file_array')
        
    way_opt.close()
    
    gc.collect()
    print(f"⏳ [Pass 3] Executing seamless merge and final write (Garbage Collection disabled for sprint)...")
    gc.disable()
    
    final_builder = FinalBuilder(output_file, temp_ways_name, temp_nodes_name, way_opt.used_node_ids)
    final_builder.apply_file(input_file, locations=False)
    final_builder.close()
    
    gc.enable() 
    
    for tmp_file in [temp_ways_name, temp_nodes_name]:
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except OSError:
                pass
    
    print(f"🎉 Map core optimization complete! Total time: {format_time(time.time() - t0)}")
    print(f"   📊 Stats: Kept nodes {len(way_opt.used_node_ids):,} | POI landmarks {final_builder.poi_count:,} | Centroids Extracted {way_opt.extracted_pois:,} | Relations {final_builder.rel_count:,}")
    print(f"   📁 Output file: {output_file} (Size: {os.path.getsize(output_file)/(1024*1024):.2f} MB)")

if __name__ == "__main__":
    input_osm = sys.argv[1] if len(sys.argv) > 1 else "base.osm.pbf"
    output_osm = sys.argv[2] if len(sys.argv) > 2 else "base_map.osm"
    if not os.path.exists(input_osm) and input_osm == "base.osm.pbf" and os.path.exists("base.osm"):
        input_osm = "base.osm" 
    optimize_osm_pyosmium(input_osm, output_osm)