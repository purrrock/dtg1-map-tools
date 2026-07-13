import io
import struct
import hashlib
from unittest.mock import patch

import pytest

from dtg1_bin_writer import MapCompiler
from dtg1_models import MapFeature, RTreeNode, HWConfig


@pytest.fixture
def memory_files():
    """Provides isolated file I/O operations directly to RAM via io.BytesIO."""
    files = {}

    class BytesIOWrapper(io.BytesIO):
        def __init__(self, path):
            super().__init__()
            self.path = path

        def close(self):
            # Block the `.close()` call so we can read the data during tests
            pass

    def mock_open_func(filepath, mode='rb', **kwargs):
        if filepath not in files:
            files[filepath] = BytesIOWrapper(filepath)
        elif 'w' in mode:
            files[filepath] = BytesIOWrapper(filepath)

        # Reset cursor for reads
        if 'r' in mode:
            files[filepath].seek(0)

        return files[filepath]

    with patch('builtins.open', side_effect=mock_open_func):
        yield files

def test_yzl_header_generation(memory_files):
    """
    Test for YZL header generation. Checks:
    1. YZL signature.
    2. Magic Extension at offset 0x03.
    3. Payload size at offset 0x04.
    4. RAM Load Type at offset 0x08.
    5. 16-byte MD5 hash at offset 0x10.
    """
    payload = b'test_payload_123'
    filepath = "test.mlp"

    MapCompiler._write_yzl_container(filepath, payload, is_idx=False)

    output = memory_files[filepath].getvalue()

    # Total file size check
    assert len(output) == len(payload) + 32, "Header size should be exactly 32 bytes"

    # 1. Signature check
    assert output[0:3] == b'YZL', "Missing b'YZL' signature"
    
    # 2. Magic Extension check (.mlp/.db = 0x00)
    assert output[3] == 0x00, "Magic Extension must be 0x00 for non-index files"

    # 3. Payload size check at offset 0x04
    payload_size = struct.unpack('<I', output[4:8])[0]
    assert payload_size == len(payload), "Payload Size differs from actual payload length"

    # 4. RAM Load Type check at offset 0x08 (0x04000000 for standard layers)
    ram_load_type = output[8:12]
    assert ram_load_type == b'\x00\x00\x00\x04', "Invalid RAM Load Type representation (Little-Endian expected)"

    # 5. MD5 hash length and match check (Offset 0x10 is 16)
    md5_hash = output[16:32]
    assert len(md5_hash) == 16, "MD5 hash must be exactly 16 bytes"

    expected_md5 = hashlib.md5(payload).digest()
    assert md5_hash == expected_md5, "MD5 hash does not match payload content"


def test_endianness_mlp_compilation(memory_files):
    """
    Verify packing strictly uses Little-Endian format for data offsets and geometries.
    """
    feature = MapFeature(
        osm_id="123",
        fclass="road",
        code=5142,
        name="test_road",
        points=struct.pack('<ii', 1000000, 1000000),
        parts=(0,)
    )
    feature.calculate_bbox()

    filepath = "roads.mlp"
    MapCompiler.compile_mlp([feature], filepath)

    output = memory_files[filepath].getvalue()

    # Offset calculations:
    # YZL header: 32 bytes
    # MLP geometry record: Header (<I record_num, <I body_size) -> 8 bytes
    # Body begins at offset 40
    header_offset = 32
    
    # ИСПРАВЛЕНО: Строгий Little-Endian '<I' вместо '>I'
    record_number = struct.unpack('<I', output[header_offset:header_offset + 4])[0]
    body_size = struct.unpack('<I', output[header_offset + 4:header_offset + 8])[0]
    body = output[header_offset + 8 : header_offset + 8 + body_size]

    # Coordinate values 1.0 scaled by 1e6 -> 1000000 (0x000F4240)
    # Expected Little-Endian order: \x40\x42\x0f\x00
    expected_le_bytes = struct.pack("<i", 1000000)
    assert expected_le_bytes == b'\x40\x42\x0f\x00', "System pack validation"
    assert body[0:4] == expected_le_bytes, "minx coordinate must be serialized in Little-Endian (<)"

def test_c_union_node_alignment_padding():
    """
    Validates C-Union padding to ensure length precisely matches hardware specs (28 bytes).
    """
    feature = MapFeature(
        osm_id="123",
        fclass="poi",
        code=2724,
        name="test_poi",
        points=struct.pack('<ii', 1000000, 1000000),
        parts=(0,)
    )
    feature.calculate_bbox()

    # 1. Validating base DataNode
    data_node_bytes = feature.pack_data_node()
    assert len(data_node_bytes) == HWConfig.NODE_SIZE, f"Data Node size must be {HWConfig.NODE_SIZE} bytes"

    # 2. Validating MacroNode (RTreeNav)
    # RTree nodes at level 1 contain other RTreeNodes (level 0), not MapFeatures directly.
    level0_node = RTreeNode(level=0, children=[feature])
    macro_node = RTreeNode(level=1, children=[level0_node])
    packed_macro = macro_node.pack()

    # level 1 macro node (28 bytes) + level 0 macro node (28 bytes) + feature data node (28 bytes) = 84 bytes
    expected_size = HWConfig.NODE_SIZE * 3
    assert len(packed_macro) == expected_size, f"MacroNode struct payload incorrectly padded"
    assert macro_node.bin_size == expected_size, "MacroNode cache size incorrectly calculated"


