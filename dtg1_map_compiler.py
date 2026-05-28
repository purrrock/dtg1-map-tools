#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Monolithic Map Compiler (C175C1 Platform)
===============================================
Однопроходный компилятор OSM -> DT G1 (roads, landuse, water, pois).
Реализует Flat List State Machine для SQT-индекса, аппаратный Z-Culling (LOD) 
и строгую бинарную совместимость (включая эталонные пустышки для обхода EOF).
"""

import os
import struct
import xml.etree.ElementTree as ET
import json

# ==============================================================================
# КОНСТАНТЫ ФОРМАТА (Platform C175C1)
# ==============================================================================
YZL_SIZE = 32           # Глобальный заголовок слоя
SQT_HEADER_SIZE = 8     # Заголовок секции LOD
NODE_SIZE = 28          # Размер одного узла SQT
CHUNK_SIZE = 15         # Максимальное кол-во объектов в одном плоском кластере

DBF_HEADER_LEN = 161    # Заголовок dBase III атрибутов
RECORD_LEN = 145        # Размер одной записи в БД

# ==============================================================================
# LOOK-UP TABLES (LUT) ИЗ FEATURES.CSV
# ==============================================================================

# Единый маппинг: "Тег OSM" -> "Аппаратный код графического контроллера"
FEATURE_CODES = {
    # --- Дороги (Roads) ---
    "motorway": 5111, "trunk": 5112, "primary": 5113, "secondary": 5114, "tertiary": 5115,
    "unclassified": 5121, "residential": 5122, "living_street": 5123, "pedestrian": 5124, "busway": 5125,
    # ХАК: motorway_link (5131) не поддерживается аппаратно, приравнен к motorway
    "motorway_link": 5111, "trunk_link": 5132, "primary_link": 5133, "secondary_link": 5134, "tertiary_link": 5135,
    "service": 5141, "track": 5142, "track_grade1": 5143, "track_grade2": 5144, "track_grade3": 5145, 
    "track_grade4": 5146, "track_grade5": 5147, "bridleway": 5151, "cycleway": 5152, "footway": 5153,
    "path": 5154, "steps": 5155, "road": 5199, "unknown_road": 5199,
    
    # --- Полигоны (Landuse / Leisure) ---
    "forest": 7201, "park": 7202, "residential": 7203, "industrial": 7204,
    "cemetery": 7206, "allotments": 7207, "meadow": 7208, "commercial": 7209,
    "nature_reserve": 7210, "recreation_ground": 7211, "retail": 7212,
    "military": 7213, "quarry": 7214, "orchard": 7215, "vineyard": 7216, "scrub": 7217
}

# Пороги аппаратного Z-Culling (Масштаб экрана в метрах, на котором объект ПОЯВЛЯЕТСЯ)
DISPLAY_SCALES = {
    # Дороги
    5111: 1000, 5112: 1000, 5113: 1000, 5114: 1000,
    5115: 500,  5131: 500,  5132: 500,  5133: 500,  5134: 500,  5135: 500,
    5121: 100,  5122: 100,  5123: 100,  5124: 100,  5125: 100,
    5141: 50,   5142: 50,   5143: 50,   5144: 50,   5145: 50,   5146: 50,   5147: 50,
    5151: 20,   5152: 20,   5153: 20,   5154: 20,   5155: 20,   5199: 20,
    
    # Полигоны (Большинство заливок появляется только на детальных масштабах, чтобы не грузить GPU)
    7201: 500, 7202: 500, 7203: 500, 7204: 500, 7206: 500, 7207: 500, 7208: 500, 7209: 500,
    7210: 500, 7211: 500, 7212: 500, 7213: 500, 7214: 500, 7215: 500, 7216: 500, 7217: 500
}

# ==============================================================================
# ФАЗА 1: ПАРСЕР ГЕОМЕТРИИ (Потоковое чтение OSM)
# ==============================================================================

def parse_osm_geometry(osm_file):
    """
    Двухпроходный парсер. Извлекает узлы, а затем собирает из них Линии (roads) 
    и Замкнутые Кольца (landuse). Использует iterparse для защиты от переполнения ОЗУ.
    """
    print("[>] Проход 1: Кэширование узлов (nodes)...")
    nodes = {}
    context = ET.iterparse(osm_file, events=('start', 'end'))
    for event, elem in context:
        if event == 'end' and elem.tag == 'node':
            nodes[elem.attrib['id']] = (float(elem.attrib['lon']), float(elem.attrib['lat']))
            elem.clear() # Очистка памяти
            
    print(f"    Загружено узлов: {len(nodes)}")
    print("[>] Проход 2: Сборка объектов (ways/polygons)...")
    
    roads = []
    landuse = []
    
    context = ET.iterparse(osm_file, events=('end',))
    for event, elem in context:
        if elem.tag == 'way':
            tags = {child.attrib['k']: child.attrib['v'] for child in elem.findall('tag')}
            # Отфильтровываем битые/отсутствующие точки (Orphan Nodes)
            points = [nodes[nd.attrib['ref']] for nd in elem.findall('nd') if nd.attrib['ref'] in nodes]
            
            if not points:
                elem.clear()
                continue
                
            name = tags.get('int_name', '').strip() or tags.get('name', '').strip()
            osm_id = elem.attrib['id']

            # --- Обработка ДОРОГ (Линейная геометрия) ---
            if 'highway' in tags and len(points) >= 2:
                fclass = tags['highway']
                code = FEATURE_CODES.get(fclass, 5142) # 5142 (track) как fallback
                roads.append({
                    "osm_id": osm_id, "fclass": fclass, "code": code, 
                    "name": name, "points": points
                })
                
            # --- Обработка ПОЛИГОНОВ (Площадная геометрия) ---
            elif ('landuse' in tags or 'leisure' in tags) and len(points) >= 4:
                # Полигон обязан быть замкнутым кольцом
                if points[0] == points[-1]:
                    fclass = tags.get('landuse', tags.get('leisure', 'unknown'))
                    code = FEATURE_CODES.get(fclass, 7208) # 7208 (meadow) как fallback
                    landuse.append({
                        "osm_id": osm_id, "fclass": fclass, "code": code, 
                        "name": name, "points": points
                    })
            elem.clear()

    print(f"    Распознано: {len(roads)} дорог, {len(landuse)} полигонов.")
    return roads, landuse

# ==============================================================================
# ФАЗА 2: БИНАРНАЯ КОМПИЛЯЦИЯ (.MLP, .DB, .IDX)
# ==============================================================================

def compile_mlp(features, mlp_out):
    """
    Упаковывает сырые координаты в бинарный формат MLP (аналог ESRI Shapefile).
    Возвращает список метаданных для линковки с индексами (DB и SQT).
    """
    print(f"[>] Компиляция {mlp_out}...")
    bin_records = bytearray()
    abs_offset = YZL_SIZE
    meta_records = []
    record_number = 1

    for feature in features:
        points = feature["points"]
        # Вычисление BBox
        minx_f, miny_f = min(p[0] for p in points), min(p[1] for p in points)
        maxx_f, maxy_f = max(p[0] for p in points), max(p[1] for p in points)
        
        # Float * 1,000,000 -> Int32 (Аппаратный стандарт)
        minx, miny = int(minx_f * 1_000_000), int(miny_f * 1_000_000)
        maxx, maxy = int(maxx_f * 1_000_000), int(maxy_f * 1_000_000)
        
        body = bytearray(struct.pack("<iiii", minx, miny, maxx, maxy))
        body += struct.pack("<II", 1, len(points)) # num_parts = 1 (пока без дырок/островов)
        body += struct.pack("<I", 0) # Массив parts_indices
        
        for p in points:
            body += struct.pack("<ii", int(p[0] * 1_000_000), int(p[1] * 1_000_000))
            
        header = struct.pack(">I", record_number) + struct.pack("<I", len(body))
        record_bin = header + body
        
        # Системное смещение (v1) указывает на начало body (минус заголовок)
        v1 = abs_offset - 24 
        
        meta_records.append({
            "osm_id": feature["osm_id"], "code": feature["code"],
            "fclass": feature["fclass"], "name": feature["name"],
            "v1": v1, "bbox": [minx_f, miny_f, maxx_f, maxy_f]
        })
        
        bin_records += record_bin
        abs_offset += len(record_bin)
        record_number += 1

    # Внимание: Размер в YZL - это Payload Size, а не полный размер файла (во избежание EOF Error)
    payload_size = len(bin_records)
    with open(mlp_out, 'wb') as f:
        f.write(b'YZL\x00' + struct.pack("<I", payload_size) + b'\x00' * 24)
        f.write(bin_records)
        
    return meta_records

def compile_db(meta_records, db_out):
    """
    Генерирует атрибутивную БД формата dBase III. 
    Определяет линковочный параметр 'v2' для SQT-индекса.
    """
    print(f"[>] Компиляция {db_out}...")
    
    def pad_str(text, length):
        return str(text).encode('utf-8')[:length].ljust(length, b'\x00')

    def make_desc(name, length):
        return name.encode('ascii').ljust(11, b'\x00') + b'C' + b'\x00' * 4 + bytes([length]) + b'\x00' * 15

    db_records = []
    db_counter = 2 
    
    # Распределение указателей v2
    for item in meta_records:
        if item.get("name"):
            item["v2"] = db_counter
            db_counter += 1
            db_records.append(item)
        else: 
            # Безымянные объекты ссылаются на пустую 'Record 0'
            item["v2"] = 1 
            
    # Record 0 (Обязательная пустышка)
    bin_records = b'\x00' * RECORD_LEN 
    
    for rec in db_records:
        record_bytes = bytearray(b'\x20') # dBase флаг 'Valid'
        record_bytes += pad_str(rec["osm_id"], 12) + pad_str(rec["code"], 4)
        record_bytes += pad_str(rec["fclass"], 28) + pad_str(rec["name"], 100)
        bin_records += record_bytes

    total_records = len(db_records) + 1
    dbf_header = bytearray(b'\x03\x00\x00\x00') + struct.pack('<I', total_records)
    dbf_header += struct.pack('<H', DBF_HEADER_LEN) + struct.pack('<H', RECORD_LEN) + b'\x00' * 20
    dbf_header += make_desc("osm_id", 12) + make_desc("code", 4)
    dbf_header += make_desc("fclass", 28) + make_desc("name", 100) + b'\x0D'
    
    payload_size = DBF_HEADER_LEN + len(bin_records)
    with open(db_out, 'wb') as f:
        f.write(b'YZL\x00' + struct.pack('<I', payload_size) + b'\x00' * 24)
        f.write(dbf_header)
        f.write(bin_records)


class ClusterBlock:
    """Плоский блок, содержащий до 15 объектов данных и их общие границы (BBox)."""
    def __init__(self, data_nodes):
        self.data_nodes = data_nodes
        self.bbox = [
            min(n["bbox"][0] for n in data_nodes), min(n["bbox"][1] for n in data_nodes),
            max(n["bbox"][2] for n in data_nodes), max(n["bbox"][3] for n in data_nodes)
        ]

def compile_idx(meta_records, idx_out):
    """
    Многоуровневый компилятор SQT-индекса (LOD 0, 1, 2).
    Использует Flat List архитектуру (Навигационный Узел -> Заголовок -> Узлы данных).
    """
    print(f"[>] Компиляция {idx_out} (LOD Filtering)...")
    idx_buffer = bytearray()
    
    # Фильтры Z-Culling: 0=Все, 1=Средние масштабы, 2=Обзорные масштабы
    lod_filters = [
        lambda c: True,
        lambda c: DISPLAY_SCALES.get(c, 20) >= 100,
        lambda c: DISPLAY_SCALES.get(c, 20) >= 1000
    ]

    for lod_index, condition in enumerate(lod_filters):
        lod_records = [r for r in meta_records if condition(r["code"])]
        
        idx_buffer.extend(b'SQT\x01' + struct.pack("<I", 1)) # LOD Header
        blocks = [ClusterBlock(lod_records[i:i+CHUNK_SIZE]) for i in range(0, len(lod_records), CHUNK_SIZE)]
        
        for block in blocks:
            if not block.data_nodes: continue
                
            first_data = block.data_nodes[0]
            cluster_len = len(block.data_nodes) + 1
            jump_v3 = (cluster_len * NODE_SIZE) + 8 # Вычисляем адрес следующего кластера
            
            # 1. Навигационный узел (Системный прыжок v3)
            idx_buffer.extend(struct.pack("<IIIffff", first_data["v1"], first_data["v2"], jump_v3, *block.bbox))
            # 2. Заголовок кластера (Cluster Descriptor)
            idx_buffer.extend(struct.pack("<IIffffI", 0, cluster_len, *block.bbox, int(first_data["code"])))
            # 3. Узлы данных
            for d in block.data_nodes:
                idx_buffer.extend(struct.pack("<IIffffI", d["v1"], d["v2"], *d["bbox"], int(d["code"])))
                
        idx_buffer.extend(b'\x00' * 8) # LOD Terminator
    
    payload_size = len(idx_buffer)
    with open(idx_out, "wb") as f:
        f.write(b'YZL\x00' + struct.pack("<I", payload_size) + b'\x00' * 24)
        f.write(idx_buffer)

# ==============================================================================
# ФАЗА 3: ВСПОМОГАТЕЛЬНЫЕ ГЕНЕРАТОРЫ
# ==============================================================================

def create_map_name(name, meta_records, out_file="map.name"):
    """Центрирует камеру приложения карт по координатам скомпилированного массива."""
    if not meta_records: return
    
    minx = min(r["bbox"][0] for r in meta_records)
    miny = min(r["bbox"][1] for r in meta_records)
    maxx = max(r["bbox"][2] for r in meta_records)
    maxy = max(r["bbox"][3] for r in meta_records)

    center_lat, center_lon = (miny + maxy) / 2.0, (minx + maxx) / 2.0

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"centerLat": center_lat, "centerLon": center_lon, "mapName": name}, f, separators=(',', ':'))
    print(f"[>] Сохранен конфигуратор {out_file} (Центр: {center_lat:.5f}, {center_lon:.5f})")

def create_empty_layer(layer_prefix):
    """
    Внедряет Hex-дампы оригинальных пустых файлов C175C1. 
    Используется для слоев, которые еще не поддерживаются (water, pois).
    """
    print(f"[>] Создание системной Hex-заглушки: {layer_prefix}...")
    mlp_hex = "595A4C00000000000000000400000000D41D8CD98F00B204E9800998ECF8427EA0B861411B1259427BD96D41FCD45A42000000000000000000000000000000008BDDE3424F40B4418BDDE3424F40B441"
    idx_hex = "595A4C10300000000000000400000010E5F9D2228804251B5F9E3EAB298C30E5535154010100000000000000000000005351540101000000000000000000000053515401010000000000000000000000"
    db_hex = "595A4C00320100000000000400000000D65E1C742D95963F147A4468DD25F93F035F071A01000000A100910000000000000000000000000000000000000000006F736D5F6964000000000043000000000C000000000000000000000000000000636F6465000000000000004E000000000400000000000000000000000000000066636C617373000000000043000000001C0000000000000000000000000000006E616D65000000000000004300000000640000000000000000000000000000000D" + "00" * 145

    with open(f"{layer_prefix}.mlp", "wb") as f: f.write(bytearray.fromhex(mlp_hex))
    with open(f"{layer_prefix}.idx", "wb") as f: f.write(bytearray.fromhex(idx_hex))
    with open(f"{layer_prefix}.db",  "wb") as f: f.write(bytearray.fromhex(db_hex))

# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main():
    if not os.path.exists("map.osm"):
        print("[-] Ошибка: Файл map.osm не найден.")
        return
        
    print("=========================================")
    print("DT G1 MAP COMPILER (v5.0 - Monolith)")
    print("=========================================")
    
    roads_data, landuse_data = parse_osm_geometry("map.osm")
    
    if not roads_data and not landuse_data:
        print("[-] Ошибка: Отсутствуют валидные объекты (roads/landuse).")
        return

    meta_all = []

    # 1. Слой Дорог
    if roads_data:
        meta_roads = compile_mlp(roads_data, "roads.mlp")
        compile_db(meta_roads, "roads.db")
        compile_idx(meta_roads, "roads.idx")
        meta_all.extend(meta_roads)

    # 2. Слой Землепользования (Полигоны)
    if landuse_data:
        meta_landuse = compile_mlp(landuse_data, "landuse.mlp")
        compile_db(meta_landuse, "landuse.db")
        compile_idx(meta_landuse, "landuse.idx")
        meta_all.extend(meta_landuse)

    # 3. Общая центровка карты (Берем BBox всех скомпилированных слоев)
    if meta_all:
        create_map_name("Custom_Map", meta_all, "map.name")
    
    # 4. Неподдерживаемые слои глушим эталонными заводскими дампами
    create_empty_layer("water")
    create_empty_layer("pois")
    
    print("\n[УСПЕХ] Пакет карт успешно скомпилирован!")

if __name__ == "__main__":
    main()