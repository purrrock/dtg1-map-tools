#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import gc
import re
import array
import bisect
import itertools
from lxml import etree as ET
from functools import lru_cache
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


@lru_cache(maxsize=32768)
def _sanitize_name_cached(name: str) -> str:
    """Cached name sanitization for POI centroid extraction and UI rendering."""
    if not name:
        return ""

    name = name.strip()
    m = _STOP_WORDS_RX.match(name)
    if m:
        word, core_name = m.groups()
        core_name = core_name.strip()
        if core_name:
            core_name = core_name[0].upper() + core_name[1:]
            name = f"{core_name} {word.lower()}"

    name = name.replace(" ", "_")

    # Limit for UI (characters)
    if len(name) > 22:
        name = name[:20].rstrip('_') + ".."

    # Limit for DB .db (bytes)
    encoded_name = name.encode('utf-8')
    if len(encoded_name) > 100:
        name_bytes = safe_encode(name, 100)
        name = name_bytes.decode('utf-8', 'ignore').rstrip('_')

    return name


class GPXParser:
    """Extract track geometry for injection into the Roads layer."""

    @staticmethod
    def parse_track(filepath: str) -> Tuple[str, List[Tuple[int, int]]]:
        if not os.path.exists(filepath):
            return "Route", []

        tree = ET.parse(filepath)
        root = tree.getroot()
        ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}

        track_name = "Route"
        metadata_name = root.find('.//gpx:metadata/gpx:name', namespaces=ns)
        if metadata_name is not None and metadata_name.text:
            track_name = metadata_name.text.strip()
        else:
            trk_name = root.find('.//gpx:trk/gpx:name', namespaces=ns)
            if trk_name is not None and trk_name.text:
                track_name = trk_name.text.strip()

        points = []
        for trkpt in root.findall('.//gpx:trkpt', namespaces=ns):
            try:
                points.append((int(float(trkpt.get('lon')) * 1000000), int(float(trkpt.get('lat')) * 1000000)))
            except (ValueError, TypeError):
                continue

        return track_name, points


