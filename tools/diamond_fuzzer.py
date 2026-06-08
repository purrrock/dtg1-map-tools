#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import xml.etree.ElementTree as ET
from xml.dom import minidom

class DiamondPOIFuzzer:
    """
    Генератор OSM XML для подмены POI точечного слоя на полигоны слоя landuse.
    Обеспечивает формирование ромбов размером 5 метров для обхода аппаратного 
    подавления текста прошивкой ATS3085S.
    """
    EARTH_RADIUS = 6378137.0  # Радиус Земли в метрах (WGS-84)

    def __init__(self, output_file: str = "map.osm", size_meters: float = 10.0):
        self.output_file = output_file
        self.radius_meters = size_meters / 2.0
        self.osm_root = ET.Element('osm', version='0.6', generator='DTG1_Fuzzer')
        self.node_id_counter = -1
        self.way_id_counter = -1

    def _calc_deltas(self, lat: float) -> tuple:
        """
        Вычисляет смещение в градусах для заданного расстояния в метрах 
        с учетом сферической проекции.
        """
        d_lat = (self.radius_meters / self.EARTH_RADIUS) * (180.0 / math.pi)
        d_lon = (self.radius_meters / (self.EARTH_RADIUS * math.cos(math.radians(lat)))) * (180.0 / math.pi)
        return d_lat, d_lon

    def add_poi(self, name: str, lon: float, lat: float) -> None:
        """
        Генерирует 5 узлов (4 вершины + 1 замыкающий) и полигон (way) ромба.
        Обход выполняется строго по часовой стрелке (CW).
        """
        d_lat, d_lon = self._calc_deltas(lat)

        # Вычисление вершин (Север, Восток, Юг, Запад)
        vertices = [
            (lon, lat + d_lat),          # 1. Top (North)
            (lon + d_lon, lat),          # 2. Right (East)
            (lon, lat - d_lat),          # 3. Bottom (South)
            (lon - d_lon, lat),          # 4. Left (West)
            (lon, lat + d_lat)           # 5. Top (Close Polygon)
        ]

        node_refs = []

        # Генерация <node>
        for v_lon, v_lat in vertices:
            node_elem = ET.SubElement(self.osm_root, 'node', {
                'id': str(self.node_id_counter),
                'lat': f"{v_lat:.7f}",
                'lon': f"{v_lon:.7f}",
                'visible': 'true'
            })
            node_refs.append(self.node_id_counter)
            self.node_id_counter -= 1

        # Генерация <way>
        way_elem = ET.SubElement(self.osm_root, 'way', {
            'id': str(self.way_id_counter),
            'visible': 'true'
        })
        self.way_id_counter -= 1

        # Линковка вершин
        for ref in node_refs:
            ET.SubElement(way_elem, 'nd', ref=str(ref))

        # Инъекция тегов для маппинга в code 7209 (Pink) и вывода текста
        ET.SubElement(way_elem, 'tag', k='landuse', v='commercial')
        ET.SubElement(way_elem, 'tag', k='name', v=name)

    def write(self) -> None:
        """Форматирует и записывает XML-дерево в файл."""
        xml_str = ET.tostring(self.osm_root, encoding='utf-8')
        parsed_xml = minidom.parseString(xml_str)
        pretty_xml = parsed_xml.toprettyxml(indent="  ")
        
        with open(self.output_file, "w", encoding="utf-8") as f:
            # Убираем пустые строки, которые генерирует minidom
            f.write('\n'.join([line for line in pretty_xml.split('\n') if line.strip()]))
        
        print(f"[УСПЕХ] Сгенерирован файл {self.output_file}")


# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    fuzzer = DiamondPOIFuzzer("map.osm", size_meters=5.0)
    
    # Тестовый набор точек для велопохода
    test_pois = [
        ("Родник 'Холодный'", 28.4194, 53.7135),
        ("Укрытие от дождя", 28.4195635, 53.7136558),
        ("Веломастерская", 28.43, 53.714)
    ]
    
    for name, lon, lat in test_pois:
        fuzzer.add_poi(name, lon, lat)
        
    fuzzer.write()