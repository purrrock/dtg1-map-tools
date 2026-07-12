import pytest
import struct
from dtg1_models import safe_encode, HWConfig, MapFeature, RTreeNode

def test_safe_encode_truncation():
    """Test truncating a string exceeding the max length limit."""
    original = "HelloWorld"
    result = safe_encode(original, 5)
    assert result == b"Hello"
    assert len(result) == 5

def test_safe_encode_short():
    """Test safe_encode on short strings to verify it null-pads them."""
    original = "Hi"
    result = safe_encode(original, 10)
    assert result == b"Hi" + b"\x00" * 8
    assert len(result) == 10

def test_safe_encode_empty():
    """Test safe_encode with an empty string, expecting a byte string of zeros."""
    result = safe_encode("", 10)
    assert result == b"\x00" * 10
    assert len(result) == 10

def test_safe_encode_unicode_edge_cases():
    """Test safe_encode with unicode (Cyrillic) characters and split byte situations."""
    original = "Привет" # Each char is 2 bytes in UTF-8

    # 5 bytes: "Пр" (4 bytes) + 1 byte of "и" (incomplete).
    # safe_encode should decode ignoring the incomplete part, so we get 4 bytes, padded to 5.
    result = safe_encode(original, 5)
    expected = "Пр".encode("utf-8") + b"\x00"
    assert result == expected
    assert len(result) == 5

def test_hwconfig_constants():
    """Test HWConfig constants against accidental modification."""
    assert HWConfig.YZL_HEADER_SIZE == 32
    assert HWConfig.NODE_SIZE == 28
    assert HWConfig.CHUNK_SIZE == 14
    assert hasattr(HWConfig, "LOD_MASK")
    assert getattr(HWConfig, "LOD_MASK") == 0x0E
    assert hasattr(HWConfig, "RAM_LOAD_TYPE")
    assert getattr(HWConfig, "RAM_LOAD_TYPE") == 0x04000000
    assert HWConfig.DBF_HEADER_LEN == 161
    assert HWConfig.DBF_RECORD_LEN == 145
    assert HWConfig.WATER_CODE == 8200
    assert HWConfig.DEFAULT_HIGHWAY_CODE == 5142
    assert HWConfig.DEFAULT_POLYGON_CODE == 7208
    assert HWConfig.DEFAULT_POI_CODE == 2724

def test_mapfeature_pack_data_node():
    """Test packing a data node yields correct length and format."""
    feature = MapFeature(
        osm_id="123",
        fclass="highway",
        code=5142,
        name="Main St",
        points=struct.pack('<ii', 1100000, 2200000) + struct.pack('<ii', -1100000, 5500000) + struct.pack('<ii', 3300000, 0),
        bbox=(10000000, 20000000, 15000000, 25000000),
        v1=100,
        v2=200
    )
    packed = feature.pack_data_node()

    assert isinstance(packed, bytes)
    assert len(packed) == HWConfig.NODE_SIZE

    # Verify Little-Endian unpacking matches what was packed
    unpacked = struct.unpack("<ffffIII", packed)
    assert unpacked[0] == pytest.approx(10.0)
    assert unpacked[1] == pytest.approx(20.0)
    assert unpacked[2] == pytest.approx(15.0)
    assert unpacked[3] == pytest.approx(25.0)
    assert unpacked[4] == 5142
    assert unpacked[5] == 100
    assert unpacked[6] == 200

def test_mapfeature_calculate_bbox_empty():
    """Test bounding box calculation with empty points list does not raise exceptions."""
    feature = MapFeature(
        osm_id="123",
        fclass="highway",
        code=5142,
        name="Nowhere",
            points=b'',
        parts=[]
    )
    # The initial bbox is (0, 0, 0, 0)
    feature.calculate_bbox()
    assert feature.bbox == (0, 0, 0, 0)

def test_mapfeature_calculate_bbox_accuracy():
    """Test bounding box calculation accuracy."""
    feature = MapFeature(
        osm_id="123",
        fclass="highway",
        code=5142,
        name="Some St",
        points=struct.pack('<ii', 1100000, 2200000) + struct.pack('<ii', -1100000, 5500000) + struct.pack('<ii', 3300000, 0)
    )
    feature.calculate_bbox()
    assert feature.bbox == (-1100000, 0, 3300000, 5500000)

def test_rtreenode_post_init_empty():
    """Test RTreeNode initialization with empty children."""
    node = RTreeNode(level=0, children=[])

    assert node.bbox == (0, 0, 0, 0)
    assert node.v3_jump == 8
    assert node.bin_size == HWConfig.NODE_SIZE

