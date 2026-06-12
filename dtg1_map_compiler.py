#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler (Platform ATS3085S)
===============================================
v3.0 (POI Layer Update)
Compiler of OpenStreetMap (OSM) vector data into closed binary formats
of DT NO.1 G1 smartwatches (.mlp, .idx, .db).
"""

import os
import struct
import json
import hashlib
import xml.etree.ElementTree as ET
import csv
import sys
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional, Any
import argparse
import math

# ==============================================================================
# CONFIGURATION AND SYSTEM CONSTANTS
# ==============================================================================

class HWConfig:
    #Hardware constants of the ATS3085S platform
    YZL_HEADER_SIZE = 32
    NODE_SIZE = 28           # Unified node size (Data Node and Nav Node)
    CHUNK_SIZE = 14          # Maximum objects in a cluster (buffer limit)
    DBF_HEADER_LEN = 161     # dBase III Fixed Header
    DBF_RECORD_LEN = 145     # dBase III Fixed Record
    
    # System LUT constants
    WATER_CODE = 8200
    DEFAULT_HIGHWAY_CODE = 5142
    DEFAULT_POLYGON_CODE = 7208
    DEFAULT_POI_CODE = 2724

# Глобальный неизменяемый кортеж топонимических дескрипторов.
# Вынесен из тела функции для предотвращения миллионных циклов аллокации памяти.
# Строго отсортирован по убыванию длины для корректной работы алгоритма startswith.
_STOP_WORDS = (
    "restaurant",
     "praspiekt", "boulevard",
     "проспект", "переулок", "ресторан", "праспект", "рэстаран", 
    "praspekt", "stancyya", "prypynak", "restaran",
    "площадь", "бульвар", "станция", "магазин", "завулак", 
    "станцыя", "highway", "grocery", "station", "zavulak", "voziera",
    "вуліца", "плошча", "возера", "vulica", "plošča", "bulvar", 
    "alieja", "skvier", "улица", "street", "avenue", "square", 
    "shoppe", "market",
    "пр-кт", "шоссе", "аллея", "озеро", "сквер", "крама", 
    "blvd.", "drive", "alley", "hotel", "river", "pr-kt", "krama",
    "кафе", "парк", "шаша", "алея", "вул.", "зав.", 
    "кафэ", "šaša", "vul.", "zav.", "kafe", 
    "road", "lane", "cafe", "shop", "mall", "lake", "ave.",
    "ул.", "пер.", "пл.", "st.", "rd.", "ln.", "dr.", "sq.", 
    "way", "pl."
)

class LookupTables:
    #Dynamic style dictionaries (LUT) loaded from external CSV.
    HIGHWAY_CODES: Dict[str, int] = {}
    POLYGON_CODES: Dict[str, int] = {}
    POI_CODES: Dict[str, int] = {}
    DISPLAY_SCALES: Dict[int, int] = {}
    POI_SHAPES: Dict[str, str] = {}
    
    # Isolated blacklists to prevent fclass namespace collisions (e.g. 'residential')
    DISABLED_ROADS: set = set()
    DISABLED_LANDUSE: set = set()
    DISABLED_POIS: set = set()

    @classmethod
    def load_from_csv(cls, filepath: str = "features.csv") -> None:
        # Parsing external style file.
        if not os.path.exists(filepath):
            print(f"[-] Error: Configuration file {filepath} not found.")
            sys.exit(1)
            
        print(f"[>] Loading LUT style table from {filepath}...")
        
        try:
            with open(filepath, mode='r', encoding='utf-8') as f:
                reader = csv.reader(f, delimiter=';')
                next(reader, None)
                
                loaded_records = 0
                for row in reader:
                    if len(row) < 11: # Expanded to 11 columns
                        continue
                        
                    fclass = row[1].strip()
                    layer = row[4].strip()
                    
                    # Read Enabled flag. Any of these values disables the class.
                    enabled_flag = row[10].strip().lower()
                    if enabled_flag in ('0', 'false', 'no', 'off', ''):
                        if layer == 'roads':
                            cls.DISABLED_ROADS.add(fclass)
                        elif layer == 'pois':
                            cls.DISABLED_POIS.add(fclass)
                        else:  # landuse, water
                            cls.DISABLED_LANDUSE.add(fclass)
                        continue  # Skip adding to active mapping dicts
                    
                    try:
                        remap_code = int(row[7].strip())
                        remap_lod = int(row[9].strip())
                    except ValueError:
                        continue

                    if layer == 'roads':
                        cls.HIGHWAY_CODES[fclass] = remap_code
                        cls.DISPLAY_SCALES[remap_code] = remap_lod
                    elif layer in ('landuse', 'water'):
                        cls.POLYGON_CODES[fclass] = remap_code
                        cls.DISPLAY_SCALES[remap_code] = remap_lod
                    elif layer == 'pois':
                        cls.POI_CODES[fclass] = remap_code
                        cls.DISPLAY_SCALES[remap_code] = remap_lod
                        
                        # Чтение геометрии. Фоллбэк на 'rhombus', если столбец пуст или отсутствует
                        shape_val = row[11].strip().lower() if len(row) > 11 else 'rhombus'
                        cls.POI_SHAPES[fclass] = shape_val if shape_val else 'rhombus'
                        
                    loaded_records += 1
                    
            print(f"    Successfully imported rules: {loaded_records}")
            print(f"[i] LUT loaded. Roads: {len(cls.HIGHWAY_CODES)}, Polygons: {len(cls.POLYGON_CODES)}, POI: {len(cls.POI_CODES)}")
            print(f"[i] Objects in Blacklist: {len(cls.DISABLED_ROADS) + len(cls.DISABLED_LANDUSE) + len(cls.DISABLED_POIS)}")

            if HWConfig.WATER_CODE not in cls.DISPLAY_SCALES:
                cls.DISPLAY_SCALES[HWConfig.WATER_CODE] = 1000
                
        except Exception as e:
            print(f"[-] Fatal parsing error {filepath}: {e}")
            sys.exit(1)

# ==============================================================================
# DATA MODELS
# ==============================================================================

@dataclass
class MapFeature:
    """Describes a single cartographic primitive"""
    osm_id: str
    fclass: str
    code: int
    name: str
    points: List[Tuple[float, float]]
    parts: List[int] = field(default_factory=lambda: [0])
    
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    v1: int = 0        # Absolute geometry offset in the .mlp file
    v2: int = 0        # Row index in the .db attributes DB
    mlp_size: int = 0  # Binary body size in .mlp

    def calculate_bbox(self) -> None:
        """
        Вычисляет Bounding Box объекта.
        Оптимизация: генераторы заменены на list comprehensions для прямого исполнения в C-бэкенде.
        """
        if not self.points:
            return
            
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    def pack_data_node(self) -> bytes:
        """
        Packing the Data Node. Strictly 28 bytes.
        Format (C-Union): [BBox 16b] [Type 4b] [v1 4b] [v2 4b]
        Offsets +20 and +24 are reserved for universal reference reading by the parser.
        """
        return struct.pack(
            "<ffffIII", 
            self.bbox[0], self.bbox[1], self.bbox[2], self.bbox[3], 
            self.code, 
            self.v1, self.v2
        )


import xml.etree.ElementTree as ET

class GPXParser:
    @staticmethod
    def parse_track(filepath: str) -> Tuple[str, List[Tuple[float, float]]]:
        """
        Extracts route name and coordinates (Lon, Lat) from a GPX file.
        Returns a tuple: (Track_name, List_of_points).
        """
        if not os.path.exists(filepath):
            return "Route", []
            
        tree = ET.parse(filepath)
        root = tree.getroot()
        
        # Standard GPX 1.1 namespace
        ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
        
        # 1. Cascading search for the track name
        track_name = "Route" # Default value
        
        # Attempt #1: Global metadata <metadata><name>
        metadata_name = root.find('.//gpx:metadata/gpx:name', ns)
        if metadata_name is not None and metadata_name.text:
            track_name = metadata_name.text.strip()
        else:
            # Attempt #2: Local name of the track itself <trk><name>
            trk_name = root.find('.//gpx:trk/gpx:name', ns)
            if trk_name is not None and trk_name.text:
                track_name = trk_name.text.strip()
                
        # 2. Geometry extraction
        points = []
        for trkpt in root.findall('.//gpx:trkpt', ns):
            lat = float(trkpt.attrib['lat'])
            lon = float(trkpt.attrib['lon'])
            points.append((lon, lat))
            
        return track_name, points

class OSMParser:
    """Two-pass streaming OSM parser with topology processing"""
    # O(1) хэш-множество для аппаратного Early Exit парсинга закрытых территорий.
    # Локализовано на уровне класса для однократной инициализации в памяти.
    RESTRICTED_ACCESS_VALUES = {'private', 'permit', 'no'}
    
    def __init__(self, osm_file: str):
        self.osm_file = osm_file
        self.nodes: Dict[str, Tuple[float, float]] = {}    
        self.ways_cache: Dict[str, List[Tuple[float, float]]] = {}
        
        self.roads: List[MapFeature] = []
        self.landuse: List[MapFeature] = []
        self.pois: List[MapFeature] = []

    @staticmethod
    def _is_clockwise(points: List[Tuple[float, float]]) -> bool:
        sum_area = 0.0
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            sum_area += (x1 * y2 - x2 * y1)
        return sum_area < 0

    def parse(self) -> Tuple[List[MapFeature], List[MapFeature], List[MapFeature]]:
        self._pass1_cache_nodes()
        self._pass2_build_features()
        return self.roads, self.landuse, self.pois

    def _pass1_cache_nodes(self) -> None:
        print("[>] Pass 1: Caching nodes...")
        context = ET.iterparse(self.osm_file, events=('start', 'end'))
        context = iter(context)
        
        try:
            _, root = next(context)
        except StopIteration:
            return

        # --- МИКРООПТИМИЗАЦИИ ВЫПОЛНЕНИЯ ---
        # Локализация объектов в пространстве имен функции (ускорение инструкций VM)
        nodes_cache = self.nodes
        root_clear = root.clear
        
        # O(1) хэш-поиск вместо O(N) перебора элементов
        TARGET_TAGS = {'node', 'way', 'relation'}
        
        count = 0
        for event, elem in context:
            if event == 'end':
                # Однократное чтение атрибута за итерацию
                tag = elem.tag
                if tag == 'node':
                    # Прямое извлечение ссылки на словарь исключает 
                    # повторные вызовы дескриптора elem.attrib
                    attr = elem.attrib
                    nodes_cache[int(attr['id'])] = (float(attr['lon']), float(attr['lat']))
                    
                    count += 1
                    
                    # --- АППАРАТНАЯ ОПТИМИЗАЦИЯ ---
                    # Побитовое И место 
                    # дорогостоящего деления по модулю (count % 100000 == 0).
                    if not (count & 0x1FFFF):
                        sys.stdout.write(f"\r    Nodes cached: {count:,}")
                        sys.stdout.flush()
                
                if tag in TARGET_TAGS:
                    elem.clear()
                    root_clear()
                
        print(f"\r    Nodes loaded: {len(nodes_cache):,}       ")
        
    def _pass2_build_features(self) -> None:
        print("[>] Pass 2: Normalizing geometry, multipolygons and POIs...")
        context = ET.iterparse(self.osm_file, events=('start', 'end'))
        context = iter(context)
        
        try:
            _, root = next(context)
        except StopIteration:
            return
        
        # --- МИКРООПТИМИЗАЦИИ ВЫПОЛНЕНИЯ ---
        # Локализация алиасов для C-бэкенда (устранение опкодов LOAD_ATTR)
        root_clear = root.clear
        stdout_write = sys.stdout.write
        stdout_flush = sys.stdout.flush
        
        # O(1) таблица переходов (Jump Table) для диспетчеризации методов парсера.
        # Связывает строку тега напрямую с адресом локализованного метода-обработчика.
        processors = {
            'way': self._process_way,
            'relation': self._process_relation,
            'node': self._process_node
        }
        
        count = 0
        for event, elem in context:
            if event == 'end':
                # Выборка функции-обработчика по тегу
                processor = processors.get(elem.tag)
                
                if processor:
                    processor(elem)
                    count += 1
                    
                    # Изолированная очистка памяти только для топологии верхнего уровня
                    elem.clear()
                    root_clear()
                    
                    # --- АППАРАТНАЯ ОПТИМИЗАЦИЯ ---
                    # Побитовое И. Условие истинно каждые 16384 обработанных элементов.
                    if not (count & 0x3FFF):
                        stdout_write(f"\r    Elements processed: {count:,}")
                        stdout_flush()
            
        print(f"\r    Assembled: {len(self.roads)} roads, {len(self.landuse)} polygons, {len(self.pois)} points (POI).      ")
        
    def _extract_tags(self, elem: ET.Element) -> Dict[str, str]:
        return {
            child.attrib['k']: child.attrib['v'] 
            for child in elem.findall('tag') 
            if 'k' in child.attrib and 'v' in child.attrib
        }

    @staticmethod
    def sanitize_osm_name(name: str) -> str:
        """
        Нормализует строку названия объекта, переносит дескрипторы в конец 
        и экранирует пробелы. Также применяет визуальное усечение (макс. 24 символа).
        """
        if not name:
            return ""
            
        # Удаляем краевые пробелы, которые могли прийти из XML
        name = name.strip()
        name_lower = name.lower()
        
        # Поиск префикса по глобальному кортежу и инверсия порядка слов
        for word in _STOP_WORDS:
            # Ищем точное совпадение слова с пробелом, чтобы избежать ложных
            # срабатываний (например, отрезания "река" в слове "Рекамендация")
            prefix = word + " "
            
            if name_lower.startswith(prefix):
                # Извлекаем основную часть названия (сдвиг указателя на длину префикса)
                core_name = name[len(prefix):].strip()
                
                if core_name:
                    # Поднимаем регистр первой буквы основной части
                    core_name = core_name[0].upper() + core_name[1:]
                    # Переносим дескриптор в конец (принудительно в нижнем регистре)
                    name = f"{core_name} {word.lower()}"
                break # После первой успешной инверсии прерываем цикл
                
        # Аппаратное экранирование (0x20 -> 0x5F)
        # Гарантирует, что движок часов считает строку монолитной
        name = name.replace(" ", "_")
        
        # Визуальное усечение под размер экрана (макс ~25 символов)
        # Используем 22 символа + 2 точки
        if len(name) > 22:
            # Используем две точки "..", чтобы сэкономить пиксели, 
            # так как символ "_" также займет место.
            name = name[:22].strip('_') + ".."
            
        # Защитная конвертация: чистим возможные битые UTF-8 глифы,
        # которые могут сломать парсер часов.
        encoded = name.encode('utf-8', 'ignore')
        
        return encoded.decode('utf-8')   
        
    def _process_node(self, elem: ET.Element) -> None:
        """
        Parsing point objects (POI) with dynamic hardware overrides for restricted barriers.
        """
        tags = self._extract_tags(elem)
        if not tags: 
            return

        is_restricted = tags.get('access') in self.RESTRICTED_ACCESS_VALUES
        
        # Флаг наличия физического препятствия (gate, lift_gate, block, bollard и т.д.)
        is_barrier = 'barrier' in tags

        # Условный Early Exit: жестко отбрасываем закрытые объекты (например, магазины для персонала),
        # но пропускаем в конвейер закрытые барьеры.
        if is_restricted and not is_barrier:
            return

        fclass = None
        code = None
        
        # Search for tag matches with the routing table (LUT)
        for val in tags.values():
            # Hard cutoff: if POI class is disabled, abort parsing immediately
            if val in LookupTables.DISABLED_POIS:
                return
            
            if val in LookupTables.POI_CODES:
                fclass = val
                code = LookupTables.POI_CODES[val]
                break
                
        if code is None:
            return

        # Node attribute extraction
        name = OSMParser.sanitize_osm_name(tags.get('short_name:en', '').strip() or tags.get('int_name', '').strip() or tags.get('name:en', '').strip() or tags.get('short_name', '').strip() or tags.get('name', '').strip())
        
        # Fallback for unnamed POIs: assign fclass to prevent blank polygons on the map
        if not name and fclass:
            name = str(fclass)
        
        osm_id = elem.attrib['id']
        
        # Поиск координат по int-ключу
        node_coord = self.nodes.get(int(osm_id))
        if not node_coord:
            return

        # Object initialization. For the pois layer, the points array 
        # always contains strictly one coordinate pair (lat, lon).
        feature = MapFeature(
            osm_id=osm_id, 
            fclass=fclass,
            code=code,
            name=name, 
            points=[node_coord]
        )
        
        # Call the calculate_bbox() method for a point (X_min=X_max, Y_min=Y_max).
        # This is necessary for hardware compatibility, as the watch parser reads 
        # POI coordinates directly from the Bounding Box fields of the data node.
        feature.calculate_bbox()
        self.pois.append(feature)

    def _process_way(self, elem: ET.Element) -> None:
        tags = self._extract_tags(elem)
        
        if tags.get('access') in self.RESTRICTED_ACCESS_VALUES:
            if not ('landuse' in tags or 'leisure' in tags or 'natural' in tags):
                return
                
        points = [
            self.nodes[int(nd.attrib['ref'])] 
            for nd in elem.findall('nd') 
            if 'ref' in nd.attrib and int(nd.attrib['ref']) in self.nodes
        ]
        
        if not points: return
        self.ways_cache[int(elem.attrib['id'])] = points
        
        name = OSMParser.sanitize_osm_name(tags.get('short_name:en', '').strip() or tags.get('int_name', '').strip() or tags.get('name:en', '').strip() or tags.get('short_name', '').strip() or tags.get('name', '').strip())
        osm_id = elem.attrib['id']

        if 'highway' in tags and len(points) >= 2:
            fclass = tags['highway']
            if fclass == 'track' and 'tracktype' in tags:
                fclass = fclass + '_' + tags['tracktype']
                
            # Check isolated roads blacklist to avoid dropping same-named landuse
            if fclass in LookupTables.DISABLED_ROADS:
                return
                
            feature = MapFeature(
                osm_id=osm_id, fclass=fclass,
                code=LookupTables.HIGHWAY_CODES.get(fclass, HWConfig.DEFAULT_HIGHWAY_CODE),
                name=name, points=points
            )
            feature.calculate_bbox()
            self.roads.append(feature)
            
        elif ('landuse' in tags or 'leisure' in tags or 'natural' in tags) and len(points) >= 4:
            fclass = tags.get('landuse', tags.get('leisure', tags.get('natural', 'unknown')))
            
            # Check isolated landuse blacklist
            if fclass in LookupTables.DISABLED_LANDUSE:
                return
                
            if points[0] == points[-1]: 
                if not self._is_clockwise(points):
                    points.reverse()
                   
                feature = MapFeature(
                    osm_id=osm_id, fclass=fclass,
                    code=LookupTables.POLYGON_CODES.get(fclass, HWConfig.DEFAULT_POLYGON_CODE),
                    name=name, points=points
                )
                feature.calculate_bbox()
                self.landuse.append(feature)

    def _process_relation(self, elem: ET.Element) -> None:
        tags = self._extract_tags(elem)
        if tags.get('type') != 'multipolygon': return
            
        fclass = tags.get('landuse', tags.get('leisure', tags.get('natural', None)))
        
        # Check isolated landuse blacklist for multipolygons
        if not fclass or fclass in LookupTables.DISABLED_LANDUSE: 
            return
            
        name = OSMParser.sanitize_osm_name(tags.get('short_name:en', '').strip() or tags.get('int_name', '').strip() or tags.get('name:en', '').strip() or tags.get('short_name', '').strip() or tags.get('name', '').strip())
        combined_points, parts = [], []
        current_index = 0
        
        members = elem.findall('member')
        sorted_members = [m for m in members if m.attrib.get('role', 'outer') == 'outer'] + \
                         [m for m in members if m.attrib.get('role', 'outer') == 'inner']
        
        for member in sorted_members:
            if member.attrib.get('type') == 'way' and 'ref' in member.attrib:
                ref = int(member.attrib['ref']) # Конвертация для поиска в ways_cache
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
            feature = MapFeature(
                osm_id=elem.attrib['id'], fclass=fclass,
                code=LookupTables.POLYGON_CODES.get(fclass, HWConfig.DEFAULT_POLYGON_CODE),
                name=name, points=combined_points, parts=parts
            )
            feature.calculate_bbox()
            self.landuse.append(feature)

class POIGeometryFactory:
    """Генератор низкополигональных примитивов для слоя POI."""
    EARTH_RADIUS = 6378137.0
    R = 4.1
    # Компенсация искажения перспективы дисплея ATS3085S
    PERSPECTIVE_Y_MULTIPLIER = 1.5

    @classmethod
    def generate_polygon(cls, shape_type: str, center_lon: float, center_lat: float) -> List[Tuple[float, float]]:
        R = cls.R
        
        # Маршрутизатор локальных координат вершин (x, y) в метрах.
        # Обход строго по часовой стрелке (CW) для корректного рендеринга на часах.
        shapes = {
            "rhombus": [(0, R * 1.4), (R, 0), (0, -R * 1.4), (-R, 0), (0, R * 1.4)],
            "triangle": [(0, R), (R, -R), (-R, -R), (0, R)],
            "house": [(0, R + 2), (R, R - 3), (R, -R), (-R, -R), (-R, R - 3), (0, R + 2)],
            "cup": [(-R, R), (R, R), (R, -R + 2.5), (R - 2.5, -R), (-R + 2.5, -R), (-R, -R + 2.5), (-R, R)],
            "cross": [(-2, R), (2, R), (2, 2), (R, 2), (R, -2), (2, -2), (2, -R), (-2, -R), (-2, -2), (-R, -2), (-R, 2), (-2, 2), (-2, R)],
            "toilet": [(-R, R), (R, R), (0.5, 0), (R, -R), (-R, -R), (-0.5, 0), (-R, R)],
            "transport": [(-R, R - 1), (R - 3, R - 1), (R, R - 3.0), (R, -R), (R - 2.0, -R), (R - 2.0, -R + 1.5), (R - 4.0, -R + 1.5), (R - 4.0, -R), (-R + 4.0, -R), (-R + 4.0, -R + 1.5), (-R + 2.0, -R + 1.5), (-R + 2.0, -R), (-R, -R), (-R, R - 1)],
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
                (0.0, 3.5), (R - 3.5, R), (R, R - 3.5), (3.5, 0.0), 
                (R, -R + 3.5), (R - 3.5, -R), (0.0, -3.5), (-R + 3.5, -R), 
                (-R, -R + 3.5), (-3.5, 0.0), (-R, R - 3.5), (-R + 3.5, R), 
                (0.0, 3.5)
            ]
        }
        
        # Безопасное извлечение с фоллбэком на ромб
        rel_coords = shapes.get(shape_type, shapes["rhombus"])
        
        points = []
        lat_rad = math.radians(center_lat)
        cos_lat = math.cos(lat_rad)
        
        for x_offset, y_offset in rel_coords:
            # Аппаратное растяжение по оси Y
            y_offset_stretched = y_offset * cls.PERSPECTIVE_Y_MULTIPLIER
            
            # Конвертация метрического смещения в сферическую дельту (WGS 84)
            d_lat = (y_offset_stretched / cls.EARTH_RADIUS) * (180.0 / math.pi)
            d_lon = (x_offset / (cls.EARTH_RADIUS * cos_lat)) * (180.0 / math.pi)
            
            points.append((center_lon + d_lon, center_lat + d_lat))
            
        return points
        
class MapCompiler:

    @staticmethod
    def _write_yzl_container(filepath: str, payload: bytes, is_idx: bool, lod2_size: int = 0) -> None:
        """Data encapsulation into the YZL system container"""
        payload_size = len(payload)
        md5_hash = hashlib.md5(payload).digest()
        
        if is_idx:
            # Standard geometry index
            header = (
                b'YZL\x08' + 
                struct.pack("<I", payload_size) + 
                b'\x02\x00\x00\x04' + 
                struct.pack(">I", lod2_size) + 
                md5_hash
            )
        else:
            # Used for .mlp, .db and ATTENTION: for pois.idx! 
            # POI architecture requires basic YZL\x00 signature for the index.
            header = (
                b'YZL\x00' + 
                struct.pack("<I", payload_size) + 
                b'\x00\x00\x00\x04\x00\x00\x00\x00' + 
                md5_hash
            )
            
        with open(filepath, 'wb') as f:
            f.write(header)
            f.write(payload)

    @staticmethod
    def pack_nav_node(v3_jump: int, bbox: Tuple[float, float, float, float], v1: int, v2_count: int) -> bytes:
        return struct.pack("<IffffII", v3_jump, bbox[0], bbox[1], bbox[2], bbox[3], v1, v2_count)

    @classmethod
    def compile_mlp(cls, features: List[MapFeature], filepath: str) -> None:
        print(f"[>] Compiling geometry: {filepath}...")
        bin_records = bytearray()
        record_number = 1

        for feature in features:
            minx_i = int(feature.bbox[0] * 1e6)
            miny_i = int(feature.bbox[1] * 1e6)
            maxx_i = int(feature.bbox[2] * 1e6)
            maxy_i = int(feature.bbox[3] * 1e6)
            
            body = bytearray(struct.pack("<iiii", minx_i, miny_i, maxx_i, maxy_i))
            body += struct.pack("<II", len(feature.parts), len(feature.points))
            
            for part_idx in feature.parts: body += struct.pack("<I", part_idx)
            for p in feature.points: body += struct.pack("<ii", int(p[0] * 1e6), int(p[1] * 1e6))
                
            header = struct.pack(">I", record_number) + struct.pack("<I", len(body))
            record_bin = header + body

            current_mlp_offset = len(bin_records)
            feature.v1 = current_mlp_offset + 8
            feature.v2 = 1 
            feature.mlp_size = len(record_bin)
            
            bin_records += record_bin
            record_number += 1

        cls._write_yzl_container(filepath, bin_records, is_idx=False)

    @classmethod
    def compile_db(cls, features: List[MapFeature], filepath: str, is_poi: bool = False) -> None:
        if not is_poi:
            has_named_features = any(feature.name for feature in features)
            if not has_named_features:
                print(f"[~] Layer {filepath} contains no named objects. .db file creation skipped.")
                for feature in features: feature.v2 = 0
                return
        else:
            if not features:
                return
    
        print(f"[>] Compiling attributes: {filepath}...")
        
        def pad(text: Any, length: int) -> bytes:
            return str(text).encode('utf-8')[:length].ljust(length, b'\x00')
            
        def desc(name: str, length: int) -> bytes:
            return name.encode('ascii').ljust(11, b'\x00') + b'C' + b'\x00'*4 + bytes([length]) + b'\x00'*15
        
        # POI layer uses 1-based indexing without an empty zero record
        if is_poi:
            bin_records = bytearray()
            db_counter = 1
            total_records = 0
        else:
            bin_records = bytearray(b'\x00' * HWConfig.DBF_RECORD_LEN) 
            db_counter = 2 
            total_records = 1

        for feature in features:
            if is_poi or feature.name:
                feature.v2 = db_counter
                db_counter += 1
                total_records += 1
                
                r_bytes = bytearray(b'\x20')
                r_bytes += pad(feature.osm_id, 12) + pad(feature.code, 4) + pad(feature.fclass, 28) + pad(feature.name, 100)
                bin_records += r_bytes

        dbf_header = (
            bytearray(b'\x03\x00\x00\x00') + 
            struct.pack('<I', total_records) +
            struct.pack('<H', HWConfig.DBF_HEADER_LEN) + 
            struct.pack('<H', HWConfig.DBF_RECORD_LEN) + 
            b'\x00' * 20 +
            desc("osm_id", 12) + 
            desc("code", 4) + 
            desc("fclass", 28) + 
            desc("name", 100) + 
            b'\x0D'
        )
        
        cls._write_yzl_container(filepath, dbf_header + bin_records, is_idx=False)

    @classmethod
    def compile_idx(cls, features: List[MapFeature], filepath: str, is_poi: bool = False) -> None:
        print(f"[>] Compiling SQT index: {filepath}...")
        idx_buffer = bytearray()
        
        if is_poi:
            # POI layer has a unique topology (no .mlp, coordinates stored in BBox)
            # File strictly consists of 1 LOD level with system marker at +4 offset of SQT header
            idx_buffer.extend(b'SQT\x01')
            idx_buffer.extend(b'\x01\x00\x00\x00') 
            
            if not features:
                idx_buffer.extend(struct.pack("<II", 0, 0))
                cls._write_yzl_container(filepath, idx_buffer, is_idx=False)
                return

            clusters = [features[i:i + HWConfig.CHUNK_SIZE] for i in range(0, len(features), HWConfig.CHUNK_SIZE)]
            is_clustered = len(clusters) > 1
            
            if is_clustered:
                mode, count = 1, len(clusters)
                idx_buffer.extend(struct.pack("<II", mode, count))
                
                for cluster in clusters:
                    if not cluster: continue
                    c_minx = min(f.bbox[0] for f in cluster)
                    c_miny = min(f.bbox[1] for f in cluster)
                    c_maxx = max(f.bbox[2] for f in cluster)
                    c_maxy = max(f.bbox[3] for f in cluster)
                    
                    v3_jump = (len(cluster) * HWConfig.NODE_SIZE) + 8 
                    idx_buffer.extend(cls.pack_nav_node(v3_jump, (c_minx, c_miny, c_maxx, c_maxy), 0, len(cluster)))                    
                    
                    for f in cluster:
                        f.v1 = 0 # No physical geometry offset exists
                        idx_buffer.extend(f.pack_data_node())
            else:
                mode, count = 0, len(clusters[0]) if clusters else 0
                idx_buffer.extend(struct.pack("<II", mode, count))
                
                for f in clusters[0] if clusters else []:
                    f.v1 = 0
                    idx_buffer.extend(f.pack_data_node())

            # ATTENTION: POI Index uses standard YZL\x00 (like .mlp)
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
                
                idx_buffer.extend(b'SQT\x01')
                idx_buffer.extend(b'\x00' * 4) 
                
                if not lod_records:
                    idx_buffer.extend(struct.pack("<II", 0, 0))
                    if lod_index == 2: lod2_size = len(idx_buffer) - start_len
                    continue

                clusters = [lod_records[i:i + HWConfig.CHUNK_SIZE] for i in range(0, len(lod_records), HWConfig.CHUNK_SIZE)]
                is_clustered = len(clusters) > 1
                
                if is_clustered:
                    mode, count = 1, len(clusters)
                    idx_buffer.extend(struct.pack("<II", mode, count))
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
                    mode, count = 0, len(clusters[0]) if clusters else 0
                    idx_buffer.extend(struct.pack("<II", mode, count))
                    for f in clusters[0] if clusters else []: idx_buffer.extend(f.pack_data_node())
       
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
# ENTRY POINT
# ==============================================================================

def main():
    # --- COMMAND LINE ARGUMENTS BLOCK ---
    cli_parser = argparse.ArgumentParser(
        description="DT G1 Map Compiler (Platform ATS3085S) - Vector OSM to Binary YZL/SQT"
    )
    cli_parser.add_argument(
        "-p", "--poi-mode",
        choices=["native", "landuse", "none"],
        default="none",
        help="POI generation mode: 'native' (native pois.idx/db layer), 'landuse' (polygon integration), 'none' (ignore POI, default)"
    )
    args = cli_parser.parse_args()
    # ----------------------------------------

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
            gpx_feature = MapFeature(
                osm_id="user_track_001",
                fclass="gpx_track",
                code=5111, 
                name=track_name,
                points=track_points
            )
            gpx_feature.calculate_bbox()
            roads_data.append(gpx_feature)
            print(f"    Track '{track_name}' successfully integrated (Points: {len(track_points)}).")
    # --------------------------
    
    meta_all: List[MapFeature] = []

    # 1. Roads layer compilation
    if roads_data:
        MapCompiler.compile_mlp(roads_data, "roads.mlp")
        MapCompiler.compile_db(roads_data, "roads.db")
        MapCompiler.compile_idx(roads_data, "roads.idx")
        meta_all.extend(roads_data)

    # --- POI BAKING BLOCK ---
    if args.poi_mode == "landuse" and pois_data:
        print("[>] Baking POI objects into landuse layer using dynamic shape factory...")
        
        for poi in pois_data:
            if not poi.points: 
                continue
                
            lon, lat = poi.points[0]
            
            # 1. Извлечение типа фигуры из глобального LUT
            shape_type = LookupTables.POI_SHAPES.get(poi.fclass, "rhombus")
            
            # 2. Вызов генератора геометрии
            poi.points = POIGeometryFactory.generate_polygon(shape_type, lon, lat)
            
            # 3. Перерасчет Bounding Box для нового полигона
            poi.calculate_bbox()
            
            # POI сохраняет свой оригинальный код из LUT для разноцветного рендеринга
            landuse_data.append(poi)
 
        print(f"    Successfully baked {len(pois_data)} POIs.")
        pois_data.clear() # Clear array to prevent processing in native DB compiler
    # ------------------------

    # 2. Separating Landuse and Water layers
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

    # 3. Point layer (POI) processing according to CLI parameters
    if args.poi_mode == "none":
        print("[>] POI layer skipped ('none' mode selected).")
        
    elif args.poi_mode == "native":
        if pois_data:
            MapCompiler.compile_db(pois_data, "pois.db", is_poi=True)
            MapCompiler.compile_idx(pois_data, "pois.idx", is_poi=True)
            meta_all.extend(pois_data)
        else:
            print("[~] Point objects (POI) are missing in the source data.")
            
    elif args.poi_mode == "landuse":
        print("[>] POI mode 'landuse' successfully handled. Objects baked into landuse layer.")

    # 4. Global camera centering
    if meta_all:
        MapCompiler.create_map_name("DTG1_Map", meta_all, "map.name")
    
    print("\n[SUCCESS] Map package compiled successfully!")

if __name__ == "__main__":
    main()