#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import gc
import re
import array
from lxml import etree as ET  # Crucial for memory management on large XML files
from functools import lru_cache
from typing import List, Tuple, Dict, Optional

from dtg1_models import MapFeature, HWConfig
from dtg1_geometry import is_clockwise, reverse_array_inplace
from dtg1_lookup import LookupTables

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

_STOP_WORDS_PATTERN = re.compile(
    r"^(" + "|".join(re.escape(w) for w in sorted(_STOP_WORDS, key=len, reverse=True)) + r")\s+(.*)", 
    re.IGNORECASE
)

@lru_cache(maxsize=32768)
def sanitize_osm_name(name: str) -> str:
    if not name: return ""
    match = _STOP_WORDS_PATTERN.match(name)
    if match:
        core = match.group(2).strip()
        if core: 
            name = f"{core[0].upper()}{core[1:]} {match.group(1).lower()}"
    name = name.replace(" ", "_")
    if len(name) > 22: name = name[:20].strip('_') + ".."
    return name.encode('utf-8', 'ignore').decode('utf-8')

class GPXParser:
    @staticmethod
    def parse_track(filepath: str) -> Tuple[str, array.array]:
        if not os.path.exists(filepath): 
            return "Route", array.array('i')
            
        tree = ET.parse(filepath)
        root = tree.getroot()
        ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
        track_name = os.path.basename(filepath)
        
        meta_name = root.find('.//gpx:metadata/gpx:name', namespaces=ns)
        if meta_name is not None and meta_name.text: track_name = meta_name.text.strip()
        else:
            trk_name = root.find('.//gpx:trk/gpx:name', namespaces=ns)
            if trk_name is not None and trk_name.text: track_name = trk_name.text.strip()
            
        arr = array.array('i')
        for pt in root.findall('.//gpx:trkpt', namespaces=ns):
            lon, lat = pt.get('lon'), pt.get('lat')
            if lon and lat: arr.extend((int(float(lon) * 1e6), int(float(lat) * 1e6)))
            
        return track_name, arr

