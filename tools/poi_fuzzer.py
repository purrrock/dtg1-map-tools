#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import datetime

# ==========================================
# GEOMETRY AND COORDINATE CONSTANTS
# ==========================================
# Base coordinates (top left corner of the starting grid)
LAT_CENTER = 53.70509
LON_CENTER = 28.419233

# Earth radius (WGS 84 model, equatorial radius)
EARTH_RADIUS = 6378137.0

# Distance between point centers in the matrix
SPACING_METERS = 20.0

OUTPUT_FILENAME = "points_test_grid_v1.osm"

# ==========================================
# AUXILIARY FUNCTIONS
# ==========================================

def meters_to_lat_delta(meters):
    # Convert linear offset (meters) to angular (degrees latitude)
    return (meters / EARTH_RADIUS) * (180.0 / math.pi)

def meters_to_lon_delta(meters, latitude):
    # Convert linear offset (meters) to angular (degrees longitude).
    # Uses the cosine of the current latitude to compensate for meridian convergence towards the poles.
    lat_rad = math.radians(latitude)
    return (meters / (EARTH_RADIUS * math.cos(lat_rad))) * (180.0 / math.pi)

# ==========================================
# DEFINITION OF POINT TAGS (POI)
# ==========================================

def get_poi_definitions():
    # Returns a list of dictionaries. Each dictionary corresponds to one node (Node).
    # Keys and values of the dictionary are directly translated to <tag k="..." v="..."/>.
    return [
        {"shop": "clothes"},
        {"shop": "hardware"},
        {"shop": "car_repair"},
        {"shop": "computer"},
        {"shop": "outdoor"},
        {"amenity": "airport"},
        {"amenity": "fuel"},
        {"amenity": "bank"},
        {"amenity": "police"},
        {"amenity": "fire_station"},
        {"barrier": "gate", "access": "private"}, # Multi-tagging for a single node
        {"amenity": "shower"}                     # Shower point (closes 3x4 matrix)
    ]

# ==========================================
# OSM XML GENERATION
# ==========================================

def generate_points_osm():
    # ISO 8601 timestamp generation to comply with OSM specification
    timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    poi_defs = get_poi_definitions()
    
    node_id = 1
    
    xml_header = f"""<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="POIFuzzer_v1" timestamp="{timestamp}">
  <bounds minlat="{LAT_CENTER - 0.1}" minlon="{LON_CENTER - 0.1}" maxlat="{LAT_CENTER + 0.1}" maxlon="{LON_CENTER + 0.1}"/>
"""
    nodes_xml = ""
    
    for i, tags in enumerate(poi_defs):
        # Matrix projection: grid of 4 points in a row
        row = i // 4  
        col = i % 4   
        
        # Calculation of offset in meters.
        # Y axis inverted (-row) for correct top-down rendering on a standard map.
        north_offset_meters = -row * SPACING_METERS 
        east_offset_meters = col * SPACING_METERS
        
        node_lat = LAT_CENTER + meters_to_lat_delta(north_offset_meters)
        node_lon = LON_CENTER + meters_to_lon_delta(east_offset_meters, LAT_CENTER)
        
        # Formation of <node> block
        nodes_xml += f'  <node id="{node_id}" lat="{node_lat:.7f}" lon="{node_lon:.7f}" timestamp="{timestamp}" version="1">\n'
        
        # Injection of tags into the node element
        for k, v in tags.items():
            nodes_xml += f'    <tag k="{k}" v="{v}"/>\n'
            
        nodes_xml += '  </node>\n'
        
        node_id += 1

    xml_footer = "</osm>\n"
    
    # Write structure memory dump to file
    with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
        f.write(xml_header)
        f.write(nodes_xml)
        f.write(xml_footer)

if __name__ == "__main__":
    generate_points_osm()