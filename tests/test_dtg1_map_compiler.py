import sys
import os
import struct
from unittest.mock import patch, MagicMock

import pytest

from dtg1_map_compiler import get_base_directory, main
from dtg1_models import MapFeature, HWConfig


def test_get_base_directory_frozen():
    with patch.object(sys, 'frozen', True, create=True), \
         patch.object(sys, 'executable', '/fake/dir/executable.exe'):
        assert get_base_directory() == '/fake/dir'


def test_get_base_directory_not_frozen():
    with patch.object(sys, 'frozen', False, create=True):
        import dtg1_map_compiler
        expected = os.path.dirname(os.path.abspath(dtg1_map_compiler.__file__))
        assert get_base_directory() == expected


@pytest.fixture
def mock_base_dir(tmp_path):
    with patch('dtg1_map_compiler.get_base_directory', return_value=str(tmp_path)):
        yield str(tmp_path)


@patch('dtg1_map_compiler.argparse.ArgumentParser.parse_args')
def test_main_map_osm_not_found(mock_parse_args, mock_base_dir, capsys):
    mock_parse_args.return_value = MagicMock(poi_mode="landuse")

    main()

    captured = capsys.readouterr()
    assert "map.osm file not found" in captured.out


@patch('dtg1_map_compiler.MapCompiler')
@patch('dtg1_map_compiler.GPXParser')
@patch('dtg1_map_compiler.OSMParser')
@patch('dtg1_map_compiler.LookupTables')
@patch('dtg1_map_compiler.argparse.ArgumentParser.parse_args')
def test_main_success_no_poi(mock_parse_args, mock_lookup, mock_osm_parser, mock_gpx_parser, mock_map_compiler, mock_base_dir):
    mock_parse_args.return_value = MagicMock(poi_mode="none")

    # Create fake map.osm
    with open(os.path.join(mock_base_dir, "map.osm"), "w") as f:
        f.write("dummy")

    # Setup parser return
    road = MapFeature(osm_id="1", fclass="residential", code=5112, name="Road", points=b"")
    landuse = MapFeature(osm_id="2", fclass="forest", code=1101, name="Forest", points=b"")
    water = MapFeature(osm_id="3", fclass="water", code=HWConfig.WATER_CODE, name="Lake", points=b"")
    poi = MapFeature(osm_id="4", fclass="restaurant", code=3100, name="Food", points=b"")

    mock_osm_instance = MagicMock()
    mock_osm_instance.parse.return_value = ([road], [landuse, water], [poi])
    mock_osm_parser.return_value = mock_osm_instance

    main()

    mock_lookup.load_from_csv.assert_called_once()
    mock_osm_parser.assert_called_once_with(os.path.join(mock_base_dir, "map.osm"))

    # Roads compiled
    mock_map_compiler.compile_mlp.assert_any_call([road], os.path.join(mock_base_dir, "roads.mlp"))
    # Landuse compiled
    mock_map_compiler.compile_mlp.assert_any_call([landuse], os.path.join(mock_base_dir, "landuse.mlp"))
    # Water compiled
    mock_map_compiler.compile_mlp.assert_any_call([water], os.path.join(mock_base_dir, "water.mlp"))

    # POIs skipped (poi_mode=none)
    assert not any("pois" in call.args[1] for call in mock_map_compiler.compile_db.call_args_list if len(call.args) > 1)


