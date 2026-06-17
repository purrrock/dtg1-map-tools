🇷🇺 [Читать на русском](README_ru.md)
# Custom Offline Maps for DT NO.1 G1 / VWAR / KKTICK / Amolde HD300 Pro (ATS3085S Hardware Platform, Zephyr RTOS Software Platform)

A set of Python utilities for reverse engineering, analyzing, and compiling custom offline maps for **DT NO.1 G1**, **VWAR**, **KKTICK** and **Amolde** smartwatches (as well as other white-label devices based on the **Actions Semiconductor ATS3085S** hardware platform).

If you are looking for **how to install custom maps on a smartwatch** or need better **DT NO.1 G1 offline maps** to sync via the **WearPro** app, this project provides a complete open-source solution. 

The toolset allows building your own highly detailed maps from open sources (e.g., OpenStreetMap). These custom maps are natively hardware-supported and perfectly rendered by the watch's built-in graphics engine.

> **⚠️ Disclaimer** > This is an unofficial project created exclusively through reverse engineering ("black box" and byte-by-byte analysis of memory dumps). The use of these utilities and flashing of modified files to the watch is done at your own risk.

---

## 🚀 Core Compiler Features

The compiler has been significantly upgraded to bypass native firmware limitations and optimize resource consumption:

* **Point of Interest (POI) Icon Baking:** Circumvents the hardware graphics pipeline limitation (which natively drops POI rendering) by parametrically "baking" point objects into the `landuse` layer. Generates low-poly geometric primitives (triangles, squares, hexagons...) with automatic display perspective distortion compensation (Y-multiplier = 1.5).
* **Software Culling (Early Exit Parsing):** Highly optimized two-pass XML streaming (`xml.etree.ElementTree.iterparse`). Drops disabled routing nodes immediately during tree traversal via the 11th column (`Enabled`) in the LUT configuration.
* **- Dynamic Hardware Overrides (Tag Interception): Overrides standard LUT routing rules on the fly based on multidimensional OSM tags.
    - Road Surface Analysis: Dynamically analyzes `surface` and `smoothness` tags. Automatically downgrades routing classes (e.g., `primary` roads) to unpaved gray paths if `smoothness=bad` or `surface=dirt`. Preserves original LUT colors for non-vehicle infrastructure (footways, cycleways) via an internal    exclusion mask.
    - Access Restrictions: Physical barriers with restricted access (`access=private/no/permit`) are intercepted prior to LUT evaluation and forced into pink diagonal crosses.
* **Namespace Collision Isolation:** Blacklist registries are strictly isolated by layer (`pois`, `roads`, `landuse`, `water`) to prevent `fclass` routing conflicts between differently categorized objects.
* **GPX Track Integration:** Natively compiles custom `.gpx` user routes directly into the hardware vector graph.
* **Advanced Key-Value Tag Routing:** Fully parses the `OSM_Tags` column from `features.csv` to resolve namespace collisions. Objects are strictly routed using precise `key=value` hash table lookups (e.g., `shop=bicycle` -> `bicycle_shop`) before applying fallback heuristics. This ensures all complex GIS classes are compiled without data loss.

---


## Toolkit Composition

Currently, the codebase provides 100% binary compatibility with the hardware parser of the watch and includes:

* `dtg1_map_specification.md` — **Technical format specification.** Note: the full documentation on the binary structure is moved to this separate file. It contains the byte-by-byte structure of `.mlp`, `.idx`, and `.db` files, the specification of the C-Union architecture, LOD switching modes, and the parsing algorithms of the firmware's graphics engine.
* `dtg1_map_compiler.py` — Vector map compiler. Automatically parses the source OpenStreetMap XML file (`map.osm`), performs topology validation, distributes objects by levels of detail, applies style aliasing, and generates ready-to-use file packages.
* `features.csv` — Modifiable style routing table (LUT) with software culling (Blacklist) support.
* `features_factory.csv` — Original dump of the factory style table (for backup and resetting to factory display parameters).

