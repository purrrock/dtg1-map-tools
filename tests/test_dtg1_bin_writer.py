import io
import struct
import hashlib
from unittest.mock import patch

import pytest

from dtg1_bin_writer import MapCompiler
from dtg1_models import MapFeature, RTreeNode, HWConfig


@pytest.fixture
def memory_files():
    """Provides isolated file I/O operations directly to RAM via io.BytesIO/StringIO."""
    files = {}

    class StringIOWrapper(io.StringIO):
        def __init__(self, path):
            super().__init__()
            self.path = path
        def close(self):
            pass

    class BytesIOWrapper(io.BytesIO):
        def __init__(self, path):
            super().__init__()
            self.path = path
        def close(self):
            # Block the `.close()` call so we can read the data during tests
            pass

    def mock_open_func(filepath, mode='rb', **kwargs):
        if filepath not in files:
            if 'b' in mode:
                files[filepath] = BytesIOWrapper(filepath)
            else:
                files[filepath] = StringIOWrapper(filepath)
        elif 'w' in mode:
            if 'b' in mode:
                files[filepath] = BytesIOWrapper(filepath)
            else:
                files[filepath] = StringIOWrapper(filepath)

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


def test_pad_function():
    """
    Test the _pad function handles standard padding and truncation for str, int, and bytes.
    """
    # 1. Normal string padding
    assert MapCompiler._pad("abc", 5) == b'abc\x00\x00'

    # 2. Exact length string padding
    assert MapCompiler._pad("abcde", 5) == b'abcde'

    # 3. Truncation (longer than length)
    assert MapCompiler._pad("abcdef", 5) == b'abcde'

    # 4. Bytes padding
    assert MapCompiler._pad(b"xyz", 5) == b'xyz\x00\x00'

    # 5. Bytes truncation (catches the case where bytes inputs longer than length are not truncated)
    assert MapCompiler._pad(b"xyz123", 4) == b'xyz1'

    # 6. Non-string inputs (int)
    assert MapCompiler._pad(123, 5) == b'123\x00\x00'

def test_build_rtree_empty():
    depth, nodes = MapCompiler._build_rtree([])
    assert depth == 0
    assert nodes == []

def test_build_rtree_single_chunk():
    features = []
    for i in range(HWConfig.CHUNK_SIZE):
        f = MapFeature(
            osm_id=str(i),
            fclass="poi",
            code=2724,
            name=f"test_{i}",
            points=struct.pack('<ii', i*1000, i*1000)
        )
        f.calculate_bbox()
        features.append(f)

    depth, nodes = MapCompiler._build_rtree(features)
    assert depth == 1
    assert len(nodes) == 1
    assert len(nodes[0].children) == HWConfig.CHUNK_SIZE

def test_build_rtree_multiple_chunks():
    features = []
    for i in range(HWConfig.CHUNK_SIZE + 1):
        f = MapFeature(
            osm_id=str(i),
            fclass="poi",
            code=2724,
            name=f"test_{i}",
            points=struct.pack('<ii', i*1000, i*1000)
        )
        f.calculate_bbox()
        features.append(f)

    depth, nodes = MapCompiler._build_rtree(features)
    assert depth == 1
    assert len(nodes) == 2

def test_build_rtree_multiple_levels():
    features = []
    # 14 chunks of 14 elements fits in 196 elements. Let's do 197 to spill over.
    for i in range(HWConfig.CHUNK_SIZE * HWConfig.CHUNK_SIZE + 1):
        f = MapFeature(
            osm_id=str(i),
            fclass="poi",
            code=2724,
            name=f"test_{i}",
            points=struct.pack('<ii', i*1000, i*1000)
        )
        f.calculate_bbox()
        features.append(f)

    depth, nodes = MapCompiler._build_rtree(features)
    assert depth == 2
    # At level 2 we should have 2 nodes, since 197 elements > 196 (14*14), needing at least 15 nodes at level 1, which requires 2 level 2 nodes.
    assert len(nodes) == 2

