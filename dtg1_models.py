#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import struct
from dataclasses import dataclass, field
from typing import List, Tuple, Any

class HWConfig:
    """Hardware and system constants for the ATS3085S platform"""
    YZL_HEADER_SIZE = 32
    NODE_SIZE = 28           # Unified node size (Data Node / Nav Node)
    CHUNK_SIZE = 14          # Maximum number of objects in a cluster
    DBF_HEADER_LEN = 161     # dBase III header
    DBF_RECORD_LEN = 145     # Fixed-length dBase III record
    
    # System rendering codes
    WATER_CODE = 8200
    DEFAULT_HIGHWAY_CODE = 5142
    DEFAULT_POLYGON_CODE = 7208
    DEFAULT_POI_CODE = 2724

def safe_encode(text: Any, max_len: int) -> bytes:
    """
    Secure truncator: Prevents incomplete UTF-8 encoding caused by forced slicing of 
    multi-byte characters such as Chinese characters, thus avoiding crashes of the watch's font engine.
    """
    b = str(text or "").encode('utf-8')
    if len(b) <= max_len:
        return b
    # Slice and decode with 'ignore' to drop incomplete sequences, then re-encode
    return b[:max_len].decode('utf-8', 'ignore').encode('utf-8')

@dataclass
class MapFeature:
    """Represents a single map primitive (Road, Polygon, POI)"""
    osm_id: str
    fclass: str
    code: int
    name: str
    points: List[Tuple[float, float]]
    parts: List[int] = field(default_factory=lambda: [0])
    
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    v1: int = 0        # Absolute geometry offset in the .mlp file
    v2: int = 0        # Row index in the attribute DB .db
    mlp_size: int = 0  # Binary body size in the .mlp file

    def calculate_bbox(self) -> None:
        """Direct bounding box calculation (optimized with list comprehensions)."""
        if not self.points:
            return
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    def pack_data_node(self) -> bytes:
        """
        Packing a Data Node (strictly 28 bytes).
        Format (C-Union): [BBox 16b] [Type 4b] [v1 4b] [v2 4b]
        """
        return struct.pack(
            "<ffffIII", 
            self.bbox[0], self.bbox[1], self.bbox[2], self.bbox[3], 
            self.code, self.v1, self.v2
        )