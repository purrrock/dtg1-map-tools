import pytest
import struct
import array
from lxml import etree as ET

from dtg1_osmparser import OSMParser, GPXParser, _sanitize_name_cached
from dtg1_lookup import LookupTables
from dtg1_models import HWConfig


@pytest.fixture(autouse=True)
def mock_hardware_lut():
    """
    [MOCK HARDWARE LUT]
    Изолируем тесты парсера от физического features.csv, 
    чтобы гарантировать идемпотентность.
    """
    LookupTables.POI_CODES = {'pharmacy': 2700, 'restaurant': 2701}
    LookupTables.HIGHWAY_CODES = {'residential': 5122, 'track': 5130}
    LookupTables.POLYGON_CODES = {'forest': 7201, 'water': 8200}
    LookupTables.TAG_ROUTING = {
        'pois': {}, 'roads': {}, 'landuse': {}, 'water': {}
    }
    LookupTables.DISABLED_POIS = set()
    LookupTables.DISABLED_ROADS = set()
    LookupTables.DISABLED_LANDUSE = set()
    LookupTables.POI_SHAPES = {}


def test_sanitize_name_toponym_reordering():
    """
    [TOPONYMIC SANITIZATION TEST]
    Проверяет, что словарь стоп-слов корректно переносит топонимы
    в конец строки (для лучшей читаемости на экране часов) 
    и заменяет пробелы на underscore.
    """
    assert _sanitize_name_cached("Кафе Ромашка") == "Ромашка_кафе"
    assert _sanitize_name_cached("вуліца Леніна") == "Леніна_вуліца"
    assert _sanitize_name_cached("McDonalds") == "McDonalds"  # Нет стоп-слова
    
    # Проверка обрезки (UI Limit)
    long_name = "Очень Длинное Название Которое Не Влезет На Экран"
    sanitized = _sanitize_name_cached(long_name)
    assert len(sanitized) == 22
    assert sanitized.endswith("..")


@pytest.fixture
def mock_gpx_file(tmp_path):
    """Генерация тестового GPX-трека."""
    content = """<?xml version="1.0" encoding="UTF-8"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1">
      <trk>
        <name>Test Hardware Route</name>
        <trkseg>
          <trkpt lat="55.123456" lon="37.654321"></trkpt>
          <trkpt lat="55.123400" lon="37.654300"></trkpt>
        </trkseg>
      </trk>
    </gpx>
    """
    path = tmp_path / "route.gpx"
    path.write_text(content)
    return str(path)


def test_gpx_parser_coordinate_scaling(mock_gpx_file):
    """
    [GPX HARDWARE PIPELINE TEST]
    Проверяет, что float-координаты трека корректно умножаются на 1 000 000
    и преобразуются в 32-bit Integer.
    """
    name, points = GPXParser.parse_track(mock_gpx_file)
    
    assert name == "Test Hardware Route"
    assert len(points) == 2
    assert points[0] == (37654321, 55123456)  # (Lon, Lat) x 10^6

@pytest.fixture
def mock_osm_file(tmp_path):
    """
    Генерация минимального OSM-графа с узлами, POI, дорогой и полигоном.
    """
    content = """<?xml version="1.0" encoding="UTF-8"?>
    <osm version="0.6">
      <node id="1" lat="50.0" lon="30.0" />
      <node id="2" lat="50.0" lon="30.1" />
      <node id="3" lat="50.1" lon="30.1" />
      <node id="4" lat="50.1" lon="30.0" />
      
      <node id="5" lat="55.0" lon="37.0">
        <tag k="amenity" v="pharmacy"/>
        <tag k="name" v="Аптека Здоровье"/>
      </node>
      
      <way id="10">
        <nd ref="1"/><nd ref="2"/>
        <tag k="highway" v="residential"/>
        <tag k="surface" v="dirt"/>
      </way>
      
      <way id="20">
        <nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
        <tag k="landuse" v="forest"/>
      </way>
      
      <relation id="30">
        <tag k="type" v="multipolygon"/>
      </relation>
    </osm>
    """
    path = tmp_path / "test_map.osm"
    # ИСПРАВЛЕНИЕ: Явное указание кодировки для защиты от локали Windows
    path.write_text(content, encoding="utf-8")
    return str(path)

