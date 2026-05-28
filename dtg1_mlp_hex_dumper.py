#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 MLP Hex Dumper (Deep Geometry Analysis)
=============================================
Извлекает полигоны с их сырыми Hex-дампами по блокам.
Специально для поиска скрытых флагов мультиполигонов.
"""

import os
import struct
import argparse

def dump_mlp_hex(filepath):
    if not os.path.exists(filepath):
        print(f"[-] Файл {filepath} не найден.")
        return

    with open(filepath, "rb") as f:
        data = f.read()

    print(f"==================================================")
    print(f"HEX-АНАЛИЗ ФАЙЛА: {filepath} (Размер: {len(data)} байт)")
    print(f"==================================================")

    payload_size = struct.unpack("<I", data[4:8])[0]
    print(f"[YZL HEADER] Payload Size: {payload_size} байт. Hex: {data[:32].hex().upper()}")

    offset = 32
    record_idx = 1

    while offset < len(data):
        if offset + 8 > len(data): break
            
        rec_num = struct.unpack(">I", data[offset:offset+4])[0]
        body_len = struct.unpack("<I", data[offset+4:offset+8])[0]
        
        # Фильтруем вывод: нас интересуют только мультиполигоны (где частей > 1)
        # Если вы хотите дампить вообще всё, закомментируйте проверку num_parts ниже
        
        body_start = offset + 8
        body_end = body_start + body_len
        
        if body_end > len(data): break

        # Читаем BBox, num_parts, num_points
        minx, miny, maxx, maxy = struct.unpack("<iiii", data[body_start : body_start+16])
        num_parts, num_points = struct.unpack("<II", data[body_start+16 : body_start+24])

        # Выводим только записи с дырками/островами (num_parts >= 2)
        if num_parts >= 2:
            print(f"\n--- [RECORD {record_idx}] Мультиполигон найден! Смещение: {hex(offset)} ---")
            print(f"  Заголовок (Record+Length): {data[offset:offset+8].hex().upper()}")
            print(f"  BBox (16 байт):          {data[body_start : body_start+16].hex().upper()}")
            print(f"  Num Parts & Points:      {data[body_start+16 : body_start+24].hex().upper()} -> (Parts: {num_parts}, Points: {num_points})")
            
            # Читаем массив parts
            parts_start = body_start + 24
            parts_end = parts_start + (num_parts * 4)
            parts = struct.unpack(f"<{num_parts}I", data[parts_start:parts_end])
            
            print(f"  Parts Array Hex:         {data[parts_start:parts_end].hex().upper()} -> Индексы: {parts}")
            
            # Читаем первые координаты для сверки
            points_hex = data[parts_end : body_end].hex().upper()
            # Покажем первые 32 байта координат и последние 16
            if len(points_hex) > 64:
                print(f"  Points Hex (начало):     {points_hex[:64]} ...")
                print(f"  Points Hex (конец):      ... {points_hex[-32:]}")
            else:
                print(f"  Points Hex:              {points_hex}")

        offset = body_end
        record_idx += 1

    print("\n[+] Дамп завершен. Найдите в выводе нужный остров и скопируйте сюда.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="Путь к заводскому .mlp")
    args = parser.parse_args()
    dump_mlp_hex(args.file)