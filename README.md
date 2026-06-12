🇷🇺 [Читать на русском](README_ru.md)
# Custom Offline Maps for DT NO.1 G1 / VWAR / KKTICK (ATS3085S Platform)

A set of Python utilities for reverse engineering, analyzing, and compiling custom offline maps for **DT NO.1 G1**, **VWAR**, and **KKTICK** smartwatches (as well as other white-label devices based on the **Actions Semiconductor ATS3085S** hardware platform).

If you are looking for **how to install custom maps on a smartwatch** or need better **DT NO.1 G1 offline maps** to sync via the **WearPro** app, this project provides a complete open-source solution. 

The toolset allows building your own highly detailed maps from open sources (e.g., OpenStreetMap). These custom maps are natively hardware-supported and perfectly rendered by the watch's built-in graphics engine.

> **⚠️ Disclaimer** > This is an unofficial project created exclusively through reverse engineering ("black box" and byte-by-byte analysis of memory dumps). The use of these utilities and flashing of modified files to the watch is done at your own risk.

---

## Toolkit Composition

Currently, the codebase provides 100% binary compatibility with the hardware parser of the watch and includes:

* `dtg1_map_specification.md` — **Technical format specification.** Note: the full documentation on the binary structure is moved to this separate file. It contains the byte-by-byte structure of `.mlp`, `.idx`, and `.db` files, the specification of the C-Union architecture, LOD switching modes, and the parsing algorithms of the firmware's graphics engine.
* `dtg1_map_compiler.py` — Vector map compiler. Automatically parses the source OpenStreetMap XML file (`map.osm`), performs topology validation, distributes objects by levels of detail, applies style aliasing, and generates ready-to-use file packages.
* `features.csv` — Modifiable style routing table (LUT) with software culling (Blacklist) support.
* `features_factory.csv` — Original dump of the factory style table (for backup and resetting to factory display parameters).

---

## 📸 Comparison: Factory Map vs. Custom Compiled Map

| Factory Map | Custom Compiled Map | Custom Map with Route| Custom Map with POIs|
| :---: | :---: | :---: | :---: |
| <img src="assets/factory_map.jpg" width="300"/> | <img src="assets/custom_map.jpg" width="300"/> | <img src="assets/gpx_injection.jpg" width="300"/> | <img src="assets/poi_injection.jpg" width="300"/> |


## Installation and Quick Start

