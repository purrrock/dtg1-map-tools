#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler (Platform ATS3085S)
===============================================
v3.0 (POI Layer Update)
Компилятор векторных данных OpenStreetMap (OSM) в закрытые бинарные форматы
смарт-часов DT NO.1 G1 (.mlp, .idx, .db).

Архитектурные особенности платформы ATS3085S:
  1. Flat List State Machine: SQT-индекс генерируется плоским списком, а не деревом.
  2. Non-Zero Winding Rule: Внутренние контуры полигонов - CCW, внешние - CW.
  3. Z-Culling (LOD): Аппаратное скрытие объектов по 3 уровням детализации.
  4. System Dummies: Hex-пустышки (Payload Size = 0) для обхода EOF-защиты прошивки.
  5. C-Union Node Architecture: Навигационные узлы и узлы данных (28 байт).
  6. POI Topology Anomaly: Слой точек (pois) не имеет файла .mlp. Координаты 
     инкапсулируются в BBox узла данных, указатель v1 обнуляется, а база атрибутов .db 
     маппится без пустой записи 0 (1-based index).
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


# ==============================================================================
# КОНФИГУРАЦИЯ И СИСТЕМНЫЕ КОНСТАНТЫ
# ==============================================================================

class HWConfig:
    """Аппаратные константы платформы ATS3085S"""
    YZL_HEADER_SIZE = 32
    NODE_SIZE = 28           # Унифицированный размер узла (Data Node и Nav Node)
    CHUNK_SIZE = 14          # Максимум объектов в кластере (ограничение буфера)
    DBF_HEADER_LEN = 161     # dBase III Fixed Header
    DBF_RECORD_LEN = 145     # dBase III Fixed Record
    
    # Системные константы LUT
    WATER_CODE = 8200
    DEFAULT_HIGHWAY_CODE = 5142
    DEFAULT_POLYGON_CODE = 7208
    DEFAULT_POI_CODE = 2724


class LookupTables:
    """Динамические словари стилей (LUT), загружаемые из внешнего CSV."""
    HIGHWAY_CODES: Dict[str, int] = {}
    POLYGON_CODES: Dict[str, int] = {}
    POI_CODES: Dict[str, int] = {}
    DISPLAY_SCALES: Dict[int, int] = {}

    @classmethod
    def load_from_csv(cls, filepath: str = "features.csv") -> None:

        #Парсинг внешнего файла стилей. Формат столбцов: [0]Code [1]fclass [2]Color [3]LOD [4]Layer [5]OSM_Tags [6]Description [7]Remap_Code [8]Remap_Color [9]Remap_LOD

        if not os.path.exists(filepath):
            print(f"[-] Ошибка: Конфигурационный файл {filepath} не найден.")
            sys.exit(1)
            
        print(f"[>] Загрузка таблицы стилей LUT из {filepath}...")
        
        try:
            with open(filepath, mode='r', encoding='utf-8') as f:
                reader = csv.reader(f, delimiter=';')
                next(reader, None)
                
                loaded_records = 0
                for row in reader:
                    if len(row) < 10:
                        continue
                        
                    fclass = row[1].strip()
                    layer = row[4].strip()
                    
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
                        
                    loaded_records += 1
                    
            print(f"    Успешно импортировано правил: {loaded_records}")
            
            if HWConfig.WATER_CODE not in cls.DISPLAY_SCALES:
                cls.DISPLAY_SCALES[HWConfig.WATER_CODE] = 1000
                
        except Exception as e:
            print(f"[-] Фатальная ошибка парсинга {filepath}: {e}")
            sys.exit(1)

# ==============================================================================
# МОДЕЛИ ДАННЫХ
# ==============================================================================

@dataclass
class MapFeature:
    """Описывает единичный картографический примитив"""
    osm_id: str
    fclass: str
    code: int
    name: str
    points: List[Tuple[float, float]]
    parts: List[int] = field(default_factory=lambda: [0])
    
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    v1: int = 0        # Абсолютное смещение геометрии в файле .mlp
    v2: int = 0        # Индекс строки в БД атрибутов .db
    mlp_size: int = 0  # Размер бинарного тела в .mlp

    def calculate_bbox(self) -> None:
        """Вычисляет ограничивающий прямоугольник (Bounding Box) объекта.
        Для слоя POI (1 точка) minX=maxX и minY=maxY автоматически."""
        minx = min(p[0] for p in self.points)
        miny = min(p[1] for p in self.points)
        maxx = max(p[0] for p in self.points)
        maxy = max(p[1] for p in self.points)
        self.bbox = (minx, miny, maxx, maxy)

    def pack_data_node(self) -> bytes:
        """
        Упаковка Узла Данных (Data Node). Строго 28 байт.
        Формат (C-Union): [BBox 16b] [Type 4b] [v1 4b] [v2 4b]
        Смещение +20 и +24 зарезервировано для универсального чтения ссылок парсером.
        """
        return struct.pack(
            "<ffffIII", 
            self.bbox[0], self.bbox[1], self.bbox[2], self.bbox[3], 
            self.code, 
            self.v1, self.v2
        )

