#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import datetime

# ==========================================
# GEOMETRY AND COORDINATE CONSTANTS
# ==========================================
# Base coordinates (center of the starting polygon)
LAT_CENTER = 53.70509
LON_CENTER = 28.419233

EARTH_RADIUS = 6378137.0

# Base radius/offset for shape geometry
R = 6.0

# Adjusted Y-axis multiplier for perspective compensation
PERSPECTIVE_Y_MULTIPLIER = 1.5

# Distance between shape centers in the matrix
SPACING_METERS = 20.0

OUTPUT_FILENAME = "shapes_test_grid_v2.osm"

# ==========================================
# AUXILIARY FUNCTIONS
# ==========================================

def meters_to_lat_delta(meters):
    return (meters / EARTH_RADIUS) * (180.0 / math.pi)

def meters_to_lon_delta(meters, latitude):
    lat_rad = math.radians(latitude)
    return (meters / (EARTH_RADIUS * math.cos(lat_rad))) * (180.0 / math.pi)

# ==========================================
# DEFINITION OF SHAPE GEOMETRY (METERS)
# Traversal: Strictly clockwise (CW)
# ==========================================

def get_shapes_definitions():
    shapes = {}

    # 1. Rhombus (stretched vertically)
    shapes["Rhombus"] = [(0, R * 1.4), (R, 0), (0, -R * 1.4), (-R, 0), (0, R * 1.4)]

    # 2. Triangle
    shapes["Triangle"] = [(0, R), (R, -R), (-R, -R), (0, R)]

    # 3. House
    shapes["House"] = [(0, R + 2), (R, R - 3), (R, -R), (-R, -R), (-R, R - 3), (0, R + 2)]

    # 4. Cup (Rectangle with chamfered bottom corners)
    # Chamfer starts at a height of 2.5 meters from the bottom edge.
    shapes["Cup"] = [
        (-R, R),          # Top left corner
        (R, R),           # Top right corner
        (R, -R + 2.5),    # Start of right chamfer (vertical descent)
        (R - 2.5, -R),    # End of right chamfer (transition to base)
        (-R + 2.5, -R),   # End of left chamfer (base)
        (-R, -R + 2.5),   # Start of left chamfer (ascent)
        (-R, R)           # Closing contour
    ]
    
    # 5. Cross ("Fat", bar thickness 4m instead of 2m for readability on screen)
    shapes["Cross"] = [
        (-2, R), (2, R), (2, 2), (R, 2), (R, -2), (2, -2),
        (2, -R), (-2, -R), (-2, -2), (-R, -2), (-R, 2), (-2, 2), (-2, R)
    ]

    # 6. Pictogram: Toilet (Stylized "hourglass" / two facing triangles)
    # To avoid hardware bugs with filling self-intersecting polygons,
    # the central point (isthmus) is widened to 1 meter along the X axis.
    shapes["Toilet"] = [
        (-R, R),           # Top left corner
        (R, R),            # Top right corner
        (0.5, 0),          # Right side of the isthmus (center)
        (R, -R),           # Bottom right corner
        (-R, -R),          # Bottom left corner
        (-0.5, 0),         # Left side of the isthmus (center)
        (-R, R)            # Closing contour
    ]

    # 7. Pictogram: Transport (Bus profile)
    # Increased windshield slant angle. Wheel arches are shifted to the center.
    shapes["Transport"] = [
        (-R, R - 1), (R - 3, R - 1),           # Bus roof (shortened)
        (R, R - 3.0), (R, -R),                   # Windshield (slant) and front bumper
        (R - 2.0, -R), (R - 2.0, -R + 1.5),      # Front wheel arch (right edge)
        (R - 4.0, -R + 1.5), (R - 4.0, -R),      # Front wheel arch (left edge)
        (-R + 4.0, -R), (-R + 4.0, -R + 1.5),    # Rear wheel arch (right edge)
        (-R + 2.0, -R + 1.5), (-R + 2.0, -R),    # Rear wheel arch (left edge)
        (-R, -R), (-R, R - 1)                    # Rear bumper and closure
    ]

    # 8. Pictogram: Shop (Stylized cart / rectangular trapezoid)
    # Left edge is strictly vertical, right edge forms a slant..
    shapes["Shop"] = [
        (-R, R),          # Top left corner (back wall of cart)
        (R, R),           # Top right corner (front edge of basket)
        (R - 2.5, -R),    # Bottom right corner (end of slanted front wall)
        (-R, -R),         # Bottom left corner (base of back wall, right angle)
        (-R, R)           # Closing contour
    ]
    
    # 9. Pictogram: Landmark (Tower with three sharp teeth)
    # Teeth have a triangular shape. Number of vertices reduced for optimization.
    shapes["Attraction"] = [
        (-R, R),             # Left peak (sharp tooth)
        (-2.5, R - 2.0),     # Left V-shaped depression
        (0.0, R),            # Central peak
        (2.5, R - 2.0),      # Right V-shaped depression
        (R, R),              # Right peak
        (R, -R),             # Bottom right corner of base
        (-R, -R),            # Bottom left corner of base
        (-R, R)              # Closing contour
    ]
    
    # 10. Pictogram: Bicycle
    shapes["Bicycle"] = [
        (-7.5, 1.5),         # Left octagon: left straight edge (top)
        (-5.25, 4.0),        # Left octagon: top-left chamfer
        (-1.5, 4.0),        # Left octagon: top straight edge
        (0.0, 1.5),         # Intersection point of top diagonals (depression)
        (1.5, 4.0),         # Right octagon: top straight edge (start)
        (5.25, 4.0),         # Right octagon: top straight edge (end)
        (7.5, 1.5),          # Right octagon: top-right chamfer
        (7.5, -1.5),         # Right octagon: right straight edge
        (5.25, -4.0),        # Right octagon: bottom-right chamfer
        (1.5, -4.0),        # Right octagon: bottom straight edge
        (0.0, -1.5),        # Intersection point of bottom diagonals (depression)
        (-1.5, -4.0),       # Left octagon: bottom straight edge (end)
        (-5.25, -4.0),       # Left octagon: bottom straight edge (start)
        (-7.5, -1.5),        # Left octagon: bottom-left chamfer
        (-7.5, 1.5)          # Closing contour
    ]

    # 11. Pictogram: Shower
    shapes["Shower"] = [
        (0.0, R),      # Apex (top) of triangle
        (5, 1.5),          # Right angle of triangle
        (-0.75, 1.5),        # Inner right corner of stand
        (-0.75, -R),   # Bottom right corner of stand
        (-5, -R),    # Bottom left corner of stand
        (-5, 1.5),         # Left edge of stand / Left angle of triangle
        (0.0, R)       # Closing contour
    ]

    # 12. Pictogram: Ban (Diagonal cross)
    # Offset parameter 'c' increased to 3.5 to thicken the rays.
    # Actual ray thickness: 2 * 3.5 * cos(45) = 4.94 m. 12 vertices.
    c = 3
    shapes["Barrier"] = [
        (0.0, c),            # Top inner depression (ray intersection)
        (R - c, R),          # Top left point of right ray (slant)
        (R, R - c),          # Top right point of right ray
        (c, 0.0),            # Right inner depression
        (R, -R + c),         # Bottom right point of right ray
        (R - c, -R),         # Bottom left point of right ray (slant)
        (0.0, -c),           # Bottom inner depression
        (-R + c, -R),        # Bottom right point of left ray
        (-R, -R + c),        # Bottom left point of left ray
        (-c, 0.0),           # Left inner depression
        (-R, R - c),         # Top left point of left ray
        (-R + c, R),         # Top right point of left ray (slant)
        (0.0, c)             # Closing contour
    ]
    return shapes

