#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler: Roads IDX & DB Generator
============================================
Версия с исправленной архитектурой пространственного индекса.
Реализована генерация плоского списка 
согласно поведению оригинальной прошивки часов. Исправлен баг "v2=0 Underflow".
"""

import os
import json
import struct

YZL_SIZE = 32
SQT_HEADER_SIZE = 8
NODE_SIZE = 28
CHUNK_SIZE = 15

DBF_HEADER_LEN = 161
RECORD_LEN = 145

class ClusterBlock:
    """Плоский блок, содержащий до 15 объектов данных и их общие границы."""
    def __init__(self, data_nodes):
        self.data_nodes = data_nodes
        # Вычисляем общие границы (Bounding Box) для всего кластера
        minx = min(n["bbox"][0] for n in data_nodes)
        miny = min(n["bbox"][1] for n in data_nodes)
        maxx = max(n["bbox"][2] for n in data_nodes)
        maxy = max(n["bbox"][3] for n in data_nodes)
        self.bbox = [minx, miny, maxx, maxy]

def serialize_flat_clusters(blocks, buffer):
    """
    Формирует строго плоский список кластеров, 
    полностью имитируя структуру заводских карт DT G1.
    """
    for block in blocks:
        # Извлекаем легитимные данные первого объекта для обмана валидатора часов
        first_data = block.data_nodes[0]
        v1_safe = first_data["v1"]
        v2_safe = first_data["v2"]
        
        # Кол-во элементов в кластере: узлы данных + 1 (сам Cluster Header)
        cluster_elements_count = len(block.data_nodes) + 1
        
        # Точная заводская формула указателя прыжка: (Кол-во_элементов * 28) + 8
        jump_v3 = (cluster_elements_count * NODE_SIZE) + 8
        
        # 1. Записываем Узел перехода (Branch/Navigation Node)
        # v1 и v2 берутся от реального узла, чтобы избежать краша (db_index = 0 - 1)
        buffer.extend(struct.pack("<IIIffff", v1_safe, v2_safe, jump_v3, *block.bbox))
        
        # 2. Записываем Заголовок кластера (Cluster Header)
        # v1 всегда 0, v2 равно количеству элементов группы
        code = int(first_data["code"])
        buffer.extend(struct.pack("<IIffffI", 0, cluster_elements_count, *block.bbox, code))
        
        # 3. Записываем Узлы данных (Data Nodes)
        for d in block.data_nodes:
            buffer.extend(struct.pack("<IIffffI", d["v1"], d["v2"], *d["bbox"], int(d["code"])))

def make_string_field(text, length):
    """Подготавливает строку фиксированной длины для DBF-записи."""
    return str(text).encode('utf-8')[:length].ljust(length, b'\x00')

def make_dbf_descriptor(name, length):
    """Создает бинарный дескриптор поля базы данных dBase III."""
    desc = name.encode('ascii').ljust(11, b'\x00')
    desc += b'C' + b'\x00' * 4 + bytes([length]) + b'\x00' * 15
    return desc

def compile_db(db_records, out_file):
    """Компилирует атрибутивную базу данных в формате dBase III."""
    # Нулевая запись (Record 0) строго обязана быть пустой
    bin_records = b'\x00' * RECORD_LEN  
    
    for rec in db_records:
        record_bytes = bytearray(b'\x20') # Пробел (валидная запись в DBF)
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
    
    with open(out_file, 'wb') as f:
        f.write(b'YZL\x00' + struct.pack('<I', YZL_SIZE + DBF_HEADER_LEN + len(bin_records)) + b'\x00' * 24)
        f.write(dbf_header)
        f.write(bin_records)

def create_map_name(name, items, out_file):
    """Генерирует метаданные JSON с вычислением географического центра."""
    if not items: return
    minx, miny = min(i["bbox"][0] for i in items), min(i["bbox"][1] for i in items)
    maxx, maxy = max(i["bbox"][2] for i in items), max(i["bbox"][3] for i in items)
    with open(out_file, "w", encoding="utf-8") as f:
        # Важно: без пробелов после разделителей
        json.dump({"centerLat": (miny+maxy)/2.0, "centerLon": (minx+maxx)/2.0, "mapName": name}, f, separators=(',', ':'))

def main():
    if not os.path.exists("roads_meta.json"): 
        print("[-] Ошибка: roads_meta.json не найден. Сначала запустите mlp_compiler.")
        return
        
    with open("roads_meta.json", 'r', encoding='utf-8') as f: 
        items = json.load(f)

    create_map_name("DT_Map", items, "map.name")

    db_records, db_counter = [], 2 
    for item in items:
        if item.get("name"):
            item["v2"] = db_counter
            db_counter += 1
            db_records.append(item)
        else: 
            item["v2"] = 1 # Ссылка на пустой Record 0 для безымянных объектов

    # Нарезаем плоские блоки по 15 объектов
    blocks = [ClusterBlock(items[i:i+CHUNK_SIZE]) for i in range(0, len(items), CHUNK_SIZE)]

    # Сборка буфера IDX файла
    idx_buffer = bytearray(b'SQT\x01' + struct.pack("<I", 1))
    
    # Сериализуем как плоский массив чанков
    serialize_flat_clusters(blocks, idx_buffer)
    
    # Терминатор LOD 0 уровня
    idx_buffer.extend(b'\x00' * 8)
    
    # Заглушки для уровней LOD 1 и LOD 2
    for _ in range(2): 
        idx_buffer.extend(b'SQT\x01' + struct.pack("<I", 1) + b'\x00' * 8)
    
    # Запись YZL заголовка и содержимого в файл
    with open("roads.idx", "wb") as f:
        f.write(b'YZL\x00' + struct.pack("<I", YZL_SIZE + len(idx_buffer)) + b'\x00' * 24)
        f.write(idx_buffer)

    compile_db(db_records, "roads.db")
    print("\n[УСПЕХ] Индекс собран в виде плоского списка!")

if __name__ == "__main__":
    main()