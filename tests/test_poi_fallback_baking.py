import pytest
import struct
import os

from dtg1_osmparser import OSMParser
from dtg1_models import HWConfig, MapFeature
from dtg1_lookup import LookupTables
from dtg1_geometry import POIGeometryFactory


@pytest.fixture(autouse=True)
def setup_lookup_tables():
    """Load Hardware LUT before executing tests."""
    features_csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'features.csv')
    LookupTables.load_from_csv(features_csv_path)


@pytest.fixture
def mock_osm_fallback(tmp_path):
    """
    Mock OSM without a 'name' tag to force the Fallback mechanism.
    """
    osm_content = """<?xml version="1.0" encoding="UTF-8"?>
    <osm version="0.6">
     <node id="10" lat="55.0" lon="37.0" version="1" timestamp="2020-01-01T00:00:00Z" changeset="1" uid="1" user="test">
      <tag k="amenity" v="pharmacy"/>
     </node>
    </osm>
    """
    file_path = tmp_path / "mock_fallback.osm"
    file_path.write_text(osm_content)
    return str(file_path)


def test_poi_name_fallback_mechanism(mock_osm_fallback):
    """
    [PARSER CONTRACT TEST]
    Проверяет, что при отсутствии тега 'name', парсер извлекает 'fclass' 
    и назначает его в качестве имени (Fallback), предотвращая сброс объекта.
    """
    parser = OSMParser(mock_osm_fallback)
    roads, landuse, pois = parser.parse()

    assert len(pois) == 1
    # Проверка Fallback: fclass 'pharmacy' должен стать именем 'pharmacy'
    assert pois[0].fclass == 'pharmacy'
    assert pois[0].name.lower() == 'pharmacy'
    
    # Проверка изначальной упаковки точки (8 байт)
    assert len(pois[0].points) == 8
    lon, lat = struct.unpack('<ii', pois[0].points)
    assert lon == 37000000
    assert lat == 55000000


def test_poi_baking_geometry_expansion():
    """
    [GEOMETRY HARDWARE TEST]
    Имитирует логику оркестратора dtg1_map_compiler.py (--poi-mode landuse).
    Проверяет, что 8-байтная точка (Signed Int32) корректно преобразуется 
    в валидный C-Array полигон через POIGeometryFactory и выравнивается по границам.
    """
    # Arrange: исходная точка (центроид)
    cx, cy = 37000000, 55000000
    poi = MapFeature(
        osm_id="10", fclass="airport", code=5651, name="SVO",
        points=struct.pack("<ii", cx, cy)
    )

    # Act: имитация пайплайна запекания
    shape_type = "square"  # Имитируем LookupTables.POI_SHAPES.get("airport")
    new_points = POIGeometryFactory.generate_polygon(shape_type, cx, cy)
    
    # Обратная упаковка в непрерывный массив байт
    poi.points = b''.join(struct.pack("<ii", p[0], p[1]) for p in new_points)
    poi.calculate_bbox()

    # Assert 1: Точка перестала быть точкой (размер больше 8 байт)
    assert len(poi.points) > 8
    
    # Assert 2: Выравнивание памяти. Каждая вершина это 2х int32 (8 байт). 
    # Массив обязан быть кратен 8, иначе произойдет сдвиг бинарного формата (Memory Misalignment).
    assert len(poi.points) % 8 == 0
    
    # Assert 3: Проверка Bounding Box. Так как это теперь полигон, 
    # минимальные координаты должны быть строго меньше максимальных (размерность > 0).
    assert poi.bbox[0] < poi.bbox[2]  # min_lon < max_lon
    assert poi.bbox[1] < poi.bbox[3]  # min_lat < max_lat