def test_compile_idx_poi_empty(memory_files):
    """Test compiling IDX for empty POI list."""
    filepath = "poi_empty.idx"
    MapCompiler.compile_idx([], filepath, is_poi=True)

    output = memory_files[filepath].getvalue()

    # YZL Header (32 bytes) + SQT header (16 bytes)
    assert len(output) == 48
    assert output[0:3] == b'YZL'
    assert output[32:48] == b'SQT\x01\x01\x00\x00\x00' + struct.pack("<II", 0, 0)

def test_compile_idx_poi_features(memory_files):
    """Test compiling IDX for POI list with features."""
    feature = MapFeature(
        osm_id="123",
        fclass="poi",
        code=2724,
        name="test_poi",
        points=struct.pack('<ii', 1000000, 1000000),
        parts=(0,)
    )
    feature.calculate_bbox()

    filepath = "poi_features.idx"
    MapCompiler.compile_idx([feature], filepath, is_poi=True)

    output = memory_files[filepath].getvalue()

    # YZL Header + SQT Header (16 bytes) + 1 Node
    assert len(output) == 32 + 16 + (HWConfig.NODE_SIZE * 2) # Macro node (28) + Data node (28)
    assert output[32:40] == b'SQT\x01\x01\x00\x00\x00'

    depth = struct.unpack("<I", output[40:44])[0]
    num_nodes = struct.unpack("<I", output[44:48])[0]

    assert depth == 1
    assert num_nodes == 1

def test_compile_idx_standard_empty(memory_files):
    """Test compiling IDX for standard empty GIS geometry."""
    filepath = "standard_empty.idx"
    MapCompiler.compile_idx([], filepath, is_poi=False)

    output = memory_files[filepath].getvalue()

    # YZL Header + 3 * empty SQT Header (16 bytes)
    assert len(output) == 32 + 3 * 16
    assert output[32:48] == b'SQT\x01\x01\x00\x00\x00' + struct.pack("<II", 0, 0)
    assert output[48:64] == b'SQT\x01\x01\x00\x00\x00' + struct.pack("<II", 0, 0)
    assert output[64:80] == b'SQT\x01\x01\x00\x00\x00' + struct.pack("<II", 0, 0)

    # Check YZL header flags for is_idx=True
    assert output[3] == 0x08  # b'YZL\x08'
    lod2_size = struct.unpack(">I", output[12:16])[0]
    assert lod2_size == 16  # Empty LOD2 only has SQT header

def test_compile_idx_standard_features(memory_files):
    """Test compiling IDX for standard GIS geometry with LOD filtering."""
    from dtg1_lookup import LookupTables
    # Reset just in case, though it should be a clean state
    LookupTables.DISPLAY_SCALES = {}
    LookupTables.DISPLAY_SCALES[100] = 20    # Only LOD0
    LookupTables.DISPLAY_SCALES[200] = 500   # LOD0, LOD1
    LookupTables.DISPLAY_SCALES[300] = 1000  # LOD0, LOD1, LOD2

    f1 = MapFeature(osm_id="1", fclass="a", code=100, name="test1", points=struct.pack('<ii', 1000000, 1000000), parts=(0,))
    f2 = MapFeature(osm_id="2", fclass="b", code=200, name="test2", points=struct.pack('<ii', 2000000, 2000000), parts=(0,))
    f3 = MapFeature(osm_id="3", fclass="c", code=300, name="test3", points=struct.pack('<ii', 3000000, 3000000), parts=(0,))

    for f in [f1, f2, f3]:
        f.calculate_bbox()

    filepath = "standard_features.idx"
    MapCompiler.compile_idx([f1, f2, f3], filepath, is_poi=False)

    output = memory_files[filepath].getvalue()

    # Payload sizes:
    # LOD0: 3 features -> SQT Header (16) + MacroNode (28) + 3*DataNode (3*28) = 128
    # LOD1: 2 features -> SQT Header (16) + MacroNode (28) + 2*DataNode (2*28) = 100
    # LOD2: 1 feature -> SQT Header (16) + MacroNode (28) + 1*DataNode (28) = 72

    assert len(output) == 32 + 128 + 100 + 72

    payload_size = struct.unpack("<I", output[4:8])[0]
    assert payload_size == 128 + 100 + 72

    lod2_size = struct.unpack(">I", output[12:16])[0]
    assert lod2_size == 72