@patch('dtg1_map_compiler.POIGeometryFactory')
@patch('dtg1_map_compiler.MapCompiler')
@patch('dtg1_map_compiler.GPXParser')
@patch('dtg1_map_compiler.OSMParser')
@patch('dtg1_map_compiler.LookupTables')
@patch('dtg1_map_compiler.argparse.ArgumentParser.parse_args')
def test_main_success_poi_landuse(mock_parse_args, mock_lookup, mock_osm_parser, mock_gpx_parser, mock_map_compiler, mock_poi_factory, mock_base_dir):
    mock_parse_args.return_value = MagicMock(poi_mode="landuse")

    # Create fake map.osm
    with open(os.path.join(mock_base_dir, "map.osm"), "w") as f:
        f.write("dummy")

    mock_lookup.POI_SHAPES = {"restaurant": "circle"}

    # POI point packing (cx, cy)
    points_bytes = struct.pack("<ii", 1000, 2000)
    poi = MapFeature(osm_id="4", fclass="restaurant", code=3100, name="Food", points=points_bytes)

    mock_osm_instance = MagicMock()
    mock_osm_instance.parse.return_value = ([], [], [poi])
    mock_osm_parser.return_value = mock_osm_instance

    mock_poi_factory.generate_polygon.return_value = [(1000, 2000), (1010, 2010), (1000, 2020)]

    main()

    # Check that POI was baked into landuse
    # poi points should be updated
    assert len(poi.points) == 24 # 3 points * 8 bytes
    mock_map_compiler.compile_mlp.assert_called_with([poi], os.path.join(mock_base_dir, "landuse.mlp"))


@patch('dtg1_map_compiler.MapCompiler')
@patch('dtg1_map_compiler.GPXParser')
@patch('dtg1_map_compiler.OSMParser')
@patch('dtg1_map_compiler.LookupTables')
@patch('dtg1_map_compiler.argparse.ArgumentParser.parse_args')
def test_main_success_poi_native(mock_parse_args, mock_lookup, mock_osm_parser, mock_gpx_parser, mock_map_compiler, mock_base_dir):
    mock_parse_args.return_value = MagicMock(poi_mode="native")

    # Create fake map.osm
    with open(os.path.join(mock_base_dir, "map.osm"), "w") as f:
        f.write("dummy")

    poi = MapFeature(osm_id="4", fclass="restaurant", code=3100, name="Food", points=b"")

    mock_osm_instance = MagicMock()
    mock_osm_instance.parse.return_value = ([], [], [poi])
    mock_osm_parser.return_value = mock_osm_instance

    main()

    mock_map_compiler.compile_db.assert_any_call([poi], os.path.join(mock_base_dir, "pois.db"), is_poi=True)
    mock_map_compiler.compile_idx.assert_any_call([poi], os.path.join(mock_base_dir, "pois.idx"), is_poi=True)


@patch('dtg1_map_compiler.MapCompiler')
@patch('dtg1_map_compiler.GPXParser')
@patch('dtg1_map_compiler.OSMParser')
@patch('dtg1_map_compiler.LookupTables')
@patch('dtg1_map_compiler.argparse.ArgumentParser.parse_args')
def test_main_gpx_injection(mock_parse_args, mock_lookup, mock_osm_parser, mock_gpx_parser, mock_map_compiler, mock_base_dir):
    mock_parse_args.return_value = MagicMock(poi_mode="none")

    # Create fake map.osm
    with open(os.path.join(mock_base_dir, "map.osm"), "w") as f:
        f.write("dummy")

    routes_dir = os.path.join(mock_base_dir, "routes")
    os.makedirs(routes_dir)
    with open(os.path.join(routes_dir, "track1.gpx"), "w") as f:
        f.write("dummy")

    mock_osm_instance = MagicMock()
    mock_osm_instance.parse.return_value = ([], [], []) # No roads, landuse, pois
    mock_osm_parser.return_value = mock_osm_instance

    # Mock GPX track parsing
    track_points = struct.pack("<ii", 1000, 2000) + struct.pack("<ii", 1000, 2010)
    mock_gpx_parser.parse_track.return_value = ("My Track", track_points)

    main()

    mock_gpx_parser.parse_track.assert_called_once_with(os.path.join(routes_dir, "track1.gpx"))

    # Check that a new road feature was created and roads were compiled
    # compile_mlp should be called with roads
    args, _ = mock_map_compiler.compile_mlp.call_args_list[0]
    roads_data = args[0]
    assert len(roads_data) == 1
    assert roads_data[0].name == "My Track"
    assert roads_data[0].fclass == "gpx_track"