class OSMParser:
    RESTRICTED_ACCESS_VALUES = frozenset({'private', 'permit', 'no'})
    
    def __init__(self, osm_file: str):
        self.osm_file = osm_file
        self.nodes: Dict[int, Tuple[int, int]] = {}
        self.ways_cache: Dict[int, array.array] = {}
        
        self.roads: List[MapFeature] = []
        self.landuse: List[MapFeature] = []
        self.pois: List[MapFeature] = []

    def _analyze_road_surface(self, tags: Dict[str, str]) -> Optional[str]:
        smoothness = tags.get("smoothness")
        if smoothness in {"bad", "very_bad", "horrible", "very_horrible", "impassable"}: return "unpaved"
        if smoothness in {"excellent", "good", "intermediate"}: return "paved"
            
        surface = tags.get("surface")
        if surface in {"unpaved", "grass_paver", "sett", "unhewn_cobblestone", "cobblestone", 
                       "bricks", "metal_grid", "wood", "stepping_stones", "tiles", 
                       "fibre_reinforced_polymer_grate", "compacted", "fine_gravel", "gravel", 
                       "shells", "rock", "pebblestone", "ground", "dirt", "earth", "laterite", 
                       "grass", "mud", "sand", "woodchips", "snow", "ice", "salt"}: return "unpaved"
        if surface in {"paved", "asphalt", "chipseal", "concrete", "paving_stones", "metal"}: return "paved"
        return None

    def parse(self) -> Tuple[List[MapFeature], List[MapFeature], List[MapFeature]]:
        self._pass1_cache_nodes()
        self._pass2_build_features()
        return self.roads, self.landuse, self.pois

    def _pass1_cache_nodes(self) -> None:
        print("[>] Pass 1: Node Caching (Protected Memory Wipe)...")
        gc.disable()
        count = 0
        
        for event, elem in ET.iterparse(self.osm_file, events=('end',), tag='node'):
            lon, lat = elem.get('lon'), elem.get('lat')
            if lon and lat:
                self.nodes[int(elem.get('id'))] = (int(float(lon) * 1e6), int(float(lat) * 1e6))
                count += 1
                if not (count & 0x1FFFF):
                    sys.stdout.write(f"\r    Nodes cached: {count:,}")
                    sys.stdout.flush()
            
            # lxml specific C-level memory deallocation
            elem.clear()
            parent = elem.getparent()
            if parent is not None:
                while elem.getprevious() is not None: del parent[0]
                
        print(f"\r    Nodes loaded: {len(self.nodes):,}       ")
        gc.enable(); gc.collect()

    def _pass2_build_features(self) -> None:
        print("[>] Pass 2: Assembling Geometry...")
        gc.disable()
        count = 0
        
        for event, elem in ET.iterparse(self.osm_file, events=('end',), tag=('node', 'way', 'relation')):
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
        gc.enable(); gc.collect()

    def _extract_tags(self, elem) -> Dict[str, str]:
        # Fast lxml iterfind
        return {child.get('k'): child.get('v') for child in elem.iterfind('tag') if child.get('k') and child.get('v')}

    def _process_node(self, elem) -> None:
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
                if (k, v) in LookupTables.TAG_ROUTING.get('pois', {}):
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
        name = sanitize_osm_name(raw_name.strip())
        if not name and fclass: name = str(fclass)
        
        osm_id = elem.get('id')
        node_coord = self.nodes.get(int(osm_id))
        if not node_coord: return

        arr = array.array('i', node_coord)
        feature = MapFeature(osm_id=osm_id, fclass=fclass, code=code, name=name, points=arr)
        feature.calculate_bbox()
        self.pois.append(feature)

    def _process_way(self, elem) -> None:
        tags = self._extract_tags(elem)
        
        if tags.get('access') in self.RESTRICTED_ACCESS_VALUES:
            if not any(k in tags for k in ('landuse', 'leisure', 'natural')): return
                
        pts_gen = (coord for nd in elem.iterfind('nd') for coord in self.nodes.get(int(nd.get('ref', 0)), ()))
        arr = array.array('i', pts_gen)
        
        if not arr: return
        osm_id = elem.get('id')
        self.ways_cache[int(osm_id)] = arr
        
        raw_name = tags.get('short_name:en') or tags.get('int_name') or tags.get('name:en') or tags.get('short_name') or tags.get('name') or ""
        name = sanitize_osm_name(raw_name.strip())

        points_count = len(arr) // 2
        is_closed = points_count >= 4 and arr[0] == arr[-2] and arr[1] == arr[-1]
        
        if is_closed and any(k in tags for k in ('building', 'amenity', 'shop', 'leisure', 'tourism', 'historic')):
            poi_fclass = None
            for k, v in tags.items():
                if (k, v) in LookupTables.TAG_ROUTING.get('pois', {}):
                    poi_fclass = LookupTables.TAG_ROUTING['pois'][(k, v)]
                    break
            if not poi_fclass:
                for val in tags.values():
                    if val in LookupTables.POI_CODES:
                        poi_fclass = val
                        break
                        
            if poi_fclass and poi_fclass not in LookupTables.DISABLED_POIS:
                poi_code = LookupTables.POI_CODES.get(poi_fclass)
                if poi_code:
                    avg_lon = sum(arr[i] for i in range(0, len(arr)-2, 2)) // (points_count - 1)
                    avg_lat = sum(arr[i+1] for i in range(0, len(arr)-2, 2)) // (points_count - 1)
                    
                    poi_name = name if name else str(poi_fclass)
                    poi_arr = array.array('i', (avg_lon, avg_lat))
                    poi_feature = MapFeature(
                        osm_id=f"v{osm_id}", fclass=poi_fclass, code=poi_code, 
                        name=poi_name, points=poi_arr
                    )
                    poi_feature.calculate_bbox()
                    self.pois.append(poi_feature)

        target_layer = fclass = None
        for k, v in tags.items():
            if (k, v) in LookupTables.TAG_ROUTING.get('roads', {}):
                fclass, target_layer = LookupTables.TAG_ROUTING['roads'][(k, v)], 'roads'
                break
            elif (k, v) in LookupTables.TAG_ROUTING.get('landuse', {}):
                fclass, target_layer = LookupTables.TAG_ROUTING['landuse'][(k, v)], 'landuse'
                break
            elif (k, v) in LookupTables.TAG_ROUTING.get('water', {}):
                fclass, target_layer = LookupTables.TAG_ROUTING['water'][(k, v)], 'landuse'
                break

        if not fclass:
            if 'highway' in tags: fclass, target_layer = tags['highway'], 'roads'
            elif 'landuse' in tags: fclass, target_layer = tags['landuse'], 'landuse'
            elif 'natural' in tags: fclass, target_layer = tags['natural'], 'landuse'
            elif 'leisure' in tags: fclass, target_layer = tags['leisure'], 'landuse'

        if target_layer == 'roads' and points_count >= 2:
            if fclass == 'track' and 'tracktype' in tags: fclass += f'_{tags["tracktype"]}'
            if fclass in LookupTables.DISABLED_ROADS: return
                
            code = LookupTables.HIGHWAY_CODES.get(fclass, HWConfig.DEFAULT_HIGHWAY_CODE)
            surface_state = self._analyze_road_surface(tags)
            
            if surface_state == "unpaved":
                code = 5142 
            elif surface_state == "paved":
                non_vehicle_classes = {'footway', 'path', 'steps', 'pedestrian', 'cycleway', 'bridleway', 'corridor', 'elevator', 'escalator'}
                if fclass not in non_vehicle_classes: 
                    code = 5113 

            feature = MapFeature(osm_id=str(osm_id), fclass=fclass, code=code, name=name, points=arr)
            feature.calculate_bbox()
            self.roads.append(feature)
            
        elif target_layer == 'landuse' and points_count >= 4:
            if fclass in LookupTables.DISABLED_LANDUSE: return
            if arr[0] == arr[-2] and arr[1] == arr[-1]: 
                if not is_clockwise(arr): reverse_array_inplace(arr)
                code = LookupTables.POLYGON_CODES.get(fclass, HWConfig.DEFAULT_POLYGON_CODE)
                feature = MapFeature(osm_id=str(osm_id), fclass=fclass, code=code, name=name, points=arr)
                feature.calculate_bbox()
                self.landuse.append(feature)

    def _process_relation(self, elem) -> None:
        tags = self._extract_tags(elem)
        if tags.get('type') != 'multipolygon': return
            
        fclass = None
        for k, v in tags.items():
            if (k, v) in LookupTables.TAG_ROUTING.get('landuse', {}): 
                fclass = LookupTables.TAG_ROUTING['landuse'][(k, v)]
                break
            elif (k, v) in LookupTables.TAG_ROUTING.get('water', {}): 
                fclass = LookupTables.TAG_ROUTING['water'][(k, v)]
                break

        if not fclass:
            fclass = tags.get('landuse') or tags.get('leisure') or tags.get('natural')
        
        if not fclass or fclass in LookupTables.DISABLED_LANDUSE: return
            
        raw_name = tags.get('short_name:en') or tags.get('int_name') or tags.get('name:en') or tags.get('short_name') or tags.get('name') or ""
        name = sanitize_osm_name(raw_name.strip())
        
        combined_arr = array.array('i')
        parts = []
        current_index = 0
        
        outer_list = []
        inner_list = []
        
        for c in elem.iterfind('member'):
            if c.get('type') == 'way' and c.get('ref'):
                ref_int = int(c.get('ref'))
                if ref_int in self.ways_cache:
                    if c.get('role', 'outer') == 'outer': outer_list.append((ref_int, True))
                    else: inner_list.append((ref_int, False))
        
        for ref_int, is_outer in (outer_list + inner_list):
            ring_arr = array.array('i', self.ways_cache[ref_int]) 
            pts_count = len(ring_arr) // 2
            
            if pts_count >= 4 and ring_arr[0] == ring_arr[-2] and ring_arr[1] == ring_arr[-1]:
                is_cw = is_clockwise(ring_arr)
                if (is_outer and not is_cw) or (not is_outer and is_cw): reverse_array_inplace(ring_arr)
                    
                parts.append(current_index)
                combined_arr.extend(ring_arr)
                current_index += pts_count
        
        osm_id = elem.get('id')
        if len(combined_arr) > 0 and parts and osm_id:
            code = LookupTables.POLYGON_CODES.get(fclass, HWConfig.DEFAULT_POLYGON_CODE)
            feature = MapFeature(osm_id=str(osm_id), fclass=fclass, code=code, name=name, points=combined_arr, parts=parts)
            feature.calculate_bbox()
            self.landuse.append(feature)