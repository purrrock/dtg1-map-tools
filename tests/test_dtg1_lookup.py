import io
import pytest
import sys
from dtg1_lookup import LookupTables
from unittest.mock import patch, mock_open
import builtins

def test_load_from_csv_file_not_found(capsys):
    """
    [ERROR PATH TEST]
    Verifies that a SystemExit is raised when the LUT configuration file
    is not found, preventing the parser from running with an invalid setup.
    Also verifies the printed error message.
    """
    filepath = "nonexistent_path_that_should_never_exist.csv"
    with pytest.raises(SystemExit) as excinfo:
        LookupTables.load_from_csv(filepath)

    assert excinfo.value.code == 1

    # Verify the correct output was printed
    captured = capsys.readouterr()
    assert f"[-] Error: LUT configuration file {filepath} not found." in captured.out

def test_load_from_csv_general_exception(capsys):
    """
    [ERROR PATH TEST]
    Verifies that a SystemExit is raised when an unexpected error occurs
    during loading of the LUT configuration file.
    """
    filepath = "features.csv" # Any valid path that might be accessed

    # We patch open to raise an unexpected Exception
    with patch('builtins.open', side_effect=Exception("Unexpected file access error")):
        with pytest.raises(SystemExit) as excinfo:
            LookupTables.load_from_csv(filepath)

        assert excinfo.value.code == 1

        captured = capsys.readouterr()
        assert f"[-] Critical error parsing {filepath}: Unexpected file access error" in captured.out

@pytest.mark.parametrize("remap_code,remap_lod", [
    ("INVALID", "1"),
    ("1000", "INVALID"),
    ("", "1"),
    ("1000", ""),
    ("3.14", "1"),
    ("1000", "3.14"),
])
def test_load_from_csv_value_error(remap_code, remap_lod):
    """
    [EDGE CASE TEST]
    Verifies that when remap_code or remap_lod are invalid integers,
    a ValueError is caught and the row is correctly skipped.
    Uses pytest.mark.parametrize to test various invalid inputs.
    """
    csv_content = (
        "id;fclass;alias;type;layer;tag;color;remap_code;width;remap_lod;enabled;shape\n"
            f"1;invalid_entry;;;roads;;#000000;{remap_code};;{remap_lod};1;\n"
    )

    # Reset state to be safe
    LookupTables.HIGHWAY_CODES.clear()
    LookupTables.DISPLAY_SCALES.clear()

    with patch('builtins.open', mock_open(read_data=csv_content)):
        LookupTables.load_from_csv("dummy.csv")

    assert "invalid_entry" not in LookupTables.HIGHWAY_CODES

def test_load_from_csv_short_row():
    """
    [EDGE CASE TEST]
    Verifies that rows with length < 11 are skipped.
    """
    csv_content = (
        "id;fclass;alias;type;layer;tag;color;remap_code;width;remap_lod\n"
        "1;short_row;;;roads;;#000000;1002;;1\n"
    )

    LookupTables.HIGHWAY_CODES.clear()

    with patch('builtins.open', mock_open(read_data=csv_content)):
        LookupTables.load_from_csv("dummy.csv")

    assert "short_row" not in LookupTables.HIGHWAY_CODES

def test_load_from_csv_disabled_layers():
    """
    [EDGE CASE TEST]
    Verifies that disabled entries are added to the corresponding disabled sets.
    """
    csv_content = (
        "id;fclass;alias;type;layer;tag;color;remap_code;width;remap_lod;enabled;shape\n"
        "1;disabled_road;;;roads;;#000000;1000;;1;0;\n"
        "2;disabled_poi;;;pois;;#000000;1000;;1;false;\n"
        "3;disabled_water;;;water;;#000000;1000;;1;no;\n"
        "4;disabled_landuse;;;landuse;;#000000;1000;;1;off;\n"
    )

    LookupTables.DISABLED_ROADS.clear()
    LookupTables.DISABLED_POIS.clear()
    LookupTables.DISABLED_WATER.clear()
    LookupTables.DISABLED_LANDUSE.clear()

    with patch('builtins.open', mock_open(read_data=csv_content)):
        LookupTables.load_from_csv("dummy.csv")

    assert "disabled_road" in LookupTables.DISABLED_ROADS
    assert "disabled_poi" in LookupTables.DISABLED_POIS
    assert "disabled_water" in LookupTables.DISABLED_WATER
    assert "disabled_landuse" in LookupTables.DISABLED_LANDUSE

def test_load_from_csv_tag_routing():
    """
    [EDGE CASE TEST]
    Verifies that tag routing parses tags with '='.
    """
    csv_content = (
        "id;fclass;alias;type;layer;tag;color;remap_code;width;remap_lod;enabled;shape\n"
        "1;hospital;;;pois;amenity=hospital,place=city;#000000;2000;;1;1;\n"
    )

    if "pois" in LookupTables.TAG_ROUTING:
        del LookupTables.TAG_ROUTING["pois"]

    with patch('builtins.open', mock_open(read_data=csv_content)):
        LookupTables.load_from_csv("dummy.csv")

    assert ("amenity", "hospital") in LookupTables.TAG_ROUTING["pois"]
    assert LookupTables.TAG_ROUTING["pois"][("amenity", "hospital")] == "hospital"
    assert ("place", "city") in LookupTables.TAG_ROUTING["pois"]
    assert LookupTables.TAG_ROUTING["pois"][("place", "city")] == "hospital"

def test_load_from_csv_layer_processing():
    """
    [EDGE CASE TEST]
    Verifies that other layers (landuse, water, pois) correctly populate their tables.
    """
    csv_content = (
        "id;fclass;alias;type;layer;tag;color;remap_code;width;remap_lod;enabled;shape\n"
        "1;forest;;;landuse;;#000000;3000;;1;1;\n"
        "2;lake;;;water;;#000000;8200;;1;1;\n"
        "3;bank;;;pois;;#000000;4000;;1;1;circle\n"
        "4;atm;;;pois;;#000000;4001;;1;1;\n"
    )

    LookupTables.POLYGON_CODES.clear()
    LookupTables.POI_CODES.clear()
    LookupTables.POI_SHAPES.clear()
    LookupTables.DISPLAY_SCALES.clear()

    with patch('builtins.open', mock_open(read_data=csv_content)):
        LookupTables.load_from_csv("dummy.csv")

    # landuse
    assert "forest" in LookupTables.POLYGON_CODES
    assert LookupTables.POLYGON_CODES["forest"] == 3000
    assert LookupTables.DISPLAY_SCALES[3000] == 1

    # water
    assert "lake" in LookupTables.POLYGON_CODES
    assert LookupTables.POLYGON_CODES["lake"] == 8200
    assert LookupTables.DISPLAY_SCALES[8200] == 1

    # pois
    assert "bank" in LookupTables.POI_CODES
    assert LookupTables.POI_CODES["bank"] == 4000
    assert LookupTables.DISPLAY_SCALES[4000] == 1
    assert LookupTables.POI_SHAPES["bank"] == "circle"

    # pois without shape fallback
    assert "atm" in LookupTables.POI_CODES
    assert LookupTables.POI_CODES["atm"] == 4001
    assert LookupTables.POI_SHAPES["atm"] == "rhombus"