---

## ⛰️ Elevation Contours (Experimental Community Tools)

Thanks to contributions from the XDA-Developers community, the repository now includes experimental utilities for processing topographic elevation contours (located in the `/contours` directory). 

These scripts allow you to merge elevation LineStrings into your base OpenStreetMap data before compilation.

**⚠️ CRITICAL HARDWARE WARNING:** The ATS3085S graphics coprocessor has strict limitations on the number of simultaneous vectors it can draw. Elevation contours consist of thousands of dense points. If you compile them with a high Level of Detail, the watch will experience a memory overflow.

If you use the contours feature, you **MUST** configure your `features.csv`.

Read `/contours/README_contours.md`.

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

### ⚡ Preprocessing Large Maps (Highly Recommended)

If you are compiling large areas, entire countries, or experiencing Out-Of-Memory errors on your PC during compilation, you should preprocess your raw `.osm` file using the `osm_optimizer.py` utility. 

The smartwatch's hardware graphics accelerator (ATS3085S) has vertex buffer limitations. Rendering excessively long, continuous lines (like major highways) as single objects can cause the watch UI to freeze or trigger a Soft Reset. The optimizer solves this by:

1. **Hardware-Safe Chunking:** Safely slicing extremely long linear routes into smaller segments (e.g., 100 vertices per chunk) while preserving the mathematical topology of closed polygons (lakes, forests) to prevent scanline rendering glitches.
2. **Aggressive Metadata Stripping:** Removing heavy OSM metadata (timestamps, users, changesets) and dropping globally blacklisted tags (e.g., `power`, `building`, `addr:*`) to drastically reduce the intermediate file size.

**How to use the optimizer:**

```bash
# 1. Preprocess and shrink the raw OSM file
python osm_optimizer.py map.osm map_optimized.osm

# 2. Feed the optimized file into the main compiler (rename map_optimized.osm to map.osm)
python dtg1_map_compiler.py
```
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
    * `landuse` — Integrate POIs into the landuse layer.

---

## Style Customization and Object Filtering

The `features.csv` file (Look-Up Table) is the main configuration file of the compiler. It is loaded dynamically upon each launch.

### LUT Table Structure (11 columns)

The configuration consists of 11 columns separated by a semicolon (`;`).
**Header format:**
```text
Code;fclass;Color;LOD;Layer;OSM_Tags;Description;Remap_Code;Remap_Color;Remap_LOD;Enabled;;Shape
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

## Custom Route Injection (GPX)

The compiler supports the direct batch injection of multiple navigation tracks on top of the base map.

1.  Place your `.gpx` route files into the `routes/` folder located in the root directory of the project.
2.  Run the build. The script will automatically scan the directory and process all tracks sequentially.
3.  The track's name is dynamically extracted from the `<name>` tag inside the GPX XML. If the tag is missing, the compiler adopts the file's name.
4.  Each injected GPX track is assigned a unique system ID (`user_track_001`, `user_track_002`, etc.) to prevent structural collisions in the `roads.db` attribute database.
5.  Tracks are converted into geometric objects with the specific type `5111` (Motorway). This code is hardware-reserved in the watch's firmware for rendering a bold, contrasting orange line. To prevent planned routes from blending with actual motorways, all motorway objects from the source OSM file are forcibly downscaled to the yellow type `5112` (Trunk) via code substitution in `features.csv`.

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

The core compilation engine is now stable. Our next major goal is to transform this CLI tool into a user-friendly "Map-as-a-Service" platform for the smartwatch community.

**Planned Features:**
* [ ] **Pre-compiled Map Archives:** Regularly updated, ready-to-download map packages for entire countries or popular tourist regions. These will be hosted directly in GitHub Releases—no Python installation required for end-users.
* [ ] **Web-based GUI Generator:** A browser-based interface where users can select a custom Bounding Box (up to 50x50 km), toggle specific layers/POIs, upload their GPX track for injection, and compile the map on-demand via a backend connected to the Overpass API.