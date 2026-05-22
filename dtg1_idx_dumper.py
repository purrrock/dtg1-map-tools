#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 IDX Dumper (Версия 3.0 - BVH & Кластеры)
======================================================
Создает человекочитаемый CSV-дамп индексного файла (.idx),
корректно распознавая узлы ветвления (Branch), заголовки (Cluster Header)
и узлы данных (Data Nodes) на основе интеллектуального автомата состояний.
"""

import os
import struct
import argparse
from pathlib import Path

def dump_idx(file_path, out_csv):
    if not os.path.exists(file_path):
        print(f"[-] Файл {file_path} не найден.")
        return

    print(f"[>] Чтение файла {file_path}...")
    with open(file_path, "rb") as f:
        data = f.read()

    size = len(data)
    if size < 32:
        print("[-] Файл слишком мал для формата DT G1.")
        return

    with open(out_csv, "w", encoding="utf-8") as out:
        # Пишем заголовок CSV
        out.write("Offset_Hex,LOD_Level,NodeType,v1,v2,v3_or_Code,MinX,MinY,MaxX,MaxY,Raw_Hex\n")

        # 1. Читаем YZL (32 байта)
        yzl_raw = data[:32]
        out.write(f"0x00,-,YZL Header,,,,,,,,{yzl_raw.hex()}\n")

        offset = 32
        lod_level = 0

        while offset < size:
            # Ищем следующий SQT (заголовок уровня детализации)
            if data[offset:offset+4] == b"SQT\x01":
                param = struct.unpack("<I", data[offset+4:offset+8])[0]
                raw_hex = data[offset:offset+8].hex()
                out.write(f"{hex(offset)},{lod_level},SQT Header,,,Param:{param},,,,,{raw_hex}\n")
                offset += 8
                
                # Счетчик оставшихся узлов в текущем кластере
                data_nodes_left = 0

                # Ищем границу текущего LOD-уровня
                next_sqt = data.find(b"SQT\x01", offset)
                if next_sqt == -1:
                    next_sqt = size

                # Читаем блоки (узлы) внутри LOD-уровня
                while offset < next_sqt:
                    rem = next_sqt - offset
                    
                    # Если байт меньше размера узла (28) - это терминатор/паддинг
                    if rem < 28:
                        pad_raw = data[offset:next_sqt]
                        node_type = f"Padding ({rem} bytes)" if pad_raw == b'\x00' * rem else f"Unknown Tail ({rem} bytes)"
                        out.write(f"{hex(offset)},{lod_level},{node_type},,,,,,,,,{pad_raw.hex()}\n")
                        offset = next_sqt
                        break

                    node_raw = data[offset:offset+28]
                    # Быстро заглядываем в первые 8 байт, чтобы узнать v1 и v2
                    v1, v2 = struct.unpack("<II", node_raw[:8])
                    
                    # --- ЛОГИКА АВТОМАТА (STATE MACHINE) ---
                    if data_nodes_left > 0:
                        # Мы внутри кластера -> Это 100% Data Node (Дорога)
                        v1, v2, minx, miny, maxx, maxy, code = struct.unpack("<IIffffI", node_raw)
                        out.write(f"{hex(offset)},{lod_level},Data Node,{v1},{v2},{code},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{node_raw.hex()}\n")
                        data_nodes_left -= 1
                        
                    else:
                        # Мы НЕ в кластере -> Это либо Заголовок, либо Ветка (Branch)
                        if v1 == 0:
                            # Флаг 0 -> Заголовок кластера (Cluster Header)
                            v1, v2, minx, miny, maxx, maxy, code = struct.unpack("<IIffffI", node_raw)
                            out.write(f"{hex(offset)},{lod_level},Cluster Header,{v1},{v2},{code},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{node_raw.hex()}\n")
                            # Устанавливаем счетчик дорог (v2 включает сам заголовок, поэтому - 1)
                            data_nodes_left = v2 - 1 if v2 > 0 else 0
                        else:
                            # Флаг не 0 -> Навигационная ветка (Branch Node)
                            # У нее ДРУГАЯ структура: v3 на третьей позиции, Code отсутствует!
                            v1, v2, v3, minx, miny, maxx, maxy = struct.unpack("<IIIffff", node_raw)
                            out.write(f"{hex(offset)},{lod_level},Branch Node,{v1},{v2},{v3},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{node_raw.hex()}\n")

                    offset += 28

                lod_level += 1
            else:
                # На случай битых данных - перепрыгиваем до следующего SQT
                next_sqt = data.find(b"SQT\x01", offset)
                if next_sqt == -1:
                    next_sqt = size
                rem = next_sqt - offset
                raw = data[offset:offset+rem]
                out.write(f"{hex(offset)},{lod_level},Unknown Chunk,,,,,,,,, {raw.hex()}\n")
                offset += rem

    print(f"[+] Дамп успешно сохранен в {out_csv}")

def main():
    parser = argparse.ArgumentParser(description="DT G1 IDX Dumper (Версия 3.0 - BVH & Clusters)")
    parser.add_argument("idx_file", help="Путь к файлу .idx")
    args = parser.parse_args()

    input_path = Path(args.idx_file)
    output_path = input_path.with_name(f"{input_path.stem}_dump.csv")
    
    dump_idx(input_path, output_path)

if __name__ == "__main__":
    main()