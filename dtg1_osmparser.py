#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import xml.etree.ElementTree as ET
from typing import List, Tuple, Dict, Optional

from dtg1_models import MapFeature, HWConfig, safe_encode
from dtg1_geometry import is_clockwise
from dtg1_lookup import LookupTables

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

_sorted_stop_words = sorted(_STOP_WORDS, key=len, reverse=True)
_STOP_WORDS_RX = re.compile(
    r"^(" + "|".join(re.escape(w) for w in _sorted_stop_words) + r")\s*(.*)$",
    re.IGNORECASE
)


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
        """Delegate to shared CW check (negative oriented area => CW)."""
        return is_clockwise(points)
        
    def _analyze_road_surface(self, tags: Dict[str, str]) -> Optional[str]:
        """
        Analyzes road quality and surface tags.
        Priority is given to the smoothness tag.
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
                    
                    if not (count & 0x1FFFF):
                        print(f"\r    Nodes cached: {count:,}", end="", flush=True)
                
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
                        print(f"\r    Elements processed: {count:,}", end="", flush=True)
            
        print(f"\r    Assembled: {len(self.roads)} roads, {len(self.landuse)} polygons, {len(self.pois)} points (POI).      ")

    def _extract_tags(self, elem: ET.Element) -> Dict[str, str]:
        return {child.attrib['k']: child.attrib['v'] for child in elem.findall('tag') 
                if 'k' in child.attrib and 'v' in child.attrib}

    @staticmethod
    def sanitize_osm_name(name: str) -> str:
        """Normalize a string: trim spaces, move descriptors, truncate with safe UTF-8 encoding."""
        if not name: return ""
            
        name = name.strip()
        
        m = _STOP_WORDS_RX.match(name)
        if m:
            word, core_name = m.groups()
            core_name = core_name.strip()
            if core_name:
                core_name = core_name[0].upper() + core_name[1:]
                name = f"{core_name} {word.lower()}"
                
        name = name.replace(" ", "_")
        
        # 1. Limit for UI (characters).
        # The watch's graphics engine correctly fits ~22 characters on the screen.
        if len(name) > 22:
            name = name[:20].rstrip('_') + ".."
            
        # 2. Limit for DB .db (bytes).
        # The dBase III specification strictly allocates 100 bytes for the name field.
        encoded_name = name.encode('utf-8')
        if len(encoded_name) > 100:
            name_bytes = safe_encode(name, 100)
            name = name_bytes.decode('utf-8', 'ignore').rstrip('_')
            
        return name
        
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

        # === PROTECTION: Runtime extraction of POIs from closed polygons ===
        is_closed = len(points) >= 4 and points[0] == points[-1]
        
        if is_closed and any(k in tags for k in ('building', 'amenity', 'shop', 'leisure', 'tourism', 'historic')):
            poi_fclass = None
            
            # 1. Search by explicit key-value pairs in LUT
            for k, v in tags.items():
                if (k, v) in LookupTables.TAG_ROUTING.get('pois', {}):
                    poi_fclass = LookupTables.TAG_ROUTING['pois'][(k, v)]
                    break
            
            # 2. Fallback search by single values
            if not poi_fclass:
                for val in tags.values():
                    if val in LookupTables.POI_CODES:
                        poi_fclass = val
                        break
            
            # 3. Centroid injection
            if poi_fclass and poi_fclass not in LookupTables.DISABLED_POIS:
                poi_code = LookupTables.POI_CODES.get(poi_fclass)
                if poi_code:
                    # Calculate mathematical centroid (discarding duplicate closing vertex)
                    unique_points = points[:-1]
                    avg_lon = sum(p[0] for p in unique_points) / len(unique_points)
                    avg_lat = sum(p[1] for p in unique_points) / len(unique_points)
                    
                    poi_name = name if name else str(poi_fclass)
                    poi_feature = MapFeature(
                        osm_id=f"v{osm_id}", 
                        fclass=poi_fclass, 
                        code=poi_code, 
                        name=poi_name, 
                        points=[(avg_lon, avg_lat)]
                    )
                    poi_feature.calculate_bbox()
                    self.pois.append(poi_feature)
        # =============================================================

        target_layer = fclass = None
        
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

        if not fclass:
            if 'highway' in tags: fclass, target_layer = tags['highway'], 'roads'
            elif 'landuse' in tags: fclass, target_layer = tags['landuse'], 'landuse'
            elif 'natural' in tags: fclass, target_layer = tags['natural'], 'landuse'
            elif 'leisure' in tags: fclass, target_layer = tags['leisure'], 'landuse'

        if target_layer == 'roads' and len(points) >= 2:
            if fclass == 'track' and 'tracktype' in tags: fclass += f'_{tags["tracktype"]}'
            if fclass in LookupTables.DISABLED_ROADS: return
                
            code = LookupTables.HIGHWAY_CODES.get(fclass, HWConfig.DEFAULT_HIGHWAY_CODE)
            surface_state = self._analyze_road_surface(tags)
            
            if surface_state == "unpaved":
                code = 5142 
            elif surface_state == "paved":
                non_vehicle_classes = {
                    'footway', 'path', 'steps', 'pedestrian', 
                    'cycleway', 'bridleway', 'corridor', 'elevator', 'escalator'
                }
                if fclass not in non_vehicle_classes:
                    code = 5113 

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