@patch('dtg1_map_compiler.MapCompiler')
@patch('dtg1_map_compiler.GPXParser')
@patch('dtg1_map_compiler.OSMParser')
@patch('dtg1_map_compiler.LookupTables')
@patch('dtg1_map_compiler.argparse.ArgumentParser.parse_args')
def test_main_gpx_injection_empty_track_name(mock_parse_args, mock_lookup, mock_osm_parser, mock_gpx_parser, mock_map_compiler, mock_base_dir):
    mock_parse_args.return_value = MagicMock(poi_mode="none")
    with open(os.path.join(mock_base_dir, "map.osm"), "w") as f:
        f.write("dummy")
    routes_dir = os.path.join(mock_base_dir, "routes")
    os.makedirs(routes_dir)
    with open(os.path.join(routes_dir, "empty_track.gpx"), "w") as f:
        f.write("dummy")

    mock_osm_parser.return_value = MagicMock(parse=MagicMock(return_value=([], [], [])))

    # Empty track name should fallback to filename
    track_points = struct.pack("<ii", 1000, 2000) + struct.pack("<ii", 1000, 2010)
    mock_gpx_parser.parse_track.return_value = ("", track_points)

    main()

    args, _ = mock_map_compiler.compile_mlp.call_args_list[0]
    roads_data = args[0]
    assert roads_data[0].name == "empty_track"


@patch('dtg1_map_compiler.MapCompiler')
@patch('dtg1_map_compiler.GPXParser')
@patch('dtg1_map_compiler.OSMParser')
@patch('dtg1_map_compiler.LookupTables')
@patch('dtg1_map_compiler.argparse.ArgumentParser.parse_args')
def test_main_gpx_injection_empty_directory(mock_parse_args, mock_lookup, mock_osm_parser, mock_gpx_parser, mock_map_compiler, mock_base_dir):
    mock_parse_args.return_value = MagicMock(poi_mode="none")
    with open(os.path.join(mock_base_dir, "map.osm"), "w") as f:
        f.write("dummy")
    routes_dir = os.path.join(mock_base_dir, "routes")
    os.makedirs(routes_dir)

    mock_osm_parser.return_value = MagicMock(parse=MagicMock(return_value=([], [], [])))

    main()

    mock_gpx_parser.parse_track.assert_not_called()


@patch('dtg1_map_compiler.POIGeometryFactory')
@patch('dtg1_map_compiler.MapCompiler')
@patch('dtg1_map_compiler.OSMParser')
@patch('dtg1_map_compiler.LookupTables')
@patch('dtg1_map_compiler.argparse.ArgumentParser.parse_args')
def test_main_poi_landuse_no_points(mock_parse_args, mock_lookup, mock_osm_parser, mock_map_compiler, mock_poi_factory, mock_base_dir):
    mock_parse_args.return_value = MagicMock(poi_mode="landuse")
    with open(os.path.join(mock_base_dir, "map.osm"), "w") as f:
        f.write("dummy")

    poi_empty = MapFeature(osm_id="4", fclass="restaurant", code=3100, name="Food", points=b"")
    mock_osm_parser.return_value = MagicMock(parse=MagicMock(return_value=([], [], [poi_empty])))

    main()

    mock_poi_factory.generate_polygon.assert_not_called()


@patch('dtg1_map_compiler.MapCompiler')
@patch('dtg1_map_compiler.OSMParser')
@patch('dtg1_map_compiler.LookupTables')
@patch('dtg1_map_compiler.argparse.ArgumentParser.parse_args')
def test_main_poi_native_no_pois(mock_parse_args, mock_lookup, mock_osm_parser, mock_map_compiler, mock_base_dir):
    mock_parse_args.return_value = MagicMock(poi_mode="native")
    with open(os.path.join(mock_base_dir, "map.osm"), "w") as f:
        f.write("dummy")

    mock_osm_parser.return_value = MagicMock(parse=MagicMock(return_value=([], [], [])))

    main()

    # compile_db with is_poi=True should not be called
    assert not any(call.kwargs.get('is_poi') for call in mock_map_compiler.compile_db.call_args_list)
