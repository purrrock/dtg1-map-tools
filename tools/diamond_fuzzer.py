#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import xml.etree.ElementTree as ET
from xml.dom import minidom

# Global generation parameters
DIAMOND_WIDTH_METERS = 5.0       # Horizontal rhombus width
DIAMOND_HEIGHT_MULTIPLIER = 3.0   # Vertical axis multiplier
MAP_EXTENT_METERS = 1000.0        # Forced size of global SQT grid

class DTG1TopologyFuzzer:
    EARTH_RADIUS = 6378137.0

    def __init__(self, output_file: str = "map.osm"):
        self.output_file = output_file
        self.osm_root = ET.Element('osm', version='0.6', generator='DTG1_Topology_Fuzzer')
        self.node_id_counter = -1
        self.way_id_counter = -1

    def _calc_deltas_asymmetric(self, lat: float, radius_x: float, radius_y: float) -> tuple:
        """
        Asymmetric spherical calculation of coordinate delta.
        radius_x - longitude offset (East/West)
        radius_y - latitude offset (North/South)
        """
        d_lat = (radius_y / self.EARTH_RADIUS) * (180.0 / math.pi)
        d_lon = (radius_x / (self.EARTH_RADIUS * math.cos(math.radians(lat)))) * (180.0 / math.pi)
        return d_lat, d_lon

    def set_bounds(self, lat_center: float, lon_center: float, extent_meters: float) -> None:
        """Injection of global map boundaries to initialize SQT index."""
        d_lat, d_lon = self._calc_deltas_asymmetric(lat_center, extent_meters / 2.0, extent_meters / 2.0)
        ET.SubElement(self.osm_root, 'bounds', {
            'minlat': f"{lat_center - d_lat:.7f}",
            'minlon': f"{lon_center - d_lon:.7f}",
            'maxlat': f"{lat_center + d_lat:.7f}",
            'maxlon': f"{lon_center + d_lon:.7f}"
        })

    def add_stretched_diamond_poi(self, name: str, lon: float, lat: float, width_meters: float, height_multiplier: float) -> None:
        """Generation of vertically stretched rhombus (CW Winding)."""
        radius_x = width_meters / 2.0
        radius_y = (width_meters * height_multiplier) / 2.0
        
        d_lat, d_lon = self._calc_deltas_asymmetric(lat, radius_x, radius_y)

        # Calculation of angles (North -> East -> South -> West)
        vertices = [
            (lon, lat + d_lat),          # North (stretched)
            (lon + d_lon, lat),          # East
            (lon, lat - d_lat),          # South (stretched)
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

        # Closing nodes for a valid topological contour
        for nid in node_ids:
            ET.SubElement(way_elem, 'nd', ref=str(nid))
        ET.SubElement(way_elem, 'nd', ref=str(node_ids[0]))

        # Injection of attributes from the active features.csv list
        ET.SubElement(way_elem, 'tag', k='landuse', v='commercial')
        ET.SubElement(way_elem, 'tag', k='name', v=name)

    def write(self) -> None:
        """Formatting and XML dump."""
        xml_str = ET.tostring(self.osm_root, encoding='utf-8')
        parsed_xml = minidom.parseString(xml_str)
        pretty_xml = parsed_xml.toprettyxml(indent="  ")
        
        with open(self.output_file, "w", encoding="utf-8") as f:
            f.write('\n'.join([line for line in pretty_xml.split('\n') if line.strip()]))
        
        print(f"[SUCCESS] Topology (Stretched rhombuses {DIAMOND_HEIGHT_MULTIPLIER}x) generated: {self.output_file}")


if __name__ == "__main__":
    LAT_CENTER = 53.70502
    LON_CENTER = 28.41933

    fuzzer = DTG1TopologyFuzzer("map.osm")
    
    # Hard boundary marking to protect SQT matrix
    fuzzer.set_bounds(LAT_CENTER, LON_CENTER, extent_meters=MAP_EXTENT_METERS)
    
    test_pois = [
        ("Spring", LON_CENTER - 0.0005, LAT_CENTER + 0.0005),
        ("Shelter", LON_CENTER + 0.0005, LAT_CENTER - 0.0005),
        ("Center", LON_CENTER, LAT_CENTER)
    ]
    
    for name, lon, lat in test_pois:
        fuzzer.add_stretched_diamond_poi(
            name, lon, lat, 
            width_meters=DIAMOND_WIDTH_METERS, 
            height_multiplier=DIAMOND_HEIGHT_MULTIPLIER
        )
        
    fuzzer.write()