def test_compile_idx_standard_cached(memory_files):
    """Test compiling IDX for standard GIS geometry with identical LODs to trigger caching."""
    from dtg1_lookup import LookupTables
    LookupTables.DISPLAY_SCALES[300] = 1000  # Matches all LODs

    f1 = MapFeature(osm_id="1", fclass="a", code=300, name="test1", points=struct.pack('<ii', 1000000, 1000000), parts=(0,))
    f1.calculate_bbox()

    filepath = "standard_cached.idx"
    MapCompiler.compile_idx([f1], filepath, is_poi=False)

    output = memory_files[filepath].getvalue()

    # LOD0, 1, 2 all have 1 feature -> SQT Header (16) + MacroNode (28) + DataNode (28) = 72

    # 3 identical layers should use caching for the payload
    payload_size = struct.unpack("<I", output[4:8])[0]
    assert payload_size == 3 * 72

    lod2_size = struct.unpack(">I", output[12:16])[0]
    assert lod2_size == 72

def test_compile_db_no_names(memory_files):
    """Test compile_db when features have no name (early return)."""
    feature = MapFeature(
        osm_id="123",
        fclass="road",
        code=5142,
        name="",
        points=struct.pack('<ii', 1000000, 1000000),
        parts=(0,)
    )
    feature.calculate_bbox()
    feature.v2 = 999  # to verify it gets set to 0

    filepath = "no_names.db"
    MapCompiler.compile_db([feature], filepath, is_poi=False)

    assert memory_files.get(filepath) is None or len(memory_files[filepath].getvalue()) == 0
    assert feature.v2 == 0

def test_compile_db_poi_empty(memory_files):
    """Test compile_db when is_poi is True and features are empty (early return)."""
    filepath = "empty_poi.db"
    MapCompiler.compile_db([], filepath, is_poi=True)

    assert memory_files.get(filepath) is None or len(memory_files[filepath].getvalue()) == 0

def test_build_str_layer_empty():
    """Test _build_str_layer with empty list."""
    assert MapCompiler._build_str_layer([], 0) == []

def test_build_str_layer_zero_slices():
    """Test _build_str_layer with 0 slices."""
    with patch('math.ceil', return_value=0):
        class Dummy:
            bbox = (0,0,0,0)
        assert MapCompiler._build_str_layer([Dummy()], 0) == []

def test_create_empty_layer(memory_files):
    """Test create_empty_layer."""
    prefix = "dummy"
    MapCompiler.create_empty_layer(prefix)

    assert memory_files[f"{prefix}.mlp"].getvalue() == bytearray.fromhex("595A4C00000000000000000400000000D41D8CD98F00B204E9800998ECF8427E")
    assert memory_files[f"{prefix}.idx"].getvalue() == bytearray.fromhex("595A4C10300000000000000400000010E5F9D2228804251B5F9E3EAB298C30E5535154010100000000000000000000005351540101000000000000000000000053515401010000000000000000000000")

def test_create_map_name_empty(memory_files):
    """Test create_map_name with empty features."""
    MapCompiler.create_map_name("Test Map", [], "empty.name")
    assert memory_files.get("empty.name") is None or len(memory_files["empty.name"].getvalue()) == 0

def test_create_map_name_features(memory_files):
    """Test create_map_name with features."""
    f1 = MapFeature(osm_id="1", fclass="a", code=100, name="test1", points=struct.pack('<iiii', -1000000, 2000000, 3000000, 4000000), parts=(0,))
    f1.calculate_bbox()

    MapCompiler.create_map_name("Test Map", [f1], "test.name")

    import json
    content = memory_files["test.name"].getvalue()
    data = json.loads(content)

    assert data["mapName"] == "Test Map"
    assert data["centerLat"] == 3.0
    assert data["centerLon"] == 1.0


def test_desc_additional_cases():
    """Test _desc with name longer than 11 characters to ensure truncation works."""
    desc1 = MapCompiler._desc("a_very_long_name_that_exceeds_11_chars", 5)
    assert len(desc1) == 32
    assert desc1[0:11] == b'a_very_long'
    assert desc1[16] == 5