# ==============================================================================
# МОДУЛЬ ПАРСИНГА ГЕОМЕТРИИ
# ==============================================================================

class OSMParser:
    """Двухпроходный потоковый парсер OSM с обработкой топологии"""
    
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
        print("[>] Проход 1: Кэширование узлов (nodes)...")
        for event, elem in ET.iterparse(self.osm_file, events=('start', 'end')):
            if event == 'end' and elem.tag == 'node':
                self.nodes[elem.attrib['id']] = (float(elem.attrib['lon']), float(elem.attrib['lat']))
                elem.clear()
        print(f"    Загружено узлов: {len(self.nodes)}")

    def _pass2_build_features(self) -> None:
        print("[>] Проход 2: Нормализация геометрии, мультиполигонов и POI...")
        context = ET.iterparse(self.osm_file, events=('end',))
        
        for event, elem in context:
            if elem.tag == 'way':
                self._process_way(elem)
                elem.clear()
            elif elem.tag == 'relation':
                self._process_relation(elem)
                elem.clear()
            elif elem.tag == 'node':
                self._process_node(elem)
                elem.clear()
            
        print(f"    Собрано: {len(self.roads)} дорог, {len(self.landuse)} полигонов, {len(self.pois)} точек (POI).")

    def _extract_tags(self, elem: ET.Element) -> Dict[str, str]:
        return {
            child.attrib['k']: child.attrib['v'] 
            for child in elem.findall('tag') 
            if 'k' in child.attrib and 'v' in child.attrib
        }

    def _process_node(self, elem: ET.Element) -> None:
        """Обработка точек интереса (POI)"""
        tags = self._extract_tags(elem)
        if not tags: return

        fclass = None
        code = None
        
        # Поиск совпадений тегов с таблицей POI (LUT)
        for val in tags.values():
            if val in LookupTables.POI_CODES:
                fclass = val
                code = LookupTables.POI_CODES[val]
                break
                
        if code is None:
            return

        name = tags.get('int_name', '').strip() or tags.get('name', '').strip()
        lon = float(elem.attrib['lon'])
        lat = float(elem.attrib['lat'])
        
        feature = MapFeature(
            osm_id=elem.attrib['id'],
            fclass=fclass,
            code=code,
            name=name,
            points=[(lon, lat)]
        )
        feature.calculate_bbox()  # min и max будут равны, что и требуется для BBox координат
        self.pois.append(feature)

    def _process_way(self, elem: ET.Element) -> None:
        tags = self._extract_tags(elem)
        points = [
            self.nodes[nd.attrib['ref']] 
            for nd in elem.findall('nd') 
            if nd.attrib.get('ref') in self.nodes
        ]
        
        if not points: return
        self.ways_cache[elem.attrib['id']] = points
        
        name = tags.get('int_name', '').strip() or tags.get('name', '').strip()
        osm_id = elem.attrib['id']

        if 'highway' in tags and len(points) >= 2:
            fclass = tags['highway']
            if fclass == 'track' and 'tracktype' in tags:
                fclass = fclass + '_' + tags['tracktype']
                
            feature = MapFeature(
                osm_id=osm_id, fclass=fclass,
                code=LookupTables.HIGHWAY_CODES.get(fclass, HWConfig.DEFAULT_HIGHWAY_CODE),
                name=name, points=points
            )
            feature.calculate_bbox()
            self.roads.append(feature)
            
        elif ('landuse' in tags or 'leisure' in tags or 'natural' in tags) and len(points) >= 4:
            if points[0] == points[-1]: 
                fclass = tags.get('landuse', tags.get('leisure', tags.get('natural', 'unknown')))
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
        if not fclass: return
            
        name = tags.get('int_name', '').strip() or tags.get('name', '').strip()
        combined_points, parts = [], []
        current_index = 0
        
        members = elem.findall('member')
        sorted_members = [m for m in members if m.attrib.get('role', 'outer') == 'outer'] + \
                         [m for m in members if m.attrib.get('role', 'outer') == 'inner']
        
        for member in sorted_members:
            if member.attrib.get('type') == 'way' and 'ref' in member.attrib:
                ref = member.attrib['ref']
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

