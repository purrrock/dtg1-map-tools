#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Roads Layer Calibration Grid Generator (v2.0)
===================================================
Generates an OSM test grid to check hardware rendering support
road types (line thickness, colors) by ATS3085S graphics pipeline.

Architecture changes:
- Grid step (STEP_METERS) reduced to 5 meters to optimize for screen area.
- The vector array is split into horizontal and vertical blocks for
  lattice formation. This allows testing hardware
  Anti-Aliasing at intersections.
"""

import csv
import math
import sys
import os

# Base coordinates (center of the test polygon)
LAT_CENTER = 53.714055
LON_CENTER = 28.420172

# Physical grid parameters
STEP_METERS = 5          # Step between parallel vectors
LINE_LENGTH_METERS = 200 # Length of each road segment

def get_road_features(csv_path="features_factory.csv"):
    roads = []
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
            if layer == 'roads':
                fclass = row[1].strip()
                osm_tags_raw = row[5].strip()
                
                tags = {}
                for tag_pair in osm_tags_raw.split(','):
                    if '=' in tag_pair:
                        k, v = tag_pair.split('=', 1)
                        tags[k.strip()] = v.strip()
                
                if tags:
                    roads.append({'fclass': fclass, 'tags': tags})
                    
    return roads

def generate_roads_grid(filename="roads_calibration.osm", csv_path="features_factory.csv"):
    features = get_road_features(csv_path)
    total_features = len(features)
    
    if total_features == 0:
        print("[-] Error: No 'roads' layer objects found.")
        return

    # Array division: half along Y axis (horizontal vectors), half along X axis (vertical)
    half_features = total_features // 2

    # Projection approximation (R_earth ~ 6378 km, 1 degree ~ 111320 m)
    lat_step_deg = STEP_METERS / 111320.0
    lon_step_deg = STEP_METERS / (111320.0 * math.cos(math.radians(LAT_CENTER)))
    
    lat_length_deg = LINE_LENGTH_METERS / 111320.0
    lon_length_deg = LINE_LENGTH_METERS / (111320.0 * math.cos(math.radians(LAT_CENTER)))

    # BBox calculation for XML header
    max_lat = LAT_CENTER + max(half_features * lat_step_deg, lat_length_deg)
    max_lon = LON_CENTER + max((total_features - half_features) * lon_step_deg, lon_length_deg)

    osm_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="DTG1_Roads_Fuzzer_v2">',
        f'  <bounds minlat="{LAT_CENTER}" minlon="{LON_CENTER}" maxlat="{max_lat}" maxlon="{max_lon}"/>'
    ]

    # Negative IDs to prevent compiler conflicts
    node_id = -1
    way_id = -1
    
    for idx, feature in enumerate(features):
        if idx < half_features:
            # Horizontal lines: fixed Y (with step), X varies from 0 to Length
            start_lat = LAT_CENTER + (idx * lat_step_deg)
            end_lat = start_lat
            start_lon = LON_CENTER
            end_lon = LON_CENTER + lon_length_deg
        else:
            # Vertical lines: fixed X (with step), Y varies from 0 to Length
            vert_idx = idx - half_features
            start_lat = LAT_CENTER
            end_lat = LAT_CENTER + lat_length_deg
            start_lon = LON_CENTER + (vert_idx * lon_step_deg)
            end_lon = start_lon
        
        # Node generation
        osm_lines.append(f'  <node id="{node_id}" lat="{start_lat:.7f}" lon="{start_lon:.7f}" version="1"/>')
        start_node_id = node_id
        node_id -= 1
        
        osm_lines.append(f'  <node id="{node_id}" lat="{end_lat:.7f}" lon="{end_lon:.7f}" version="1"/>')
        end_node_id = node_id
        node_id -= 1
        
        # Way geometry assembly
        osm_lines.append(f'  <way id="{way_id}" version="1">')
        osm_lines.append(f'    <nd ref="{start_node_id}"/>')
        osm_lines.append(f'    <nd ref="{end_node_id}"/>')
        
        # Injection of attributes from configuration
        for k, v in feature['tags'].items():
            osm_lines.append(f'    <tag k="{k}" v="{v}"/>')
            
        osm_lines.append(f'    <tag k="name" v="{feature["fclass"]}"/>')
        osm_lines.append('  </way>')
        way_id -= 1

    osm_lines.append('</osm>')

    with open(filename, 'w', encoding='utf-8') as f:
        f.write("\n".join(osm_lines))
        
    print(f"[+] Generated calibration road lattice: {total_features} types.")
    print(f"    - Horizontal vectors: {half_features}")
    print(f"    - Vertical vectors: {total_features - half_features}")
    print(f"    - Parameters: step {STEP_METERS}m, length {LINE_LENGTH_METERS}m.")
    print(f"[+] File successfully saved as: {filename}")

if __name__ == "__main__":
    generate_roads_grid()