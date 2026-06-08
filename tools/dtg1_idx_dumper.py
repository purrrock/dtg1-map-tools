#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 IDX Dumper (CSV Export)
=============================
Утилита для создания читаемых CSV-дампов из бинарных пространственных индексов (.idx).
Рефакторинг под Спецификацию v4.0 (C-Union Node Architecture & Mode Switch).
"""

import os
import struct
import argparse

def dump_idx(file_path: str, out_csv: str) -> None:
    if not os.path.exists(file_path):
        print(f"[-] Файл {file_path} не найден.")
        return

    with open(file_path, "rb") as f:
        data = f.read()

    size = len(data)
    with open(out_csv, "w", encoding="utf-8") as out:
        # Унифицированный заголовок CSV. Столбец Code_or_v3Jump меняет смысл в зависимости от типа узла (C-Union)
        out.write("Offset_Hex,LOD_Level,Mode,NodeType,v1,v2,Code_or_v3Jump,MinX,MinY,MaxX,MaxY,Raw_Hex\n")
        
        # Глобальный контейнер YZL всегда занимает первые 32 байта памяти
        if size < 32:
            print("[-] Ошибка: Файл слишком мал для YZL-контейнера.")
            return
            
        out.write(f"0x00,-,-,YZL Header,,,,,,,,{data[:32].hex()}\n")

        offset = 32
        lod_level = 0

        while offset < size:
            # 1. Поиск сигнатуры уровня детализации (LOD SQT)
            if data[offset:offset+4] == b"SQT\x01":
                # Заголовок SQT занимает строго 16 байт: 
                # [Магия 4b] [Паддинг 4b] [Mode 4b] [Count 4b]
                header_raw = data[offset:offset+16]
                if len(header_raw) < 16:
                    break
                    
                mode, count = struct.unpack("<II", header_raw[8:16])
                mode_str = "Clustered" if mode == 1 else "Flat List"
                
                out.write(f"{hex(offset)},{lod_level},{mode_str},SQT Header,,,{count},,,,,{header_raw.hex()}\n")
                offset += 16
                
                # 2. Обработка пустого слоя (System Dummy / защита от EOF Panic)
                if count == 0:
                    lod_level += 1
                    continue
                    
                # 3. Парсинг узлов стейт-машины в зависимости от режима Mode
                if mode == 1:
                    # Режим 0x01 (Clustered): Сначала считывается Nav Node, затем вложенные Data Nodes
                    clusters_processed = 0
                    while clusters_processed < count and offset < size:
                        nav_raw = data[offset:offset+28]
                        if len(nav_raw) < 28:
                            break
                            
                        # Распаковка Nav Node (Паттерн C-Union: <IffffII)
                        # v3_jump: указатель аппаратного прыжка (размер кластера + 8 байт компенсации префетча)
                        # v1: зарезервировано (обычно 0)
                        # v2: количество Data Nodes в данном кластере
                        v3_jump, minx, miny, maxx, maxy, v1, v2 = struct.unpack("<IffffII", nav_raw)
                        out.write(f"{hex(offset)},{lod_level},{mode_str},Nav Node,{v1},{v2},{v3_jump},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{nav_raw.hex()}\n")
                        offset += 28
                        
                        data_nodes_count = v2
                        
                        for _ in range(data_nodes_count):
                            if offset >= size: break
                            data_raw = data[offset:offset+28]
                            
                            # Распаковка Data Node (Паттерн C-Union: <ffffIII)
                            # code: системный алиас стиля объекта (LUT)
                            # d_v1: абсолютное смещение геометрии в .mlp
                            # d_v2: индекс строки в атрибутивной БД .db
                            d_minx, d_miny, d_maxx, d_maxy, code, d_v1, d_v2 = struct.unpack("<ffffIII", data_raw)
                            out.write(f"{hex(offset)},{lod_level},{mode_str},Data Node,{d_v1},{d_v2},{code},{d_minx:.6f},{d_miny:.6f},{d_maxx:.6f},{d_maxy:.6f},{data_raw.hex()}\n")
                            offset += 28
                            
                        clusters_processed += 1
                        
                elif mode == 0:
                    # Режим 0x00 (Flat List): Только Data Nodes (без кластеризации и прыжков)
                    nodes_processed = 0
                    while nodes_processed < count and offset < size:
                        data_raw = data[offset:offset+28]
                        if len(data_raw) < 28:
                            break
                            
                        d_minx, d_miny, d_maxx, d_maxy, code, d_v1, d_v2 = struct.unpack("<ffffIII", data_raw)
                        out.write(f"{hex(offset)},{lod_level},{mode_str},Data Node,{d_v1},{d_v2},{code},{d_minx:.6f},{d_miny:.6f},{d_maxx:.6f},{d_maxy:.6f},{data_raw.hex()}\n")
                        offset += 28
                        nodes_processed += 1
                        
                lod_level += 1
                
            else:
                # 4. Fallback для битых или неизвестных блоков памяти (поиск сигнатуры следующего SQT)
                next_sqt = data.find(b"SQT\x01", offset)
                if next_sqt == -1: 
                    next_sqt = size
                rem = next_sqt - offset
                chunk_hex = data[offset:offset+rem].hex()
                out.write(f"{hex(offset)},{lod_level},Unknown,Unknown Chunk,,,,,,,,{chunk_hex}\n")
                offset += rem
                
    print(f"[+] Дамп успешно сохранен в {out_csv}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Декомпилятор пространственного индекса DT G1 (.idx)")
    parser.add_argument("input", help="Путь к файлу .idx")
    parser.add_argument("output", help="Путь для сохранения .csv дампа")
    args = parser.parse_args()
    
    dump_idx(args.input, args.output)