# ==============================================================================
# МОДУЛЬ КОМПИЛЯЦИИ БИНАРНЫХ ФАЙЛОВ
# ==============================================================================

class MapCompiler:

    @staticmethod
    def _write_yzl_container(filepath: str, payload: bytes, is_idx: bool, lod2_size: int = 0) -> None:
        """Инкапсуляция данных в системный контейнер YZL"""
        payload_size = len(payload)
        md5_hash = hashlib.md5(payload).digest()
        
        if is_idx:
            # Стандартный индекс геометрии
            header = (
                b'YZL\x08' + 
                struct.pack("<I", payload_size) + 
                b'\x02\x00\x00\x04' + 
                struct.pack(">I", lod2_size) + 
                md5_hash
            )
        else:
            # Используется для .mlp, .db и ВНИМАНИЕ: для pois.idx! 
            # Архитектура POI требует базовой сигнатуры YZL\x00 для индекса.
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
        print(f"[>] Компиляция геометрии: {filepath}...")
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
                print(f"[~] Слой {filepath} не содержит именованных объектов. Создание .db файла пропущено.")
                for feature in features: feature.v2 = 0
                return
        else:
            if not features:
                return
    
        print(f"[>] Компиляция атрибутов: {filepath}...")
        
        def pad(text: Any, length: int) -> bytes:
            return str(text).encode('utf-8')[:length].ljust(length, b'\x00')
            
        def desc(name: str, length: int) -> bytes:
            return name.encode('ascii').ljust(11, b'\x00') + b'C' + b'\x00'*4 + bytes([length]) + b'\x00'*15
        
        # Слой POI использует 1-based indexing без пустой нулевой записи
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
        print(f"[>] Компиляция индекса SQT: {filepath}...")
        idx_buffer = bytearray()
        
        if is_poi:
            # POI слой имеет уникальную топологию (отсутствие .mlp, хранение координат в BBox)
            # Файл состоит строго из 1 уровня LOD с системным маркером по смещению +4 заголовка SQT
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
                        f.v1 = 0 # Физического смещения геометрии не существует
                        idx_buffer.extend(f.pack_data_node())
            else:
                mode, count = 0, len(clusters[0]) if clusters else 0
                idx_buffer.extend(struct.pack("<II", mode, count))
                
                for f in clusters[0] if clusters else []:
                    f.v1 = 0
                    idx_buffer.extend(f.pack_data_node())

            # ВНИМАНИЕ: POI Index использует стандартный YZL\x00 (как у .mlp)
            cls._write_yzl_container(filepath, idx_buffer, is_idx=False)

        else:
            # Стандартная многоуровневая геометрия (LOD 0, 1, 2)
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
        print(f"[>] Создание системной Hex-заглушки: {layer_prefix}...")
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
    if not os.path.exists("map.osm"):
        print("[-] Ошибка: Файл map.osm не найден. Завершение работы.")
        return
        
    print("=========================================")
    print("DT G1 MAP COMPILER")
    print("=========================================")
    
    LookupTables.load_from_csv("features.csv")

    parser = OSMParser("map.osm")
    roads_data, landuse_data, pois_data = parser.parse()
    meta_all: List[MapFeature] = []

    # 1. Компиляция слоя Дорог
    if roads_data:
        MapCompiler.compile_mlp(roads_data, "roads.mlp")
        MapCompiler.compile_db(roads_data, "roads.db")
        MapCompiler.compile_idx(roads_data, "roads.idx")
        meta_all.extend(roads_data)

    # 2. Разделение слоя Землепользования (Landuse и Water)
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

    # 3. Компиляция слоя точек (POI)
    if pois_data:
        MapCompiler.compile_db(pois_data, "pois.db", is_poi=True)
        MapCompiler.compile_idx(pois_data, "pois.idx", is_poi=True)
        meta_all.extend(pois_data)
    else:
        # В случае отсутствия тегов в OSM - оставляем без генерации слоя pois.
        pass

    # 4. Общая центровка камеры
    if meta_all:
        MapCompiler.create_map_name("DTG1_Map", meta_all, "map.name")
    
    print("\n[УСПЕХ] Пакет карт скомпилирован успешно!")

if __name__ == "__main__":
    main()