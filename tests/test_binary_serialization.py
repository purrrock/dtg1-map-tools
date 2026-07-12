import pytest
import struct
from dtg1_models import MapFeature, RTreeNode, HWConfig
from dtg1_bin_writer import MapCompiler
from unittest.mock import patch
import io

def test_idx_rtree_serialization():
    """Verify packed arrays serialize correctly into binary streams."""
    feature = MapFeature(
        osm_id="1",
        fclass="f",
        code=1,
        name="1",
        points=struct.pack('<ii', 1000000, 2000000),
        bbox=(1000000, 2000000, 1000000, 2000000),
        v1=10,
        v2=20
    )

    node0 = RTreeNode(level=0, children=[feature])
    node1 = RTreeNode(level=1, children=[node0])

    packed = node1.pack()

    # Check node1 header
    v3_jump = struct.unpack('<I', packed[0:4])[0]
    bbox_f = struct.unpack('<ffff', packed[4:20])
    level, count = struct.unpack('<II', packed[20:28])

    assert v3_jump == node1.v3_jump
    assert bbox_f == (1.0, 2.0, 1.0, 2.0)  # HW indexing expects descale / 1000000.0 floats
    assert level == 1
    assert count == 1

    # Check node0 header
    v3_jump0 = struct.unpack('<I', packed[28:32])[0]
    bbox_f0 = struct.unpack('<ffff', packed[32:48])
    level0, count0 = struct.unpack('<II', packed[48:56])

    assert v3_jump0 == node0.v3_jump
    assert bbox_f0 == (1.0, 2.0, 1.0, 2.0)
    assert level0 == 0
    assert count0 == 1

    # Check DataNode
    bbox_d = struct.unpack('<ffff', packed[56:72])
    code, v1, v2 = struct.unpack('<III', packed[72:84])

    assert bbox_d == (1.0, 2.0, 1.0, 2.0)
    assert code == 1
    assert v1 == 10
    assert v2 == 20

def test_mlp_compilation_endianness():
    """
    Test explicitly the Little-Endian byte-packing mapping.
    """
    feature = MapFeature(
        osm_id="2",
        fclass="r",
        code=2,
        name="2",
        points=struct.pack('<ii', 2500000, 3500000),
        bbox=(2500000, 3500000, 2500000, 3500000),
        parts=(0,)
    )

    class BytesIOWrapper(io.BytesIO):
        def close(self): pass

    file_obj = BytesIOWrapper()

    with patch('builtins.open', return_value=file_obj):
        MapCompiler.compile_mlp([feature], "dummy.mlp")

    output = file_obj.getvalue()

    header_offset = 32

    record_number = struct.unpack('>I', output[header_offset:header_offset+4])[0]
    body_size = struct.unpack('<I', output[header_offset+4:header_offset+8])[0]

    assert record_number == 1

    body = output[header_offset+8:header_offset+8+body_size]

    # Body begins with bbox <iiii
    bbox_ints = struct.unpack('<iiii', body[0:16])
    assert bbox_ints == (2500000, 3500000, 2500000, 3500000)
