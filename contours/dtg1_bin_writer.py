#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import math
import struct
import json
import hashlib
from typing import List, Tuple, Any

from dtg1_models import (
    MapFeature, HWConfig, safe_encode,
    PACK_INT_BIG, PACK_INT_LITTLE, PACK_HEADER_INTS,
    PACK_STR_12, PACK_STR_4, PACK_STR_28, PACK_STR_100, 
    PACK_BBOX_INT, PACK_NAV_NODE
)
from dtg1_lookup import LookupTables

class PipelineOptimizer:
    """STR (Sort-Tile-Recursive) Packing & Flattening Optimizer."""
    @staticmethod
    def str_pack(features: List[MapFeature], chunk_size: int = HWConfig.CHUNK_SIZE) -> List[List[MapFeature]]:
        n = len(features)
        if n == 0: return []
        num_chunks = (n + chunk_size - 1) // chunk_size
        if num_chunks == 1: return [features]

        centers = [((f.bbox[0] + f.bbox[2]) / 2, (f.bbox[1] + f.bbox[3]) / 2, f) for f in features]
        centers.sort(key=lambda item: item[0]) 
        
        slice_count = math.isqrt(num_chunks) or 1
        slice_capacity = slice_count * chunk_size
        
        chunks = []
        for i in range(0, n, slice_capacity):
            slice_items = centers[i:i + slice_capacity]
            slice_items.sort(key=lambda item: item[1]) 
            for j in range(0, len(slice_items), chunk_size):
                chunks.append([item[2] for item in slice_items[j:j + chunk_size]])
        return chunks

    @classmethod
    def optimize_layer(cls, features: List[MapFeature], is_poi: bool = False) -> Tuple[List[MapFeature], List[List[List[MapFeature]]]]:
        lods_chunks = []
        flat_sequential_features = []
        seen_ids = set()
        
        if is_poi:
            chunks = cls.str_pack(features)
            lods_chunks.append(chunks)
            for chunk in chunks: flat_sequential_features.extend(chunk)
        else:
            lod0, lod1, lod2, lod3, lod4 = [], [], [], [], []
            for f in features:
                lod0.append(f)
                scale = LookupTables.DISPLAY_SCALES.get(f.code, 20)
                if scale >= 50: lod1.append(f)
                if scale >= 100: lod2.append(f)
                if scale >= 500: lod3.append(f)
                if scale >= 1000: lod4.append(f)
            
            for lod_list in (lod0, lod1, lod2, lod3, lod4):
                chunks = cls.str_pack(lod_list)
                lods_chunks.append(chunks)
                for chunk in chunks: 
                    for f in chunk:
                        if id(f) not in seen_ids:
                            seen_ids.add(id(f))
                            flat_sequential_features.append(f)
                            
        return flat_sequential_features, lods_chunks

class BufferedFileWriter:
    """Fast write buffer capable of generating real-time hardware MD5 validation."""
    def __init__(self, filepath: str, is_idx: bool):
        self.f = open(filepath, 'wb')
        self.md5 = hashlib.md5()
        self.size = self.lod2_size = 0
        self.is_idx = is_idx
        self.buffer = bytearray()
        self.BUFFER_LIMIT = 1048576 
        self.f.write(b'\x00' * HWConfig.YZL_HEADER_SIZE) 

    @property
    def current_size(self) -> int: return self.size + len(self.buffer)

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)
        if len(self.buffer) >= self.BUFFER_LIMIT: self._flush()

    def _flush(self) -> None:
        if self.buffer:
            self.f.write(self.buffer); self.md5.update(self.buffer)
            self.size += len(self.buffer); self.buffer.clear()

    def close(self) -> None:
        self._flush(); self.f.seek(0)
        hash_bytes = self.md5.digest()
        if self.is_idx:
            header = b'YZL\x08' + PACK_INT_LITTLE(self.size) + b'\x02\x00\x00\x04' + PACK_INT_BIG(self.lod2_size) + hash_bytes
        else:
            header = b'YZL\x00' + PACK_INT_LITTLE(self.size) + b'\x00\x00\x00\x04\x00\x00\x00\x00' + hash_bytes
        self.f.write(header); self.f.close()

