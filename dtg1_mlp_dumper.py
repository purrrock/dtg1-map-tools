#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 MLP Dumper (Geometry Debugger)
====================================
Инструмент для реверс-инжиниринга бинарной геометрии полигонов платформы C175C1.
Читает YZL-заголовок, распаковывает BBox, массив частей (parts) и сырые координаты.
Выводит информацию в удобочитаемом текстовом формате для глубокого анализа.
"""

import os
import struct
import argparse

def dump_mlp(filepath):
    if not os.path.exists(filepath):
        print(f"[-] Файл {filepath} не найден.")
        return

    with open(filepath, "rb") as f:
        data = f.read()

    print(f"==================================================")
    print(f"Анализ файла: {filepath} (Размер: {len(data)} байт)")
    print(f"==================================================")

    # 1. Чтение глобального заголовка YZL (32 байта)
    magic = data[:4]
    payload_size = struct.unpack("<I", data[4:8])[0]
    print(f"[YZL HEADER] Magic: {magic}, Payload Size: {payload_size} байт")

    offset = 32
    record_idx = 1

    # 2. Итерация по бинарным записям геометрии
    while offset < len(data):
        # 2.1. Заголовок записи (8 байт: Record Number(BE) + Body Length(LE))
        if offset + 8 > len(data):
            print(f"[-] Ошибка: Неожиданный конец файла на смещении {offset}")
            break
            
        rec_num = struct.unpack(">I", data[offset:offset+4])[0]
        body_len = struct.unpack("<I", data[offset+4:offset+8])[0]
        
        print(f"\n--- [RECORD {record_idx} (Позиция {rec_num})] Смещение: {hex(offset)} ---")
        print(f"    Размер тела данных (Body Length): {body_len} байт")
        
        offset += 8
        body_end = offset + body_len
        
        # 2.2. Заголовок полигона (BBox + num_parts + num_points)
        # BBox (4 int32) = 16 байт
        minx, miny, maxx, maxy = struct.unpack("<iiii", data[offset:offset+16])
        offset += 16
        
        # Количество колец и общее количество точек
        num_parts, num_points = struct.unpack("<II", data[offset:offset+8])
        offset += 8
        
        print(f"    BBox (Int32 * 1M): MinX={minx}, MinY={miny}, MaxX={maxx}, MaxY={maxy}")
        print(f"    Количество колец (num_parts): {num_parts}")
        print(f"    Всего точек (num_points): {num_points}")
        
        # 2.3. Чтение массива индексов (Parts Array)
        parts = []
        for _ in range(num_parts):
            part_idx = struct.unpack("<I", data[offset:offset+4])[0]
            parts.append(part_idx)
            offset += 4
            
        print(f"    Массив индексов колец (parts): {parts}")
        
        # 2.4. Чтение сырых координат (Points Array)
        points = []
        for _ in range(num_points):
            px, py = struct.unpack("<ii", data[offset:offset+8])
            points.append((px, py))
            offset += 8
            
        # Проверка целостности записи
        if offset != body_end:
            print(f"    [!] ВНИМАНИЕ: Прочитано {offset}, а ожидалось {body_end}")
            offset = body_end # Принудительное выравнивание

        # 2.5. Форматированный вывод координат по кольцам
        for i in range(num_parts):
            start_idx = parts[i]
            # Определяем конец текущего кольца
            end_idx = parts[i+1] if i + 1 < num_parts else num_points
            ring = points[start_idx:end_idx]
            
            # Конвертируем обратно в градусы для читаемости
            ring_deg = [(p[0]/1_000_000, p[1]/1_000_000) for p in ring]
            
            # Простейшая проверка направления (через координаты 3х точек)
            # Внимание: это грубая оценка, точное направление мы уже считали в компиляторе.
            print(f"      -> Кольцо {i} (Точек: {len(ring)}). От {start_idx} до {end_idx-1}.")
            print(f"         Начало: {ring_deg[0]}")
            print(f"         Конец:  {ring_deg[-1]}")
            if ring_deg[0] != ring_deg[-1]:
                print(f"         [!] ОШИБКА: Кольцо {i} не замкнуто!")

        record_idx += 1

    print("\n[+] Дамп геометрии завершен.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DT G1 MLP Geometry Dumper")
    parser.add_argument("file", help="Путь к файлу .mlp (например, landuse.mlp)")
    args = parser.parse_args()
    
    dump_mlp(args.file)