# ==========================================
# OSM XML GENERATION
# ==========================================

def generate_shapes_osm():
    timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    shapes_defs = get_shapes_definitions()
    
    # Array of output keys (12 shapes)
    shape_names = [
        "Rhombus", "Triangle", "House", 
        "Cup", "Cross", "Toilet", 
        "Transport", "Shop", "Attraction",
        "Bicycle", "Shower", "Barrier"
    ]
    
    node_id = 1
    way_id = 1
    
    xml_header = f"""<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="CustomShapesFuzzer_v2" timestamp="{timestamp}">
  <bounds minlat="{LAT_CENTER - 0.1}" minlon="{LON_CENTER - 0.1}" maxlat="{LAT_CENTER + 0.1}" maxlon="{LON_CENTER + 0.1}"/>
"""
    nodes_xml = ""
    ways_xml = ""
    
    for i, name in enumerate(shape_names):
        if name not in shapes_defs:
            continue
            
        # Matrix projection
        row = i // 4  
        col = i % 4   
        
        north_offset_meters = -row * SPACING_METERS 
        east_offset_meters = col * SPACING_METERS
        
        
        center_lat = LAT_CENTER + meters_to_lat_delta(north_offset_meters)
        center_lon = LON_CENTER + meters_to_lon_delta(east_offset_meters, LAT_CENTER)
        
        rel_coords = shapes_defs[name]
        way_nodes = []
        
        for x_offset, y_offset in rel_coords:
            # Hardware perspective compensation (reduced to 1.5)
            y_offset_stretched = y_offset * PERSPECTIVE_Y_MULTIPLIER
            
            lat_d = meters_to_lat_delta(y_offset_stretched)
            lon_d = meters_to_lon_delta(x_offset, center_lat)
            
            node_lat = center_lat + lat_d
            node_lon = center_lon + lon_d
            
            nodes_xml += f'  <node id="{node_id}" lat="{node_lat:.7f}" lon="{node_lon:.7f}" timestamp="{timestamp}" version="1"/>\n'
            way_nodes.append(node_id)
            node_id += 1
            
        ways_xml += f'  <way id="{way_id}" timestamp="{timestamp}" version="1">\n'
        for ref in way_nodes:
            ways_xml += f'    <nd ref="{ref}"/>\n'
        
        ways_xml += f'    <tag k="landuse" v="commercial"/>\n'
        ways_xml += f'    <tag k="name" v="Test {name}"/>\n'
        ways_xml += '  </way>\n'
        
        way_id += 1

    xml_footer = "</osm>\n"
    
    with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
        f.write(xml_header)
        f.write(nodes_xml)
        f.write(ways_xml)
        f.write(xml_footer)

if __name__ == "__main__":
    generate_shapes_osm()