import pytest
import xml.etree.ElementTree as ET

from osm_optimizer import clean_element_metadata

def test_clean_element_metadata_removes_attributes():
    elem = ET.Element("node", {
        "id": "123",
        "timestamp": "2023-10-25T12:00:00Z",
        "changeset": "12345",
        "uid": "67890",
        "user": "johndoe",
        "lat": "51.5",
        "lon": "-0.1"
    })

    clean_element_metadata(elem)

    assert "timestamp" not in elem.attrib
    assert "changeset" not in elem.attrib
    assert "uid" not in elem.attrib
    assert "user" not in elem.attrib

    # Should not remove other attributes
    assert elem.attrib["id"] == "123"
    assert elem.attrib["lat"] == "51.5"
    assert elem.attrib["lon"] == "-0.1"

def test_clean_element_metadata_adds_missing_attributes():
    elem = ET.Element("node", {"id": "123"})

    clean_element_metadata(elem)

    assert elem.attrib["version"] == "1"
    assert elem.attrib["visible"] == "true"

def test_clean_element_metadata_preserves_existing_attributes():
    elem = ET.Element("node", {
        "id": "123",
        "version": "2",
        "visible": "false"
    })

    clean_element_metadata(elem)

    assert elem.attrib["version"] == "2"
    assert elem.attrib["visible"] == "false"

def test_clean_element_metadata_removes_drop_keys():
    elem = ET.Element("node", {"id": "123"})

    # Add tags to be dropped
    ET.SubElement(elem, "tag", {"k": "wikidata", "v": "Q123"})
    ET.SubElement(elem, "tag", {"k": "phone", "v": "12345"})
    ET.SubElement(elem, "tag", {"k": "building", "v": "yes"})

    # Add tag to be kept
    ET.SubElement(elem, "tag", {"k": "name", "v": "My Node"})

    clean_element_metadata(elem)

    tags = elem.findall("tag")
    assert len(tags) == 1
    assert tags[0].get("k") == "name"

def test_clean_element_metadata_removes_drop_prefixes():
    elem = ET.Element("node", {"id": "123"})

    # Add tags with drop prefixes
    ET.SubElement(elem, "tag", {"k": "addr:street", "v": "Main St"})
    ET.SubElement(elem, "tag", {"k": "contact:email", "v": "test@example.com"})

    # Add tag to be kept
    ET.SubElement(elem, "tag", {"k": "amenity", "v": "cafe"})

    clean_element_metadata(elem)

    tags = elem.findall("tag")
    assert len(tags) == 1
    assert tags[0].get("k") == "amenity"

def test_clean_element_metadata_no_tags():
    elem = ET.Element("node", {"id": "123"})

    clean_element_metadata(elem)

    # Should not crash, and should add default attributes
    assert elem.attrib["version"] == "1"
    assert elem.attrib["visible"] == "true"
    assert len(elem.findall("tag")) == 0
