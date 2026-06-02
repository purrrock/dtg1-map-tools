#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler (Platform ATS3085S)
===============================================
v2.0 (Strict OOP & Typed)
Компилятор векторных данных OpenStreetMap (OSM) в закрытые бинарные форматы
смарт-часов DT NO.1 G1 (.mlp, .idx, .db).

Архитектурные особенности движка платформы ATS3085S:
  1. Flat List State Machine: SQT-индекс генерируется плоским списком, а не деревом.
  2. Non-Zero Winding Rule: Внутренние контуры полигонов - CCW, внешние - CW.
  3. Z-Culling (LOD): Аппаратное скрытие объектов по 3 уровням детализации.
  4. System Dummies: Hex-пустышки (Payload Size = 0) для обхода EOF-защиты прошивки.
"""

import os
import struct
import json
import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional


# ==============================================================================
# КОНФИГУРАЦИЯ И СИСТЕМНЫЕ КОНСТАНТЫ
# ==============================================================================

class HWConfig:
    """Аппаратные константы платформы ATS3085S"""
    YZL_HEADER_SIZE = 32
    NODE_SIZE = 28
    CHUNK_SIZE = 14          # Максимум объектов в кластере (ограничение буфера)
    DBF_HEADER_LEN = 161     # dBase III Fixed Header
    DBF_RECORD_LEN = 145     # dBase III Fixed Record

class LookupTables:
    """Словари соответствия тегов OSM внутренним кодам стилей прошивки"""
    HIGHWAY_CODES = {
        "motorway": 5111, "trunk": 5112, "primary": 5113, "secondary": 5114, "tertiary": 5115,
        "unclassified": 5121, "residential": 5122, "living_street": 5123, "pedestrian": 5124, "busway": 5125,
        "motorway_link": 5131, "trunk_link": 5132, "primary_link": 5133, "secondary_link": 5134, "tertiary_link": 5135,
        "service": 5141, "track": 5142, "track_grade1": 5143, "track_grade2": 5144, "track_grade3": 5145, 
        "track_grade4": 5146, "track_grade5": 5147, "bridleway": 5151, "cycleway": 5152, "footway": 5153,
        "path": 5154, "steps": 5155, "road": 5199, "unknown": 5199
    }

    POLYGON_CODES = {
        "forest": 7201, "park": 7202, "residential": 7203, "industrial": 7204,
        "cemetery": 7206, "allotments": 7207, "meadow": 7208, "commercial": 7209,
        "nature_reserve": 7210, "recreation_ground": 7211, "retail": 7212,
        "military": 7213, "quarry": 7214, "orchard": 7215, "vineyard": 7216, "scrub": 7217,
        "grass": 7218, "heath": 7219, "farmland": 7228, "farmyard": 7229, "landfill": 7233,
        "water": 8200
    }

    # Пороги Z-Culling (масштаб появления объекта на экране часов в метрах)
    DISPLAY_SCALES = {
        5111: 1000, 5112: 1000, 5113: 1000, 5114: 1000,
        5115: 500,  5131: 500,  5132: 500,  5133: 500,  5134: 500,  5135: 500,
        5121: 100,  5122: 100,  5123: 100,  5124: 100,  5125: 100,
        5141: 50,   5142: 50,   5143: 50,   5144: 50,   5145: 50,   5146: 50,   5147: 50,
        5151: 20,   5152: 20,   5153: 20,   5154: 20,   5155: 20,   5199: 20,
        7201: 500,  7202: 500,  7203: 500,  7204: 500,  7206: 500,  7207: 500,  7208: 500,  
        7209: 500,  7210: 500,  7211: 500,  7212: 500,  7213: 500,  7214: 500,  7215: 500,  
        7216: 500,  7217: 500,  7218: 500,  7219: 500,  7228: 500,  7229: 500,  7233: 500,
        8200: 500
    }


# ==============================================================================
# МОДЕЛИ ДАННЫХ
# ==============================================================================

@dataclass
class MapFeature:
    """Описывает единичный картографический примитив (дорогу или полигон)"""
    osm_id: str
    fclass: str
    code: int
    name: str
    points: List[Tuple[float, float]]
    parts: List[int] = field(default_factory=lambda: [0])
    
    # Системные атрибуты, вычисляемые на этапе компиляции:
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    v1: int = 0        # Абсолютное смещение геометрии в файле .mlp
    v2: int = 0        # Индекс строки в БД атрибутов .db
    mlp_size: int = 0  # Размер бинарного тела в .mlp (нужно для Jump-указателей)

    def calculate_bbox(self) -> None:
        """Вычисляет ограничивающий прямоугольник (Bounding Box) объекта"""
        minx = min(p[0] for p in self.points)
        miny = min(p[1] for p in self.points)
        maxx = max(p[0] for p in self.points)
        maxy = max(p[1] for p in self.points)
        self.bbox = (minx, miny, maxx, maxy)


# ==============================================================================
# МОДУЛЬ ПАРСИНГА ГЕОМЕТРИИ
# ==============================================================================

class OSMParser:
    """Двухпроходный потоковый парсер OSM с обработкой триангуляционной топологии"""
    
    def __init__(self, osm_file: str):
        self.osm_file = osm_file
        self.nodes: Dict[str, Tuple[float, float]] = {}
        self.ways_cache: Dict[str, List[Tuple[float, float]]] = {}
        
        self.roads: List[MapFeature] = []
        self.landuse: List[MapFeature] = []

    @staticmethod
    def _is_clockwise(points: List[Tuple[float, float]]) -> bool:
        """Алгоритм шнуровки Гаусса для определения направления обхода."""
        sum_area = 0.0
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            sum_area += (x1 * y2 - x2 * y1)
        return sum_area < 0

    def parse(self) -> Tuple[List[MapFeature], List[MapFeature]]:
        self._pass1_cache_nodes()
        self._pass2_build_features()
        return self.roads, self.landuse

    def _pass1_cache_nodes(self) -> None:
        """Кэширует сырые координаты точек для сборки линий"""
        print("[>] Проход 1: Кэширование узлов (nodes)...")
        for event, elem in ET.iterparse(self.osm_file, events=('start', 'end')):
            if event == 'end' and elem.tag == 'node':
                self.nodes[elem.attrib['id']] = (float(elem.attrib['lon']), float(elem.attrib['lat']))
                elem.clear()
        print(f"    Загружено узлов: {len(self.nodes)}")

    def _pass2_build_features(self) -> None:
        """Сборка линий, полигонов и мультиполигонов"""
        print("[>] Проход 2: Нормализация геометрии и мультиполигонов...")
        context = ET.iterparse(self.osm_file, events=('end',))
        
        for event, elem in context:
            if elem.tag == 'way':
                self._process_way(elem)
                elem.clear()
            elif elem.tag == 'relation':
                self._process_relation(elem)
                elem.clear()
            elif elem.tag == 'node':
                # Защита от утечки памяти: узлы нам больше не нужны как XML-объекты
                elem.clear()
            
        print(f"    Собрано: {len(self.roads)} дорог, {len(self.landuse)} полигонов.")

    def _process_way(self, elem: ET.Element) -> None:
        tags = {child.attrib['k']: child.attrib['v'] for child in elem.findall('tag') if 'k' in child.attrib and 'v' in child.attrib}
        points = [self.nodes[nd.attrib['ref']] for nd in elem.findall('nd') if nd.attrib.get('ref') in self.nodes]
        
        if not points:
            return
            
        self.ways_cache[elem.attrib['id']] = points
        name = tags.get('int_name', '').strip() or tags.get('name', '').strip()
        osm_id = elem.attrib['id']

        # Обработка линий (дорог)
        if 'highway' in tags and len(points) >= 2:
            fclass = tags['highway']
            if fclass == 'track' and 'tracktype' in tags:
                fclass = fclass + '_' + tags['tracktype']
                
            feature = MapFeature(
                osm_id=osm_id,
                fclass=fclass,
                code=LookupTables.HIGHWAY_CODES.get(fclass, 5142),
                name=name,
                points=points
            )
            feature.calculate_bbox()
            self.roads.append(feature)
            
        # Обработка простых полигонов (Outer Ring)
        elif ('landuse' in tags or 'leisure' in tags or 'natural' in tags) and len(points) >= 4:
            if points[0] == points[-1]: 
                fclass = tags.get('landuse', tags.get('leisure', tags.get('natural', 'unknown')))
                
                # Принудительно задаем Clockwise обход (требование аппаратного рендерера)
                if not self._is_clockwise(points):
                    points.reverse()
                    
                feature = MapFeature(
                    osm_id=osm_id,
                    fclass=fclass,
                    code=LookupTables.POLYGON_CODES.get(fclass, 7208),
                    name=name,
                    points=points
                )
                feature.calculate_bbox()
                self.landuse.append(feature)

    def _process_relation(self, elem: ET.Element) -> None:
        """Сборка сложных структур с внутренними дырками (Multipolygons)"""
        tags = {child.attrib['k']: child.attrib['v'] for child in elem.findall('tag') if 'k' in child.attrib and 'v' in child.attrib}
        
        if tags.get('type') != 'multipolygon':
            return
            
        fclass = tags.get('landuse', tags.get('leisure', tags.get('natural', None)))
        if not fclass:
            return
            
        name = tags.get('int_name', '').strip() or tags.get('name', '').strip()
        combined_points = []
        parts = []
        current_index = 0
        
        # Сортировка: Outer кольца обрабатываются первыми
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
                        
                        # Non-Zero Winding Rule: Outer = CW, Inner = CCW
                        if role == 'outer' and not is_cw:
                            ring_points.reverse()
                        elif role == 'inner' and is_cw:
                            ring_points.reverse()
                            
                        parts.append(current_index)
                        combined_points.extend(ring_points)
                        current_index += len(ring_points)
        
        if combined_points and parts:
            feature = MapFeature(
                osm_id=elem.attrib['id'],
                fclass=fclass,
                code=LookupTables.POLYGON_CODES.get(fclass, 7208),
                name=name,
                points=combined_points,
                parts=parts
            )
            feature.calculate_bbox()
            self.landuse.append(feature)


# ==============================================================================
# МОДУЛЬ КОМПИЛЯЦИИ БИНАРНЫХ ФАЙЛОВ
# ==============================================================================

class MapCompiler:
    """Оркестратор сборки бинарных файлов YZL, SQT, DBF и MLP."""

    @staticmethod
    def _write_yzl_container(filepath: str, payload: bytes, is_idx: bool, lod2_size: int = 0) -> None:
        """
        Инкапсулирует полезную нагрузку в глобальный 32-байтовый контейнер YZL.
        Строго соблюдает спецификацию C175C1 для MD5 хэширования и сигнатур.
        """
        payload_size = len(payload)
        md5_hash = hashlib.md5(payload).digest()
        
        if is_idx:
            # Специфичный заголовок для .idx (флаги 0x0C и 0x01)
            header = b'YZL\x0C' + struct.pack("<I", payload_size) + b'\x01\x00\x00\x04' + struct.pack(">I", lod2_size) + md5_hash
        else:
            # Стандартный заголовок для геометрии и БД
            header = b'YZL\x00' + struct.pack("<I", payload_size) + b'\x00\x00\x00\x04\x00\x00\x00\x00' + md5_hash
            
        with open(filepath, 'wb') as f:
            f.write(header)
            f.write(payload)

    @classmethod
    def compile_mlp(cls, features: List[MapFeature], filepath: str) -> None:
        """Упаковка сырой геометрии."""
        print(f"[>] Компиляция геометрии: {filepath}...")
        bin_records = bytearray()
        abs_offset = HWConfig.YZL_HEADER_SIZE
        record_number = 1

        for feature in features:
            # Перевод Float координат в Int32 с множителем 1 млн.
            minx_i = int(feature.bbox[0] * 1e6)
            miny_i = int(feature.bbox[1] * 1e6)
            maxx_i = int(feature.bbox[2] * 1e6)
            maxy_i = int(feature.bbox[3] * 1e6)
            
            body = bytearray(struct.pack("<iiii", minx_i, miny_i, maxx_i, maxy_i))
            body += struct.pack("<II", len(feature.parts), len(feature.points))
            
            for part_idx in feature.parts: 
                body += struct.pack("<I", part_idx)
            for p in feature.points: 
                body += struct.pack("<ii", int(p[0] * 1e6), int(p[1] * 1e6))
                
            header = struct.pack(">I", record_number) + struct.pack("<I", len(body))
            record_bin = header + body

            # Текущее смещение геометрии внутри полезной нагрузки (относительно начала payload)
            current_mlp_offset = len(bin_records)
            
            # Указатель (v1) в SQT-индексе должен пробивать заголовок объекта
            # и указывать на саму полезную нагрузку (Payload), минуя 8 байт: ID (4) + Length (4).
            feature.v1 = current_mlp_offset + 8
            
            feature.v2 = 1 # Значение по умолчанию (указатель на пустышку в .db)
            feature.mlp_size = len(record_bin)
            
            bin_records += record_bin
            record_number += 1

        cls._write_yzl_container(filepath, bin_records, is_idx=False)

    @classmethod
    def compile_db(cls, features: List[MapFeature], filepath: str) -> None:
        """Упаковка атрибутов в формат dBase III."""
        print(f"[>] Компиляция атрибутов: {filepath}...")
        
        # Первая запись dBase строго пустая (зарезервирована для безымянных объектов)
        bin_records = bytearray(b'\x00' * HWConfig.DBF_RECORD_LEN) 
        db_counter = 2 
        
        def pad(text: any, length: int) -> bytes:
            # Принудительное приведение к строке, т.к. feature.code - это int, 
            # а dBase III хранит данные в символьном (Character) формате.
            return str(text).encode('utf-8')[:length].ljust(length, b'\x00')
            
        def desc(name: str, length: int) -> bytes:
            return name.encode('ascii').ljust(11, b'\x00') + b'C' + b'\x00'*4 + bytes([length]) + b'\x00'*15
        
        total_records = 1
        for feature in features:
            if feature.name:
                feature.v2 = db_counter
                db_counter += 1
                total_records += 1
                
                r_bytes = bytearray(b'\x20') # Валидный dBase row marker
                r_bytes += pad(feature.osm_id, 12) + pad(feature.code, 4) + pad(feature.fclass, 28) + pad(feature.name, 100)
                bin_records += r_bytes

        # Сборка 161-байтового заголовка dBase III
        dbf_header = bytearray(b'\x03\x00\x00\x00') + struct.pack('<I', total_records)
        dbf_header += struct.pack('<H', HWConfig.DBF_HEADER_LEN) + struct.pack('<H', HWConfig.DBF_RECORD_LEN) + b'\x00' * 20
        dbf_header += desc("osm_id", 12) + desc("code", 4) + desc("fclass", 28) + desc("name", 100) + b'\x0D'
        
        cls._write_yzl_container(filepath, dbf_header + bin_records, is_idx=False)

    @classmethod
    def compile_idx(cls, features: List[MapFeature], filepath: str) -> None:
        """Сборка пространственного индекса (State Machine SQT)."""
        print(f"[>] Компиляция индекса SQT: {filepath}...")
        idx_buffer = bytearray()
        
        # 3 уровня детализации
        lod_filters = [
            lambda c: True,
            lambda c: LookupTables.DISPLAY_SCALES.get(c, 20) >= 500, 
            lambda c: LookupTables.DISPLAY_SCALES.get(c, 20) >= 1000
        ]
        
        lod2_size = 0
        for lod_index, condition in enumerate(lod_filters):
            start_len = len(idx_buffer)
            lod_records = [f for f in features if condition(f.code)]
            
            # Маркер начала SQT
            idx_buffer.extend(b'SQT\x01' + struct.pack("<I", 1))
            
            # Разбивка на плоские кластеры (по CHUNK_SIZE объектов)
            clusters = [lod_records[i:i + HWConfig.CHUNK_SIZE] for i in range(0, len(lod_records), HWConfig.CHUNK_SIZE)]
            
            # Защита от EOF Panic аппаратного парсера.
            # Если уровень детализации пуст, записываем 8 байт нулей.
            if not clusters:
                idx_buffer.extend(b'\x00' * 8) # Аппаратное выравнивание
                if lod_index == 2:
                    # Принудительно фиксируем размер LOD 2 перед continue (всегда 16 байт: 4+4+8)
                    lod2_size = len(idx_buffer) - start_len
                continue
            # ---------------------------------------------------------

            # Флаг одиночного кластера для применения аппаратного правила Omission
            is_single_cluster = len(clusters) == 1
            
            for cluster_idx, cluster in enumerate(clusters):
                if not cluster: continue
                
                # Вычисление общего BBox для кластера
                c_minx = min(f.bbox[0] for f in cluster)
                c_miny = min(f.bbox[1] for f in cluster)
                c_maxx = max(f.bbox[2] for f in cluster)
                c_maxy = max(f.bbox[3] for f in cluster)
                
                cluster_len = len(cluster) + 1
                
                # Запись Узла Навигации (Nav Node) - ТОЛЬКО если кластеров больше одного
                if not is_single_cluster:
                    jump_v3 = (cluster_len * HWConfig.NODE_SIZE) + 8 # +8 байт компенсации Early Exit
                    
                    if cluster_idx == 0:
                        nav_v1 = 1  # Root Node
                        nav_v2 = len(clusters)
                    else:
                        last_prev = clusters[cluster_idx - 1][-1]
                        nav_v1 = last_prev.v1 + last_prev.mlp_size
                        nav_v2 = 1
                    
                    idx_buffer.extend(struct.pack("<IIIffff", nav_v1, nav_v2, jump_v3, c_minx, c_miny, c_maxx, c_maxy))
                
                # Запись Заголовка Кластера (Cluster Header)
                # Поле v1=0 аппаратно переключает стейт-машину в пакетный режим чтения узлов данных.
                # Структуры BBox и Code здесь полностью игнорируются прошивкой. В заводских картах 
                # сюда попадал случайный мусор из оперативной памяти компилятора.
                # Записываем нули.
                idx_buffer.extend(struct.pack("<IIffffI", 0, cluster_len, 0.0, 0.0, 0.0, 0.0, 0))
                
                # Запись Узлов Данных
                for f in cluster:
                    idx_buffer.extend(struct.pack("<IIffffI", f.v1, f.v2, *f.bbox, f.code))
   
            if lod_index == 2:
                lod2_size = len(idx_buffer) - start_len
        
        cls._write_yzl_container(filepath, idx_buffer, is_idx=True, lod2_size=lod2_size)

    @staticmethod
    def create_empty_layer(layer_prefix: str) -> None:
        """Генерация hex-пустышек для обхода EOF-проверки прошивки (System Dummies)."""
        print(f"[>] Создание системной Hex-заглушки: {layer_prefix}...")
        # Очищенная бинарная строка пустого .mlp (убран мусор из RAM заводского компилятора с координатами Шэньчжэня)
        mlp_hex = "595A4C00000000000000000400000000D41D8CD98F00B204E9800998ECF8427E"
        idx_hex = "595A4C10300000000000000400000010E5F9D2228804251B5F9E3EAB298C30E5535154010100000000000000000000005351540101000000000000000000000053515401010000000000000000000000"
        db_hex = "595A4C00320100000000000400000000D65E1C742D95963F147A4468DD25F93F035F071A01000000A100910000000000000000000000000000000000000000006F736D5F6964000000000043000000000C000000000000000000000000000000636F6465000000000000004E000000000400000000000000000000000000000066636C617373000000000043000000001C0000000000000000000000000000006E616D65000000000000004300000000640000000000000000000000000000000D" + "00" * HWConfig.DBF_RECORD_LEN
        
        with open(f"{layer_prefix}.mlp", "wb") as f: f.write(bytearray.fromhex(mlp_hex))
        with open(f"{layer_prefix}.idx", "wb") as f: f.write(bytearray.fromhex(idx_hex))
        with open(f"{layer_prefix}.db",  "wb") as f: f.write(bytearray.fromhex(db_hex))

    @staticmethod
    def create_map_name(name: str, meta_records: List[MapFeature], out_file: str = "map.name") -> None:
        """Центрирование начальной камеры GPS-приложения."""
        if not meta_records: return
        center_lat = (min(r.bbox[1] for r in meta_records) + max(r.bbox[3] for r in meta_records)) / 2.0
        center_lon = (min(r.bbox[0] for r in meta_records) + max(r.bbox[2] for r in meta_records)) / 2.0

        # Жесткое требование парсера: никаких пробелов (separators)
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
    
    # 1. Парсинг геометрии и сборка примитивов
    parser = OSMParser("map.osm")
    roads_data, landuse_data = parser.parse()
    meta_all: List[MapFeature] = []

    # 2. Компиляция слоя Дорог
    if roads_data:
        MapCompiler.compile_mlp(roads_data, "roads.mlp")
        MapCompiler.compile_db(roads_data, "roads.db")
        MapCompiler.compile_idx(roads_data, "roads.idx")
        meta_all.extend(roads_data)

    # 3. Разделение слоя Землепользования (Landuse и Water)
    landuse_only = [f for f in landuse_data if f.code != 8200]
    water_only = [f for f in landuse_data if f.code == 8200]

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
    else:
        MapCompiler.create_empty_layer("water")

    # 4. Общая центровка камеры и генерация обязательных пустышек
    if meta_all:
        MapCompiler.create_map_name("DTG1_Map", meta_all, "map.name")
    
    MapCompiler.create_empty_layer("pois")
    
    print("\n[УСПЕХ] Пакет карт скомпилирован успешно!")

if __name__ == "__main__":
    main()