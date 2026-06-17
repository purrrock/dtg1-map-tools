#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler (Platform ATS3085S)
===============================================
v13.5 (The Flawless Ultimate Batch Edition)
Compiler of OpenStreetMap (OSM) vector data into closed binary formats
of DT NO.1 G1 smartwatches (.mlp, .idx, .db).
"""

import os
import struct
import json
import hashlib
from lxml import etree as ET
import csv
import sys
import gc
import re
import glob
from typing import List, Tuple, Dict, Any, Set
import argparse
import math
from functools import lru_cache
import array

# ==============================================================================
# PRE-COMPILED C-STRUCTS FOR MAXIMUM PERFORMANCE
# ==============================================================================
PACK_DATA_NODE = struct.Struct("<ffffIII").pack
PACK_NAV_NODE = struct.Struct("<IffffII").pack
PACK_BBOX_INT = struct.Struct("<iiii").pack
PACK_HEADER_INTS = struct.Struct("<II").pack
PACK_INT_LITTLE = struct.Struct("<I").pack
PACK_INT_BIG = struct.Struct(">I").pack

PACK_STR_12 = struct.Struct("<12s").pack
PACK_STR_4 = struct.Struct("<4s").pack
PACK_STR_28 = struct.Struct("<28s").pack
PACK_STR_100 = struct.Struct("<100s").pack

# ==============================================================================
# CONFIGURATION AND SYSTEM CONSTANTS
# ==============================================================================

class HWConfig:
    YZL_HEADER_SIZE = 32
    NODE_SIZE = 28           
    CHUNK_SIZE = 48          
    DBF_HEADER_LEN = 161     
    DBF_RECORD_LEN = 145     
    
    WATER_CODE = 8200
    DEFAULT_HIGHWAY_CODE = 5142
    DEFAULT_POLYGON_CODE = 7208
    DEFAULT_POI_CODE = 2724

_STOP_WORDS = (
    "restaurant", "praspiekt", "boulevard", "проспект", "переулок", "ресторан", 
    "праспект", "рэстаран", "praspekt", "stancyya", "prypynak", "restaran",
    "площадь", "бульвар", "станция", "магазин", "завулак", "станцыя", "highway", 
    "grocery", "station", "zavulak", "voziera", "вуліца", "плошча", "возера", 
    "vulica", "plošča", "bulvar", "alieja", "skvier", "улица", "street", "avenue", 
    "square", "shoppe", "market", "пр-кт", "шоссе", "аллея", "озеро", "сквер", 
    "крама", "blvd.", "drive", "alley", "hotel", "river", "pr-kt", "krama",
    "кафе", "парк", "шаша", "алея", "вул.", "зав.", "кафэ", "šaša", "vul.", 
    "zav.", "kafe", "road", "lane", "cafe", "shop", "mall", "lake", "ave.",
    "ул.", "пер.", "пл.", "st.", "rd.", "ln.", "dr.", "sq.", "way", "pl."
)

_STOP_WORDS_PATTERN = re.compile(r"^(" + "|".join(re.escape(w) for w in sorted(_STOP_WORDS, key=len, reverse=True)) + r")\s+(.*)", re.IGNORECASE)

@lru_cache(maxsize=32768)
def sanitize_osm_name(name: str) -> str:
    if not name: return ""
    match = _STOP_WORDS_PATTERN.match(name)
    if match:
        core = match.group(2).strip()
        if core: name = f"{core[0].upper()}{core[1:]} {match.group(1).lower()}"
    name = name.replace(" ", "_")
    if len(name) > 22: name = name[:22].strip('_') + ".."
    return name.encode('utf-8', 'ignore').decode('utf-8')   

# Safe truncator: prevents broken UTF-8 from multi-byte characters (e.g. CJK) that could crash the watch font engine
def safe_encode(text: Any, max_len: int) -> bytes:
    b = str(text or "").encode('utf-8')
    if len(b) <= max_len: return b
    return b[:max_len].decode('utf-8', 'ignore').encode('utf-8')

class LookupTables:
    HIGHWAY_CODES: Dict[str, int] = {}
    POLYGON_CODES: Dict[str, int] = {}
    POI_CODES: Dict[str, int] = {}
    DISPLAY_SCALES: Dict[int, int] = {}
    POI_SHAPES: Dict[str, str] = {}
    
    DISABLED_ROADS: set = set()
    DISABLED_LANDUSE: set = set()
    DISABLED_POIS: set = set()
    
    MASTER_ROUTING: Dict[Tuple[str, str], Tuple[str, str]] = {}
    POI_ROUTING: Dict[Tuple[str, str], str] = {}

    @classmethod
    def load_from_csv(cls, filepath: str = "features.csv") -> None:
        if not os.path.exists(filepath):
            print(f"[-] Error: Configuration file {filepath} not found.")
            sys.exit(1)
            
        print(f"[>] Loading LUT style table from {filepath}...")
        with open(filepath, mode='r', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=';')
            next(reader, None)
            
            for row in reader:
                if len(row) < 11: continue
                    
                fclass = row[1].strip()
                layer = row[4].strip()
                osm_tag = row[5].strip()             
                enabled_flag = row[10].strip().lower()
                
                if enabled_flag in ('0', 'false', 'no', 'off', ''):
                    if layer == 'roads': cls.DISABLED_ROADS.add(fclass)
                    elif layer == 'pois': cls.DISABLED_POIS.add(fclass)
                    else: cls.DISABLED_LANDUSE.add(fclass)
                    continue 
                
                if osm_tag and "=" in osm_tag:
                    for tag_pair in osm_tag.split(','):
                        if "=" in tag_pair:
                            k, v = tag_pair.split("=", 1)
                            key_tuple = (k.strip(), v.strip())
                            if layer == 'pois': 
                                cls.POI_ROUTING[key_tuple] = fclass
                            else: 
                                mapped_layer = 'landuse' if layer == 'water' else layer
                                cls.MASTER_ROUTING[key_tuple] = (mapped_layer, fclass)
                
                try:
                    remap_code, remap_lod = int(row[7].strip()), int(row[9].strip())
                except ValueError: continue

                if layer == 'roads':
                    cls.HIGHWAY_CODES[fclass] = remap_code
                    cls.DISPLAY_SCALES[remap_code] = remap_lod
                elif layer in ('landuse', 'water'):
                    cls.POLYGON_CODES[fclass] = remap_code
                    cls.DISPLAY_SCALES[remap_code] = remap_lod
                elif layer == 'pois':
                    cls.POI_CODES[fclass] = remap_code
                    cls.DISPLAY_SCALES[remap_code] = remap_lod
                    shape_val = row[11].strip().lower() if len(row) > 11 else 'rhombus'
                    cls.POI_SHAPES[fclass] = shape_val if shape_val else 'rhombus'

        if HWConfig.WATER_CODE not in cls.DISPLAY_SCALES: cls.DISPLAY_SCALES[HWConfig.WATER_CODE] = 1000

# ==============================================================================
# DATA MODELS
# ==============================================================================

class MapFeature:
    __slots__ = ('osm_id', 'fclass', 'code', 'name', 'points', 'parts', 'bbox', 'v1', 'v2', 'mlp_size')

    def __init__(self, osm_id: str, fclass: str, code: int, name: str, points: array.array, parts: List[int] = None):
        self.osm_id = osm_id
        self.fclass = fclass
        self.code = code
        self.name = name
        self.points = points
        self.parts = parts if parts is not None else [0]
        self.bbox = (0, 0, 0, 0)
        self.v1 = 0
        self.v2 = 0
        self.mlp_size = 0

    def calculate_bbox(self) -> None:
        pts = self.points
        if not pts: return
        n = len(pts)
        minx, miny = pts[0], pts[1]
        maxx, maxy = minx, miny
        for i in range(2, n, 2):
            x, y = pts[i], pts[i+1]
            if x < minx: minx = x
            elif x > maxx: maxx = x
            if y < miny: miny = y
            elif y > maxy: maxy = y
        self.bbox = (minx, miny, maxx, maxy)

    def pack_data_node(self) -> bytes:
        bx0, by0, bx1, by1 = self.bbox
        return PACK_DATA_NODE(bx0*1e-6, by0*1e-6, bx1*1e-6, by1*1e-6, self.code, self.v1, self.v2)

class GPXParser:
    @staticmethod
    def parse_track(filepath: str) -> Tuple[str, List[Tuple[float, float]]]:
        if not os.path.exists(filepath):
            return "Route", []
            
        tree = ET.parse(filepath)
        root = tree.getroot()
        ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
        
        track_name = os.path.basename(filepath)
        metadata_name = root.find('.//gpx:metadata/gpx:name', namespaces=ns)
        if metadata_name is not None and metadata_name.text:
            track_name = metadata_name.text.strip()
        else:
            trk_name = root.find('.//gpx:trk/gpx:name', namespaces=ns)
            if trk_name is not None and trk_name.text:
                track_name = trk_name.text.strip()
                
        points = []
        for trkpt in root.findall('.//gpx:trkpt', namespaces=ns):
            attr = trkpt.attrib
            lat = float(attr.get('lat'))
            lon = float(attr.get('lon'))
            points.append((lon, lat))
            
        return track_name, points

class OSMParser:
    RESTRICTED_ACCESS_VALUES = frozenset({'private', 'permit', 'no'})
    
    def __init__(self, osm_file: str):
        self.osm_file = osm_file
        self.nodes: Dict[int, Tuple[int, int]] = {}    
        self.ways_cache: Dict[int, array.array] = {}
        self.roads: List[MapFeature] = []
        self.landuse: List[MapFeature] = []
        self.pois: List[MapFeature] = []

    @staticmethod
    def _is_clockwise(arr: array.array) -> bool:
        area = 0
        x1, y1 = arr[0], arr[1]
        for i in range(2, len(arr), 2):
            x2, y2 = arr[i], arr[i+1]
            area += (x1 * y2 - x2 * y1)
            x1, y1 = x2, y2
        return area < 0

    @staticmethod
    def _reverse_array_inplace(arr: array.array) -> None:
        n = len(arr)
        for i in range(0, n // 2, 2):
            j = n - 2 - i
            arr[i], arr[j] = arr[j], arr[i]
            arr[i+1], arr[j+1] = arr[j+1], arr[i+1]

    @staticmethod
    def _extract_best_name(tags: Dict[str, str]) -> str:
        for k in ('short_name:en', 'int_name', 'name:en', 'short_name', 'name'):
            if k in tags:
                val = tags[k].strip()
                if val: return val
        return ""

    def parse(self) -> Tuple[List[MapFeature], List[MapFeature], List[MapFeature]]:
        self._pass1_cache_nodes()
        self._pass2_build_features()
        return self.roads, self.landuse, self.pois

    def _pass1_cache_nodes(self) -> None:
        print("[>] Pass 1: Caching nodes (Pre-scaled Integer Conversion)...")
        gc.disable()
        nodes_cache = self.nodes
        count = 0
        
        with open(self.osm_file, 'rb', buffering=16777216) as f:
            for event, elem in ET.iterparse(f, events=('end',), tag='node'):
                attr = elem.attrib
                lon, lat = attr.get('lon'), attr.get('lat')
                if lon and lat:
                    nodes_cache[int(attr.get('id'))] = (int(float(lon) * 1000000.0), int(float(lat) * 1000000.0))
                
                count += 1
                if not (count & 0x1FFFF):
                    sys.stdout.write(f"\r    Nodes cached: {count:,}")
                    sys.stdout.flush()
                
                elem.clear()
                parent = elem.getparent()
                if parent is not None:
                    while elem.getprevious() is not None: del parent[0]
        print(f"\r    Nodes loaded: {len(nodes_cache):,}       ")
        gc.enable()
        gc.collect()

    def _pass2_build_features(self) -> None:
        print("[>] Pass 2: Normalizing geometry and POIs (C-Array Baking)...")
        gc.disable()
        count = 0
        
        with open(self.osm_file, 'rb', buffering=16777216) as f:
            for event, elem in ET.iterparse(f, events=('end',), tag=('node', 'way', 'relation')):
                tag = elem.tag
                if tag == 'node' and len(elem): self._process_node(elem)
                elif tag == 'way' and len(elem): self._process_way(elem)
                elif tag == 'relation' and len(elem): self._process_relation(elem)
                    
                count += 1
                if not (count & 0x3FFF):
                    sys.stdout.write(f"\r    Elements processed: {count:,}")
                    sys.stdout.flush()
                    
                elem.clear(keep_tail=True)
                parent = elem.getparent()
                if parent is not None:
                    while elem.getprevious() is not None: del parent[0]
        print(f"\r    Assembled: {len(self.roads)} roads, {len(self.landuse)} polygons, {len(self.pois)} points (POI).      ")
        gc.enable()
        gc.collect()

    def _process_node(self, elem: ET._Element) -> None:
        tags = {}
        fclass = None
        code = None
        routing_pois = LookupTables.POI_ROUTING
        poi_codes = LookupTables.POI_CODES
        
        for c in elem.iterfind('tag'):
            attr = c.attrib
            k, v = attr.get('k'), attr.get('v')
            tags[k] = v
            if not fclass: fclass = routing_pois.get((k, v))

        if not tags or tags.get('access') in self.RESTRICTED_ACCESS_VALUES:
            if 'barrier' in tags:
                fclass = "barrier"
                code = 7209
                LookupTables.POI_SHAPES[fclass] = "barrier"
            else:
                return

        if not fclass:
            for val in tags.values():
                if val in poi_codes:
                    fclass = val; break
                    
        if not fclass or fclass in LookupTables.DISABLED_POIS: return
        
        if code is None:
            code = poi_codes.get(fclass)
        if code is None: return
            
        osm_id = elem.attrib.get('id')
        c_node = self.nodes.get(int(osm_id))
        if not c_node: return

        raw_name = self._extract_best_name(tags)
        name = sanitize_osm_name(raw_name) if raw_name else str(fclass)

        arr = array.array('i', [c_node[0], c_node[1]])
        feature = MapFeature(osm_id, fclass, code, name, arr)
        feature.calculate_bbox()
        self.pois.append(feature)

    def _process_way(self, elem: ET._Element) -> None:
        get_node = self.nodes.get
        arr = array.array('i')
        
        for c in elem.iterfind('nd'):
            ref = c.attrib.get('ref')
            if ref is not None:
                n = get_node(int(ref))
                if n is not None: arr.extend(n)
                
        if len(arr) == 0: return
        osm_id = int(elem.attrib.get('id'))
        self.ways_cache[osm_id] = arr
        
        tags = {}
        target_layer = fclass = None
        master_routing = LookupTables.MASTER_ROUTING
        
        for c in elem.iterfind('tag'):
            attr = c.attrib
            k, v = attr.get('k'), attr.get('v')
            tags[k] = v
            if not target_layer:
                match = master_routing.get((k, v))
                if match: target_layer, fclass = match

        if not tags or tags.get('access') in self.RESTRICTED_ACCESS_VALUES:
            if not ('landuse' in tags or 'leisure' in tags or 'natural' in tags): return

        if not fclass:
            if 'highway' in tags: fclass, target_layer = tags['highway'], 'roads'
            elif 'landuse' in tags: fclass, target_layer = tags['landuse'], 'landuse'
            elif 'natural' in tags: fclass, target_layer = tags['natural'], 'landuse'
            elif 'leisure' in tags: fclass, target_layer = tags['leisure'], 'landuse'

        if not fclass: return
        points_count = len(arr) // 2
        
        if points_count > 1024:
            return

        if target_layer == 'roads' and points_count >= 2:
            if fclass == 'track' and 'tracktype' in tags: fclass = fclass + '_' + tags['tracktype']
            if fclass in LookupTables.DISABLED_ROADS: return
                
            raw_name = self._extract_best_name(tags)
            name = sanitize_osm_name(raw_name) if raw_name else ""
                
            feature = MapFeature(str(osm_id), fclass, LookupTables.HIGHWAY_CODES.get(fclass, HWConfig.DEFAULT_HIGHWAY_CODE), name, arr)
            feature.calculate_bbox()
            self.roads.append(feature)
            
        elif target_layer == 'landuse' and points_count >= 4:
            if fclass in LookupTables.DISABLED_LANDUSE: return
                
            if arr[0] == arr[-2] and arr[1] == arr[-1]: 
                if not self._is_clockwise(arr): self._reverse_array_inplace(arr)
                raw_name = self._extract_best_name(tags)
                name = sanitize_osm_name(raw_name) if raw_name else ""

                feature = MapFeature(str(osm_id), fclass, LookupTables.POLYGON_CODES.get(fclass, HWConfig.DEFAULT_POLYGON_CODE), name, arr)
                feature.calculate_bbox()
                self.landuse.append(feature)

    def _process_relation(self, elem: ET._Element) -> None:
        tags = {}
        fclass = None
        master_routing = LookupTables.MASTER_ROUTING
        
        for c in elem.iterfind('tag'):
            attr = c.attrib
            k, v = attr.get('k'), attr.get('v')
            tags[k] = v
            if not fclass:
                match = master_routing.get((k, v))
                if match and match[0] == 'landuse': fclass = match[1]

        if tags.get('type') != 'multipolygon': return
        if not fclass: fclass = tags.get('landuse') or tags.get('leisure') or tags.get('natural')
        if not fclass or fclass in LookupTables.DISABLED_LANDUSE: return
            
        combined_arr = array.array('i')
        parts = []
        current_index = 0
        outer_list, inner_list = [], []
        
        for c in elem.iterfind('member'):
            attr = c.attrib
            if attr.get('type') == 'way':
                ref = attr.get('ref')
                if not ref: continue
                ref_int = int(ref)
                if ref_int not in self.ways_cache: continue
                
                if attr.get('role', 'outer') == 'outer': outer_list.append((ref_int, True))
                else: inner_list.append((ref_int, False))
        
        for ref_int, is_outer in (outer_list + inner_list):
            ring_arr = array.array('i', self.ways_cache[ref_int])
            pts_count = len(ring_arr) // 2
            
            if pts_count >= 4 and ring_arr[0] == ring_arr[-2] and ring_arr[1] == ring_arr[-1]:
                is_cw = self._is_clockwise(ring_arr)
                if is_outer and not is_cw: self._reverse_array_inplace(ring_arr)
                elif not is_outer and is_cw: self._reverse_array_inplace(ring_arr)
                    
                parts.append(current_index)
                combined_arr.extend(ring_arr)
                current_index += pts_count
        
        osm_id = elem.attrib.get('id')
        if len(combined_arr) > 0 and parts and osm_id:
            raw_name = self._extract_best_name(tags)
            name = sanitize_osm_name(raw_name) if raw_name else ""

            feature = MapFeature(osm_id, fclass, LookupTables.POLYGON_CODES.get(fclass, HWConfig.DEFAULT_POLYGON_CODE), name, combined_arr, parts)
            feature.calculate_bbox()
            self.landuse.append(feature)
            
class POIGeometryFactory:
    EARTH_RADIUS, RAD_TO_DEG, R, PERSPECTIVE_Y_MULTIPLIER = 6378137.0, 180.0 / math.pi, 4.1, 1.5

    @classmethod
    def generate_polygon(cls, shape_type: str, center_lon_scaled: int, center_lat_scaled: int) -> array.array:
        R = cls.R
        shapes = {
            "rhombus": [(0, R * 1.4), (R, 0), (0, -R * 1.4), (-R, 0), (0, R * 1.4)],
            "triangle": [(0, R), (R, -R), (-R, -R), (0, R)],
            "house": [(0, R + 1), (R, R - 3), (R, -R), (-R, -R), (-R, R - 3), (0, R + 1)],
            "cup": [(-R, R), (R, R), (R, -R + 2.5), (R - 2.5, -R), (-R + 2.5, -R), (-R, -R + 2.5), (-R, R)],
            "cross": [(-2, R), (2, R), (2, 2), (R, 2), (R, -2), (2, -2), (2, -R), (-2, -R), (-2, -2), (-R, -2), (-R, 2), (-2, 2), (-2, R)],
            "toilet": [(-R, R), (R, R), (0.5, 0), (R, -R), (-R, -R), (-0.5, 0), (-R, R)],
            "transport": [
                (-R, R - 1), (R - 3, R - 1), (R, R - 3.0), (R, -R), 
                (R - 1.0, -R), (R - 1.0, -R + 1.5), (R - 3.0, -R + 1.5), (R - 3.0, -R), 
                (-R + 3.0, -R), (-R + 3.0, -R + 1.5), (-R + 1.0, -R + 1.5), (-R + 1.0, -R), 
                (-R, -R), (-R, R - 1)
            ],
            "shop": [(-R, R), (R, R), (R - 2.5, -R), (-R, -R), (-R, R)],
            "attraction": [(-R, R), (-2.5, R - 2.0), (0.0, R), (2.5, R - 2.0), (R, R), (R, -R), (-R, -R), (-R, R)],
            "bicycle": [
                (-7.5, 1.5), (-5.25, 4.0), (-1.5, 4.0), (0.0, 1.5),
                (1.5, 4.0), (5.25, 4.0), (7.5, 1.5), (7.5, -1.5),
                (5.25, -4.0), (1.5, -4.0), (0.0, -1.5), (-1.5, -4.0),
                (-5.25, -4.0), (-7.5, -1.5), (-7.5, 1.5)
            ],
            "shower": [
                (0.0, R), (5, 1.5), (-0.75, 1.5), (-0.75, -R), 
                (-5, -R), (-5, 1.5), (0.0, R)
            ],
            "barrier": [
                (0.0, 1.5), (R - 1.5, R), (R, R - 1.5), (1.5, 0.0), 
                (R, -R + 1.5), (R - 1.5, -R), (0.0, -1.5), 
                (-R + 1.5, -R), (-R, -R + 1.5), (-1.5, 0.0), 
                (-R, R - 1.5), (-R + 1.5, R), (0.0, 1.5)
            ]
        }
        rel_coords = shapes.get(shape_type, shapes["rhombus"])
        
        lon, lat = center_lon_scaled * 1e-6, center_lat_scaled * 1e-6
        earth_rad_cos = cls.EARTH_RADIUS * max(abs(math.cos(math.radians(lat))), 1e-10)
        
        arr = array.array('i')
        for x, y in rel_coords:
            arr.append(int((lon + (x / earth_rad_cos) * cls.RAD_TO_DEG) * 1000000.0))
            arr.append(int((lat + ((y * cls.PERSPECTIVE_Y_MULTIPLIER) / cls.EARTH_RADIUS) * cls.RAD_TO_DEG) * 1000000.0))
        return arr

class BufferedFileWriter:
    def __init__(self, filepath: str, is_idx: bool):
        self.f = open(filepath, 'wb')
        self.md5 = hashlib.md5()
        self.size = 0
        self.lod2_size = 0
        self.is_idx = is_idx
        self.buffer = bytearray()
        self.BUFFER_LIMIT = 1048576 
        self.f.write(b'\x00' * HWConfig.YZL_HEADER_SIZE) 

    @property
    def current_size(self) -> int:
        return self.size + len(self.buffer)

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)
        if len(self.buffer) >= self.BUFFER_LIMIT: self._flush()

    def _flush(self) -> None:
        if self.buffer:
            self.f.write(self.buffer)
            self.md5.update(self.buffer)
            self.size += len(self.buffer)
            self.buffer.clear()

    def close(self) -> None:
        self._flush() 
        self.f.seek(0)
        hash_bytes = self.md5.digest()
        
        if self.is_idx: header = b'YZL\x08' + PACK_INT_LITTLE(self.size) + b'\x02\x00\x00\x04' + PACK_INT_BIG(self.lod2_size) + hash_bytes
        else: header = b'YZL\x00' + PACK_INT_LITTLE(self.size) + b'\x00\x00\x00\x04\x00\x00\x00\x00' + hash_bytes
            
        self.f.write(header)
        self.f.close()
        
class MapCompiler:
    @classmethod
    def compile_mlp(cls, features: List[MapFeature], filepath: str) -> None:
        print(f"[>] Compiling geometry: {os.path.basename(filepath)}...")
        writer = BufferedFileWriter(filepath, is_idx=False)
        current_offset = 0
        swap_needed = sys.byteorder == 'big'

        for record_number, feature in enumerate(features, 1):
            body_chunks = [
                PACK_BBOX_INT(*feature.bbox),
                PACK_HEADER_INTS(len(feature.parts), len(feature.points) // 2)
            ]
            
            if feature.parts: body_chunks.append(struct.pack(f"<{len(feature.parts)}I", *feature.parts))
            if feature.points:
                if swap_needed: 
                    feature.points.byteswap()
                    body_chunks.append(feature.points.tobytes())
                    feature.points.byteswap()
                else:
                    body_chunks.append(feature.points.tobytes())
            
            body_bin = b''.join(body_chunks)
            record_bin = PACK_INT_BIG(record_number) + PACK_INT_LITTLE(len(body_bin)) + body_bin

            feature.v1 = current_offset + 8
            feature.v2 = 1 
            feature.mlp_size = len(record_bin)
            
            writer.write(record_bin)
            current_offset += len(record_bin)

        writer.close()

    @classmethod
    def compile_db(cls, features: List[MapFeature], filepath: str, is_poi: bool = False) -> None:
        if not is_poi and not any(feature.name for feature in features): return
        elif is_poi and not features: return
    
        print(f"[>] Compiling attributes: {os.path.basename(filepath)}...")
        writer = BufferedFileWriter(filepath, is_idx=False)
        
        total_records = len(features) if is_poi else len([f for f in features if f.name]) + 1
        db_counter = 1 if is_poi else 2 
        
        def desc(name: str, length: int) -> bytes:
            return name.encode('ascii').ljust(11, b'\x00') + b'C' + b'\x00'*4 + bytes([length]) + b'\x00'*15

        writer.write(
            b'\x03\x00\x00\x00' + 
            struct.pack('<IHH', total_records, HWConfig.DBF_HEADER_LEN, HWConfig.DBF_RECORD_LEN) + 
            b'\x00' * 20 + desc("osm_id", 12) + desc("code", 4) + desc("fclass", 28) + desc("name", 100) + b'\x0D'
        )
        if not is_poi: writer.write(b'\x00' * HWConfig.DBF_RECORD_LEN) 

        for feature in features:
            if is_poi or feature.name:
                feature.v2 = db_counter
                db_counter += 1
                
                writer.write(
                    b'\x20' + 
                    PACK_STR_12(safe_encode(feature.osm_id, 12)) + 
                    PACK_STR_4(safe_encode(feature.code, 4)) + 
                    PACK_STR_28(safe_encode(feature.fclass, 28)) + 
                    PACK_STR_100(safe_encode(feature.name, 100))
                )
        writer.close()

    @classmethod
    def compile_idx(cls, features: List[MapFeature], filepath: str, is_poi: bool = False) -> None:
        print(f"[>] Compiling SQT index: {os.path.basename(filepath)}...")
        writer = BufferedFileWriter(filepath, is_idx=not is_poi)
        
        def write_cluster(cluster):
            c_minx, c_miny, c_maxx, c_maxy = cluster[0].bbox
            for f in cluster[1:]:
                bx0, by0, bx1, by1 = f.bbox
                if bx0 < c_minx: c_minx = bx0
                if by0 < c_miny: c_miny = by0
                if bx1 > c_maxx: c_maxx = bx1
                if by1 > c_maxy: c_maxy = by1
            
            writer.write(PACK_NAV_NODE((len(cluster) * HWConfig.NODE_SIZE) + 8, c_minx*1e-6, c_miny*1e-6, c_maxx*1e-6, c_maxy*1e-6, 0, len(cluster)))                    
            for f in cluster: writer.write(f.pack_data_node())

        if is_poi:
            writer.write(b'SQT\x01\x01\x00\x00\x00') 
            if not features:
                writer.write(b'\x00\x00\x00\x00\x00\x00\x00\x00')
                return writer.close()

            clusters = [features[i:i + HWConfig.CHUNK_SIZE] for i in range(0, len(features), HWConfig.CHUNK_SIZE)]
            if len(clusters) > 1:
                writer.write(PACK_HEADER_INTS(1, len(clusters)))
                for cluster in clusters: write_cluster(cluster)
            else:
                writer.write(PACK_HEADER_INTS(0, len(clusters[0]) if clusters else 0))
                for f in clusters[0] if clusters else []: writer.write(f.pack_data_node())
        else:
            scales_dict = LookupTables.DISPLAY_SCALES
            # Build 5 LOD arrays (LOD 0 ~ LOD 4)
            lod0, lod1, lod2, lod3, lod4 = [], [], [], [], []
            for f in features:
                lod0.append(f)
                scale = scales_dict.get(f.code, 20)
                if scale >= 50: lod1.append(f)    # LOD 1 (rural tracks, service roads)
                if scale >= 100: lod2.append(f)   # LOD 2 (urban roads, small water bodies)
                if scale >= 500: lod3.append(f)   # LOD 3 (county roads, provincial roads, water areas)
                if scale >= 1000: lod4.append(f)  # LOD 4 (motorways, global outline)

            lod_records_list = [lod0, lod1, lod2, lod3, lod4]
            last_lod_size = 0
            
            for lod_index, lod_records in enumerate(lod_records_list):
                start_len = writer.current_size
                writer.write(b'SQT\x01\x00\x00\x00\x00')
                
                if not lod_records:
                    writer.write(b'\x00\x00\x00\x00\x00\x00\x00\x00')
                    # Record the exact length of the last layer (LOD 4, Index 4)
                    if lod_index == 4: last_lod_size = writer.current_size - start_len
                    continue

                clusters = [lod_records[i:i + HWConfig.CHUNK_SIZE] for i in range(0, len(lod_records), HWConfig.CHUNK_SIZE)]
                if len(clusters) > 1:
                    writer.write(PACK_HEADER_INTS(1, len(clusters)))
                    for cluster in clusters: write_cluster(cluster)
                else:
                    writer.write(PACK_HEADER_INTS(0, len(clusters[0]) if clusters else 0))
                    for f in clusters[0] if clusters else []: writer.write(f.pack_data_node())
       
                # Record the last layer length for the YZL header pointer
                if lod_index == 4: last_lod_size = writer.current_size - start_len
                
            writer.lod2_size = last_lod_size # Variable name kept for BufferedFileWriter compatibility, but the value is the correct LOD 4
        writer.close()

    @staticmethod
    def create_empty_layer(layer_prefix: str) -> None:
        print(f"[>] Creating system dynamically dummy: {os.path.basename(layer_prefix)}...")
        
        # 1. Generate an empty MLP
        mlp_hex = "595A4C00000000000000000400000000D41D8CD98F00B204E9800998ECF8427E"
        with open(f"{layer_prefix}.mlp", "wb") as f: 
            f.write(bytearray.fromhex(mlp_hex))
        
        # 2. Dynamically generate an IDX containing 5 empty SQT layers (MD5 and size auto-calculated)
        idx_writer = BufferedFileWriter(f"{layer_prefix}.idx", is_idx=True)
        # Write 5 empty LOD blocks
        for _ in range(5):
            # SQT\x01 (4) + Marker (4) + Mode (4) + Count (4) = 16 bytes
            idx_writer.write(b'SQT\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
        
        # Each empty block is 16 bytes
        idx_writer.lod2_size = 16 
        idx_writer.close()
 
    @staticmethod
    def create_map_name(name: str, meta_records: List[MapFeature], out_file: str) -> None:
        if not meta_records: return
        center_lat = (min(r.bbox[1] for r in meta_records) + max(r.bbox[3] for r in meta_records)) * 5e-7
        center_lon = (min(r.bbox[0] for r in meta_records) + max(r.bbox[2] for r in meta_records)) * 5e-7
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump({"centerLat": center_lat, "centerLon": center_lon, "mapName": name}, f, separators=(',', ':'))

# ==============================================================================
# BATCH PROCESSING ENGINE
# ==============================================================================

def process_single_tile(osm_path: str, out_dir: str, tile_name: str, args: argparse.Namespace) -> None:
    print(f"\n" + "="*50)
    print(f"📦 PROCESSING TILE: {tile_name}")
    print(f"📄 Source: {osm_path}")
    print(f"="*50)

    # Ensure the output directory exists
    os.makedirs(out_dir, exist_ok=True)

    parser = OSMParser(osm_path)
    roads_data, landuse_data, pois_data = parser.parse()

    # Free parser memory
    del parser.nodes
    del parser.ways_cache
    gc.collect()

    # ==============================================================================
    # Multi-GPX track injection logic (scans the route/ folder)
    # ==============================================================================
    route_dir = "route"
    if os.path.isdir(route_dir):
        gpx_files = sorted([f for f in os.listdir(route_dir) if f.lower().endswith('.gpx')])
        if gpx_files:
            print(f"[>] '{route_dir}' folder detected. Performing multi-GPX injection...")
            gpx_counter = 1
            for gpx_filename in gpx_files:
                gpx_filepath = os.path.join(route_dir, gpx_filename)
                track_name, track_points = GPXParser.parse_track(gpx_filepath)
                if track_points and len(track_points) >= 2:
                    arr = array.array('i')
                    for p in track_points:
                        arr.extend((int(p[0]*1e6), int(p[1]*1e6)))
                        
                    feature_id = f"user_track_{gpx_counter:03d}"
                    gpx_code = LookupTables.HIGHWAY_CODES.get("gpx_track", 5124)
                    gpx_feature = MapFeature(feature_id, "gpx_track", gpx_code, track_name, arr)
                    gpx_feature.calculate_bbox()
                    roads_data.append(gpx_feature)
                    print(f"    - Injected GPX: '{track_name}' ({len(track_points)} points) from {gpx_filename}")
                    gpx_counter += 1
    # ==============================================================================
    
    meta_all: List[MapFeature] = []

    if roads_data:
        MapCompiler.compile_mlp(roads_data, os.path.join(out_dir, "roads.mlp"))
        MapCompiler.compile_db(roads_data, os.path.join(out_dir, "roads.db"))
        MapCompiler.compile_idx(roads_data, os.path.join(out_dir, "roads.idx"))
        meta_all.extend(roads_data)

    if args.poi_mode == "landuse" and pois_data:
        print("[>] Baking POI objects into landuse layer...")
        for poi in pois_data:
            if not poi.points: continue
            shape_type = LookupTables.POI_SHAPES.get(poi.fclass, "rhombus")
            poi.points = POIGeometryFactory.generate_polygon(shape_type, poi.points[0], poi.points[1])
            poi.calculate_bbox()
            landuse_data.append(poi)
        pois_data.clear() 

    landuse_only = [f for f in landuse_data if f.code != HWConfig.WATER_CODE]
    water_only = [f for f in landuse_data if f.code == HWConfig.WATER_CODE]

    if landuse_only:
        MapCompiler.compile_mlp(landuse_only, os.path.join(out_dir, "landuse.mlp"))
        MapCompiler.compile_db(landuse_only, os.path.join(out_dir, "landuse.db"))
        MapCompiler.compile_idx(landuse_only, os.path.join(out_dir, "landuse.idx"))
        meta_all.extend(landuse_only)
    else: 
        MapCompiler.create_empty_layer(os.path.join(out_dir, "landuse"))

    if water_only:
        MapCompiler.compile_mlp(water_only, os.path.join(out_dir, "water.mlp"))
        MapCompiler.compile_db(water_only, os.path.join(out_dir, "water.db"))
        MapCompiler.compile_idx(water_only, os.path.join(out_dir, "water.idx"))
        meta_all.extend(water_only)
    else:
        # Pre-create an empty water layer even when absent to prevent watch errors
        MapCompiler.create_empty_layer(os.path.join(out_dir, "water"))

    if args.poi_mode == "native" and pois_data:
        MapCompiler.compile_db(pois_data, os.path.join(out_dir, "pois.db"), is_poi=True)
        MapCompiler.compile_idx(pois_data, os.path.join(out_dir, "pois.idx"), is_poi=True)
        meta_all.extend(pois_data)

    if meta_all: 
        # Automatically set map.name to the tile name
        MapCompiler.create_map_name(tile_name, meta_all, os.path.join(out_dir, "map.name"))
        
    print(f"[SUCCESS] Tile {tile_name} compiled.")
    
    # 🚨 Aggressive memory cleanup (ensures batch processing doesn't crash)
    roads_data.clear()
    landuse_data.clear()
    pois_data.clear()
    meta_all.clear()
    gc.collect()

def main():
    cli_parser = argparse.ArgumentParser(description="DT G1 Map Compiler (Platform ATS3085S) - Vector OSM to Binary YZL/SQT")
    cli_parser.add_argument("-p", "--poi-mode", choices=["native", "landuse", "none"], default="none")
    args = cli_parser.parse_args()

    print("=========================================")
    print("DT G1 BATCH MAP COMPILER")
    print("=========================================")
    
    LookupTables.load_from_csv("features.csv")

    osm_folder = "osm"
    if not os.path.isdir(osm_folder):
        print(f"[-] Error: '{osm_folder}' directory not found.")
        print(f"[*] Please create a folder named '{osm_folder}' and put your .osm tiles inside it.")
        sys.exit(1)

    # Find all .osm files inside the OSM directory
    osm_files = sorted([f for f in os.listdir(osm_folder) if f.lower().endswith('.osm')])
    
    if not osm_files:
        print(f"[-] Error: No .osm files found inside the '{osm_folder}' directory.")
        sys.exit(1)

    print(f"[+] Found {len(osm_files)} OSM file(s). Starting batch compilation...")

    # Start processing files one by one
    for filename in osm_files:
        osm_path = os.path.join(osm_folder, filename)
        tile_name = os.path.splitext(filename)[0] # Strip the .osm extension (e.g. N23E120)
        out_dir = os.path.join(osm_folder, tile_name)
        
        process_single_tile(osm_path, out_dir, tile_name, args)

    print("\n🎉 [ALL DONE] Entire batch operation completed successfully!")
    print(f"📁 Please check the '{osm_folder}' folder for your compiled map directories.")

if __name__ == "__main__":
    main()