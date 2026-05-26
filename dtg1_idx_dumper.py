#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 IDX Dumper (CSV Export)
=============================
Утилита для создания читаемых CSV-дампов из бинарных .idx файлов.
Обновлена для работы со строго плоским списком кластеров (Flat List Paradigm).
"""

import os
import struct
import argparse
from pathlib import Path

def dump_idx(file_path, out_csv):
    if not os.path.exists(file_path):
        print(f"[-] Файл {file_path} не найден.")
        return

    with open(file_path, "rb") as f:
        data = f.read()

    size = len(data)
    with open(out_csv, "w", encoding="utf-8") as out:
        # Заголовок CSV
        out.write("Offset_Hex,LOD_Level,NodeType,v1,v2,v3_or_Code,MinX,MinY,MaxX,MaxY,Raw_Hex\n")
        
        # Глобальный YZL всегда занимает первые 32 байта
        out.write(f"0x00,-,YZL Header,,,,,,,,{data[:32].hex()}\n")

        offset = 32
        lod_level = 0

        while offset < size:
            # Поиск сигнатуры уровня детализации (LOD)
            if data[offset:offset+4] == b"SQT\x01":
                param = struct.unpack("<I", data[offset+4:offset+8])[0]
                out.write(f"{hex(offset)},{lod_level},SQT Header,,,Param:{param},,,,,{data[offset:offset+8].hex()}\n")
                offset += 8
                
                # Автомат состояний для плоского списка
                data_nodes_left = 0
                
                next_sqt = data.find(b"SQT\x01", offset)
                if next_sqt == -1: 
                    next_sqt = size

                while offset < next_sqt:
                    rem = next_sqt - offset
                    # Обработка терминаторов уровня и выравнивания
                    if rem < 28:
                        pad_raw = data[offset:next_sqt]
                        node_type = f"Padding ({rem} bytes)" if pad_raw == b'\x00' * rem else "Unknown Tail"
                        out.write(f"{hex(offset)},{lod_level},{node_type},,,,,,,,,{pad_raw.hex()}\n")
                        offset = next_sqt
                        break

                    node_raw = data[offset:offset+28]
                    # Считываем первые 8 байт для определения типа узла
                    v1, v2 = struct.unpack("<II", node_raw[:8])
                    
                    if data_nodes_left > 0:
                        # Состояние 3: Чтение массива узлов данных (Data Nodes)
                        v1, v2, minx, miny, maxx, maxy, code = struct.unpack("<IIffffI", node_raw)
                        out.write(f"{hex(offset)},{lod_level},Data Node,{v1},{v2},{code},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{node_raw.hex()}\n")
                        data_nodes_left -= 1
                    else:
                        # Состояние 1 или 2: Ожидаем либо Узел перехода, либо Заголовок кластера
                        if v1 == 0:
                            # Состояние 2: Заголовок кластера (v1 жестко равен 0)
                            v1, v2, minx, miny, maxx, maxy, code = struct.unpack("<IIffffI", node_raw)
                            out.write(f"{hex(offset)},{lod_level},Cluster Header,{v1},{v2},{code},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{node_raw.hex()}\n")
                            # v2 содержит (Кол-во_объектов + 1). Вычисляем, сколько узлов данных ждать дальше.
                            data_nodes_left = v2 - 1 if v2 > 0 else 0
                        else:
                            # Состояние 1: Узел перехода (Navigation Node)
                            # Распаковываем <IIIffff (v3 - это указатель аппаратного прыжка)
                            v1, v2, v3, minx, miny, maxx, maxy = struct.unpack("<IIIffff", node_raw)
                            out.write(f"{hex(offset)},{lod_level},Navigation Node,{v1},{v2},{v3},{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f},{node_raw.hex()}\n")

                    offset += 28
                lod_level += 1
            else:
                # Fallback для битых или неизвестных блоков памяти
                next_sqt = data.find(b"SQT\x01", offset)
                if next_sqt == -1: 
                    next_sqt = size
                rem = next_sqt - offset
                out.write(f"{hex(offset)},{lod_level},Unknown Chunk,,,,,,,,, {data[offset:offset+rem].hex()}\n")
                offset += rem
                
    print(f"[+] Дамп сохранен в {out_csv} (Режим: Плоский список)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("idx_file")
    args = parser.parse_args()
    
    in_path = Path(args.idx_file)
    out_path = in_path.with_name(f"{in_path.stem}_dump.csv")
    dump_idx(in_path, out_path)