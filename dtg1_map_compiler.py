#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DT G1 Map Compiler (Platform ATS3085S)
===============================================
v1.4.0 (Fully Modular Architecture)
Main orchestrator. Converts OpenStreetMap (XML) data into closed binary formats
of DT NO.1 G1 smartwatches (.mlp, .idx, .db).
"""

import sys
import os
import argparse
from typing import List

from dtg1_models import MapFeature, HWConfig
from dtg1_osmparser import GPXParser, OSMParser
from dtg1_bin_writer import MapCompiler
from dtg1_geometry import POIGeometryFactory
from dtg1_lookup import LookupTables


def get_base_directory() -> str:
    """
    Determines the base directory for program execution.
    Critical for the hybrid distribution to function:
    - sys.frozen detects the PyInstaller (.exe) environment
    - __file__ is used for the source code (.py)
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    cli_parser = argparse.ArgumentParser(description="DT G1 Map Compiler (Platform ATS3085S)")
    cli_parser.add_argument(
        "-p", "--poi-mode", choices=["native", "landuse", "none"], default="landuse",
        help="POI mode: 'native' (pois.idx/db), 'landuse' (polygon baking), 'none' (ignore)"
    )
    args = cli_parser.parse_args()

    # Initialize hardware-independent paths
    base_dir = get_base_directory()
    map_osm_path = os.path.join(base_dir, "map.osm")
    features_csv_path = os.path.join(base_dir, "features.csv")
    routes_dir_path = os.path.join(base_dir, "routes")

    if not os.path.exists(map_osm_path):
        print(f"[-] Error: {map_osm_path} file not found. Terminating.")
        return

    print("=========================================")
    print("DT G1 MAP COMPILER")
    print(f"POI layer mode: {args.poi_mode.upper()}")
    print(f"Base Directory: {base_dir}")
    print("=========================================")

    # 1. Initialize Look-Up Tables
    LookupTables.load_from_csv(features_csv_path)

    # 2. Parse Source Data
    parser = OSMParser(map_osm_path)
    roads_data, landuse_data, pois_data = parser.parse()

    # 3. GPX Track Injection
    if os.path.exists(routes_dir_path) and os.path.isdir(routes_dir_path):
        gpx_files = [f for f in os.listdir(routes_dir_path) if f.lower().endswith(".gpx")]

        if gpx_files:
            print(f"[>] Scanning '{routes_dir_path}/' directory. Found {len(gpx_files)} GPX track(s)...")
            for idx, file_name in enumerate(gpx_files, start=1):
                gpx_path = os.path.join(routes_dir_path, file_name)
                track_name, track_points = GPXParser.parse_track(gpx_path)

                if not track_name or track_name == "Route":
                    track_name = os.path.splitext(file_name)[0]

                if track_points and len(track_points) >= 2:
                    unique_track_id = f"user_track_{idx:03d}"
                    gpx_feature = MapFeature(
                        osm_id=unique_track_id, fclass="gpx_track",
                        code=5111, name=track_name, points=track_points
                    )
                    gpx_feature.calculate_bbox()
                    roads_data.append(gpx_feature)
                    print(f"    [{idx}/{len(gpx_files)}] Track '{track_name}' successfully integrated.")
        else:
            print(f"[~] Directory '{routes_dir_path}/' is empty. No GPX tracks to inject.")

    # 4. Serialize Layers
    # Helper to route output binary files to the base directory
    def out_path(filename: str) -> str:
        return os.path.join(base_dir, filename)

    meta_all: List[MapFeature] = []

    # 4.1 Roads Layer
    if roads_data:
        MapCompiler.compile_mlp(roads_data, out_path("roads.mlp"))
        MapCompiler.compile_db(roads_data, out_path("roads.db"))
        MapCompiler.compile_idx(roads_data, out_path("roads.idx"))
        meta_all.extend(roads_data)

    # 4.2 POI Baking (if required)
    if args.poi_mode == "landuse" and pois_data:
        print("[>] Baking POI objects into landuse layer using dynamic shape factory...")
        for poi in pois_data:
            if not poi.points:
                continue
            shape_type = LookupTables.POI_SHAPES.get(poi.fclass, "rhombus").lower()
            poi.points = POIGeometryFactory.generate_polygon(shape_type, poi.points[0][0], poi.points[0][1])
            poi.calculate_bbox()
            landuse_data.append(poi)

        print(f"    Successfully baked {len(pois_data)} POIs.")
        pois_data.clear()

    # 4.3 Landuse and Water Layers
    landuse_only = [f for f in landuse_data if f.code != HWConfig.WATER_CODE]
    water_only = [f for f in landuse_data if f.code == HWConfig.WATER_CODE]

    if landuse_only:
        MapCompiler.compile_mlp(landuse_only, out_path("landuse.mlp"))
        MapCompiler.compile_db(landuse_only, out_path("landuse.db"))
        MapCompiler.compile_idx(landuse_only, out_path("landuse.idx"))
        meta_all.extend(landuse_only)
    else:
        # Pass the absolute prefix to the empty layer creation method
        MapCompiler.create_empty_layer(out_path("landuse"))

    if water_only:
        MapCompiler.compile_mlp(water_only, out_path("water.mlp"))
        MapCompiler.compile_db(water_only, out_path("water.db"))
        MapCompiler.compile_idx(water_only, out_path("water.idx"))
        meta_all.extend(water_only)

    # 4.4 Native POI Layer
    if args.poi_mode == "none":
        print("[>] POI layer skipped ('none' mode selected).")
    elif args.poi_mode == "native":
        if pois_data:
            MapCompiler.compile_db(pois_data, out_path("pois.db"), is_poi=True)
            MapCompiler.compile_idx(pois_data, out_path("pois.idx"), is_poi=True)
            meta_all.extend(pois_data)
        else:
            print("[~] Point objects (POI) are missing in the source data.")
    elif args.poi_mode == "landuse":
        print("[>] POI mode 'landuse' successfully handled.")

    # 5. Export JSON Metadata
    if meta_all:
        MapCompiler.create_map_name("DTG1_Map", meta_all, out_path("map.name"))

    print("\n[SUCCESS] Map package compiled successfully!")


if __name__ == "__main__":
    main()
