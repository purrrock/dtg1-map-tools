#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Landuse Layer Calibration Grid Generator
==============================================
Generates an OSM test matrix of closed polygons to check
hardware support for rendering landuse types (fill colors,
patterns, z-index of polygons) by the ATS3085S graphics pipeline.

Geometry architecture:
- A closed contour (Closed Way) of 5 nodes is formed (last = first).
- Vertex traversal (Winding) is performed clockwise (CW), which is
  the standard for outer (Outer) polygons in the target architecture.
"""

import csv
import math
import sys
import os

# Base coordinates (center of the starting polygon)
LAT_CENTER = 53.7135
LON_CENTER = 28.4194

# Physical grid parameters
POLYGON_SIZE_METERS = 5  # Square edge size
GAP_METERS = 5       # Gap between neighboring polygons

def get_landuse_features(csv_path="features_factory.csv"):
    landuse = []
    if not os.path.exists(csv_path):
        print(f"[-] Error: File not found.")
        sys.exit(1)
        
    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter=';')
        next(reader, None) # Skip header
        
        for row in reader:
            if len(row) < 6:
                continue
                
            layer = row[4].strip()
            # Strictly filter landuse layer
            if layer == 'landuse':
                fclass = row[1].strip()
                osm_tags_raw = row[5].strip()
                
                tags = {}
                # Parsing composite tags (comma separator)
                for tag_pair in osm_tags_raw.split(','):
                    if '=' in tag_pair:
                        k, v = tag_pair.split('=', 1)
                        tags[k.strip()] = v.strip()
                
                if tags:
                    landuse.append({'fclass': fclass, 'tags': tags})
                    
    return landuse

def generate_landuse_grid(filename="landuse_calibration.osm", csv_path="features_factory.csv"):
    features = get_landuse_features(csv_path)
    total_features = len(features)
    
    if total_features == 0:
        print("[-] Error: No 'landuse' layer objects found in LUT configuration.")
        return

    # Calculation of square matrix dimension
    grid_size = math.ceil(math.sqrt(total_features))
    
    # Grid cell step (polygon + gap)
    step_total_meters = POLYGON_SIZE_METERS + GAP_METERS

    # Projection approximation (R_earth ~ 6378 km)
    lat_factor = 111320.0
    lon_factor = 111320.0 * math.cos(math.radians(LAT_CENTER))
    
    step_lat_deg = step_total_meters / lat_factor
    step_lon_deg = step_total_meters / lon_factor
    
    poly_lat_deg = POLYGON_SIZE_METERS / lat_factor
    poly_lon_deg = POLYGON_SIZE_METERS / lon_factor

    osm_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="DTG1_Landuse_Fuzzer">',
        f'  <bounds minlat="{LAT_CENTER}" minlon="{LON_CENTER}" maxlat="{LAT_CENTER + step_lat_deg * grid_size}" maxlon="{LON_CENTER + step_lon_deg * grid_size}"/>'
    ]

    # Negative IDs to bypass conflicts
    node_id = -1
    way_id = -1
    
    for idx, feature in enumerate(features):
        row = idx // grid_size
        col = idx % grid_size
        
        # Calculate coordinates of the Bottom-Left corner of the polygon
        base_lat = LAT_CENTER + (row * step_lat_deg)
        base_lon = LON_CENTER + (col * step_lon_deg)
        
        # Square vertices (clockwise traversal)
        nodes_coords = [
            (base_lat, base_lon),                                # N1: Bottom-Left
            (base_lat + poly_lat_deg, base_lon),                 # N2: Top-Left
            (base_lat + poly_lat_deg, base_lon + poly_lon_deg),  # N3: Top-Right
            (base_lat, base_lon + poly_lon_deg)                  # N4: Bottom-Right
        ]
        
        current_node_ids = []
        
        # Generating XML nodes
        for lat, lon in nodes_coords:
            osm_lines.append(f'  <node id="{node_id}" lat="{lat:.7f}" lon="{lon:.7f}" version="1"/>')
            current_node_ids.append(node_id)
            node_id -= 1
            
        # Assembling closed contour (Closed Way)
        osm_lines.append(f'  <way id="{way_id}" version="1">')
        for nid in current_node_ids:
            osm_lines.append(f'    <nd ref="{nid}"/>')
        # Closing to the first node
        osm_lines.append(f'    <nd ref="{current_node_ids[0]}"/>')
        
        # Injection of attributes from LUT
        for k, v in feature['tags'].items():
            osm_lines.append(f'    <tag k="{k}" v="{v}"/>')
            
        # Injection of text label for visual fill identification
        osm_lines.append(f'    <tag k="name" v="{feature["fclass"]}"/>')
        
        osm_lines.append('  </way>')
        way_id -= 1

    osm_lines.append('</osm>')

    with open(filename, 'w', encoding='utf-8') as f:
        f.write("\n".join(osm_lines))
        
    print(f"[+] Generated landuse calibration matrix {grid_size}x{grid_size}.")
    print(f"[+] Total polygons generated: {total_features}.")
    print(f"[+] Parameters: size {POLYGON_SIZE_METERS}x{POLYGON_SIZE_METERS}m, gap {GAP_METERS}m.")
    print(f"[+] File successfully saved as: {filename}")

if __name__ == "__main__":
    generate_landuse_grid()