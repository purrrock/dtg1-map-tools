#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Water Layer Calibration Grid Generator
============================================
Generates a regular grid of OSM polygons to check
hardware styles (Feature Codes) of hydrological objects (82xx series).
"""

import math

# Target coordinates (center of testing polygon)
LAT_CENTER = 53.7135
LON_CENTER = 28.4194

# List of "water" layer objects (based on Geofabrik Section 6.5 / 6.6)
# These tags are translated into the 82xx series (8211, 8212, 8221, etc.)
WATER_FEATURES = [
    {"natural": "water"},               # Base water (8211)
    {"landuse": "reservoir"},           # Reservoir (8212)
    {"waterway": "riverbank"},          # Riverbed (8213)
    {"natural": "glacier"},             # Glacier (8214)
    {"natural": "wetland"},             # Swamp/wetland (8221)
    {"natural": "bay"},                 # Bay
    {"natural": "water", "water": "lake"},  # Lake (clarification)
    {"natural": "water", "water": "river"}, # River (clarification)
    {"landuse": "basin"}                # Basin
]

def generate_calibration_grid(filename="water_calibration.osm"):
    # Conversion of degrees to meters for Mercator projection
    METER_PER_LAT = 111320.0
    METER_PER_LON = 111320.0 * math.cos(math.radians(LAT_CENTER))
    
    # Geometry of test polygon
    SIZE_M = 50.0  # Square size 100x100 meters
    GAP_M = 25.0    # Indent between squares 50 meters
    COLS = 3        # Number of columns in the matrix
    
    osm_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="dtg1_water_grid_fuzzer">'
    ]
    
    node_id = 1000
    way_id = 4000
    
    for idx, tags in enumerate(WATER_FEATURES):
        row = idx // COLS
        col = idx % COLS
        
        # Calculation of offset from center
        offset_lat_m = -row * (SIZE_M + GAP_M)
        offset_lon_m = col * (SIZE_M + GAP_M)
        
        base_lat = LAT_CENTER + (offset_lat_m / METER_PER_LAT)
        base_lon = LON_CENTER + (offset_lon_m / METER_PER_LON)
        
        d_lat = SIZE_M / METER_PER_LAT
        d_lon = SIZE_M / METER_PER_LON
        
        # Square vertices (clockwise traversal)
        nodes_coords = [
            (base_lat, base_lon),                   # Bottom left
            (base_lat + d_lat, base_lon),           # Top left
            (base_lat + d_lat, base_lon + d_lon),   # Top right
            (base_lat, base_lon + d_lon)            # Bottom right
        ]
        
        current_node_ids = []
        
        # Generation of XML <node>
        for lat, lon in nodes_coords:
            osm_lines.append(f'  <node id="{node_id}" lat="{lat:.7f}" lon="{lon:.7f}" version="1"/>')
            current_node_ids.append(node_id)
            node_id += 1
            
        # Generation of XML <way>
        osm_lines.append(f'  <way id="{way_id}" version="1">')
        
        # Binding of vertices
        for nid in current_node_ids:
            osm_lines.append(f'    <nd ref="{nid}"/>')
        # Closing contour
        osm_lines.append(f'    <nd ref="{current_node_ids[0]}"/>') 
        
        # Injection of tags
        for k, v in tags.items():
            osm_lines.append(f'    <tag k="{k}" v="{v}"/>')
        
        # Name generation for debugging
        tag_desc = " ".join([f"{k}={v}" for k, v in tags.items()])
        osm_lines.append(f'    <tag k="name" v="Style Test: {tag_desc}"/>')
        osm_lines.append('  </way>')
        
        way_id += 1
        
    osm_lines.append('</osm>')
    
    # Physical writing of file
    with open(filename, 'w', encoding='utf-8') as f:
        f.write('\n'.join(osm_lines))
        
    print(f"[*] Calibration grid generated: {filename}")
    print(f"[*] Number of test polygons: {len(WATER_FEATURES)}")

if __name__ == '__main__':
    generate_calibration_grid()