class OSMParser:
    """Two-pass streaming OSM parser with hybrid memory optimization (C-Arrays + lxml)."""
    RESTRICTED_ACCESS_VALUES = {'private', 'permit', 'no'}

    def __init__(self, osm_file: str):
        self.osm_file = osm_file

        # [MEMORY OPTIMIZATION 1]: Node Arrays
        self.node_ids = array.array('q')
        self.node_coords = array.array('i')

        # [MEMORY OPTIMIZATION 2]: Way Pool (Flattened coordinates to save 4-5GB of RAM)
        self.way_coords_pool = array.array('i')
        # ways_cache replaced with parallel arrays to prevent dict memory bloat
        self.way_cache_ids = array.array('q')
        self.way_cache_starts = array.array('q')
        self.way_cache_lengths = array.array('i')
        self._ways_sorted = True
        self._last_way_id = -1

        self._nodes_freed = False

        self.roads: List[MapFeature] = []
        self.landuse: List[MapFeature] = []
        self.pois: List[MapFeature] = []

    @staticmethod
    def _is_clockwise(points: List[Tuple[int, int]]) -> bool:
        return is_clockwise(points)

    def _analyze_road_surface(self, tags: Dict[str, str]) -> Optional[str]:
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

    def _get_node_coord(self, node_id: int) -> Optional[Tuple[int, int]]:
        # Protection in case relation/way calls for node after nodes are freed
        if self.node_ids is None:
            return None

        idx = bisect.bisect_left(self.node_ids, node_id)
        if idx < len(self.node_ids) and self.node_ids[idx] == node_id:
            coord_idx = idx * 2
            return (self.node_coords[coord_idx], self.node_coords[coord_idx + 1])
        return None

    def _pass1_cache_nodes(self) -> None:
        total_nodes_str = os.environ.get('TOTAL_NODES', None)
        total_nodes = int(total_nodes_str) if total_nodes_str and total_nodes_str.isdigit() else None

        if total_nodes:
            print(f"[>] Pass 1: Caching nodes... (0 / {total_nodes})")
        else:
            print("[>] Pass 1: Caching nodes...")

        gc.disable()
        context = ET.iterparse(self.osm_file, events=('end',))

        node_ids_append = self.node_ids.append
        node_coords_append = self.node_coords.append

        count = 0
        is_sorted = True
        last_id = -1

        TARGET_TAGS = {'node', 'way', 'relation'}

        for event, elem in context:
            if elem.tag not in TARGET_TAGS:
                continue

            if elem.tag == 'node':
                try:
                    nid = int(elem.get('id'))
                    node_ids_append(nid)
                    node_coords_append(int(float(elem.get('lon')) * 1000000))
                    node_coords_append(int(float(elem.get('lat')) * 1000000))

                    if is_sorted and nid < last_id:
                        is_sorted = False
                    last_id = nid

                except (TypeError, ValueError):
                    pass

                count += 1
                if not (count & 0xFFFFF):  # Триггер каждые 1,048,575 элементов
                    # sys.getsizeof дает точный размер непрерывных C-массивов
                    mem_mb = (sys.getsizeof(self.node_ids) + sys.getsizeof(self.node_coords)) / (1024 * 1024)
                    print(f"\r    Nodes cached: {count:,} | Arrays RAM: {mem_mb:.1f} MB", end="", flush=True)
            elif elem.tag == 'way':
                break

            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]

        if not is_sorted:
            print("\r    [!] Nodes are unsorted. Indexing arrays (may take some memory)...")
            indices = list(range(len(self.node_ids)))
            indices.sort(key=lambda i: self.node_ids[i])

            new_ids = array.array('q', [0]) * len(self.node_ids)
            new_coords = array.array('i', [0]) * len(self.node_coords)

            for new_i, old_i in enumerate(indices):
                new_ids[new_i] = self.node_ids[old_i]
                new_coords[new_i * 2] = self.node_coords[old_i * 2]
                new_coords[new_i * 2 + 1] = self.node_coords[old_i * 2 + 1]

            self.node_ids = new_ids
            self.node_coords = new_coords
            del indices

        gc.enable()
        gc.collect()
        print(f"\r    Nodes loaded into Arrays: {len(self.node_ids):,}        ")

    def _pass2_build_features(self) -> None:
        print("[>] Pass 2: Normalizing geometry, multipolygons and POIs...")

        context = ET.iterparse(self.osm_file, events=('end',))

        processors = {
            'way': self._process_way,
            'relation': self._process_relation,
            'node': self._process_node
        }

        TARGET_TAGS = {'node', 'way', 'relation'}
        count = 0

        for event, elem in context:
            if elem.tag not in TARGET_TAGS:
                continue

            # [MEMORY OPTIMIZATION 3]: EARLY NODE EJECTION
            # The exact moment we hit the first relation, we know nodes/ways are done.
            # We free up RAM immediately to give relations room to breathe.
            if elem.tag == 'relation' and not self._nodes_freed:
                arrays_mb = (sys.getsizeof(self.node_ids) + sys.getsizeof(self.node_coords)) / (1024 * 1024)
                print(f"\n    [!] Reaching Relations. Ejecting Node Cache to free ~{arrays_mb:.1f} MB...", flush=True)

                self.node_ids = None
                self.node_coords = None

                if not self._ways_sorted:
                    print("\r    [!] Ways are unsorted. Indexing arrays (may take some memory)...", flush=True)
                    indices = list(range(len(self.way_cache_ids)))
                    indices.sort(key=lambda i: self.way_cache_ids[i])

                    new_ids = array.array('q', [0]) * len(self.way_cache_ids)
                    new_starts = array.array('q', [0]) * len(self.way_cache_starts)
                    new_lengths = array.array('i', [0]) * len(self.way_cache_lengths)

                    for new_i, old_i in enumerate(indices):
                        new_ids[new_i] = self.way_cache_ids[old_i]
                        new_starts[new_i] = self.way_cache_starts[old_i]
                        new_lengths[new_i] = self.way_cache_lengths[old_i]

                    self.way_cache_ids = new_ids
                    self.way_cache_starts = new_starts
                    self.way_cache_lengths = new_lengths
                    del indices

                gc.collect()
                self._nodes_freed = True

            processor = processors.get(elem.tag)
            if processor:
                processor(elem)
                count += 1

                if not (count & 0x7FFFF):  # Триггер каждые 524,287 элементов
                    ways_pool_mb = sys.getsizeof(self.way_coords_pool) / (1024 * 1024)
                    features_count = len(self.roads) + len(self.landuse) + len(self.pois)

                    # Получение фактического использования RAM процессом (только для Linux/GitHub Actions)
                    rss_mb = 0.0
                    if sys.platform.startswith('linux'):
                        try:
                            with open(f'/proc/{os.getpid()}/status', 'r') as f:
                                for line in f:
                                    if line.startswith('VmRSS:'):
                                        rss_mb = int(line.split()[1]) / 1024
                                        break
                        except Exception:
                            pass

                    mem_str = f" | Sys VmRSS: {rss_mb:.1f} MB" if rss_mb else ""
                    print(f"\r    Processed: {count:,} | Features: {features_count:,} | WayPool: {ways_pool_mb:.1f} MB{mem_str}", end="", flush=True)

            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]

        print(f"\r    Assembled: {len(self.roads)} roads, {len(self.landuse)} polygons, {len(self.pois)} points (POI).      ")

    def _extract_tags(self, elem: ET.Element) -> Dict[str, str]:
        return {child.get('k'): child.get('v') for child in elem.iterchildren('tag')
                if child.get('k') and child.get('v')}

    def _process_node(self, elem: ET.Element) -> None:
        tags = self._extract_tags(elem)
        if not tags:
            return

        is_restricted = tags.get('access') in self.RESTRICTED_ACCESS_VALUES
        is_barrier = 'barrier' in tags

        if is_restricted and not is_barrier:
            return

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
                if fclass in LookupTables.DISABLED_POIS:
                    return
                code = LookupTables.POI_CODES.get(fclass)

        if code is None:
            return

        raw_name = tags.get('short_name:en') or tags.get('int_name') or tags.get('name:en') or tags.get('short_name') or tags.get('name') or ""
        name = _sanitize_name_cached(raw_name)
        if not name and fclass:
            name = str(fclass)

        try:
            osm_id = elem.get('id')
            node_coord = self._get_node_coord(int(osm_id))
            if not node_coord:
                return
        except (TypeError, ValueError):
            return

        feature = MapFeature(osm_id=osm_id, fclass=fclass, code=code, name=name, points=[node_coord])
        feature.calculate_bbox()
        self.pois.append(feature)

    def _process_way(self, elem: ET.Element) -> None:
        tags = self._extract_tags(elem)

        if tags.get('access') in self.RESTRICTED_ACCESS_VALUES:
            if not any(k in tags for k in ('landuse', 'leisure', 'natural')):
                return

        points = []
        for nd in elem.iterchildren('nd'):
            ref = nd.get('ref')
            if ref is not None:
                try:
                    coord = self._get_node_coord(int(ref))
                    if coord:
                        points.append(coord)
                except ValueError:
                    continue

        if not points:
            return

        try:
            way_id = int(elem.get('id'))

            # [C-ARRAY FLATTENING]: Replaces massive List[Tuple] overhead with ultra-fast contiguous memory
            start_idx = len(self.way_coords_pool)
            self.way_coords_pool.extend(itertools.chain.from_iterable(points))

            self.way_cache_ids.append(way_id)
            self.way_cache_starts.append(start_idx)
            self.way_cache_lengths.append(len(points))

            if self._ways_sorted and way_id < self._last_way_id:
                self._ways_sorted = False
            self._last_way_id = way_id

        except (TypeError, ValueError):
            return

        raw_name = tags.get('short_name:en') or tags.get('int_name') or tags.get('name:en') or tags.get('short_name') or tags.get('name') or ""
        name = _sanitize_name_cached(raw_name)
        osm_id = str(way_id)

        is_closed = len(points) >= 4 and points[0] == points[-1]

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
                    unique_points = points[:-1]
                    avg_lon = sum(p[0] for p in unique_points) // len(unique_points)
                    avg_lat = sum(p[1] for p in unique_points) // len(unique_points)

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
            if 'highway' in tags:
                fclass, target_layer = tags['highway'], 'roads'
            elif 'landuse' in tags:
                fclass, target_layer = tags['landuse'], 'landuse'
            elif 'natural' in tags:
                fclass, target_layer = tags['natural'], 'landuse'
            elif 'leisure' in tags:
                fclass, target_layer = tags['leisure'], 'landuse'

        if target_layer == 'roads' and len(points) >= 2:
            if fclass == 'track' and 'tracktype' in tags:
                fclass += f'_{tags["tracktype"]}'
            if fclass in LookupTables.DISABLED_ROADS:
                return

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
            if fclass in LookupTables.DISABLED_LANDUSE:
                return

            if points[0] == points[-1]:
                if not self._is_clockwise(points):
                    points.reverse()
                code = LookupTables.POLYGON_CODES.get(fclass, HWConfig.DEFAULT_POLYGON_CODE)
                feature = MapFeature(osm_id=osm_id, fclass=fclass, code=code, name=name, points=points)
                feature.calculate_bbox()
                self.landuse.append(feature)

    def _process_relation(self, elem: ET.Element) -> None:
        tags = self._extract_tags(elem)
        if tags.get('type') != 'multipolygon':
            return

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

        if not fclass or fclass in LookupTables.DISABLED_LANDUSE:
            return

        raw_name = tags.get('short_name:en') or tags.get('int_name') or tags.get('name:en') or tags.get('short_name') or tags.get('name') or ""
        name = _sanitize_name_cached(raw_name)

        combined_points, parts = [], []
        current_index = 0

        members = list(elem.iterchildren('member'))
        sorted_members = [m for m in members if m.get('role', 'outer') == 'outer'] + \
                         [m for m in members if m.get('role', 'outer') == 'inner']

        for member in sorted_members:
            if member.get('type') == 'way' and member.get('ref') is not None:
                try:
                    ref = int(member.get('ref'))
                    role = member.get('role', 'outer')

                    idx = bisect.bisect_left(self.way_cache_ids, ref)
                    if idx < len(self.way_cache_ids) and self.way_cache_ids[idx] == ref:
                        # [C-ARRAY UNFLATTENING]: Reconstructing geometries at memory-safe speed
                        start_idx = self.way_cache_starts[idx]
                        length = self.way_cache_lengths[idx]
                        end_idx = start_idx + (length * 2)
                        flat_coords = self.way_coords_pool[start_idx:end_idx]

                        ring_points = [(flat_coords[i], flat_coords[i + 1]) for i in range(0, length * 2, 2)]

                        if len(ring_points) >= 4 and ring_points[0] == ring_points[-1]:
                            is_cw = self._is_clockwise(ring_points)
                            if role == 'outer' and not is_cw:
                                ring_points.reverse()
                            elif role == 'inner' and is_cw:
                                ring_points.reverse()

                            parts.append(current_index)
                            combined_points.extend(ring_points)
                            current_index += len(ring_points)
                except ValueError:
                    continue

        osm_id = elem.get('id')
        if combined_points and parts and osm_id:
            code = LookupTables.POLYGON_CODES.get(fclass, HWConfig.DEFAULT_POLYGON_CODE)
            feature = MapFeature(osm_id=osm_id, fclass=fclass, code=code, name=name, points=combined_points, parts=parts)
            feature.calculate_bbox()
            self.landuse.append(feature)