def test_dbf_padding_validation(memory_files):
    """
    Verifies dBase III (.db) layout structure sizes encapsulate exactly hardware expectations.
    """
    features = [
        MapFeature(
            osm_id="123",
            fclass="poi",
            code=2724,
            name="point",
            points=struct.pack('<ii', 1000000, 1000000)
        )
    ]
    filepath = "poi.db"
    MapCompiler.compile_db(features, filepath, is_poi=True)

    output = memory_files[filepath].getvalue()

    # Size: 32 (YZL) + 161 (DBF Header) + 145 (DBF Record)
    expected_size = 32 + HWConfig.DBF_HEADER_LEN + (HWConfig.DBF_RECORD_LEN * 1)
    assert len(output) == expected_size, f"DBF binary padded to {len(output)} instead of {expected_size}"


def test_desc_field_descriptor():
    """
    Validates _desc packing for dBase III field descriptors.
    Ensures correct 32-byte layout, null-padding, and type indicators.
    """
    name = "osm_id"
    length = 12

    desc_bytes = MapCompiler._desc(name, length)

    # 1. Check total length is exactly 32 bytes
    assert len(desc_bytes) == 32, "Field descriptor must be exactly 32 bytes"

    # 2. Check the name is properly null-padded to 11 bytes
    expected_name = b'osm_id\x00\x00\x00\x00\x00'
    assert desc_bytes[0:11] == expected_name, "Name is not correctly padded to 11 bytes"

    # 3. Check the type indicator 'C'
    assert desc_bytes[11:12] == b'C', "Field type must be 'C' (Character)"

    # 4. Check the 4-byte reserved padding
    assert desc_bytes[12:16] == b'\x00' * 4, "Reserved bytes 12-15 must be null"

    # 5. Check the field length
    assert desc_bytes[16] == length, "Field length indicator is incorrect"

    # 6. Check the remaining 15 bytes of reserved padding
    assert desc_bytes[17:32] == b'\x00' * 15, "Reserved bytes 17-31 must be null"

    # Edge case: max length name (11 chars)
    name_11 = "abcdefghijk"
    length_11 = 50
    desc_bytes_11 = MapCompiler._desc(name_11, length_11)
    assert len(desc_bytes_11) == 32
    assert desc_bytes_11[0:11] == b'abcdefghijk'
    assert desc_bytes_11[16] == 50

    # Edge case: empty name
    desc_bytes_empty = MapCompiler._desc("", 10)
    assert len(desc_bytes_empty) == 32
    assert desc_bytes_empty[0:11] == b'\x00' * 11
    assert desc_bytes_empty[16] == 10

def test_build_rtree_empty():
    """Test _build_rtree with empty features list."""
    depth, nodes = MapCompiler._build_rtree([])
    assert depth == 0
    assert nodes == []

def test_build_rtree_single_level():
    """Test _build_rtree with a small number of features that fits in one CHUNK_SIZE."""
    features = []
    for i in range(10):
        feature = MapFeature(
            osm_id=str(i),
            fclass="poi",
            code=2724,
            name=f"test_poi_{i}",
            points=struct.pack('<ii', i * 1000000, i * 1000000)
        )
        feature.calculate_bbox()
        features.append(feature)

    depth, nodes = MapCompiler._build_rtree(features)
    assert depth == 1
    assert len(nodes) == 1
    assert nodes[0].level == 0
    assert len(nodes[0].children) == 10

def test_build_rtree_multiple_levels():
    """Test _build_rtree with enough features to require multiple nested R-Tree levels."""
    features = []
    # Create 200 features. CHUNK_SIZE is 14.
    # At level 0, we'll have ceil(200 / 14) = 15 chunks (which are nodes of level 0)
    # At level 1, those 15 nodes will be clustered. ceil(15 / 14) = 2 chunks (nodes of level 1)
    # The depth returned should be 2.
    for i in range(200):
        feature = MapFeature(
            osm_id=str(i),
            fclass="poi",
            code=2724,
            name=f"test_poi_{i}",
            points=struct.pack('<ii', i * 1000000, i * 1000000)
        )
        feature.calculate_bbox()
        features.append(feature)

    depth, root_nodes = MapCompiler._build_rtree(features)

    assert depth == 2
    assert len(root_nodes) == 2
    assert all(node.level == 1 for node in root_nodes)

    # Check the children of the first root node
    assert len(root_nodes[0].children) > 0
    assert all(child.level == 0 for child in root_nodes[0].children)

    # Check that the first child node (level 0) contains the actual MapFeatures
    first_level0_node = root_nodes[0].children[0]
    assert len(first_level0_node.children) > 0
    assert all(isinstance(f, MapFeature) for f in first_level0_node.children)
