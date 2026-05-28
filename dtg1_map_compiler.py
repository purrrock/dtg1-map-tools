#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Monolithic Map Compiler (C175C1 Platform)
===============================================
Оптимизация: Внедрена поддержка многоуровневого индекса LOD 0/1/2 
для аппаратного Z-Culling на основе порогов Geofabrik.
"""

import os
import struct
import xml.etree.ElementTree as ET
import json

# Константы формата YZL / SQT
YZL_SIZE = 32
SQT_HEADER_SIZE = 8
NODE_SIZE = 28
CHUNK_SIZE = 15

DBF_HEADER_LEN = 161
RECORD_LEN = 145

# Полный словарь типов дорог согласно спецификации Geofabrik
HIGHWAY_CODES = {
    "motorway": 5111, "trunk": 5112, "primary": 5113, "secondary": 5114, "tertiary": 5115,
    "unclassified": 5121, "residential": 5122, "living_street": 5123, "pedestrian": 5124, "busway": 5125,
    "motorway_link": 5131, "trunk_link": 5132, "primary_link": 5133, "secondary_link": 5134, "tertiary_link": 5135,
    "service": 5141, "track": 5142, "track_grade1": 5143, "track_grade2": 5144, "track_grade3": 5145, 
    "track_grade4": 5146, "track_grade5": 5147, "bridleway": 5151, "cycleway": 5152, "footway": 5153,
    "path": 5154, "steps": 5155, "road": 5199, "unknown": 5199
}

# Таблица порогов видимости Z-Culling (на основе features_colors.csv)
# Связывает код рендеринга графического процессора с масштабом отображения (в метрах)
DISPLAY_SCALES = {
    5111: 1000, 5112: 1000, 5113: 1000, 5114: 1000,
    5115: 500,  5131: 500,  5132: 500,  5133: 500,  5134: 500,  5135: 500,
    5121: 100,  5122: 100,  5123: 100,  5124: 100,  5125: 100,
    5141: 50,   5142: 50,   5143: 50,   5144: 50,   5145: 50,   5146: 50,   5147: 50,
    5151: 20,   5152: 20,   5153: 20,   5154: 20,   5155: 20,   5199: 20
}

# Строгий резервный код, который точно переваривается часами
DEFAULT_CODE = 5142

# ==============================================================================
# ФАЗА 1: ПАРСИНГ ГЕОМЕТРИИ OSM
# ==============================================================================

def parse_osm_geometry(osm_file):
    print("[>] Проход 1: Загрузка узлов (nodes) в память...")
    nodes = {}
    context = ET.iterparse(osm_file, events=('start', 'end'))
    
    for event, elem in context:
        if event == 'end' and elem.tag == 'node':
            nodes[elem.attrib['id']] = (float(elem.attrib['lon']), float(elem.attrib['lat']))
            elem.clear()
            
    print(f"    Загружено узлов: {len(nodes)}")
    print("[>] Проход 2: Сборка объектов (ways)...")
    
    ways = []
    context = ET.iterparse(osm_file, events=('end',))
    
    for event, elem in context:
        if elem.tag == 'way':
            tags = {child.attrib['k']: child.attrib['v'] for child in elem.findall('tag')}
            if 'highway' in tags:
                points = [nodes[nd.attrib['ref']] for nd in elem.findall('nd') if nd.attrib['ref'] in nodes]
                if len(points) >= 2:
                    name = tags.get('int_name', '').strip() or tags.get('name', '').strip()
                    code = HIGHWAY_CODES.get(tags['highway'], DEFAULT_CODE)
                    ways.append({
                        "osm_id": elem.attrib['id'],
                        "fclass": tags['highway'],
                        "code": code,
                        "name": name,
                        "points": points
                    })
            elem.clear()

    print(f"    Собрано объектов: {len(ways)}")
    return ways

# ==============================================================================
# ФАЗА 2: КОМПИЛЯЦИЯ .MLP (Геометрия)
# ==============================================================================

def compile_mlp(ways, mlp_out):
    print(f"[>] Компиляция {mlp_out}...")
    bin_records = bytearray()
    abs_offset = YZL_SIZE
    meta_records = []
    record_number = 1

    for way in ways:
        points = way["points"]
        minx_f, miny_f = min(p[0] for p in points), min(p[1] for p in points)
        maxx_f, maxy_f = max(p[0] for p in points), max(p[1] for p in points)
        
        # Конвертация координат для рендерера (Float * 1M -> Int32)
        minx, miny = int(minx_f * 1_000_000), int(miny_f * 1_000_000)
        maxx, maxy = int(maxx_f * 1_000_000), int(maxy_f * 1_000_000)
        
        body = bytearray(struct.pack("<iiii", minx, miny, maxx, maxy))
        body += struct.pack("<II", 1, len(points)) # num_parts=1, num_points
        body += struct.pack("<I", 0) # Начальный индекс части (parts array)
        
        for p in points:
            body += struct.pack("<ii", int(p[0] * 1_000_000), int(p[1] * 1_000_000))
            
        header = struct.pack(">I", record_number) + struct.pack("<I", len(body))
        record_bin = header + body
        v1 = abs_offset - 24 # Системное смещение v1 для SQT индекса
        
        meta_records.append({
            "osm_id": way["osm_id"],
            "code": way["code"],
            "fclass": way["fclass"],
            "name": way["name"],
            "v1": v1,
            "bbox": [minx_f, miny_f, maxx_f, maxy_f]
        })
        
        bin_records += record_bin
        abs_offset += len(record_bin)
        record_number += 1

    payload_size = len(bin_records)
    with open(mlp_out, 'wb') as f:
        f.write(b'YZL\x00' + struct.pack("<I", payload_size) + b'\x00' * 24)
        f.write(bin_records)
        
    print(f"    Успешно сохранен {mlp_out} ({payload_size} байт)")
    return meta_records

# ==============================================================================
# ФАЗА 3: КОМПИЛЯЦИЯ .DB (Атрибуты dBase III)
# ==============================================================================

def make_string_field(text, length):
    return str(text).encode('utf-8')[:length].ljust(length, b'\x00')

def make_dbf_descriptor(name, length):
    desc = name.encode('ascii').ljust(11, b'\x00')
    desc += b'C' + b'\x00' * 4 + bytes([length]) + b'\x00' * 15
    return desc

def compile_db(meta_records, db_out):
    print(f"[>] Компиляция {db_out}...")
    db_records = []
    db_counter = 2 
    
    for item in meta_records:
        if item.get("name"):
            item["v2"] = db_counter
            db_counter += 1
            db_records.append(item)
        else: 
            item["v2"] = 1 # Ссылка на обязательную пустую Record 0
            
    bin_records = b'\x00' * RECORD_LEN # Record 0 (пустышка для безымянных объектов)
    
    for rec in db_records:
        record_bytes = bytearray(b'\x20')
        record_bytes += make_string_field(rec["osm_id"], 12)
        record_bytes += make_string_field(rec["code"], 4)
        record_bytes += make_string_field(rec["fclass"], 28)
        record_bytes += make_string_field(rec["name"], 100)
        bin_records += record_bytes

    total_records = len(db_records) + 1
    dbf_header = bytearray(b'\x03\x00\x00\x00') + struct.pack('<I', total_records)
    dbf_header += struct.pack('<H', DBF_HEADER_LEN) + struct.pack('<H', RECORD_LEN) + b'\x00' * 20
    dbf_header += make_dbf_descriptor("osm_id", 12) + make_dbf_descriptor("code", 4)
    dbf_header += make_dbf_descriptor("fclass", 28) + make_dbf_descriptor("name", 100) + b'\x0D'
    
    payload_size = DBF_HEADER_LEN + len(bin_records)
    with open(db_out, 'wb') as f:
        f.write(b'YZL\x00' + struct.pack('<I', payload_size) + b'\x00' * 24)
        f.write(dbf_header)
        f.write(bin_records)
        
    print(f"    Успешно сохранен {db_out} ({payload_size} байт)")

# ==============================================================================
# ФАЗА 4: КОМПИЛЯЦИЯ .IDX (Многоуровневый SQT Индекс)
# ==============================================================================

class ClusterBlock:
    def __init__(self, data_nodes):
        self.data_nodes = data_nodes
        self.bbox = [
            min(n["bbox"][0] for n in data_nodes), min(n["bbox"][1] for n in data_nodes),
            max(n["bbox"][2] for n in data_nodes), max(n["bbox"][3] for n in data_nodes)
        ]

def compile_idx(meta_records, idx_out):
    print(f"[>] Компиляция {idx_out} (Генерация уровней LOD 0, LOD 1, LOD 2)...")
    idx_buffer = bytearray()
    
    # Правила фильтрации для каждого уровня детализации
    lod_filters = [
        lambda c: True,                                      # LOD 0: Все объекты
        lambda c: DISPLAY_SCALES.get(c, 20) >= 100,          # LOD 1: Средний масштаб (100м+)
        lambda c: DISPLAY_SCALES.get(c, 20) >= 1000          # LOD 2: Обзорный масштаб (1000м+)
    ]

    for lod_index, condition in enumerate(lod_filters):
        # 1. Отбираем объекты, проходящие порог Z-Culling для текущего уровня
        lod_records = [r for r in meta_records if condition(r["code"])]
        
        # 2. Сигнатура начала блока SQT
        idx_buffer.extend(b'SQT\x01' + struct.pack("<I", 1))
        
        # 3. Нарезка плоского списка
        blocks = [ClusterBlock(lod_records[i:i+CHUNK_SIZE]) for i in range(0, len(lod_records), CHUNK_SIZE)]
        
        # 4. Упаковка данных автомата состояний
        for block in blocks:
            if not block.data_nodes:
                continue
                
            first_data = block.data_nodes[0]
            v1_safe, v2_safe = first_data["v1"], first_data["v2"]
            cluster_elements_count = len(block.data_nodes) + 1
            jump_v3 = (cluster_elements_count * NODE_SIZE) + 8
            
            # Узел 1: Навигационный (Аппаратный прыжок)
            idx_buffer.extend(struct.pack("<IIIffff", v1_safe, v2_safe, jump_v3, *block.bbox))
            # Узел 2: Заголовок кластера
            idx_buffer.extend(struct.pack("<IIffffI", 0, cluster_elements_count, *block.bbox, int(first_data["code"])))
            # Узел 3..N: Узлы данных
            for d in block.data_nodes:
                idx_buffer.extend(struct.pack("<IIffffI", d["v1"], d["v2"], *d["bbox"], int(d["code"])))
                
        # 5. Терминатор LOD уровня (8 нулевых байт)
        idx_buffer.extend(b'\x00' * 8)
        print(f"    - LOD {lod_index}: упаковано объектов {len(lod_records)}")
    
    payload_size = len(idx_buffer)
    with open(idx_out, "wb") as f:
        f.write(b'YZL\x00' + struct.pack("<I", payload_size) + b'\x00' * 24)
        f.write(idx_buffer)
        
    print(f"    Успешно сохранен {idx_out} ({payload_size} байт)")

def create_map_name(name, meta_records, out_file="map.name"):
    #Генерирует конфигурационный файл map.name с координатами центра карты.
    #Прошивка использует его для первоначального позиционирования камеры при открытии приложения.
    print(f"[>] Генерация {out_file}...")
    if not meta_records:
        print("    [-] Ошибка: Нет объектов для расчета центра карты.")
        return

    # Вычисляем экстремумы (Bounding Box) всей карты
    minx = min(r["bbox"][0] for r in meta_records)
    miny = min(r["bbox"][1] for r in meta_records)
    maxx = max(r["bbox"][2] for r in meta_records)
    maxy = max(r["bbox"][3] for r in meta_records)

    # Вычисляем геометрический центр
    center_lat = (miny + maxy) / 2.0
    center_lon = (minx + maxx) / 2.0

    # Записываем строго без пробелов, чтобы аппаратный парсер JSON в часах не сбоил
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(
            {"centerLat": center_lat, "centerLon": center_lon, "mapName": name}, 
            f, 
            separators=(',', ':')
        )
    print(f"    Успешно сохранен {out_file} (Центр: {center_lat:.5f}, {center_lon:.5f})")

def generate_background_landuse(roads_meta):
    """
    Генерирует слой landuse, состоящий из одного большого полигона,
    полностью накрывающего площадь всех дорог с запасом в 10%.
    Используется для обхода аппаратной защиты от 'пустых карт'.
    """
    print("[>] Генерация фонового полигона (Landuse)...")
    if not roads_meta:
        print("    [-] Ошибка: Нет объектов дорог для расчета фона.")
        return

    # 1. Вычисляем Bounding Box всех дорог
    minx = min(r["bbox"][0] for r in roads_meta)
    miny = min(r["bbox"][1] for r in roads_meta)
    maxx = max(r["bbox"][2] for r in roads_meta)
    maxy = max(r["bbox"][3] for r in roads_meta)

    # 2. Добавляем 10% margin, чтобы фон выходил за края экрана при скролле
    margin_x = (maxx - minx) * 0.1 if (maxx - minx) > 0 else 0.01
    margin_y = (maxy - miny) * 0.1 if (maxy - miny) > 0 else 0.01

    minx -= margin_x
    miny -= margin_y
    maxx += margin_x
    maxy += margin_y

    # 3. Формируем Замкнутый Контур (Closed Ring)
    # В бинарной геометрии полигон отличается от линии тем, 
    # что его последняя координата строго равна первой.
    bg_way = {
        "osm_id": "0000000001",
        "fclass": "meadow",
        "code": 7208, # Код луга (Meadow) из features.csv
        "name": "Background_Area",
        "points": [
            (minx, miny), # 1. Юго-Запад
            (minx, maxy), # 2. Северо-Запад
            (maxx, maxy), # 3. Северо-Восток
            (maxx, miny), # 4. Юго-Восток
            (minx, miny)  # 5. ВОЗВРАТ в Юго-Запад (Замыкание контура!)
        ]
    }

    # 4. Модифицируем глобальную таблицу Z-Culling на лету
    # Фон обязан отрисовываться на абсолютно всех масштабах от 1000м до 20м.
    # Поэтому мы принудительно назначаем ему порог LOD 2 (1000м).
    global DISPLAY_SCALES
    DISPLAY_SCALES[7208] = 1000

    # 5. Переиспользуем существующие компиляторы
    # Наша архитектура настолько универсальна, что переварит простой полигон
    # через ту же функцию, что и дороги (num_parts = 1).
    bg_meta = compile_mlp([bg_way], "landuse.mlp")
    compile_db(bg_meta, "landuse.db")
    compile_idx(bg_meta, "landuse.idx")
    
    print("    Фоновый полигон успешно скомпилирован!")

def create_empty_layer(layer_prefix):
    """
    Генерирует файлы-заглушки (.mlp, .db, .idx) байт-в-байт совпадающие 
    с оригинальными заводскими пустыми картами DT G1.
    Это исключает ошибки EOF и проверки контрольных сумм в железе.
    """
    print(f"[>] Генерация эталонной заводской заглушки: {layer_prefix}...")
    
    # Заводской пустой .mlp (80 байт, Payload Size = 0)
    # Содержит артефакты памяти (координаты Шэньчжэня)
    mlp_hex = (
        "595A4C00000000000000000400000000"
        "D41D8CD98F00B204E9800998ECF8427E"
        "A0B861411B1259427BD96D41FCD45A42"
        "00000000000000000000000000000000"
        "8BDDE3424F40B4418BDDE3424F40B441"
    )
    
    # Заводской пустой .idx (80 байт, Payload Size = 48)
    # Сигнатура YZL\x10 + 3 пустых блока SQT
    idx_hex = (
        "595A4C10300000000000000400000010"
        "E5F9D2228804251B5F9E3EAB298C30E5"
        "53515401010000000000000000000000"
        "53515401010000000000000000000000"
        "53515401010000000000000000000000"
    )
    
    # Заводской пустой .db (338 байт, Payload Size = 306)
    # Заголовок dBase III + 1 пустая запись на 145 байт
    db_hex = (
        "595A4C00320100000000000400000000"
        "D65E1C742D95963F147A4468DD25F93F"
        "035F071A01000000A100910000000000"
        "00000000000000000000000000000000"
        "6F736D5F696400000000004300000000"
        "0C000000000000000000000000000000"
        "636F6465000000000000004E00000000"
        "04000000000000000000000000000000"
        "66636C61737300000000004300000000"
        "1C000000000000000000000000000000"
        "6E616D65000000000000004300000000"
        "64000000000000000000000000000000"
        "0D" + "00" * 145
    )

    with open(f"{layer_prefix}.mlp", "wb") as f:
        f.write(bytearray.fromhex(mlp_hex))
    with open(f"{layer_prefix}.idx", "wb") as f:
        f.write(bytearray.fromhex(idx_hex))
    with open(f"{layer_prefix}.db", "wb") as f:
        f.write(bytearray.fromhex(db_hex))

    print(f"    Слой {layer_prefix} успешно заменен на заводскую пустышку.")

# ==============================================================================
# MAIN
# ==============================================================================
def main():
    if not os.path.exists("map.osm"):
        print("[-] Ошибка: Файл map.osm не найден в текущей папке.")
        return
        
    print("=========================================")
    print("DT G1 MAP COMPILER (v4.1 - Multi-LOD & Dummies)")
    print("=========================================")
    
    ways = parse_osm_geometry("map.osm")
    
    if not ways:
        print("[-] Ошибка: Не найдено валидных объектов для компиляции.")
        return
        
    # 1. Компиляция дорожной сети (Базовый рабочий слой)
    meta_records = compile_mlp(ways, "roads.mlp")
    compile_db(meta_records, "roads.db")
    compile_idx(meta_records, "roads.idx")
    
    # 2. Генерация файла конфигурации (Используем meta_records от дорог для центрирования)
    create_map_name("Custom_Map", meta_records, "map.name")
    
    # 3. Генерация слоев (Фон и пустышки)
    generate_background_landuse(meta_records) # Генерируем реальный полигон
    #create_empty_layer("landuse")
    create_empty_layer("water")
    create_empty_layer("pois") # Раскомментируйте, если нужен слой POI
    
    print("\n[УСПЕХ] Пакет карт собран! Можно копировать файлы на часы.")

if __name__ == "__main__":
    main()