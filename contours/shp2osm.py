#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Shapefile to OSM Converter (V16.6 True Extreme Streaming Edition)
===========================================================
- Removed Z-Order spatial sorting which duplicates compiler efforts (Releases 100% memory pressure)
- O(1) reading of built-in BBox replacing O(N) matrix transposition
- Pure Streaming read/write on the fly, handles tens of GBs of contour files effortlessly
"""

import shapefile
import sys
import os
import tempfile
import shutil

# Pre-compiled binary tag cache
TAGS_XML = {
    'c1': b'<tag k="highway" v="c1"/>\n',
    'c2': b'<tag k="highway" v="c2"/>\n',
    'c3': b'<tag k="highway" v="c3"/>\n'
}

def convert_shp_to_osm_turbo(shp_file, osm_file):
    base_name = os.path.splitext(shp_file)[0]
    
    if not os.path.exists(base_name + ".shp"):
        print(f"❌ Error: Cannot find file {base_name}.shp")
        return

    # --- 🌟 Core Parameters ---
    MAX_POINTS = 50        
    MIN_STUB = 4           
    MIN_SPAN = 0.0002      # ~10m
    MIN_LOOP_SPAN = 0.0002 
    FLUSH_LIMIT = 8 * 1024 * 1024  # 8 MB memory buffer
    
    node_id, way_id = -1, -1
    
    try:
        with shapefile.Reader(base_name) as sf:
            fields = [f[0].upper() for f in sf.fields[1:]]
            ele_idx = next((i for i, k in enumerate(fields) if k in ['ELE', 'ELEVATION', 'Z', 'CONTOUR']), None)
            
            print("💾 Phase 1: True Extreme Binary Streaming Conversion (Streaming Mode)...")
            
            with open(osm_file, "wb", buffering=16*1024*1024) as f_osm, \
                 tempfile.TemporaryFile("w+b") as f_ways:
                
                f_osm.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="shp2osm_turbo">\n')
                
                node_buf = bytearray()
                way_buf = bytearray()
                
                # ⚡ Core Optimization: Use Generator to read and clear on the fly, memory usage is near 0
                for i, shapeRec in enumerate(sf.iterShapeRecords()):
                    
                    # ⚡ God-tier Optimization: Directly read Shapefile's built-in BBox, instantly calculate span (O(1))
                    bbox = shapeRec.shape.bbox
                    if not bbox: continue
                    span_x, span_y = bbox[2] - bbox[0], bbox[3] - bbox[1]
                    
                    # Get elevation and determine tags
                    if ele_idx is not None:
                        raw_ele = shapeRec.record[ele_idx]
                        try: ele_val = int(float(raw_ele))
                        except (ValueError, TypeError): ele_val = 0
                    else: ele_val = 0
                        
                    hw_val = 'c1' if ele_val % 100 == 0 else ('c2' if ele_val % 50 == 0 else 'c3')
                    
                    if hw_val == 'c1':
                        # Major contour lines every 100 meters, add name tag (e.g., name="3200M")
                        dynamic_tag = f'<tag k="highway" v="{hw_val}"/>\n<tag k="name" v="{ele_val}M"/>\n'
                        tag_bytes = dynamic_tag.encode('ascii')
                    else:
                        # Keep minor contour lines nameless to prevent screen cluttering with numbers
                        tag_bytes = TAGS_XML[hw_val]
                    
                    pts = shapeRec.shape.points
                    parts = shapeRec.shape.parts
                    num_pts = len(pts)
                    
                    for p in range(len(parts)):
                        start = parts[p]
                        end = parts[p+1] if p + 1 < len(parts) else num_pts
                        
                        if end - start < MIN_STUB: continue
                        
                        is_closed = (pts[start] == pts[end-1])
                        
                        # Fast noise filtering
                        if span_x < MIN_SPAN and span_y < MIN_SPAN:
                            if not is_closed or (span_x < MIN_LOOP_SPAN and span_y < MIN_LOOP_SPAN):
                                continue
                                
                        seg = pts[start:end]
                        seg_len = len(seg)
                        
                        current_node_ids = range(node_id, node_id - seg_len, -1)
                        node_id -= seg_len
                        
                        # Batch write Nodes
                        for nid, (x, y) in zip(current_node_ids, seg):
                            node_buf.extend(f'<node id="{nid}" lat="{y:.5f}" lon="{x:.5f}"/>\n'.encode('ascii'))
                            
                        # Batch write Ways
                        for c_start in range(0, seg_len, MAX_POINTS - 1):
                            chunk = current_node_ids[c_start : c_start + MAX_POINTS]
                            if len(chunk) < 2: continue
                            
                            way_buf.extend(f'<way id="{way_id}">\n'.encode('ascii'))
                            way_buf.extend(tag_bytes)
                            for nid in chunk:
                                way_buf.extend(f'<nd ref="{nid}"/>\n'.encode('ascii'))
                            way_buf.extend(b'</way>\n')
                            way_id -= 1

                    # Flush to disk only when memory reaches 8MB
                    if len(node_buf) > FLUSH_LIMIT:
                        f_osm.write(node_buf); node_buf.clear()
                    if len(way_buf) > FLUSH_LIMIT:
                        f_ways.write(way_buf); way_buf.clear()
                        
                    if i % 20000 == 0 and i > 0:
                        print(f"    ...High-speed streaming progress: {i} contour lines processed ...")

                # Write remaining buffer
                if node_buf: f_osm.write(node_buf)
                if way_buf: f_ways.write(way_buf)
                            
                print("💾 Phase 2: Binary Dump and Merge (Binary Block Copy)...")
                f_ways.seek(0)
                shutil.copyfileobj(f_ways, f_osm, length=16*1024*1024)
                f_osm.write(b'</osm>\n')
                
        final_size = os.path.getsize(osm_file) / (1024 * 1024)
        print(f"✅ Lightning conversion complete! Generated {abs(way_id + 1)} short segments. File size: {final_size:.2f} MB")

    except Exception as e:
        print(f"❌ An unknown error occurred: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python shp2osm.py <input_contours.shp> <output_contours.osm>")
    else:
        convert_shp_to_osm_turbo(sys.argv[1], sys.argv[2])