import sys
import csv
from typing import Dict, Tuple, Set

class HWConstants:
    """Hardware reserved constants for Fallback mechanics."""
    WATER_CODE = 8200

class LookupTables:
    """
    Look-Up Tables (LUT) and routing registries for the parser.
    Includes hash tables for Advanced Key-Value Tag Routing and
    layer-isolated Blacklist registries (Namespace Collisions prevention).
    """
    HIGHWAY_CODES: Dict[str, int] = {}
    POLYGON_CODES: Dict[str, int] = {}
    POI_CODES: Dict[str, int] = {}
    DISPLAY_SCALES: Dict[int, int] = {}
    POI_SHAPES: Dict[str, str] = {}
    
    # Isolation of Blacklist registries by layer (Software Culling)
    DISABLED_ROADS: Set[str] = set()
    DISABLED_LANDUSE: Set[str] = set()
    DISABLED_POIS: Set[str] = set()
    DISABLED_WATER: Set[str] = set()
    
    # Advanced Key-Value Tag Routing: layer -> (key, value) -> fclass
    TAG_ROUTING: Dict[str, Dict[Tuple[str, str], str]] = {
        'pois': {}, 
        'roads': {}, 
        'landuse': {}, 
        'water': {}
    }

    @classmethod
    def load_from_csv(cls, filepath: str = "features.csv") -> None:
        """
        Method for parsing the routing configuration (LUT).
        Loads mapping rules, populates tables for Early Exit
        and dynamically binds aliases (LOD/Color/Shape).
        """
        print(f"[>] Loading LUT style table from {filepath}...")
        
        try:
            with open(filepath, mode="r", encoding="utf-8") as f:
                reader = csv.reader(f, delimiter=";")
                next(reader, None)  # Skip header
                
                loaded_records = 0
                for row in reader:
                    # Check minimum length of configuration string
                    if len(row) < 11:
                        continue
                        
                    fclass = row[1].strip()
                    layer = row[4].strip()
                    osm_tag = row[5].strip()
                    enabled_flag = row[10].strip().lower()
                    
                    # === Software Culling Mechanism (Early Exit parsing) ===
                    # Isolate registries to prevent Namespace Collisions
                    if enabled_flag in ("0", "false", "no", "off", ""):
                        if layer == "roads":
                            cls.DISABLED_ROADS.add(fclass)
                        elif layer == "pois":
                            cls.DISABLED_POIS.add(fclass)
                        elif layer == "water":
                            cls.DISABLED_WATER.add(fclass)
                        else:
                            cls.DISABLED_LANDUSE.add(fclass)
                        continue
                        
                    # === Advanced Key-Value Tag Routing ===
                    # Parsing complex OSM tags (e.g. amenity=hospital, place=city)
                    if osm_tag and "=" in osm_tag:
                        for tag_pair in osm_tag.split(","):
                            if "=" in tag_pair:
                                k, v = tag_pair.split("=", 1)
                                if layer not in cls.TAG_ROUTING:
                                    cls.TAG_ROUTING[layer] = {}
                                cls.TAG_ROUTING[layer][(k.strip(), v.strip())] = fclass
                                
                    # Parsing remapping parameters (hardware IDs and SQT indexation levels)
                    try:
                        remap_code = int(row[7].strip())
                        remap_lod = int(row[9].strip())
                    except ValueError:
                        continue
                        
                    # === Populating LUT tables for the binary graph generator ===
                    if layer == "roads":
                        cls.HIGHWAY_CODES[fclass] = remap_code
                        cls.DISPLAY_SCALES[remap_code] = remap_lod
                    elif layer in ("landuse", "water"):
                        cls.POLYGON_CODES[fclass] = remap_code
                        cls.DISPLAY_SCALES[remap_code] = remap_lod
                    elif layer == "pois":
                        cls.POI_CODES[fclass] = remap_code
                        cls.DISPLAY_SCALES[remap_code] = remap_lod
                        # Fallback mechanism for missing POI shape
                        shape_val = row[11].strip().lower() if len(row) > 11 else "rhombus"
                        cls.POI_SHAPES[fclass] = shape_val if shape_val else "rhombus"
                        
                    loaded_records += 1
                    
            print(f"    Successfully imported rules: {loaded_records}")
            print(f"[i] LUT loaded. Roads: {len(cls.HIGHWAY_CODES)}, Polygons: {len(cls.POLYGON_CODES)}, POI: {len(cls.POI_CODES)}")
            
            # Failsafe for the water layer (must be present in LOD2)
            if HWConstants.WATER_CODE not in cls.DISPLAY_SCALES:
                cls.DISPLAY_SCALES[HWConstants.WATER_CODE] = 1000
                
        except FileNotFoundError:
            print(f"[-] Error: LUT configuration file {filepath} not found.")
            sys.exit(1)
        except Exception as e:
            print(f"[-] Critical error parsing {filepath}: {e}")
            sys.exit(1)
