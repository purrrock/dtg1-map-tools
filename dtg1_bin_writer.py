#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import math
import struct
import hashlib
from typing import List, Tuple, Any

from dtg1_models import MapFeature, RTreeNode, HWConfig, safe_encode
from dtg1_lookup import LookupTables


class MapCompiler:
    """Generator of hardware binary structures (YZL/SQT/DBF) for ATS3085S platform."""

    @staticmethod
    def _write_yzl_container(filepath: str, payload: bytes, is_idx: bool, lod2_size: int = 0) -> None:
        """Encapsulate data in the system YZL container with hardware MD5 validation."""
        payload_size = len(payload)
        md5_hash = hashlib.md5(payload).digest()

        if is_idx:
            header = b'YZL\x08' + struct.pack("<I", payload_size) + b'\x02\x00\x00\x04' + struct.pack(">I", lod2_size) + md5_hash
        else:
            header = b'YZL\x00' + struct.pack("<I", payload_size) + b'\x00\x00\x00\x04\x00\x00\x00\x00' + md5_hash

        with open(filepath, 'wb') as f:
            f.write(header)
            f.write(payload)

    @staticmethod
    def _pad(text: Any, length: int) -> bytes:
        """Pad text to fixed length using safe UTF-8 encoding."""
        if isinstance(text, bytes):
            text = text[:length]
        else:
            text = safe_encode(text, length)
        return text.ljust(length, b'\x00')

    @staticmethod
    def _desc(name: str, length: int) -> bytes:
        """Pack dBase III field descriptor."""
        return name.encode('ascii')[:11].ljust(11, b'\x00') + b'C' + b'\x00' * 4 + bytes([length]) + b'\x00' * 15

    @classmethod
    def compile_mlp(cls, features: List[MapFeature], filepath: str) -> None:
        """Serializes raw geometry points into the .mlp binary format."""
        print(f"[>] Compiling geometry: {filepath}...")
        bin_records = bytearray()
        record_number = 1

        for feature in features:
            body = bytearray(struct.pack("<iiii", feature.bbox[0], feature.bbox[1], feature.bbox[2], feature.bbox[3]))
            body += struct.pack("<II", len(feature.parts), len(feature.points) // 8)

            for part_idx in feature.parts:
                body += struct.pack("<I", part_idx)
            body += feature.points

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
            for f in features:
                f.v2 = 0
            return
        if is_poi and not features:
            return

        print(f"[>] Compiling attributes: {filepath}...")

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
            bytearray(b'\x03\x00\x00\x00')
            + struct.pack('<I', total_records)
            + struct.pack('<H', HWConfig.DBF_HEADER_LEN)
            + struct.pack('<H', HWConfig.DBF_RECORD_LEN)
            + b'\x00' * 20
            + cls._desc("osm_id", 12)
            + cls._desc("code", 4)
            + cls._desc("fclass", 28)
            + cls._desc("name", 100)
            + b'\x0D'
        )
        cls._write_yzl_container(filepath, dbf_header + bin_records, is_idx=False)

    @classmethod
    def _build_str_layer(cls, items: List[Any], level: int) -> List[RTreeNode]:
        """
        Sort-Tile-Recursive (STR) Bulk Loading Algorithm.
        Divides a flat array into square matrices (Tiles) for hardware Z-Culling.
        """
        if not items:
            return []

        # 1. Sort objects by X axis (Centroid longitude)
        items.sort(key=lambda item: (item.bbox[0] + item.bbox[2]) / 2.0)

        # 2. Calculate mathematical slice limits
        num_nodes = math.ceil(len(items) / HWConfig.CHUNK_SIZE)
        num_slices = math.ceil(math.sqrt(num_nodes))

        if num_slices == 0:
            return []

        slice_capacity = num_slices * HWConfig.CHUNK_SIZE

        nodes = []
        # 3. Cut vertical slices and sort them by Y axis (Centroid latitude)
        for i in range(0, len(items), slice_capacity):
            slc = items[i:i + slice_capacity]
            slc.sort(key=lambda item: (item.bbox[1] + item.bbox[3]) / 2.0)

            # 4. Pack into C-Union clusters (CHUNK_SIZE elements each)
            for j in range(0, len(slc), HWConfig.CHUNK_SIZE):
                chunk = slc[j:j + HWConfig.CHUNK_SIZE]
                nodes.append(RTreeNode(level=level, children=chunk))

        return nodes

    @classmethod
    def _build_rtree(cls, features: List[MapFeature]) -> Tuple[int, List[RTreeNode]]:
        """
        Recursively builds a tree hierarchy of Macro-nodes (Nav Nodes).
        Returns: (Tree depth, List of root macro-nodes)
        """
        if not features:
            return 0, []

        current_layer = features
        level = 0

        while True:
            current_layer = cls._build_str_layer(current_layer, level)
            level += 1
            # Stop if the entire layer fits into one array of root nodes
            if len(current_layer) <= HWConfig.CHUNK_SIZE:
                break

        return level, current_layer

    @classmethod
    def compile_idx(cls, features: List[MapFeature], filepath: str, is_poi: bool = False) -> None:
        """Serializes the multi-level SQT hardware index using R-Tree Hierarchy."""
        print(f"[>] Compiling Hierarchical SQT index: {filepath}...")
        idx_buffer = bytearray()

        if is_poi:
            # For large POI arrays, R-Tree compression is now also applied
            if not features:
                idx_buffer.extend(b'SQT\x01\x01\x00\x00\x00' + struct.pack("<II", 0, 0))
            else:
                for f in features:
                    f.v1 = 0
                depth, root_nodes = cls._build_rtree(features)

                idx_buffer.extend(b'SQT\x01\x01\x00\x00\x00' + struct.pack("<II", depth, len(root_nodes)))
                for node in root_nodes:
                    idx_buffer.extend(node.pack())

            cls._write_yzl_container(filepath, idx_buffer, is_idx=False)

        else:
            # Standard multi-level GIS geometry (LOD 0, 1, 2)
            lod_filters = [
                lambda c: True,
                lambda c: LookupTables.DISPLAY_SCALES.get(c, 20) >= 500,
                lambda c: LookupTables.DISPLAY_SCALES.get(c, 20) >= 1000
            ]

            lod2_size = 0
            prev_len = -1
            cached_packed = b""
            for lod_index, condition in enumerate(lod_filters):
                start_len = len(idx_buffer)
                lod_records = [f for f in features if condition(f.code)]

                if not lod_records:
                    idx_buffer.extend(b'SQT\x01\x01\x00\x00\x00' + struct.pack("<II", 0, 0))
                else:
                    if len(lod_records) == prev_len:
                        idx_buffer.extend(cached_packed)
                    else:
                        depth, root_nodes = cls._build_rtree(lod_records)

                        # Dynamic recording of a 16-byte SQT header
                        header = b'SQT\x01\x01\x00\x00\x00' + struct.pack("<II", depth, len(root_nodes))
                        packed_data = bytearray(header)
                        for node in root_nodes:
                            packed_data.extend(node.pack())

                        idx_buffer.extend(packed_data)

                        cached_packed = packed_data
                        prev_len = len(lod_records)

                if lod_index == 2:
                    lod2_size = len(idx_buffer) - start_len

            cls._write_yzl_container(filepath, idx_buffer, is_idx=True, lod2_size=lod2_size)

    @staticmethod
    def create_empty_layer(layer_prefix: str) -> None:
        """Generates system dummy layers for missing geometry types."""
        print(f"[>] Creating system Hex dummy: {layer_prefix}...")
        mlp_hex = "595A4C00000000000000000400000000D41D8CD98F00B204E9800998ECF8427E"
        idx_hex = "595A4C10300000000000000400000010E5F9D2228804251B5F9E3EAB298C30E5535154010100000000000000000000005351540101000000000000000000000053515401010000000000000000000000"
        with open(f"{layer_prefix}.mlp", "wb") as f:
            f.write(bytearray.fromhex(mlp_hex))
        with open(f"{layer_prefix}.idx", "wb") as f:
            f.write(bytearray.fromhex(idx_hex))

    @staticmethod
    def create_map_name(name: str, meta_records: List[MapFeature], out_file: str = "map.name") -> None:
        """Generates the JSON camera centering file."""
        if not meta_records:
            return
        center_lat = (min(r.bbox[1] for r in meta_records) + max(r.bbox[3] for r in meta_records)) / 2.0 / 1000000.0
        center_lon = (min(r.bbox[0] for r in meta_records) + max(r.bbox[2] for r in meta_records)) / 2.0 / 1000000.0
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump({"centerLat": center_lat, "centerLon": center_lon, "mapName": name}, f, separators=(',', ':'))
