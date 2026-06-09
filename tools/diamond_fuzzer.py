#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import xml.etree.ElementTree as ET
from xml.dom import minidom

# Глобальные параметры генерации
DIAMOND_WIDTH_METERS = 5.0       # Горизонтальная ширина ромба
DIAMOND_HEIGHT_MULTIPLIER = 3.0   # Множитель вертикальной оси
MAP_EXTENT_METERS = 1000.0        # Принудительный размер глобальной сетки SQT

class DTG1TopologyFuzzer:
    EARTH_RADIUS = 6378137.0

    def __init__(self, output_file: str = "map.osm"):
        self.output_file = output_file
        self.osm_root = ET.Element('osm', version='0.6', generator='DTG1_Topology_Fuzzer')
        self.node_id_counter = -1
        self.way_id_counter = -1

    def _calc_deltas_asymmetric(self, lat: float, radius_x: float, radius_y: float) -> tuple:
        """
        Асимметричное сферическое вычисление дельты координат.
        radius_x - смещение по долготе (Восток/Запад)
        radius_y - смещение по широте (Север/Юг)
        """
        d_lat = (radius_y / self.EARTH_RADIUS) * (180.0 / math.pi)
        d_lon = (radius_x / (self.EARTH_RADIUS * math.cos(math.radians(lat)))) * (180.0 / math.pi)
        return d_lat, d_lon

    def set_bounds(self, lat_center: float, lon_center: float, extent_meters: float) -> None:
        """Инъекция глобальных границ карты для инициализации SQT-индекса."""
        d_lat, d_lon = self._calc_deltas_asymmetric(lat_center, extent_meters / 2.0, extent_meters / 2.0)
        ET.SubElement(self.osm_root, 'bounds', {
            'minlat': f"{lat_center - d_lat:.7f}",
            'minlon': f"{lon_center - d_lon:.7f}",
            'maxlat': f"{lat_center + d_lat:.7f}",
            'maxlon': f"{lon_center + d_lon:.7f}"
        })

    def add_stretched_diamond_poi(self, name: str, lon: float, lat: float, width_meters: float, height_multiplier: float) -> None:
        """Генерация ромба, вытянутого по вертикали (CW Winding)."""
        radius_x = width_meters / 2.0
        radius_y = (width_meters * height_multiplier) / 2.0
        
        d_lat, d_lon = self._calc_deltas_asymmetric(lat, radius_x, radius_y)

        # Вычисление углов (North -> East -> South -> West)
        vertices = [
            (lon, lat + d_lat),          # North (вытянутый)
            (lon + d_lon, lat),          # East
            (lon, lat - d_lat),          # South (вытянутый)
            (lon - d_lon, lat)           # West
        ]

        node_ids = []
        for v_lon, v_lat in vertices:
            ET.SubElement(self.osm_root, 'node', {
                'id': str(self.node_id_counter),
                'lat': f"{v_lat:.7f}", 'lon': f"{v_lon:.7f}", 'visible': 'true'
            })
            node_ids.append(self.node_id_counter)
            self.node_id_counter -= 1

        way_elem = ET.SubElement(self.osm_root, 'way', {
            'id': str(self.way_id_counter), 'visible': 'true'
        })
        self.way_id_counter -= 1

        # Замыкание узлов для валидного топологического контура
        for nid in node_ids:
            ET.SubElement(way_elem, 'nd', ref=str(nid))
        ET.SubElement(way_elem, 'nd', ref=str(node_ids[0]))

        # Инъекция атрибутов из активного списка features.csv
        ET.SubElement(way_elem, 'tag', k='landuse', v='commercial')
        ET.SubElement(way_elem, 'tag', k='name', v=name)

    def write(self) -> None:
        """Форматирование и дамп XML."""
        xml_str = ET.tostring(self.osm_root, encoding='utf-8')
        parsed_xml = minidom.parseString(xml_str)
        pretty_xml = parsed_xml.toprettyxml(indent="  ")
        
        with open(self.output_file, "w", encoding="utf-8") as f:
            f.write('\n'.join([line for line in pretty_xml.split('\n') if line.strip()]))
        
        print(f"[УСПЕХ] Топология (Вытянутые ромбы {DIAMOND_HEIGHT_MULTIPLIER}x) сгенерирована: {self.output_file}")


if __name__ == "__main__":
    LAT_CENTER = 53.714055
    LON_CENTER = 28.420172

    fuzzer = DTG1TopologyFuzzer("map.osm")
    
    # Жесткая разметка границ для защиты SQT-матрицы
    fuzzer.set_bounds(LAT_CENTER, LON_CENTER, extent_meters=MAP_EXTENT_METERS)
    
    test_pois = [
        ("Родник", LON_CENTER - 0.0005, LAT_CENTER + 0.0005),
        ("Укрытие", LON_CENTER + 0.0005, LAT_CENTER - 0.0005),
        ("Центр", LON_CENTER, LAT_CENTER)
    ]
    
    for name, lon, lat in test_pois:
        fuzzer.add_stretched_diamond_poi(
            name, lon, lat, 
            width_meters=DIAMOND_WIDTH_METERS, 
            height_multiplier=DIAMOND_HEIGHT_MULTIPLIER
        )
        
    fuzzer.write()