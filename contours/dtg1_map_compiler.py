#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler (Platform ATS3085S)
===============================================
v1.6.1 (Pipeline-Optimized Modular Architecture with Batch Processing)
Integrates v16.0 performance upgrades, lxml optimizations, and correct GPX folders.
"""

import sys
import os
import argparse
import gc
from typing import List

from dtg1_models import MapFeature, HWConfig
from dtg1_osmparser import GPXParser, OSMParser
from dtg1_bin_writer import MapCompiler, PipelineOptimizer
from dtg1_geometry import POIGeometryFactory, douglas_peucker_gpx
from dtg1_lookup import LookupTables

def get_base_directory() -> str:
    """Detect executing environment directory for hybrid distros."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

def compile_layer(features: List[MapFeature], base_path: str, layer_name: str, is_poi: bool = False):
    """Orchestrates STR packing and file writing logic for an optimized pipeline."""
    if not features:
        if not is_poi: 
            # Write a 5-layer empty hex dummy if a standard layer is missing
            MapCompiler.create_empty_layer(os.path.join(base_path, layer_name))
        return []
    
    print(f"[>] Packing & writing layer: {layer_name} (STR + Cache Aligned)...")
    flat_seq_features, lods_chunks = PipelineOptimizer.optimize_layer(features, is_poi)
    
    # CRITICAL: POI features do not have physical geometry files (.mlp)
    if not is_poi:
        MapCompiler.compile_mlp(flat_seq_features, os.path.join(base_path, f"{layer_name}.mlp"))
        
    MapCompiler.compile_db(flat_seq_features, os.path.join(base_path, f"{layer_name}.db"), is_poi)
    MapCompiler.compile_idx(lods_chunks, os.path.join(base_path, f"{layer_name}.idx"), is_poi)
    
    return flat_seq_features

def process_single_tile(osm_path: str, out_dir: str, tile_name: str, args: argparse.Namespace, routes_dir_path: str) -> None:
    """Processes a single OSM tile, injects GPX, compiles layers, and manages memory."""
    print(f"\n" + "="*50)
    print(f"📦 PROCESSING TILE: {tile_name}")
    print(f"="*50)
    
    os.makedirs(out_dir, exist_ok=True)

    # 1. Parse OSM Data (lxml Engine inside)
    parser = OSMParser(osm_path)
    roads_data, landuse_data, pois_data = parser.parse()
    
    # Free memory occupied by raw maps immediately
    del parser.nodes, parser.ways_cache
    gc.collect()

    # 2. Inject External GPX Routes
    if os.path.exists(routes_dir_path) and os.path.isdir(routes_dir_path):
        gpx_files = [f for f in os.listdir(routes_dir_path) if f.lower().endswith(".gpx")]
        
        if gpx_files:
            print(f"[>] Scanning '{routes_dir_path}/' directory. Found {len(gpx_files)} GPX track(s)...")
            for idx, file_name in enumerate(sorted(gpx_files), start=1):
                gpx_path = os.path.join(routes_dir_path, file_name)
                track_name, track_points = GPXParser.parse_track(gpx_path)
                
                if not track_name or track_name == "Route":
                    track_name = os.path.splitext(file_name)[0]
                
                # Check for at least 2 points (4 flattened elements: x,y,x,y)
                if track_points and len(track_points) >= 4: 
                    # Apply Dynamic Douglas-Peucker exclusively on raw GPX traces
                    dp_points = douglas_peucker_gpx(track_points, HWConfig.GPX_DP_EPSILON)
                    
                    unique_track_id = f"user_track_{idx:03d}"
                    gpx_feature = MapFeature(
                        osm_id=unique_track_id, 
                        fclass="gpx_track", 
                        code=LookupTables.HIGHWAY_CODES.get("gpx_track", 5111), 
                        name=track_name, 
                        points=dp_points
                    )
                    gpx_feature.calculate_bbox()
                    roads_data.append(gpx_feature)
                    print(f"    [{idx}/{len(gpx_files)}] Track '{track_name}' successfully integrated.")

    # 3. Serialize Output Layers
    meta_all: List[MapFeature] = []

    # Road Layer
    meta_all.extend(compile_layer(roads_data, out_dir, "roads"))
    
    # Landuse Layer (Handle POI Baking if requested)
    if args.poi_mode == "landuse" and pois_data:
        print("[>] Baking POI objects into landuse layer using dynamic shape factory...")
        for poi in pois_data:
            if not poi.points: continue
            
            shape_type = LookupTables.POI_SHAPES.get(poi.fclass, "rhombus").lower()
            poi.points = POIGeometryFactory.generate_polygon(shape_type, poi.points[0], poi.points[1])
            poi.calculate_bbox()
            landuse_data.append(poi)
            
        print(f"    Successfully baked {len(pois_data)} POIs.")
        pois_data.clear() 

    # Filter out Water blocks from Landuse elements
    landuse_only = [f for f in landuse_data if f.code != HWConfig.WATER_CODE]
    water_only = [f for f in landuse_data if f.code == HWConfig.WATER_CODE]

    meta_all.extend(compile_layer(landuse_only, out_dir, "landuse"))
    meta_all.extend(compile_layer(water_only, out_dir, "water"))

    # Native POI Layer
    if args.poi_mode == "none": 
        print("[>] POI layer skipped ('none' mode selected).")
    elif args.poi_mode == "native":
        if pois_data:
            meta_all.extend(compile_layer(pois_data, out_dir, "pois", is_poi=True))
        else: 
            print("[~] Point objects (POI) are missing in the source data.")

    # 4. Build Map Name Identifier
    if meta_all: 
        MapCompiler.create_map_name(tile_name, meta_all, os.path.join(out_dir, "map.name"))
    
    print(f"[SUCCESS] Tile '{tile_name}' completely built.")
    
    # 5. Prevent Memory Leaks during Batch Processing
    roads_data.clear()
    landuse_data.clear()
    pois_data.clear()
    meta_all.clear()
    gc.collect()