def test_rtreenode_post_init_size():
    """Test RTreeNode size computation with nested children."""
    feature1 = MapFeature(osm_id="1", fclass="f", code=1, name="1", points=struct.pack('<ii', 1100000, 2200000) + struct.pack('<ii', -1100000, 5500000) + struct.pack('<ii', 3300000, 0), bbox=(0, 0, 1000000, 1000000))
    feature2 = MapFeature(osm_id="2", fclass="f", code=1, name="2", points=struct.pack('<ii', 1100000, 2200000) + struct.pack('<ii', -1100000, 5500000) + struct.pack('<ii', 3300000, 0), bbox=(2000000, 2000000, 3000000, 3000000))

    # Level 0 node containing 2 features
    node0 = RTreeNode(level=0, children=[feature1, feature2])

    # Check level 0 size
    expected_child_payload_0 = 2 * HWConfig.NODE_SIZE
    assert node0.v3_jump == expected_child_payload_0 + 8
    assert node0.bin_size == HWConfig.NODE_SIZE + expected_child_payload_0
    assert node0.bbox == (0, 0, 3000000, 3000000)

    # Level 1 node containing the Level 0 node
    node1 = RTreeNode(level=1, children=[node0])

    # Check level 1 size
    expected_child_payload_1 = node0.bin_size
    assert node1.v3_jump == expected_child_payload_1 + 8
    assert node1.bin_size == HWConfig.NODE_SIZE + expected_child_payload_1
    assert node1.bbox == (0, 0, 3000000, 3000000)

def test_rtreenode_pack():
    """Test recursive serialization of RTreeNode."""
    feature1 = MapFeature(osm_id="1", fclass="f", code=1, name="1", points=struct.pack('<ii', 1100000, 2200000) + struct.pack('<ii', -1100000, 5500000) + struct.pack('<ii', 3300000, 0), bbox=(0, 0, 1000000, 1000000))
    feature2 = MapFeature(osm_id="2", fclass="f", code=1, name="2", points=struct.pack('<ii', 1100000, 2200000) + struct.pack('<ii', -1100000, 5500000) + struct.pack('<ii', 3300000, 0), bbox=(2000000, 2000000, 3000000, 3000000))

    node = RTreeNode(level=0, children=[feature1, feature2])
    packed = node.pack()

    # Total size should be node itself (NODE_SIZE) + 2 children (2 * NODE_SIZE)
    expected_length = HWConfig.NODE_SIZE + 2 * HWConfig.NODE_SIZE
    assert len(packed) == expected_length
    assert len(packed) == node.bin_size

def test_mapfeature_shared_memory_optimization():
    """
    [MEMORY OPTIMIZATION TEST]
    Проверяет, что механизм Shared Memory (Flyweight) работает корректно.
    Разные экземпляры MapFeature без мультиполигонов должны делить
    ОДИН И ТОТ ЖЕ кортеж (0,) в памяти (через проверку id указателей).
    """
    f1 = MapFeature(osm_id="1", fclass="water", code=8200, name="Lake1", points=b'')
    f2 = MapFeature(osm_id="2", fclass="water", code=8200, name="Lake2", points=b'')
    
    # Жесткая проверка: указатели (identity) на объект в RAM должны совпадать (is),
    # а не просто быть равными по значению (==).
    assert f1.parts is f2.parts
    assert isinstance(f1.parts, tuple)
    assert f1.parts == (0,)

def test_rtreenode_pack_binary_structure():
    """
    [HARDWARE CONTRACT TEST]
    Глубокая валидация C-Union структуры макро-узла (Nav Node) для ATS3085S.
    Проверяет соответствие Little-Endian формату <IffffII и деление BBox на 1 000 000.0.
    """
    # Arrange: Создаем тестовую геометрию
    feature = MapFeature(
        osm_id="1", fclass="f", code=1, name="1", 
        points=b'', 
        bbox=(1000000, 2000000, 3000000, 4000000), 
        v1=10, v2=20
    )
    
    # ИСПРАВЛЕНИЕ ИЕРАРХИИ:
    # 1. Упаковываем геометрию в Leaf Node (Уровень 0)
    leaf_node = RTreeNode(level=0, children=[feature])
    
    # 2. Создаем Macro Node (Уровень 1), вкладывая в него Leaf Node
    node = RTreeNode(level=1, children=[leaf_node])
    
    # Act: Вызываем рекурсивную упаковку от корня
    packed_data = node.pack()
    
    # Assert: 1. Проверка общего размера
    # 28 байт (Macro Node lvl 1) + 28 байт (Leaf Node lvl 0) + 28 байт (Data Node) = 84 байта
    assert len(packed_data) == 84 
    
    # Отсекаем только заголовок самого макро-узла (первые 28 байт)
    node_header = packed_data[:HWConfig.NODE_SIZE]
    
    # Распаковываем бинарные данные обратно, используя контрактный формат
    unpacked = struct.unpack("<IffffII", node_header)
    
    # 2. Проверка аппаратного смещения Z-Culling (Early Exit pointer)
    # v3_jump для уровня 1 должен быть равен: бинарный размер всех детей (56 байт) + 8 = 64
    assert unpacked[0] == 64
    
    # 3. Проверка границы типов (Type Boundary)
    # Целые числа (int) должны быть корректно преобразованы в IEEE-754 float
    assert unpacked[1] == pytest.approx(1.0)  # minx
    assert unpacked[2] == pytest.approx(2.0)  # miny
    assert unpacked[3] == pytest.approx(3.0)  # maxx
    assert unpacked[4] == pytest.approx(4.0)  # maxy
    
    # 4. Метаданные дерева
    assert unpacked[5] == 1  # Level
    assert unpacked[6] == 1  # Children count (один узел leaf_node)