#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Empty Map Layer Generator
===============================
Создает абсолютно пустые, но структурно валидные файлы 
roads.idx, roads.mlp и roads.db для тестирования парсера часов.
"""

import struct

YZL_SIZE = 32
DBF_HEADER_LEN = 161
RECORD_LEN = 145

def make_dbf_descriptor(name, length):
    desc = name.encode('ascii').ljust(11, b'\x00')
    desc += b'C' + b'\x00' * 4 + bytes([length]) + b'\x00' * 15
    return desc

def generate_empty_idx(filename):
    """Генерирует пустой IDX из 3-х деревьев с паддингами"""
    idx_buffer = bytearray()
    
    # 3 пустых дерева (Tree 0, 1, 2)
    for _ in range(3):
        idx_buffer.extend(b'SQT\x01')                # Сигнатура
        idx_buffer.extend(struct.pack("<I", 1))      # Параметр = 1
        idx_buffer.extend(b'\x00' * 8)               # Обязательный паддинг
        
    total_size = YZL_SIZE + len(idx_buffer)
    yzl = b'YZL\x00' + struct.pack("<I", total_size) + b'\x00' * 24
    
    with open(filename, "wb") as f:
        f.write(yzl)
        f.write(idx_buffer)
    print(f"[OK] Создан {filename} ({total_size} байт)")

def generate_empty_mlp(filename):
    """Генерирует пустой MLP (только YZL заголовок)"""
    total_size = YZL_SIZE
    yzl = b'YZL\x00' + struct.pack("<I", total_size) + b'\x00' * 24
    
    with open(filename, "wb") as f:
        f.write(yzl)
    print(f"[OK] Создан {filename} ({total_size} байт)")

def generate_empty_db(filename):
    """Генерирует пустую DB (Заголовок + 1 обязательная запись Record 0)"""
    # 1. Запись Record 0 (состоит из нулей)
    bin_records = b'\x00' * RECORD_LEN
    total_records = 1
    
    # 2. Формируем DBF заголовок
    dbf_header = bytearray()
    dbf_header += b'\x03\x00\x00\x00'
    dbf_header += struct.pack('<I', total_records)
    dbf_header += struct.pack('<H', DBF_HEADER_LEN)
    dbf_header += struct.pack('<H', RECORD_LEN)
    dbf_header += b'\x00' * 20
    dbf_header += make_dbf_descriptor("osm_id", 12)
    dbf_header += make_dbf_descriptor("code", 4)
    dbf_header += make_dbf_descriptor("fclass", 28)
    dbf_header += make_dbf_descriptor("name", 100)
    dbf_header += b'\x0D'
    
    # 3. YZL заголовок
    total_file_size = YZL_SIZE + DBF_HEADER_LEN + len(bin_records)
    yzl_header = b'YZL\x00' + struct.pack('<I', total_file_size) + b'\x00' * 24
    
    with open(filename, 'wb') as f:
        f.write(yzl_header)
        f.write(dbf_header)
        f.write(bin_records)
    print(f"[OK] Создан {filename} ({total_file_size} байт)")

if __name__ == "__main__":
    print("Генерация пустых файлов слоя roads...")
    generate_empty_idx("roads.idx")
    generate_empty_mlp("roads.mlp")
    generate_empty_db("roads.db")
    print("Готово! Загрузите эти файлы на часы вместе с landuse и map.name.")