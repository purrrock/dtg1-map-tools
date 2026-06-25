#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import struct
import array
from dataclasses import dataclass, field
from typing import List, Tuple, Any

# Pre-compiled C-Structs for maximum performance (v16.0 optimization)
PACK_DATA_NODE = struct.Struct("<ffffIII").pack
PACK_NAV_NODE = struct.Struct("<IffffII").pack
PACK_BBOX_INT = struct.Struct("<iiii").pack
PACK_HEADER_INTS = struct.Struct("<II").pack
PACK_INT_LITTLE = struct.Struct("<I").pack
PACK_INT_BIG = struct.Struct(">I").pack
PACK_STR_12 = struct.Struct("<12s").pack
PACK_STR_4 = struct.Struct("<4s").pack
PACK_STR_28 = struct.Struct("<28s").pack
PACK_STR_100 = struct.Struct("<100s").pack

class HWConfig:
    """Hardware and system constants for the ATS3085S platform"""
    YZL_HEADER_SIZE = 32
    NODE_SIZE = 28           
    CHUNK_SIZE = 32          # Optimized chunk size for SQT clusters
    DBF_HEADER_LEN = 161     
    DBF_RECORD_LEN = 145     
    GPX_DP_EPSILON = 10.0    # Only used for dynamic user GPX tracks
    
    WATER_CODE = 8200
    DEFAULT_HIGHWAY_CODE = 5142
    DEFAULT_POLYGON_CODE = 7208
    DEFAULT_POI_CODE = 2724

def safe_encode(text: Any, max_len: int) -> bytes:
    """
    Secure truncator: Prevents incomplete UTF-8 encoding caused by forced slicing.
    """
    b = str(text or "").encode('utf-8')
    if len(b) <= max_len:
        return b
    return b[:max_len].decode('utf-8', 'ignore').encode('utf-8')

@dataclass
class MapFeature:
    """Represents a single map primitive (Road, Polygon, POI)"""
    osm_id: str
    fclass: str
    code: int
    name: str
    points: array.array
    parts: List[int] = field(default_factory=lambda: [0])
    
    # Bounding box using integer coordinates scaled by 1e6
    bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)
    v1: int = 0        
    v2: int = 0        
    mlp_size: int = 0  

    def calculate_bbox(self) -> None:
        """Optimized bounding box calculation for array.array('i')"""
        if not self.points:
            return
        x_coords = self.points[0::2]
        y_coords = self.points[1::2]
        self.bbox = (min(x_coords), min(y_coords), max(x_coords), max(y_coords))

    def pack_data_node(self) -> bytes:
        """Packing a Data Node using pre-compiled C-struct."""
        bx0, by0, bx1, by1 = self.bbox
        return PACK_DATA_NODE(bx0*1e-6, by0*1e-6, bx1*1e-6, by1*1e-6, self.code, self.v1, self.v2)