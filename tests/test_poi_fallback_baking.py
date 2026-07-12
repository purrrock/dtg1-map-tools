import pytest
import struct
from dtg1_osmparser import OSMParser
from dtg1_models import HWConfig
from dtg1_lookup import LookupTables
import os

@pytest.fixture(autouse=True)
def setup_lookup_tables():
    # Load LUT so that we have actual values to test against
    import os
    features_csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'features.csv')
    LookupTables.load_from_csv(features_csv_path)

@pytest.fixture
def mock_osm_file(tmp_path):
    osm_content = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="CGImap 0.8.8 (3719089 spike-08.openstreetmap.org)">
 <node id="1" lat="10.0" lon="20.0" version="1" timestamp="2020-01-01T00:00:00Z" changeset="1" uid="1" user="test"/>
 <node id="2" lat="10.1" lon="20.0" version="1" timestamp="2020-01-01T00:00:00Z" changeset="1" uid="1" user="test"/>
 <node id="3" lat="10.1" lon="20.1" version="1" timestamp="2020-01-01T00:00:00Z" changeset="1" uid="1" user="test"/>
 <node id="4" lat="10.0" lon="20.1" version="1" timestamp="2020-01-01T00:00:00Z" changeset="1" uid="1" user="test"/>
 <way id="100" version="1" timestamp="2020-01-01T00:00:00Z" changeset="1" uid="1" user="test">
  <nd ref="1"/>
  <nd ref="2"/>
  <nd ref="3"/>
  <nd ref="4"/>
  <nd ref="1"/>
  <tag k="natural" v="wood"/>
  <tag k="amenity" v="restaurant"/>
  <tag k="name" v="Test Restaurant"/>
 </way>
</osm>
"""
    file_path = tmp_path / "mock.osm"
    file_path.write_text(osm_content)
    return str(file_path)

def test_poi_fallback_baking(mock_osm_file):
    """
    Ensure POI 'baking' mechanism (point objects baked into landuse as polygons)
    still outputs correctly aligned integer arrays.
    """
    parser = OSMParser(mock_osm_file)
    roads, landuse, pois = parser.parse()

    # Check that pois has the restaurant
    assert len(pois) == 1
    assert pois[0].fclass == 'restaurant'
    assert pois[0].name == "Test_Restaurant"  # Sanitized name with underscore
    assert pois[0].code == LookupTables.POI_CODES.get('restaurant')

    # Verify points packed as integer struct "<ii"
    assert len(pois[0].points) == 8 # 2 integers
    lon, lat = struct.unpack('<ii', pois[0].points)

    assert lon == 20050000
    assert lat == 10050000

    # Check landuse
    assert len(landuse) == 1
    assert landuse[0].fclass == 'wood'

    # points should be exactly 5 pairs of integers (4 corners + closing)
    assert len(landuse[0].points) == 5 * 8 # 40 bytes

    unpacked_points = struct.unpack('<iiiiiiiiii', landuse[0].points)
    assert len(unpacked_points) == 10