1.  Ensure you have Python 3.8 or higher installed. No additional third-party dependencies are required (the project exclusively uses built-in modules: `os`, `struct`, `xml.etree`, `csv`, `math`, `argparse`).
2.  Export the desired map area from [OpenStreetMap](https://www.openstreetmap.org/export) in XML format.
3.  Rename the downloaded file to `map.osm` and place it in the compiler directory.
4.  The minimum files required for the build are: `dtg1_map_compiler.py` and `features.csv`.
5.  Run the compilation script. The compiled files (`roads.mlp`, `roads.idx`, `landuse.db`, `map.name`, etc.) will appear in the current directory.
6.  Copy the generated files to the internal memory of the watch (usually into the `MAP/Map_Name` folder via USB connection).

---

## Command Line Interface (CLI) Parameters

The compiler implements the management of the Points of Interest (POI) layer, the output of which is hardware-suppressed by the ATS3085S graphics engine in current firmware versions.

**Launch syntax:**
```bash
python dtg1_map_compiler.py [arguments]
```

**Available flags:**
* `-h`, `--help` — Output reference information on available arguments.
* `-p MODE`, `--poi-mode MODE` — Generation mode for the Points of Interest (POI) database.
    * `none` *(default)* — Completely ignore POIs during the build. Protects the database from bloating.
    * `native` — Generate original `pois.idx` and `pois.db` binaries. Useful for testing firmware reaction.
    * `landuse` — Integrate POIs into the landuse layer (Pink diamonds).

---

## Style Customization and Object Filtering

The `features.csv` file (Look-Up Table) is the main configuration file of the compiler. It is loaded dynamically upon each launch.

### LUT Table Structure (11 columns)

The configuration consists of 11 columns separated by a semicolon (`;`).
**Header format:**
```text
Code;fclass;Color;LOD;Layer;OSM_Tags;Description;Remap_Code;Remap_Color;Remap_LOD;Enabled
```

**Remapping (aliasing) parameters are of particular importance:**
* **Remap_Code:** The system 32-bit ID into which the object will be forcibly converted. 
    * *Example:* Paved roads are mapped to the yellow color ID 5113.
* **Remap_LOD:** The hardware Z-Culling hide distance (in meters) at which the object will appear on the screen when zooming.

### Software Culling (Blacklist)

To protect the watch's graphics pipeline from RAM overflow and `.idx` binary graph bloating, a software culling system is implemented during the stream parsing stage. The 11th column of the configuration — **`Enabled`** — is responsible for filtering.

* **`1` (or `true`)** — The object is loaded into the compiler and participates in map generation.
* **`0` (or `false`)** — Muted class. Hardware-culled. 

The algorithm utilizes an Early Exit parsing interrupt: upon encountering a tag with the `Enabled=0` value, the `OSMParser` immediately discards the XML node prior to calculating the Bounding Box. This saves CPU time and prevents replacing excluded objects with default gray or green styles.

---
## 🚀 Key Features & Hardware Workarounds

Our custom compiler `dtg1_map_compiler.py` overcomes several critical hardware limitations of the ATS3085S graphics pipeline:

* **Software Z-Culling (Early Exit):** Implemented via the `Enabled` column in the `features.csv` Look-Up Table (LUT). Allows ignoring specific OpenStreetMap tags during XML parsing to save memory and prevent rendering clutter.
* **Namespace Isolation:** Blacklists are strictly isolated by layers (`roads`, `landuse`, `water`, `pois`) to prevent namespace collisions.
* **POI Injection (Landuse Baking):** The watch's hardware engine inherently lacks support for rendering Point of Interest (POI) text and Z-Culling natively. We bypassed this by "baking" point objects into the `landuse` layer. Points are converted into 5x15m elongated rhombuses (observing the strict CW Winding Rule) using a reserved fallback style `7209` (Pink).
* **Text Truncation Bug Fix (Name Sanitization):** The firmware's GUI text-wrapping algorithm contains a bug that truncates strings after a space character (`0x20`). Our compiler pre-processes all object names:
  * Spaces are hardware-escaped to underscores (`0x5F`).
  * Topographic descriptors (e.g., "Street", "Square", "Lake") are dynamically inverted and moved to the end of the name to ensure unique identification (supports multi-language stop-words: EN, RU, BY, Latin).
  * A fallback mechanism automatically assigns the `fclass` value if the object lacks a name, preventing `dBase III` dummy record corruption.
* **GPX Route Injection:** Custom GPX tracks are injected into the map by replacing native `motorway` objects (style `5111`, reserved in ROM for rendering a bold, contrasting orange line) to prevent style collisions, while native motorways are forcibly downscaled to `trunk` (`5112`, yellow line) via code substitution in `features.csv`.

---

## Custom Route Injection (GPX)

The compiler supports direct injection of navigation tracks on top of the base map.
    
1.  Place your route file named `route.gpx` in the root directory of the project.
2.  Run the build. The script will automatically find the track and extract its name from the `<name>` tag. The extracted string is compiled into the `roads.db` attribute database, ensuring the track retains its original name on the watch.
3.  The injected GPX track is converted into an object with the specific type `5111` (Motorway). This code is hardware-reserved in the watch's firmware for rendering a bold, contrasting orange line. To prevent the planned route from blending with actual motorways, all `motorway` objects from the source OSM file are forcibly downscaled to the yellow type `5112` (Trunk) via code substitution in `features.csv`.

> **Note:** To disable injection and build a clean map, simply delete or rename the `route.gpx` file in the compiler's root directory before launching.

---

## Factory Reset

The original style table, built through reverse engineering, is preserved in the reference file `features_factory.csv`.

To revert all user modifications to colors, blacklists, and levels of detail:
1.  Delete the currently modified working file `features.csv`.
2.  Make a copy of the `features_factory.csv` file and rename it to `features.csv`.
3.  Run the build script `python dtg1_map_compiler.py` to recompile the `.mlp` and `.idx` binaries with factory parameter values.

## 🛠 Reverse Engineering Tools (/tools)
The repository includes internal scripts used during the reverse engineering of the ATS3085S graphics pipeline:

dtg1_idx_dumper.py — A decompiler that extracts .idx spatial indices into readable CSV formats for binary analysis.
roads_fuzzer.py — Generates a coordinate grid of geometric primitives to test hardware Z-Index rendering and C-Union structural limits.
And other tools.

## 🗺️ Roadmap & Future Plans (v1.1+)

The core compilation engine is now stable (v1.0). Our next major goal is to transform this CLI tool into a user-friendly "Map-as-a-Service" platform for the smartwatch community.

**Planned Features:**
* [ ] **Pre-compiled Map Archives:** Regularly updated, ready-to-download map packages for entire countries or popular tourist regions. These will be hosted directly in GitHub Releases—no Python installation required for end-users.
* [ ] **Web-based GUI Generator:** A browser-based interface where users can select a custom Bounding Box (up to 50x50 km), toggle specific layers/POIs, upload their GPX track for injection, and compile the map on-demand via a backend connected to the Overpass API.