import xml.etree.ElementTree as ET
import pytest
import numpy as np

from osm_optimizer import clean_element_metadata, douglas_peucker_indices_fast

def test_dp_less_than_3_points():
    pts_0 = np.array([], dtype=np.float64).reshape(0, 2)
    assert np.array_equal(douglas_peucker_indices_fast(pts_0, 1.0), np.array([], dtype=np.int64))

    pts_1 = np.array([[0.0, 0.0]])
    assert np.array_equal(douglas_peucker_indices_fast(pts_1, 1.0), np.array([0], dtype=np.int64))

    pts_2 = np.array([[0.0, 0.0], [1.0, 1.0]])
    assert np.array_equal(douglas_peucker_indices_fast(pts_2, 1.0), np.array([0, 1], dtype=np.int64))

def test_dp_collinear_points():
    # Points on a straight line should only retain the start and end points
    pts = np.array([
        [0.0, 0.0],
        [1.0, 1.0],
        [2.0, 2.0],
        [3.0, 3.0],
        [4.0, 4.0]
    ])
    indices = douglas_peucker_indices_fast(pts, 0.1)
    assert np.array_equal(indices, np.array([0, 4], dtype=np.int64))

def test_dp_epsilon_filtering():
    # Points forming a triangle
    pts = np.array([
        [0.0, 0.0],
        [5.0, 1.0],  # Distance from the line (0,0)-(10,0) is 1.0
        [10.0, 0.0]
    ])

    # If epsilon > 1.0, the middle point should be removed
    indices_large_eps = douglas_peucker_indices_fast(pts, 1.1)
    assert np.array_equal(indices_large_eps, np.array([0, 2], dtype=np.int64))

    # If epsilon < 1.0, the middle point should be kept
    indices_small_eps = douglas_peucker_indices_fast(pts, 0.9)
    assert np.array_equal(indices_small_eps, np.array([0, 1, 2], dtype=np.int64))

def test_dp_point_cluster():
    # All points are in the exact same spot (l2 == 0)
    pts = np.array([
        [0.0, 0.0],
        [0.0, 0.0],
        [0.0, 0.0],
        [0.0, 0.0]
    ])
    indices = douglas_peucker_indices_fast(pts, 0.1)
    # The start and end are kept. Others have distance 0 to the start/end, so they are dropped.
    assert np.array_equal(indices, np.array([0, 3], dtype=np.int64))

def test_clean_element_metadata_attributes():
    elem = ET.Element("node", {
        "id": "123",
        "timestamp": "2023-01-01T00:00:00Z",
        "changeset": "1000",
        "uid": "1",
        "user": "alice",
        "lat": "10.0",
        "lon": "20.0"
    })
    clean_element_metadata(elem)

    # Check that garbage metadata is removed
    assert "timestamp" not in elem.attrib
    assert "changeset" not in elem.attrib
    assert "uid" not in elem.attrib
    assert "user" not in elem.attrib

    # Check that defaults are set
    assert elem.attrib.get("version") == "1"
    assert elem.attrib.get("visible") == "true"

    # Check that legitimate attributes are kept
    assert elem.attrib.get("id") == "123"
    assert elem.attrib.get("lat") == "10.0"
    assert elem.attrib.get("lon") == "20.0"

def test_clean_element_metadata_existing_defaults():
    elem = ET.Element("way", {
        "id": "456",
        "version": "2",
        "visible": "false"
    })
    clean_element_metadata(elem)

    # Existing defaults should not be overwritten
    assert elem.attrib.get("version") == "2"
    assert elem.attrib.get("visible") == "false"

def test_clean_element_metadata_tags():
    elem = ET.Element("node", {"id": "1"})

    # Add some tags that should be dropped
    ET.SubElement(elem, "tag", {"k": "wikidata", "v": "Q123"})
    ET.SubElement(elem, "tag", {"k": "building", "v": "yes"})
    ET.SubElement(elem, "tag", {"k": "addr:street", "v": "Main St"})
    ET.SubElement(elem, "tag", {"k": "contact:phone", "v": "12345"})

    # Add some tags that should be kept
    ET.SubElement(elem, "tag", {"k": "name", "v": "Test Place"})
    ET.SubElement(elem, "tag", {"k": "amenity", "v": "cafe"})
    ET.SubElement(elem, "tag", {"k": "highway", "v": "residential"})

    clean_element_metadata(elem)

    # Verify remaining tags
    remaining_tags = {tag.get("k"): tag.get("v") for tag in elem.findall("tag")}

    assert "wikidata" not in remaining_tags
    assert "building" not in remaining_tags
    assert "addr:street" not in remaining_tags
    assert "contact:phone" not in remaining_tags

    assert remaining_tags.get("name") == "Test Place"
    assert remaining_tags.get("amenity") == "cafe"
    assert remaining_tags.get("highway") == "residential"
    assert len(remaining_tags) == 3
