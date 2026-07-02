# AI Agent Instructions (AGENTS.md)

This file contains strict rules, constraints, and contextual information for AI agents (GitHub Copilot, Cursor, Claude Code, etc.) interacting with this repository.

## Project Context
* **Name:** dtg1-map-tools
* **Domain:** Reverse-engineering, spatial processing, and compiling custom offline vector maps for DT NO.1 G1 smartwatches (Actions Semiconductor ATS3085S SoC / Zephyr RTOS).
* **Core Stack:** Python 3.10+
* **Primary Formats:** OpenStreetMap XML (`.osm`) for input; Proprietary Binary (`.mlp`, `.idx`, `.db`) for hardware output.

## Critical Hardware & Binary Constraints (DO NOT IGNORE)
1. **NO HALLUCINATIONS IN BYTE PACKING:** The target hardware parser is a closed "black box" C-Union node structure. When modifying `dtg1_bin_writer.py`, you MUST strictly adhere to the byte-alignment, chunk sizes, and padding documented in `dtg1_map_specification.md`. Always use Little-Endian (`<`) in `struct.pack`.
2. **Clockwise Winding Rule:** The ATS3085S hardware rasterizer strictly requires all polygons (outer rings) to be sorted in a Clockwise (CW) direction. Do not alter geometry math algorithms without verifying CW compliance.
3. **SRAM Limits & Watchdog Resets:** The smartwatch has extreme memory limits. Avoid introducing high-vertex geometric shapes or disabling hardware Z-Culling (LOD). Be aware of current workarounds like "POI Baking" and "GPX Route Injection" before refactoring logic.
4. **Data Types:** Coordinates are heavily scaled and stored as signed 32-bit integers (`int32_t`) relative to spatial quadrants. Keep precision limits in mind.

## Python & Architecture Conventions
1. **Modular Architecture (SRP):** The codebase is intentionally split into discrete modules (`dtg1_osmparser`, `dtg1_bin_writer`, `dtg1_geometry`, etc.). Do not mix binary serialization logic with OSM XML parsing logic.
2. **Memory Management (XML):** When parsing `.osm` files, ALWAYS use streaming parsing (`xml.etree.ElementTree.iterparse()`). NEVER load the entire XML tree into RAM using `ET.parse()`, as source maps can exceed several gigabytes.
3. **Error Handling:** Avoid generic `except Exception:` blocks. Catch explicit parsing or packing errors and clearly log which OSM ID (Node/Way/Relation) or byte chunk failed.
4. **Type Hinting:** All new Python functions must use explicit static type hints.

## Agent Workflow Rules
1. Before proposing structural changes to binary file generation, you MUST read `dtg1_map_specification.md`.
2. Do not introduce external C/C++ dependencies for packing; use Python's built-in `struct` and `math` libraries to ensure standalone `.exe` compatibility.
3. If creating an automated Pull Request, prefix the title with `[AI]` and explicitly state which binary layer (`.mlp`, `.idx`, `.db`) your changes affect.