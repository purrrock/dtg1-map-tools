#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Multipolygon Fuzzer
=========================
Генератор сложных топологических структур для тестирования массива 'parts' 
в формате бинарной геометрии .mlp.
Создает два мультиполигона: Лес с поляной (дыркой) и Озеро с островом.
"""

import math

LAT_CENTER = 53.7135
LON_CENTER = 28.4194

def main():
    METER_PER_LAT = 111320.0
    METER_PER_LON = 111320.0 * math.cos(math.radians(LAT_CENTER))

    # Настройки геометрии
    OUTER_SIZE = 150.0  # Внешний квадрат 150х150 метров
    INNER_SIZE = 50.0   # Внутренняя дырка 50х50 метров
    OFFSET = 150.0      # Смещение от центра для объектов (вправо/влево)

    osm_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="dtg1_multipoly_fuzzer">'
    ]
    
    global_node_id = 1
    global_way_id = 4000
    global_rel_id = 5000

    def create_ring(lat_c, lon_c, size_m):
        """Генерирует 4 узла и замкнутый way (кольцо). Возвращает ID созданного way."""
        nonlocal global_node_id, global_way_id
        
        half_lat = (size_m / 2.0) / METER_PER_LAT
        half_lon = (size_m / 2.0) / METER_PER_LON

        min_lat, max_lat = lat_c - half_lat, lat_c + half_lat
        min_lon, max_lon = lon_c - half_lon, lon_c + half_lon

        # Генерируем 4 узла
        n1 = global_node_id; global_node_id += 1
        n2 = global_node_id; global_node_id += 1
        n3 = global_node_id; global_node_id += 1
        n4 = global_node_id; global_node_id += 1

        osm_lines.extend([
            f'  <node id="{n1}" lat="{min_lat:.7f}" lon="{min_lon:.7f}" version="1"/>',
            f'  <node id="{n2}" lat="{max_lat:.7f}" lon="{min_lon:.7f}" version="1"/>',
            f'  <node id="{n3}" lat="{max_lat:.7f}" lon="{max_lon:.7f}" version="1"/>',
            f'  <node id="{n4}" lat="{min_lat:.7f}" lon="{max_lon:.7f}" version="1"/>'
        ])

        # Замыкаем в way (без тегов! Теги будут в relation)
        w_id = global_way_id
        global_way_id += 1
        
        osm_lines.extend([
            f'  <way id="{w_id}" version="1">',
            f'    <nd ref="{n1}"/>',
            f'    <nd ref="{n2}"/>',
            f'    <nd ref="{n3}"/>',
            f'    <nd ref="{n4}"/>',
            f'    <nd ref="{n1}"/>', # Замыкание кольца
            f'  </way>'
        ])
        return w_id

    print("[>] Генерация мультиполигонов (Лес и Озеро)...")

    # ==========================================
    # ОБЪЕКТ 1: ЛЕС С ОТВЕРСТИЕМ (Слева от центра)
    # ==========================================
    forest_lon = LON_CENTER - (OFFSET / METER_PER_LON)
    forest_outer_way = create_ring(LAT_CENTER, forest_lon, OUTER_SIZE)
    forest_inner_way = create_ring(LAT_CENTER, forest_lon, INNER_SIZE)

    osm_lines.extend([
        f'  <relation id="{global_rel_id}" version="1">',
        f'    <member type="way" ref="{forest_outer_way}" role="outer"/>',
        f'    <member type="way" ref="{forest_inner_way}" role="inner"/>',
        f'    <tag k="type" v="multipolygon"/>',
        f'    <tag k="landuse" v="forest"/>',
        f'    <tag k="name" v="Forest with a Hole"/>',
        f'  </relation>'
    ])
    global_rel_id += 1

    # ==========================================
    # ОБЪЕКТ 2: ОЗЕРО С ОСТРОВОМ (Справа от центра)
    # ==========================================
    lake_lon = LON_CENTER + (OFFSET / METER_PER_LON)
    lake_outer_way = create_ring(LAT_CENTER, lake_lon, OUTER_SIZE)
    lake_inner_way = create_ring(LAT_CENTER, lake_lon, INNER_SIZE)

    osm_lines.extend([
        f'  <relation id="{global_rel_id}" version="1">',
        f'    <member type="way" ref="{lake_outer_way}" role="outer"/>',
        f'    <member type="way" ref="{lake_inner_way}" role="inner"/>',
        f'    <tag k="type" v="multipolygon"/>',
        f'    <tag k="natural" v="water"/>',
        f'    <tag k="name" v="Lake with an Island"/>',
        f'  </relation>'
    ])

    osm_lines.append('</osm>')

    with open("map.osm", "w", encoding="utf-8") as f:
        f.write("\n".join(osm_lines))

    print("[+] Сгенерирован map.osm (2 relation, 4 ways, 16 nodes).")

if __name__ == "__main__":
    main()