def main():
    cli_parser = argparse.ArgumentParser(description="DT G1 Map Compiler (Platform ATS3085S)")
    cli_parser.add_argument(
        "-p", "--poi-mode", choices=["native", "landuse", "none"], default="landuse",
        help="POI mode: 'native' (pois.idx/db), 'landuse' (polygon baking), 'none' (ignore)"
    )
    args = cli_parser.parse_args()

    base_dir = get_base_directory()
    features_csv_path = os.path.join(base_dir, "features.csv")
    routes_dir_path = os.path.join(base_dir, "routes") # Corrected from "route"
    osm_dir_path = os.path.join(base_dir, "osm")

    print("=========================================")
    print("DT G1 MAP COMPILER (Batch Optimizer Edition)")
    print(f"POI layer mode: {args.poi_mode.upper()}")
    print(f"Base Directory: {base_dir}")
    print("=========================================")
    
    LookupTables.load_from_csv(features_csv_path)

    if not os.path.isdir(osm_dir_path): 
        print(f"[-] Error: 'osm' folder not found in {base_dir}!")
        sys.exit(1)
        
    osm_files = sorted([f for f in os.listdir(osm_dir_path) if f.lower().endswith('.osm')])
    if not osm_files: 
        print(f"[-] Error: No .osm files found inside the 'osm' folder!")
        sys.exit(1)

    print(f"[i] Found {len(osm_files)} OSM file(s) for batch processing.")

    for filename in osm_files:
        tile_name = os.path.splitext(filename)[0]
        osm_file_path = os.path.join(osm_dir_path, filename)
        output_directory = os.path.join(osm_dir_path, tile_name)
        
        process_single_tile(osm_file_path, output_directory, tile_name, args, routes_dir_path)

    print("\n[🎉] All map tiles processed successfully!")

if __name__ == "__main__":
    main()