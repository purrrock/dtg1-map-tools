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

@dataclass
class RTreeNode:
    """
    Represents a Hierarchical Macro-Node (Nav Node) for the Spatial Quadrant Tree.
    Calculates boundaries and pre-fetches byte-offsets for hardware Z-Culling.
    """
    level: int
    children: List[Any]  # List of MapFeature (if level 0) or RTreeNode (if level > 0)
    bbox: Tuple[float, float, float, float] = field(init=False)
    v3_jump: int = field(init=False)
    bin_size: int = field(init=False)

    def __post_init__(self):
        # 1. Calculating the bounding rectangle (Enveloping BBox)
        minx = min(c.bbox[0] for c in self.children)
        miny = min(c.bbox[1] for c in self.children)
        maxx = max(c.bbox[2] for c in self.children)
        maxy = max(c.bbox[3] for c in self.children)
        self.bbox = (minx, miny, maxx, maxy)

        # 2. Calculating the size of the child subtree in bytes
        if self.level == 0:
            # Level 0 (Bottom of the tree): Children are raw geometry (Data Nodes)
            child_payload_size = len(self.children) * HWConfig.NODE_SIZE
        else:
            # Level > 0 (Macro-nodes): Children are other RTreeNodes
            child_payload_size = sum(c.bin_size for c in self.children)
            
        # Hardware jump = size of the entire tree under this node + 8 bytes of compensation
        self.v3_jump = child_payload_size + 8
        # Own size in binary = 28 bytes (the node itself) + the whole subtree
        self.bin_size = HWConfig.NODE_SIZE + child_payload_size

    def pack(self) -> bytes:
        """
        Recursive packing of C-Union tree structures into a binary stream.
        """
        data = bytearray(struct.pack(
            "<IffffII", 
            self.v3_jump, 
            self.bbox[0], self.bbox[1], self.bbox[2], self.bbox[3], 
            self.level, 
            len(self.children)
        ))
        
        for child in self.children:
            if self.level == 0:
                data.extend(child.pack_data_node())
            else:
                data.extend(child.pack())
                
        return bytes(data)