class MapCompiler:
    @classmethod
    def compile_mlp(cls, features: List[MapFeature], filepath: str) -> None:
        print(f"[>] Compiling geometry: {filepath}...")
        writer = BufferedFileWriter(filepath, is_idx=False)
        current_offset = 0
        swap_needed = sys.byteorder == 'big'

        for record_number, feature in enumerate(features, 1):
            body_chunks = [PACK_BBOX_INT(*feature.bbox), PACK_HEADER_INTS(len(feature.parts), len(feature.points) // 2)]
            if feature.parts: body_chunks.append(struct.pack(f"<{len(feature.parts)}I", *feature.parts))
            if feature.points:
                if swap_needed: 
                    feature.points.byteswap(); body_chunks.append(feature.points.tobytes()); feature.points.byteswap()
                else: 
                    body_chunks.append(feature.points.tobytes())
            
            body_bin = b''.join(body_chunks)
            record_bin = PACK_INT_BIG(record_number) + PACK_INT_LITTLE(len(body_bin)) + body_bin

            feature.v1 = current_offset + 8
            feature.v2 = 1 
            writer.write(record_bin); current_offset += len(record_bin)
        writer.close()

    @classmethod
    def compile_db(cls, features: List[MapFeature], filepath: str, is_poi: bool = False) -> None:
        if not is_poi and not any(f.name for f in features):
            print(f"[~] Layer {filepath} contains no named objects. .db file creation skipped.")
            return
        elif is_poi and not features: return
        
        print(f"[>] Compiling attributes: {filepath}...")
        writer = BufferedFileWriter(filepath, is_idx=False)
        total_records = len(features) if is_poi else sum(1 for f in features if f.name) + 1
        db_counter = 1 if is_poi else 2 
        
        def desc(n: str, l: int) -> bytes: return n.encode('ascii').ljust(11, b'\x00') + b'C' + b'\x00'*4 + bytes([l]) + b'\x00'*15
        
        writer.write(b'\x03\x00\x00\x00' + struct.pack('<IHH', total_records, HWConfig.DBF_HEADER_LEN, HWConfig.DBF_RECORD_LEN) + b'\x00' * 20 + desc("osm_id", 12) + desc("code", 4) + desc("fclass", 28) + desc("name", 100) + b'\x0D')
        if not is_poi: writer.write(b'\x00' * HWConfig.DBF_RECORD_LEN) 

        for f in features:
            if is_poi or f.name:
                f.v2 = db_counter; db_counter += 1
                writer.write(b'\x20' + 
                             PACK_STR_12(safe_encode(f.osm_id, 12)) + 
                             PACK_STR_4(safe_encode(f.code, 4)) + 
                             PACK_STR_28(safe_encode(f.fclass, 28)) + 
                             PACK_STR_100(safe_encode(f.name, 100)))
        writer.close()

    @classmethod
    def compile_idx(cls, lods_chunks: List[List[List[MapFeature]]], filepath: str, is_poi: bool = False) -> None:
        print(f"[>] Compiling SQT index: {filepath}...")
        writer = BufferedFileWriter(filepath, is_idx=not is_poi)
        
        def write_cluster(cluster):
            c_minx, c_miny, c_maxx, c_maxy = cluster[0].bbox
            for f in cluster[1:]:
                bx0, by0, bx1, by1 = f.bbox
                if bx0 < c_minx: c_minx = bx0
                if by0 < c_miny: c_miny = by0
                if bx1 > c_maxx: c_maxx = bx1
                if by1 > c_maxy: c_maxy = by1
            v3_jump = (len(cluster) * HWConfig.NODE_SIZE) + 8
            writer.write(PACK_NAV_NODE(v3_jump, c_minx*1e-6, c_miny*1e-6, c_maxx*1e-6, c_maxy*1e-6, 0, len(cluster)))                    
            for f in cluster: writer.write(f.pack_data_node())

        if is_poi:
            writer.write(b'SQT\x01\x01\x00\x00\x00') 
            clusters = lods_chunks[0] if lods_chunks else []
            if not clusters: writer.write(b'\x00\x00\x00\x00\x00\x00\x00\x00')
            elif len(clusters) > 1:
                writer.write(PACK_HEADER_INTS(1, len(clusters)))
                for c in clusters: write_cluster(c)
            else:
                writer.write(PACK_HEADER_INTS(0, len(clusters[0])))
                for f in clusters[0]: writer.write(f.pack_data_node())
        else:
            last_lod_size = 0
            for lod_index, clusters in enumerate(lods_chunks):
                start_len = writer.current_size
                writer.write(b'SQT\x01\x00\x00\x00\x00')
                if not clusters: writer.write(b'\x00\x00\x00\x00\x00\x00\x00\x00')
                elif len(clusters) > 1:
                    writer.write(PACK_HEADER_INTS(1, len(clusters)))
                    for c in clusters: write_cluster(c)
                else:
                    writer.write(PACK_HEADER_INTS(0, len(clusters[0])))
                    for f in clusters[0]: writer.write(f.pack_data_node())
                if lod_index == 4: last_lod_size = writer.current_size - start_len
            writer.lod2_size = last_lod_size 
        writer.close()

    @staticmethod
    def create_empty_layer(layer_prefix: str) -> None:
        print(f"[>] Creating system Hex dummy: {layer_prefix}...")
        with open(f"{layer_prefix}.mlp", "wb") as f: 
            f.write(bytearray.fromhex("595A4C00000000000000000400000000D41D8CD98F00B204E9800998ECF8427E"))
        idx_writer = BufferedFileWriter(f"{layer_prefix}.idx", is_idx=True)
        # Writes the 5 empty LODs required by the hardware
        for _ in range(5): 
            idx_writer.write(b'SQT\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
        idx_writer.lod2_size = 16
        idx_writer.close()
 
    @staticmethod
    def create_map_name(name: str, meta_records: List[MapFeature], out_file: str) -> None:
        if not meta_records: return
        center_lat = (min(r.bbox[1] for r in meta_records) + max(r.bbox[3] for r in meta_records)) * 5e-7
        center_lon = (min(r.bbox[0] for r in meta_records) + max(r.bbox[2] for r in meta_records)) * 5e-7
        with open(out_file, "w", encoding="utf-8") as f: 
            json.dump({"centerLat": center_lat, "centerLon": center_lon, "mapName": name}, f, separators=(',', ':'))