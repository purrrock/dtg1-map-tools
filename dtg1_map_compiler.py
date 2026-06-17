#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler (Platform ATS3085S)
===============================================
v3.1 (Refactored & Typified)
Compiler of OpenStreetMap (OSM) vector data into closed binary formats
of DT NO.1 G1 smartwatches (.mlp, .idx, .db).
"""

import os
import sys
import csv
import json
import math
import struct
import hashlib
import argparse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any, Optional

# ==============================================================================
# 1. CONFIGURATION AND SYSTEM CONSTANTS
# ==============================================================================

class HWConfig:
    """Hardware and system constants for the ATS3085S platform"""
    YZL_HEADER_SIZE = 32
    NODE_SIZE = 28           # Unified node size (Data Node / Nav Node)
    CHUNK_SIZE = 14          # Maximum number of objects in a cluster
    DBF_HEADER_LEN = 161     # dBase III header
    DBF_RECORD_LEN = 145     # Fixed-length dBase III record
    
    # System rendering codes
    WATER_CODE = 8200
    DEFAULT_HIGHWAY_CODE = 5142
    DEFAULT_POLYGON_CODE = 7208
    DEFAULT_POI_CODE = 2724

# Global immutable tuple of toponymic descriptors.
# Strictly sorted by descending length for the startswith algorithm.
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

class LookupTables:
    """Dynamic style tables (LUT) and hardware early-exit registries"""
    HIGHWAY_CODES: Dict[str, int] = {}
    POLYGON_CODES: Dict[str, int] = {}
    POI_CODES: Dict[str, int] = {}
    DISPLAY_SCALES: Dict[int, int] = {}
    POI_SHAPES: Dict[str, str] = {}
    
    # Isolated blacklists (protection against namespace collisions)
    DISABLED_ROADS: set = set()
    DISABLED_LANDUSE: set = set()
    DISABLED_POIS: set = set()
    
    # Tag routing registries
    TAG_ROUTING: Dict[str, Dict[Tuple[str, str], str]] = {
        'pois': {}, 'roads': {}, 'landuse': {}, 'water': {}
    }

    @classmethod
    def load_from_csv(cls, filepath: str = "features.csv") -> None:
        """Parse the external routing configuration file."""
        if not os.path.exists(filepath):
            print(f"[-] Error: Configuration file {filepath} not found.")
            sys.exit(1)
            
        print(f"[>] Loading LUT style table from {filepath}...")
        
        try:
            with open(filepath, mode='r', encoding='utf-8') as f:
                reader = csv.reader(f, delimiter=';')
                next(reader, None) # Skip header
                
                loaded_records = 0
                for row in reader:
                    if len(row) < 11:
                        continue
                        
                    fclass = row[1].strip()
                    layer = row[4].strip()
                    osm_tag = row[5].strip()             
                    
                    # Software culling (early-exit parsing)
                    enabled_flag = row[10].strip().lower()
                    if enabled_flag in ('0', 'false', 'no', 'off', ''):
                        if layer == 'roads': cls.DISABLED_ROADS.add(fclass)
                        elif layer == 'pois': cls.DISABLED_POIS.add(fclass)
                        else: cls.DISABLED_LANDUSE.add(fclass)
                        continue 
                    
                    # Tag routing integration (key-value)
                    if osm_tag and "=" in osm_tag:
                        for tag_pair in osm_tag.split(','):
                            if "=" in tag_pair:
                                k, v = tag_pair.split("=", 1)
                                cls.TAG_ROUTING[layer][(k.strip(), v.strip())] = fclass
                    
                    try:
                        remap_code = int(row[7].strip())
                        remap_lod = int(row[9].strip())
                    except ValueError:
                        continue

                    # Layer distribution
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
                        
                    loaded_records += 1
                    
            print(f"    Successfully imported rules: {loaded_records}")
            print(f"[i] LUT loaded. Roads: {len(cls.HIGHWAY_CODES)}, Polygons: {len(cls.POLYGON_CODES)}, POI: {len(cls.POI_CODES)}")
            
            # Fail-safe LOD initialization for water
            if HWConfig.WATER_CODE not in cls.DISPLAY_SCALES:
                cls.DISPLAY_SCALES[HWConfig.WATER_CODE] = 1000
                
        except Exception as e:
            print(f"[-] Fatal parsing error {filepath}: {e}")
            sys.exit(1)

# ==============================================================================
# 2. DATA MODELS
# ==============================================================================

@dataclass
class MapFeature:
    """Represents a single map primitive (Road, Polygon, POI)"""
    osm_id: str
    fclass: str
    code: int
    name: str
    points: List[Tuple[float, float]]
    parts: List[int] = field(default_factory=lambda: [0])
    
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    v1: int = 0        # Absolute geometry offset in the .mlp file
    v2: int = 0        # Row index in the attribute DB .db
    mlp_size: int = 0  # Binary body size in the .mlp file

    def calculate_bbox(self) -> None:
        """Direct bounding box calculation (optimized with list comprehensions)."""
        if not self.points:
            return
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    def pack_data_node(self) -> bytes:
        """
        Packing a Data Node (strictly 28 bytes).
        Format (C-Union): [BBox 16b] [Type 4b] [v1 4b] [v2 4b]
        """
        return struct.pack(
            "<ffffIII", 
            self.bbox[0], self.bbox[1], self.bbox[2], self.bbox[3], 
            self.code, self.v1, self.v2
        )

# ==============================================================================
# 3. GEOMETRY & MATH
# ==============================================================================

class POIGeometryFactory:
    """Generator of low-polygon primitives for the POI layer."""
    EARTH_RADIUS = 6378137.0
    R = 4.1
    PERSPECTIVE_Y_MULTIPLIER = 1.5 # Compensation for ATS3085S display distortion

    @classmethod
    def generate_polygon(cls, shape_type: str, center_lon: float, center_lat: float) -> List[Tuple[float, float]]:
        """Convert metric shapes into spherical polygons (WGS 84)."""
        R = cls.R
        
        shapes = {
            "rhombus": [(0, R * 1.4), (R, 0), (0, -R * 1.4), (-R, 0), (0, R * 1.4)],
            "triangle": [(0, R), (R, -R), (-R, -R), (0, R)],
            "house": [(0, R + 1), (R, R - 3), (R, -R), (-R, -R), (-R, R - 3), (0, R + 1)],
            "cup": [(-R, R), (R, R), (R, -R + 2.5), (R - 2.5, -R), (-R + 2.5, -R), (-R, -R + 2.5), (-R, R)],
            "cross": [(-2, R), (2, R), (2, 2), (R, 2), (R, -2), (2, -2), (2, -R), (-2, -R), (-2, -2), (-R, -2), (-R, 2), (-2, 2), (-2, R)],
            "toilet": [(-R, R), (R, R), (0.5, 0), (R, -R), (-R, -R), (-0.5, 0), (-R, R)],
            "transport": [(-R, R - 1), (R - 3, R - 1), (R, R - 3.0), (R, -R), (R - 1.0, -R), (R - 1.0, -R + 1.5), (R - 3.0, -R + 1.5), (R - 3.0, -R), (-R + 3.0, -R), (-R + 3.0, -R + 1.5), (-R + 1.0, -R + 1.5), (-R + 1.0, -R), (-R, -R), (-R, R - 1)],
            "shop": [(-R, R), (R, R), (R - 2.5, -R), (-R, -R), (-R, R)],
            "attraction": [(-R, R), (-2.5, R - 2.0), (0.0, R), (2.5, R - 2.0), (R, R), (R, -R), (-R, -R), (-R, R)],
            "bicycle": [(-7.5, 1.5), (-5.25, 4.0), (-1.5, 4.0), (0.0, 1.5), (1.5, 4.0), (5.25, 4.0), (7.5, 1.5), (7.5, -1.5), (5.25, -4.0), (1.5, -4.0), (0.0, -1.5), (-1.5, -4.0), (-5.25, -4.0), (-7.5, -1.5), (-7.5, 1.5)],
            "shower": [(0.0, R), (5, 1.5), (-0.75, 1.5), (-0.75, -R), (-5, -R), (-5, 1.5), (0.0, R)],
            "barrier": [(0.0, 1.5), (R - 1.5, R), (R, R - 1.5), (1.5, 0.0), (R, -R + 1.5), (R - 1.5, -R), (0.0, -1.5), (-R + 1.5, -R), (-R, -R + 1.5), (-1.5, 0.0), (-R, R - 1.5), (-R + 1.5, R), (0.0, 1.5)]
        }
        
        rel_coords = shapes.get(shape_type, shapes["rhombus"])
        points = []
        lat_rad = math.radians(center_lat)
        cos_lat = math.cos(lat_rad)
        
        for x_offset, y_offset in rel_coords:
            y_offset_stretched = y_offset * cls.PERSPECTIVE_Y_MULTIPLIER
            d_lat = (y_offset_stretched / cls.EARTH_RADIUS) * (180.0 / math.pi)
            d_lon = (x_offset / (cls.EARTH_RADIUS * cos_lat)) * (180.0 / math.pi)
            points.append((center_lon + d_lon, center_lat + d_lat))
            
        return points

# ==============================================================================
# 4. PARSERS
# ==============================================================================

class GPXParser:
    """Extract track geometry for injection into the Roads layer."""
    
    @staticmethod
    def parse_track(filepath: str) -> Tuple[str, List[Tuple[float, float]]]:
        if not os.path.exists(filepath):
            return "Route", []
            
        tree = ET.parse(filepath)
        root = tree.getroot()
        ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
        
        # Cascading name lookup
        track_name = "Route" 
        metadata_name = root.find('.//gpx:metadata/gpx:name', ns)
        if metadata_name is not None and metadata_name.text:
            track_name = metadata_name.text.strip()
        else:
            trk_name = root.find('.//gpx:trk/gpx:name', ns)
            if trk_name is not None and trk_name.text:
                track_name = trk_name.text.strip()
                
        # Geometry extraction
        points = []
        for trkpt in root.findall('.//gpx:trkpt', ns):
            points.append((float(trkpt.attrib['lon']), float(trkpt.attrib['lat'])))
            
        return track_name, points

class OSMParser:
    """Two-pass streaming OSM parser with topology handling."""
    RESTRICTED_ACCESS_VALUES = {'private', 'permit', 'no'}
    
    def __init__(self, osm_file: str):
        self.osm_file = osm_file
        self.nodes: Dict[int, Tuple[float, float]] = {}    
        self.ways_cache: Dict[int, List[Tuple[float, float]]] = {}
        
        self.roads: List[MapFeature] = []
        self.landuse: List[MapFeature] = []
        self.pois: List[MapFeature] = []

    @staticmethod
    def _is_clockwise(points: List[Tuple[float, float]]) -> bool:
        """Mathematical ring orientation check (negative area == CW)."""
        sum_area = sum((points[i][0] * points[i+1][1] - points[i+1][0] * points[i][1]) 
                       for i in range(len(points) - 1))
        return sum_area < 0
        
    def _analyze_road_surface(self, tags: Dict[str, str]) -> Optional[str]:
        """
        Анализирует теги качества и покрытия дороги.
        Приоритет отдается тегу smoothness, так как разбитый асфальт (surface=asphalt + smoothness=bad) 
        для навигации должен трактоваться как unpaved (серый цвет).
        
        Если теги отсутствуют, возвращает None, позволяя компилятору использовать 
        базовые цвета маршрутизации из LUT (features.csv).
        """
        smoothness = tags.get("smoothness")
        if smoothness in {"bad", "very_bad", "horrible", "very_horrible", "impassable"}:
            return "unpaved"
        if smoothness in {"excellent", "good", "intermediate"}:
            return "paved"

        surface = tags.get("surface")
        if surface in {"unpaved", "grass_paver", "sett", "unhewn_cobblestone", "cobblestone", 
                       "bricks", "metal_grid", "wood", "stepping_stones", "tiles", 
                       "fibre_reinforced_polymer_grate", "compacted", "fine_gravel", "gravel", 
                       "shells", "rock", "pebblestone", "ground", "dirt", "earth", "laterite", 
                       "grass", "mud", "sand", "woodchips", "snow", "ice", "salt"}:
            return "unpaved"
        if surface in {"paved", "asphalt", "chipseal", "concrete", "paving_stones", "metal"}:
            return "paved"

        # Pass-through to LUT features.csv
        return None
        
    def parse(self) -> Tuple[List[MapFeature], List[MapFeature], List[MapFeature]]:
        self._pass1_cache_nodes()
        self._pass2_build_features()
        return self.roads, self.landuse, self.pois

    def _pass1_cache_nodes(self) -> None:
        """Cache node geometry (micro-optimized for the Python VM's C backend)."""
        print("[>] Pass 1: Caching nodes...")
        context = ET.iterparse(self.osm_file, events=('start', 'end'))
        context = iter(context)
        
        try: _, root = next(context)
        except StopIteration: return

        # Local binding to eliminate LOAD_ATTR
        nodes_cache = self.nodes
        root_clear = root.clear
        TARGET_TAGS = {'node', 'way', 'relation'}
        
        count = 0
        for event, elem in context:
            if event == 'end':
                if elem.tag == 'node':
                    attr = elem.attrib
                    nodes_cache[int(attr['id'])] = (float(attr['lon']), float(attr['lat']))
                    count += 1
                    
                    # Fast modulo check via bitwise AND
                    if not (count & 0x1FFFF):
                        sys.stdout.write(f"\r    Nodes cached: {count:,}")
                        sys.stdout.flush()
                
                if elem.tag in TARGET_TAGS:
                    elem.clear()
                    root_clear()
                
        print(f"\r    Nodes loaded: {len(nodes_cache):,}       ")

    def _pass2_build_features(self) -> None:
        """Assemble primitives by topology."""
        print("[>] Pass 2: Normalizing geometry, multipolygons and POIs...")
        context = iter(ET.iterparse(self.osm_file, events=('start', 'end')))
        
        try: _, root = next(context)
        except StopIteration: return
        
        root_clear = root.clear
        stdout_write = sys.stdout.write
        stdout_flush = sys.stdout.flush
        
        # O(1) dispatch table (jump table)
        processors = {
            'way': self._process_way,
            'relation': self._process_relation,
            'node': self._process_node
        }
        
        count = 0
        for event, elem in context:
            if event == 'end':
                processor = processors.get(elem.tag)
                if processor:
                    processor(elem)
                    count += 1
                    elem.clear()
                    root_clear()
                    
                    if not (count & 0x3FFF):
                        stdout_write(f"\r    Elements processed: {count:,}")
                        stdout_flush()
            
        print(f"\r    Assembled: {len(self.roads)} roads, {len(self.landuse)} polygons, {len(self.pois)} points (POI).      ")

    def _extract_tags(self, elem: ET.Element) -> Dict[str, str]:
        return {child.attrib['k']: child.attrib['v'] for child in elem.findall('tag') 
                if 'k' in child.attrib and 'v' in child.attrib}

    @staticmethod
    def sanitize_osm_name(name: str) -> str:
        """Normalize a string: trim spaces, move descriptors, truncate."""
        if not name: return ""
            
        name = name.strip()
        name_lower = name.lower()
        
        for word in _STOP_WORDS:
            prefix = word + " "
            if name_lower.startswith(prefix):
                core_name = name[len(prefix):].strip()
                if core_name:
                    core_name = core_name[0].upper() + core_name[1:]
                    name = f"{core_name} {word.lower()}"
                break 
                
        # Hardware escaping of spaces into '_'
        name = name.replace(" ", "_")
        
        if len(name) > 22:
            name = name[:22].strip('_') + ".."
            
        return name.encode('utf-8', 'ignore').decode('utf-8')   

    def _process_node(self, elem: ET.Element) -> None:
        tags = self._extract_tags(elem)
        if not tags: return

        is_restricted = tags.get('access') in self.RESTRICTED_ACCESS_VALUES
        is_barrier = 'barrier' in tags

        if is_restricted and not is_barrier: return
         
        fclass = code = None
  
        if is_restricted and is_barrier:
            fclass = tags.get('barrier', 'barrier')
            code = 7209
            LookupTables.POI_SHAPES[fclass] = "barrier"
        else:
            for k, v in tags.items():
                if (k, v) in LookupTables.TAG_ROUTING['pois']:
                    fclass = LookupTables.TAG_ROUTING['pois'][(k, v)]
                    break
            
            if not fclass:
                for val in tags.values():
                    if val in LookupTables.POI_CODES:
                        fclass = val
                        break
            
            if fclass:
                if fclass in LookupTables.DISABLED_POIS: return
                code = LookupTables.POI_CODES.get(fclass)
                
        if code is None: return
            
        raw_name = tags.get('short_name:en') or tags.get('int_name') or tags.get('name:en') or tags.get('short_name') or tags.get('name') or ""
        name = OSMParser.sanitize_osm_name(raw_name.strip())
        if not name and fclass: name = str(fclass)
        
        osm_id = elem.attrib['id']
        node_coord = self.nodes.get(int(osm_id))
        if not node_coord: return

        feature = MapFeature(osm_id=osm_id, fclass=fclass, code=code, name=name, points=[node_coord])
        feature.calculate_bbox()
        self.pois.append(feature)

    def _process_way(self, elem: ET.Element) -> None:
        tags = self._extract_tags(elem)
        
        if tags.get('access') in self.RESTRICTED_ACCESS_VALUES:
            if not any(k in tags for k in ('landuse', 'leisure', 'natural')): return
                
        points = [self.nodes[int(nd.attrib['ref'])] for nd in elem.findall('nd') 
                  if 'ref' in nd.attrib and int(nd.attrib['ref']) in self.nodes]
        
        if not points: return
        self.ways_cache[int(elem.attrib['id'])] = points
        
        raw_name = tags.get('short_name:en') or tags.get('int_name') or tags.get('name:en') or tags.get('short_name') or tags.get('name') or ""
        name = OSMParser.sanitize_osm_name(raw_name.strip())
        osm_id = elem.attrib['id']

        target_layer = fclass = None
        
        # Tag routing
        for k, v in tags.items():
            if (k, v) in LookupTables.TAG_ROUTING['roads']:
                fclass, target_layer = LookupTables.TAG_ROUTING['roads'][(k, v)], 'roads'
                break
            elif (k, v) in LookupTables.TAG_ROUTING['landuse']:
                fclass, target_layer = LookupTables.TAG_ROUTING['landuse'][(k, v)], 'landuse'
                break
            elif (k, v) in LookupTables.TAG_ROUTING['water']:
                fclass, target_layer = LookupTables.TAG_ROUTING['water'][(k, v)], 'landuse'
                break

        # Fallback heuristic
        if not fclass:
            if 'highway' in tags: fclass, target_layer = tags['highway'], 'roads'
            elif 'landuse' in tags: fclass, target_layer = tags['landuse'], 'landuse'
            elif 'natural' in tags: fclass, target_layer = tags['natural'], 'landuse'
            elif 'leisure' in tags: fclass, target_layer = tags['leisure'], 'landuse'

        # Dispatch
        if target_layer == 'roads' and len(points) >= 2:
            if fclass == 'track' and 'tracktype' in tags: fclass += f'_{tags["tracktype"]}'
            if fclass in LookupTables.DISABLED_ROADS: return
                
            # 1. Базовое присвоение кода из LUT (features.csv)
            code = LookupTables.HIGHWAY_CODES.get(fclass, HWConfig.DEFAULT_HIGHWAY_CODE)
            
            # =================================================================
            # 2. HARDWARE OVERRIDE (Dynamic Tag Interception)
            # Перехватываем физическое покрытие и переопределяем цвет линии,
            # игнорируя базовое значение из таблицы маршрутизации.
            # =================================================================
            surface_state = self._analyze_road_surface(tags)
            if surface_state == "unpaved":
                # Любая дорога без твердого покрытия (даже primary) становится серой
                code = 5142 
            
            elif surface_state == "paved":
                # Маска исключений: блокируем апгрейд до желтого цвета для 
                # пешеходной, велосипедной и служебной инфраструктуры.
                # Для этих fclass сохранится оригинальный 'code', извлеченный из features.csv.
                non_vehicle_classes = {
                    'footway', 'path', 'steps', 'pedestrian', 
                    'cycleway', 'bridleway', 'corridor', 'elevator', 'escalator'
                }
                
                # Если это не пешеходная зона, переводим в желтый цвет
                if fclass not in non_vehicle_classes:
                    code = 5113 
            # =================================================================                
            # Если surface_state == None, сохраняется оригинальный code из LUT
            # =================================================================

            # 3. Упаковка финального кода в узел данных
            feature = MapFeature(osm_id=osm_id, fclass=fclass, code=code, name=name, points=points)
            feature.calculate_bbox()
            self.roads.append(feature)
            
        elif target_layer == 'landuse' and len(points) >= 4:
            if fclass in LookupTables.DISABLED_LANDUSE: return
                
            if points[0] == points[-1]: 
                if not self._is_clockwise(points): points.reverse()
                code = LookupTables.POLYGON_CODES.get(fclass, HWConfig.DEFAULT_POLYGON_CODE)
                feature = MapFeature(osm_id=osm_id, fclass=fclass, code=code, name=name, points=points)
                feature.calculate_bbox()
                self.landuse.append(feature)

    def _process_relation(self, elem: ET.Element) -> None:
        tags = self._extract_tags(elem)
        if tags.get('type') != 'multipolygon': return
            
        fclass = None
        for k, v in tags.items():
            if (k, v) in LookupTables.TAG_ROUTING['landuse']: fclass = LookupTables.TAG_ROUTING['landuse'][(k, v)]; break
            elif (k, v) in LookupTables.TAG_ROUTING['water']: fclass = LookupTables.TAG_ROUTING['water'][(k, v)]; break

        if not fclass:
            fclass = tags.get('landuse') or tags.get('leisure') or tags.get('natural')
        
        if not fclass or fclass in LookupTables.DISABLED_LANDUSE: return
            
        raw_name = tags.get('short_name:en') or tags.get('int_name') or tags.get('name:en') or tags.get('short_name') or tags.get('name') or ""
        name = OSMParser.sanitize_osm_name(raw_name.strip())
        
        combined_points, parts = [], []
        current_index = 0
        
        members = elem.findall('member')
        sorted_members = [m for m in members if m.attrib.get('role', 'outer') == 'outer'] + \
                         [m for m in members if m.attrib.get('role', 'outer') == 'inner']
        
        for member in sorted_members:
            if member.attrib.get('type') == 'way' and 'ref' in member.attrib:
                ref = int(member.attrib['ref'])
                role = member.attrib.get('role', 'outer')
                
                if ref in self.ways_cache:
                    ring_points = list(self.ways_cache[ref])                    
                    
                    if len(ring_points) >= 4 and ring_points[0] == ring_points[-1]:
                        is_cw = self._is_clockwise(ring_points)
                        if role == 'outer' and not is_cw: ring_points.reverse()
                        elif role == 'inner' and is_cw: ring_points.reverse()
                            
                        parts.append(current_index)
                        combined_points.extend(ring_points)
                        current_index += len(ring_points)
        
        if combined_points and parts:
            code = LookupTables.POLYGON_CODES.get(fclass, HWConfig.DEFAULT_POLYGON_CODE)
            feature = MapFeature(osm_id=elem.attrib['id'], fclass=fclass, code=code, name=name, points=combined_points, parts=parts)
            feature.calculate_bbox()
            self.landuse.append(feature)

# ==============================================================================
# 5. BINARY COMPILERS
# ==============================================================================

class MapCompiler:
    """Generator of hardware binary structures (YZL/SQT/DBF)."""

    @staticmethod
    def _write_yzl_container(filepath: str, payload: bytes, is_idx: bool, lod2_size: int = 0) -> None:
        """Encapsulate data in the system YZL container."""
        payload_size = len(payload)
        md5_hash = hashlib.md5(payload).digest()
        
        if is_idx:
            header = b'YZL\x08' + struct.pack("<I", payload_size) + b'\x02\x00\x00\x04' + struct.pack(">I", lod2_size) + md5_hash
        else:
            header = b'YZL\x00' + struct.pack("<I", payload_size) + b'\x00\x00\x00\x04\x00\x00\x00\x00' + md5_hash
            
        with open(filepath, 'wb') as f:
            f.write(header)
            f.write(payload)

    @staticmethod
    def pack_nav_node(v3_jump: int, bbox: Tuple[float, float, float, float], v1: int, v2_count: int) -> bytes:
        return struct.pack("<IffffII", v3_jump, bbox[0], bbox[1], bbox[2], bbox[3], v1, v2_count)

    @staticmethod
    def _pad(text: Any, length: int) -> bytes:
        return str(text).encode('utf-8')[:length].ljust(length, b'\x00')
            
    @staticmethod
    def _desc(name: str, length: int) -> bytes:
        return name.encode('ascii').ljust(11, b'\x00') + b'C' + b'\x00'*4 + bytes([length]) + b'\x00'*15

    @classmethod
    def compile_mlp(cls, features: List[MapFeature], filepath: str) -> None:
        print(f"[>] Compiling geometry: {filepath}...")
        bin_records = bytearray()
        record_number = 1

        for feature in features:
            minx_i, miny_i, maxx_i, maxy_i = (int(c * 1e6) for c in feature.bbox)
            
            body = bytearray(struct.pack("<iiii", minx_i, miny_i, maxx_i, maxy_i))
            body += struct.pack("<II", len(feature.parts), len(feature.points))
            
            for part_idx in feature.parts: body += struct.pack("<I", part_idx)
            for p in feature.points: body += struct.pack("<ii", int(p[0] * 1e6), int(p[1] * 1e6))
                
            header = struct.pack(">I", record_number) + struct.pack("<I", len(body))
            record_bin = header + body

            feature.v1 = len(bin_records) + 8
            feature.v2 = 1 
            feature.mlp_size = len(record_bin)
            
            bin_records += record_bin
            record_number += 1

        cls._write_yzl_container(filepath, bin_records, is_idx=False)

    @classmethod
    def compile_db(cls, features: List[MapFeature], filepath: str, is_poi: bool = False) -> None:
        if not is_poi and not any(f.name for f in features):
            print(f"[~] Layer {filepath} contains no named objects. .db file creation skipped.")
            for f in features: f.v2 = 0
            return
        if is_poi and not features: return
    
        print(f"[>] Compiling attributes: {filepath}...")
        
        bin_records = bytearray() if is_poi else bytearray(b'\x00' * HWConfig.DBF_RECORD_LEN) 
        db_counter = 1 if is_poi else 2 
        total_records = 0 if is_poi else 1

        for feature in features:
            if is_poi or feature.name:
                feature.v2 = db_counter
                db_counter += 1
                total_records += 1
                
                r_bytes = bytearray(b'\x20')
                r_bytes += cls._pad(feature.osm_id, 12) + cls._pad(feature.code, 4) + cls._pad(feature.fclass, 28) + cls._pad(feature.name, 100)
                bin_records += r_bytes

        dbf_header = (
            bytearray(b'\x03\x00\x00\x00') + 
            struct.pack('<I', total_records) +
            struct.pack('<H', HWConfig.DBF_HEADER_LEN) + 
            struct.pack('<H', HWConfig.DBF_RECORD_LEN) + 
            b'\x00' * 20 +
            cls._desc("osm_id", 12) + cls._desc("code", 4) + cls._desc("fclass", 28) + cls._desc("name", 100) + 
            b'\x0D'
        )
        cls._write_yzl_container(filepath, dbf_header + bin_records, is_idx=False)

    @classmethod
    def _pack_clusters(cls, records: List[MapFeature], idx_buffer: bytearray) -> None:
        """Inject SQT clusters into the index buffer."""
        clusters = [records[i:i + HWConfig.CHUNK_SIZE] for i in range(0, len(records), HWConfig.CHUNK_SIZE)]
        
        if len(clusters) > 1:
            idx_buffer.extend(struct.pack("<II", 1, len(clusters)))
            for cluster in clusters:
                if not cluster: continue
                c_minx = min(f.bbox[0] for f in cluster)
                c_miny = min(f.bbox[1] for f in cluster)
                c_maxx = max(f.bbox[2] for f in cluster)
                c_maxy = max(f.bbox[3] for f in cluster)
                
                v3_jump = (len(cluster) * HWConfig.NODE_SIZE) + 8 
                idx_buffer.extend(cls.pack_nav_node(v3_jump, (c_minx, c_miny, c_maxx, c_maxy), 0, len(cluster)))                    
                
                for f in cluster: idx_buffer.extend(f.pack_data_node())
        else:
            count = len(clusters[0]) if clusters else 0
            idx_buffer.extend(struct.pack("<II", 0, count))
            for f in clusters[0] if clusters else []: idx_buffer.extend(f.pack_data_node())

    @classmethod
    def compile_idx(cls, features: List[MapFeature], filepath: str, is_poi: bool = False) -> None:
        print(f"[>] Compiling SQT index: {filepath}...")
        idx_buffer = bytearray()
        
        if is_poi:
            # POI layer without .mlp (coordinates stored in BBox). 1 LOD level.
            idx_buffer.extend(b'SQT\x01\x01\x00\x00\x00') 
            if not features:
                idx_buffer.extend(struct.pack("<II", 0, 0))
            else:
                for f in features: f.v1 = 0
                cls._pack_clusters(features, idx_buffer)
                
            cls._write_yzl_container(filepath, idx_buffer, is_idx=False)

        else:
            # Standard multi-level geometry (LOD 0, 1, 2)
            lod_filters = [
                lambda c: True,
                lambda c: LookupTables.DISPLAY_SCALES.get(c, 20) >= 500, 
                lambda c: LookupTables.DISPLAY_SCALES.get(c, 20) >= 1000
            ]
            
            lod2_size = 0
            for lod_index, condition in enumerate(lod_filters):
                start_len = len(idx_buffer)
                lod_records = [f for f in features if condition(f.code)]
                
                idx_buffer.extend(b'SQT\x01\x00\x00\x00\x00') 
                
                if not lod_records:
                    idx_buffer.extend(struct.pack("<II", 0, 0))
                else:
                    cls._pack_clusters(lod_records, idx_buffer)
       
                if lod_index == 2: lod2_size = len(idx_buffer) - start_len
            
            cls._write_yzl_container(filepath, idx_buffer, is_idx=True, lod2_size=lod2_size)

    @staticmethod
    def create_empty_layer(layer_prefix: str) -> None:
        print(f"[>] Creating system Hex dummy: {layer_prefix}...")
        mlp_hex = "595A4C00000000000000000400000000D41D8CD98F00B204E9800998ECF8427E"
        idx_hex = "595A4C10300000000000000400000010E5F9D2228804251B5F9E3EAB298C30E5535154010100000000000000000000005351540101000000000000000000000053515401010000000000000000000000"
        with open(f"{layer_prefix}.mlp", "wb") as f: f.write(bytearray.fromhex(mlp_hex))
        with open(f"{layer_prefix}.idx", "wb") as f: f.write(bytearray.fromhex(idx_hex))
 
    @staticmethod
    def create_map_name(name: str, meta_records: List[MapFeature], out_file: str = "map.name") -> None:
        if not meta_records: return
        center_lat = (min(r.bbox[1] for r in meta_records) + max(r.bbox[3] for r in meta_records)) / 2.0
        center_lon = (min(r.bbox[0] for r in meta_records) + max(r.bbox[2] for r in meta_records)) / 2.0
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump({"centerLat": center_lat, "centerLon": center_lon, "mapName": name}, f, separators=(',', ':'))

# ==============================================================================
# 6. ENTRY POINT
# ==============================================================================

def main():
    cli_parser = argparse.ArgumentParser(description="DT G1 Map Compiler (Platform ATS3085S)")
    cli_parser.add_argument(
        "-p", "--poi-mode", choices=["native", "landuse", "none"], default="none",
        help="POI mode: 'native' (pois.idx/db), 'landuse' (polygon baking), 'none' (ignore)"
    )
    args = cli_parser.parse_args()

    if not os.path.exists("map.osm"):
        print("[-] Error: map.osm file not found. Terminating.")
        return
        
    print("=========================================")
    print("DT G1 MAP COMPILER")
    print(f"POI layer mode: {args.poi_mode.upper()}")
    print("=========================================")
    
    LookupTables.load_from_csv("features.csv")

    parser = OSMParser("map.osm")
    roads_data, landuse_data, pois_data = parser.parse()

    # --- GPX INJECTION BLOCK ---
    gpx_file = "route.gpx"
    if os.path.exists(gpx_file):
        print(f"[>] Route file {gpx_file} detected. Performing injection...")
        track_name, track_points = GPXParser.parse_track(gpx_file)
        
        if track_points and len(track_points) >= 2:
            gpx_feature = MapFeature(osm_id="user_track_001", fclass="gpx_track", code=5111, name=track_name, points=track_points)
            gpx_feature.calculate_bbox()
            roads_data.append(gpx_feature)
            print(f"    Track '{track_name}' successfully integrated.")
    
    meta_all: List[MapFeature] = []

    # 1. Roads layer
    if roads_data:
        MapCompiler.compile_mlp(roads_data, "roads.mlp")
        MapCompiler.compile_db(roads_data, "roads.db")
        MapCompiler.compile_idx(roads_data, "roads.idx")
        meta_all.extend(roads_data)

    # --- POI BAKING BLOCK ---
    if args.poi_mode == "landuse" and pois_data:
        print("[>] Baking POI objects into landuse layer using dynamic shape factory...")
        for poi in pois_data:
            if not poi.points: continue
            shape_type = LookupTables.POI_SHAPES.get(poi.fclass, "rhombus").lower()
            poi.points = POIGeometryFactory.generate_polygon(shape_type, poi.points[0][0], poi.points[0][1])
            poi.calculate_bbox()
            landuse_data.append(poi)
 
        print(f"    Successfully baked {len(pois_data)} POIs.")
        pois_data.clear() 

    # 2. Landuse and Water layers
    landuse_only = [f for f in landuse_data if f.code != HWConfig.WATER_CODE]
    water_only = [f for f in landuse_data if f.code == HWConfig.WATER_CODE]

    if landuse_only:
        MapCompiler.compile_mlp(landuse_only, "landuse.mlp")
        MapCompiler.compile_db(landuse_only, "landuse.db")
        MapCompiler.compile_idx(landuse_only, "landuse.idx")
        meta_all.extend(landuse_only)
    else:
        MapCompiler.create_empty_layer("landuse")

    if water_only:
        MapCompiler.compile_mlp(water_only, "water.mlp")
        MapCompiler.compile_db(water_only, "water.db")
        MapCompiler.compile_idx(water_only, "water.idx")
        meta_all.extend(water_only)

    # 3. Native POI layer logic
    if args.poi_mode == "none": print("[>] POI layer skipped ('none' mode selected).")
    elif args.poi_mode == "native":
        if pois_data:
            MapCompiler.compile_db(pois_data, "pois.db", is_poi=True)
            MapCompiler.compile_idx(pois_data, "pois.idx", is_poi=True)
            meta_all.extend(pois_data)
        else: print("[~] Point objects (POI) are missing in the source data.")
    elif args.poi_mode == "landuse": print("[>] POI mode 'landuse' successfully handled.")

    # 4. Global camera centering
    if meta_all: MapCompiler.create_map_name("DTG1_Map", meta_all, "map.name")
    
    print("\n[SUCCESS] Map package compiled successfully!")

if __name__ == "__main__":
    main()