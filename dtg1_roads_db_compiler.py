#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler: Roads DB Generator
======================================
Извлекает именованные дороги из map.osm и компилирует roads.db
"""

import os
import struct
import xml.etree.ElementTree as ET

# Маппинг тегов OSM 'highway' во внутренние коды DT G1 (примерный)
# На базе дампов: residential=5122, tertiary=5115, service=5142 и т.д.
HIGHWAY_CODES = {
    "motorway": "5111",
    "trunk": "5112",
    "primary": "5113",
    "secondary": "5114",
    "tertiary": "5115",
    "unclassified": "5121",
    "residential": "5122",
    "living_street": "5122",
    "pedestrian": "5122",
    "service": "5142",
    "track": "5145",
    "path": "5145",
    "footway": "5145",
    "cycleway": "5146"
}
DEFAULT_CODE = "5142" # Код по умолчанию, если тип не найден

YZL_SIZE = 32
DBF_HEADER_LEN = 161
RECORD_LEN = 145

def make_string_field(text, length):
    """Кодирует строку в UTF-8, обрезает и добивает нулями до нужной длины"""
    encoded = str(text).encode('utf-8')[:length]
    return encoded.ljust(length, b'\x00')

def make_dbf_field_descriptor(name, length):
    """Формирует 32-байтный дескриптор поля для DBF заголовка"""
    desc = name.encode('ascii').ljust(11, b'\x00')
    desc += b'C'                 # Field type (Character)
    desc += b'\x00' * 4          # Data address
    desc += bytes([length])      # Field length
    desc += b'\x00' * 15         # Reserved
    return desc

def parse_osm_roads(osm_file):
    """Извлекает только именованные дороги из OSM файла (с приоритетом int_name)"""
    records = []
    print(f"[>] Парсинг {osm_file}...")
    
    context = ET.iterparse(osm_file, events=('end',))
    for event, elem in context:
        if elem.tag == 'way':
            tags = {child.attrib['k']: child.attrib['v'] for child in elem.findall('tag')}
            
            # Проверяем, что это дорога
            if 'highway' in tags:
                # Логика приоритета имени: сначала ищем int_name, затем name
                road_name = tags.get('int_name', '').strip()
                if not road_name:
                    road_name = tags.get('name', '').strip()
                
                # Если хоть какое-то имя нашлось — добавляем в базу
                if road_name:
                    osm_id = elem.attrib['id']
                    fclass = tags['highway']
                    code = HIGHWAY_CODES.get(fclass, DEFAULT_CODE)
                    
                    records.append({
                        "osm_id": osm_id,
                        "code": code,
                        "fclass": fclass,
                        "name": road_name
                    })
            
            elem.clear() # Очистка памяти для работы с гигантскими файлами OSM
            
    print(f"    Найдено именованных дорог: {len(records)}")
    return records

def compile_roads_db(records, out_file):
    print(f"[>] Компиляция {out_file}...")
    
    # 1. Формируем бинарные записи
    bin_records = bytearray()
    
    # Record 0: Обязательная пустышка для слоя дорог (заполнена 0x00)
    bin_records += b'\x00' * RECORD_LEN
    
    # Record 1..N: Валидные записи
    for rec in records:
        record_bytes = bytearray()
        record_bytes += b'\x20'  # 0x20 = Valid flag
        record_bytes += make_string_field(rec["osm_id"], 12)
        record_bytes += make_string_field(rec["code"], 4)
        record_bytes += make_string_field(rec["fclass"], 28)
        record_bytes += make_string_field(rec["name"], 100)
        
        assert len(record_bytes) == RECORD_LEN
        bin_records += record_bytes

    total_records = len(records) + 1
    
    # 2. Формируем DBF заголовок (161 байт)
    dbf_header = bytearray()
    dbf_header += b'\x03' # Версия DBF
    dbf_header += b'\x00\x00\x00' # Дата (YY MM DD)
    dbf_header += struct.pack('<I', total_records) # Количество записей (UInt32 LE)
    dbf_header += struct.pack('<H', DBF_HEADER_LEN) # Длина заголовка (UInt16 LE)
    dbf_header += struct.pack('<H', RECORD_LEN) # Длина записи (UInt16 LE)
    dbf_header += b'\x00' * 20 # Reserved
    
    # Дескрипторы полей (4 шт * 32 байта)
    dbf_header += make_dbf_field_descriptor("osm_id", 12)
    dbf_header += make_dbf_field_descriptor("code", 4)
    dbf_header += make_dbf_field_descriptor("fclass", 28)
    dbf_header += make_dbf_field_descriptor("name", 100)
    
    # Терминатор заголовка DBF
    dbf_header += b'\x0D'
    
    assert len(dbf_header) == DBF_HEADER_LEN
    
    # 3. Формируем YZL заголовок (32 байта)
    total_file_size = YZL_SIZE + DBF_HEADER_LEN + len(bin_records)
    yzl_header = bytearray()
    yzl_header += b'YZL\x00'
    yzl_header += struct.pack('<I', total_file_size)
    yzl_header += b'\x00' * 24
    
    # 4. Сохраняем файл
    with open(out_file, 'wb') as f:
        f.write(yzl_header)
        f.write(dbf_header)
        f.write(bin_records)
        
    print(f"    Успешно сохранено: {out_file} ({total_file_size} байт)")
    print(f"    Всего записей (включая Record 0): {total_records}")

if __name__ == "__main__":
    if not os.path.exists("map.osm"):
        print("Ошибка: Файл map.osm не найден в текущей папке.")
    else:
        db_records = parse_osm_roads("map.osm")
        compile_roads_db(db_records, "roads.db")