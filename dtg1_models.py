#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import struct
from dataclasses import dataclass, field
from typing import List, Tuple, Any, Sequence


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

    # Other constants
    LOD_MASK = 0x0E
    RAM_LOAD_TYPE = 0x04000000


def safe_encode(text: Any, max_len: int) -> bytes:
    """
    Secure truncator: Prevents incomplete UTF-8 encoding caused by forced slicing of
    multi-byte characters such as Chinese characters, thus avoiding crashes of the watch's font engine.
    """
    b = str(text or "").encode('utf-8')
    if len(b) <= max_len:
        return b.ljust(max_len, b'\x00')
    # Slice and decode with 'ignore' to drop incomplete sequences, then re-encode
    return b[:max_len].decode('utf-8', 'ignore').encode('utf-8').ljust(max_len, b'\x00')


# [MEMORY OPTIMIZATION 1]: Указание slots=True предотвращает создание __dict__ и __weakref__
# Это экономит ~56-64 байта чистой памяти на каждом экземпляре класса.
@dataclass(slots=True)
class MapFeature:
    """Represents a single map primitive (Road, Polygon, POI)"""
    osm_id: str
    fclass: str
    code: int
    name: str
    points: List[Tuple[float, float]]
    
    # [MEMORY OPTIMIZATION 2]: Используем иммутабельный (неизменяемый) кортеж по умолчанию.
    # Это позволяет всем миллионам объектов без multipolygon ссылаться на один и тот же (0,) в памяти.
    parts: Sequence[int] = (0,)

    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    v1: int = 0        # Absolute geometry offset in the .mlp file
    v2: int = 0        # Row index in the attribute DB .db
    mlp_size: int = 0  # Binary body size in the .mlp file

    def calculate_bbox(self) -> None:
        """Direct bounding box calculation (optimized O(N) single pass)."""
        if not self.points:
            return
            
        # [MEMORY OPTIMIZATION 3]: Отказ от List Comprehension. 
        # Проходим по массиву координат ровно один раз (O(N)), не выделяя память под временные списки.
        p0 = self.points[0]
        minx, miny = p0[0], p0[1]
        maxx, maxy = p0[0], p0[1]
        
        for x, y in self.points[1:]:
            if x < minx: minx = x
            elif x > maxx: maxx = x
            
            if y < miny: miny = y
            elif y > maxy: maxy = y
            
        self.bbox = (minx, miny, maxx, maxy)

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


# Распространяем паттерн __slots__ на узлы R-Дерева
@dataclass(slots=True)
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
        # [MEMORY OPTIMIZATION 4]: Аналогичный O(N) проход для Macro-Nodes
        if not self.children:
            self.bbox = (0.0, 0.0, 0.0, 0.0)
            self.v3_jump = 8
            self.bin_size = HWConfig.NODE_SIZE
            return

        c0_bbox = self.children[0].bbox
        minx, miny, maxx, maxy = c0_bbox[0], c0_bbox[1], c0_bbox[2], c0_bbox[3]

        for c in self.children[1:]:
            cb = c.bbox
            if cb[0] < minx: minx = cb[0]
            if cb[1] < miny: miny = cb[1]
            if cb[2] > maxx: maxx = cb[2]
            if cb[3] > maxy: maxy = cb[3]

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