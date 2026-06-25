#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import struct
import hashlib
from typing import List, Tuple, Any

from dtg1_models import MapFeature, HWConfig, safe_encode
from dtg1_lookup import LookupTables

class MapCompiler:
    """Generator of hardware binary structures (YZL/SQT/DBF) for ATS3085S platform."""

    @staticmethod
    def _write_yzl_container(filepath: str, payload: bytes, is_idx: bool, lod2_size: int = 0) -> None:
        """Encapsulate data in the system YZL container with hardware MD5 validation."""
        payload_size = len(payload)
        md5_hash = hashlib.md5(payload).digest()
        
        # is_idx condition changes the RAM Load Type marker and LOD2 offset
        if is_idx:
            header = b'YZL\x08' + struct.pack("<I", payload_size) + b'\x02\x00\x00\x04' + struct.pack(">I", lod2_size) + md5_hash
        else:
            header = b'YZL\x00' + struct.pack("<I", payload_size) + b'\x00\x00\x00\x04\x00\x00\x00\x00' + md5_hash
            
        with open(filepath, 'wb') as f:
            f.write(header)
            f.write(payload)

    @staticmethod
    def pack_nav_node(v3_jump: int, bbox: Tuple[float, float, float, float], v1: int, v2_count: int) -> bytes:
        """Packs a 28-byte SQT Navigation Node (C-Union structure)."""
        return struct.pack("<IffffII", v3_jump, bbox[0], bbox[1], bbox[2], bbox[3], v1, v2_count)

    @staticmethod
    def _pad(text: Any, length: int) -> bytes:
        """Pad text to fixed length using safe UTF-8 encoding."""
        return safe_encode(text, length).ljust(length, b'\x00')
            
    @staticmethod
    def _desc(name: str, length: int) -> bytes:
        """Pack dBase III field descriptor."""
        return name.encode('ascii').ljust(11, b'\x00') + b'C' + b'\x00'*4 + bytes([length]) + b'\x00'*15

    @classmethod
    def compile_mlp(cls, features: List[MapFeature], filepath: str) -> None:
        """Serializes raw geometry points into the .mlp binary format."""
        print(f"[>] Compiling geometry: {filepath}...")
        bin_records = bytearray()
        record_number = 1

        for feature in features:
            minx_i, miny_i, maxx_i, maxy_i = (int(c * 1e6) for c in feature.bbox)
            
            body = bytearray(struct.pack("<iiii", minx_i, miny_i, maxx_i, maxy_i))
            body += struct.pack("<II", len(feature.parts), len(feature.points))
            
            for part_idx in feature.parts: body += struct.pack("<I", part_idx)
            for p in feature.points: body += struct.pack("<ii", int(p[0] * 1e6), int(p[1] * 1e6))
                
            header = struct.pack(">I", record_number) + struct.pack("<I", len(body))
            record_bin = header + body

            feature.v1 = len(bin_records) + 8
            feature.v2 = 1 
            feature.mlp_size = len(record_bin)
            
            bin_records += record_bin
            record_number += 1

        cls._write_yzl_container(filepath, bin_records, is_idx=False)

    @classmethod
    def compile_db(cls, features: List[MapFeature], filepath: str, is_poi: bool = False) -> None:
        """Serializes text attributes into a dBase III (.db) format encapsulated in YZL."""
        if not is_poi and not any(f.name for f in features):
            print(f"[~] Layer {filepath} contains no named objects. .db file creation skipped.")
            for f in features: f.v2 = 0
            return
        if is_poi and not features: return
    
        print(f"[>] Compiling attributes: {filepath}...")
        
        # Standard maps require a dummy zero-record at index 1
        bin_records = bytearray() if is_poi else bytearray(b'\x00' * HWConfig.DBF_RECORD_LEN) 
        db_counter = 1 if is_poi else 2 
        total_records = 0 if is_poi else 1

        for feature in features:
            if is_poi or feature.name:
                feature.v2 = db_counter
                db_counter += 1
                total_records += 1
                
                r_bytes = bytearray(b'\x20')
                r_bytes += cls._pad(feature.osm_id, 12) + cls._pad(feature.code, 4) + cls._pad(feature.fclass, 28) + cls._pad(feature.name, 100)
                bin_records += r_bytes

        dbf_header = (
            bytearray(b'\x03\x00\x00\x00') + 
            struct.pack('<I', total_records) +
            struct.pack('<H', HWConfig.DBF_HEADER_LEN) + 
            struct.pack('<H', HWConfig.DBF_RECORD_LEN) + 
            b'\x00' * 20 +
            cls._desc("osm_id", 12) + cls._desc("code", 4) + cls._desc("fclass", 28) + cls._desc("name", 100) + 
            b'\x0D'
        )
        cls._write_yzl_container(filepath, dbf_header + bin_records, is_idx=False)

    @classmethod
    def _pack_clusters(cls, records: List[MapFeature], idx_buffer: bytearray) -> None:
        """Inject SQT clusters (max 14 objects) into the index buffer."""
        clusters = [records[i:i + HWConfig.CHUNK_SIZE] for i in range(0, len(records), HWConfig.CHUNK_SIZE)]
        
        if len(clusters) > 1:
            idx_buffer.extend(struct.pack("<II", 1, len(clusters)))
            for cluster in clusters:
                if not cluster: continue
                c_minx = min(f.bbox[0] for f in cluster)
                c_miny = min(f.bbox[1] for f in cluster)
                c_maxx = max(f.bbox[2] for f in cluster)
                c_maxy = max(f.bbox[3] for f in cluster)
                
                v3_jump = (len(cluster) * HWConfig.NODE_SIZE) + 8 
                idx_buffer.extend(cls.pack_nav_node(v3_jump, (c_minx, c_miny, c_maxx, c_maxy), 0, len(cluster)))                    
                
                for f in cluster: idx_buffer.extend(f.pack_data_node())
        else:
            count = len(clusters[0]) if clusters else 0
            idx_buffer.extend(struct.pack("<II", 0, count))
            for f in clusters[0] if clusters else []: idx_buffer.extend(f.pack_data_node())

    @classmethod
    def compile_idx(cls, features: List[MapFeature], filepath: str, is_poi: bool = False) -> None:
        """Serializes the multi-level SQT hardware index."""
        print(f"[>] Compiling SQT index: {filepath}...")
        idx_buffer = bytearray()
        
        if is_poi:
            # Single LOD level for POI
            idx_buffer.extend(b'SQT\x01\x01\x00\x00\x00') 
            if not features:
                idx_buffer.extend(struct.pack("<II", 0, 0))
            else:
                for f in features: f.v1 = 0
                cls._pack_clusters(features, idx_buffer)
                
            cls._write_yzl_container(filepath, idx_buffer, is_idx=False)

        else:
            # Standard multi-level geometry (LOD 0, 1, 2)
            lod_filters = [
                lambda c: True,
                lambda c: LookupTables.DISPLAY_SCALES.get(c, 20) >= 500, 
                lambda c: LookupTables.DISPLAY_SCALES.get(c, 20) >= 1000
            ]
            
            lod2_size = 0
            for lod_index, condition in enumerate(lod_filters):
                start_len = len(idx_buffer)
                lod_records = [f for f in features if condition(f.code)]
                
                idx_buffer.extend(b'SQT\x01\x00\x00\x00\x00') 
                
                if not lod_records:
                    idx_buffer.extend(struct.pack("<II", 0, 0))
                else:
                    cls._pack_clusters(lod_records, idx_buffer)
        
                if lod_index == 2: lod2_size = len(idx_buffer) - start_len
            
            cls._write_yzl_container(filepath, idx_buffer, is_idx=True, lod2_size=lod2_size)

    @staticmethod
    def create_empty_layer(layer_prefix: str) -> None:
        """Generates system dummy layers for missing geometry types."""
        print(f"[>] Creating system Hex dummy: {layer_prefix}...")
        mlp_hex = "595A4C00000000000000000400000000D41D8CD98F00B204E9800998ECF8427E"
        idx_hex = "595A4C10300000000000000400000010E5F9D2228804251B5F9E3EAB298C30E5535154010100000000000000000000005351540101000000000000000000000053515401010000000000000000000000"
        with open(f"{layer_prefix}.mlp", "wb") as f: f.write(bytearray.fromhex(mlp_hex))
        with open(f"{layer_prefix}.idx", "wb") as f: f.write(bytearray.fromhex(idx_hex))
  
    @staticmethod
    def create_map_name(name: str, meta_records: List[MapFeature], out_file: str = "map.name") -> None:
        """Generates the JSON camera centering file."""
        if not meta_records: return
        center_lat = (min(r.bbox[1] for r in meta_records) + max(r.bbox[3] for r in meta_records)) / 2.0
        center_lon = (min(r.bbox[0] for r in meta_records) + max(r.bbox[2] for r in meta_records)) / 2.0
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump({"centerLat": center_lat, "centerLon": center_lon, "mapName": name}, f, separators=(',', ':'))