def test_osm_parser_c_array_memory_and_ejection(mock_osm_file):
    """
    [MEMORY OPTIMIZATION TEST]
    Критический тест для Embedded-среды. Проверяет:
    1. Использование непрерывных массивов array('q') и array('i').
    2. Высвобождение памяти (Early Node Ejection) при достижении слоя relations.
    """
    parser = OSMParser(mock_osm_file)
    
    # Имитация конца первого прохода (Caching)
    parser._pass1_cache_nodes()
    
    assert isinstance(parser.node_ids, array.array)
    assert isinstance(parser.node_coords, array.array)
    assert len(parser.node_ids) == 5  # 5 узлов
    assert len(parser.node_coords) == 10  # 5 узлов * 2 координаты (lon, lat)
    
    # Выполнение второго прохода
    parser._pass2_build_features()
    
    # Сразу после начала обработки relations, парсер обязан сбросить кэш узлов
    assert parser._nodes_freed is True
    assert parser.node_ids is None
    assert parser.node_coords is None


def test_osm_parser_surface_fallback_and_byte_packing(mock_osm_file):
    """
    [GEOMETRY Normalization TEST]
    Проверяет:
    1. Переопределение типа дороги при наличии тега surface="dirt".
    2. Упаковку вершин полигонов и линий в байт-код (Little-Endian int32).
    """
    parser = OSMParser(mock_osm_file)
    roads, landuse, pois = parser.parse()
    
    # 1. Проверка POI
    assert len(pois) == 1
    assert pois[0].fclass == 'pharmacy'
    assert pois[0].code == 2700
    
    # 2. Проверка дорог (Surface Fallback)
    assert len(roads) == 1
    # residential (5122) должен деградировать в unpaved (5142), 
    # так как в XML указан тег surface="dirt".
    assert roads[0].code == 5142 
    assert len(roads[0].points) == 16  # 2 узла * 8 байт (2x int32)
    
    # Распаковка и проверка геометрии первой точки дороги
    lon, lat = struct.unpack("<ii", roads[0].points[0:8])
    assert lon == 30000000  # 30.0 * 1 000 000
    assert lat == 50000000  # 50.0 * 1 000 000

    # 3. Проверка полигона (Landuse)
    assert len(landuse) == 1
    assert landuse[0].fclass == 'forest'
    assert landuse[0].code == 7201
    assert len(landuse[0].points) == 40  # 5 узлов (с замыканием) * 8 байт


@pytest.fixture
def mock_empty_gpx_file(tmp_path):
    """Генерация пустого, но валидного GPX-файла без треков."""
    content = """<?xml version="1.0" encoding="UTF-8"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1">
    </gpx>
    """
    path = tmp_path / "empty_route.gpx"
    path.write_text(content)
    return str(path)


def test_gpx_parser_empty_file(mock_empty_gpx_file):
    """
    [GPX EMPTY FILE TEST]
    Проверяет, что пустой gpx-файл без треков корректно парсится
    и возвращает пустой список точек и дефолтное имя 'Route'.
    """
    name, points = GPXParser.parse_track(mock_empty_gpx_file)

    assert name == "Route"
    assert len(points) == 0

@pytest.fixture
def mock_empty_gpx_without_tracks(tmp_path):
    """
    Генерация валидной, но пустой GPX структуры,
    чтобы проверить корректность работы парсера на пустых данных.
    """
    content = """<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1" creator="Test">
</gpx>
"""
    path = tmp_path / "empty_without_tracks.gpx"
    path.write_text(content)
    return str(path)

def test_parse_track_empty_gpx_without_tracks(mock_empty_gpx_without_tracks):
    """
    [EDGE CASE]
    Missing edge case: empty gpx file without tracks.
    Just need to provide an empty but valid GPX XML structure to ensure it returns cleanly.
    """
    name, points = GPXParser.parse_track(mock_empty_gpx_without_tracks)
    assert name == "Route"
    assert points == []
