# DT G1 MAP FORMAT TECHNICAL SPECIFICATION (Reverse Engineering)

**Version:** 4.0 (C-Union Node / Mode Switch)
**Application Area:** GIS data compilers/decompilers for wearable devices based on the ATS3085S platform (DT NO.1 G1)

---

## 1. GLOBAL LAYER HEADER (YZL HEADER)

Each binary map layer file (`.idx`, `.mlp`, `.db`) must begin with a global system header with a fixed length of **32 bytes**. The header is used by the firmware bootloader for memory mapping and integrity checking via the MD5 algorithm.

### 1.1. YZL Header Byte Structure

| Offset | Size | Data Type | Field Description | Value / Format |
| :--- | :--- | :--- | :--- | :--- |
| `0x00` | 3 | `char[3]` | **Magic Signature (Magic)** | Strictly `b'YZL'` (`59 5A 4C`) |
| `0x03` | 1 | `uint8` | **File Magic Extension** | For `.mlp` and `.db` = `0x00`. For `.idx` variable (`0x08`, `0x10`, `0x0C`, etc.) |
| `0x04` | 4 | `uint32` | **Payload Size** | In bytes (Little-Endian). Formula: `File Size - 32` |
| `0x08` | 4 | `uint32` | **RAM Load Type** | For `.mlp`/`.db` = `0x00000000`. For `.idx` = `0x02000004` (LE: `0x04000002`) |
| `0x0C` | 4 | `uint32` | **LOD 2 Section Size** | In bytes (Big-Endian). For `fseek` from EOF (fast pointer calculation). For `.mlp` and `.db` = `0x00000000` |
| `0x10` | 16 | `byte[16]`| **MD5 Checksum** | Computed in hardware from the payload body (from `0x20` to EOF) |

> **Exception for the POI layer (`pois.idx`):**
> * **Offset `0x03` (Magic Extension):** Value is strictly `0x00`.
> * **Offset `0x08` (RAM Load Type):** Takes the value `0x00 00 00 04` (Little-Endian: `0x04000000`), identical to `.mlp` and `.db` headers.

---

## 2. GEOMETRY FILE STRUCTURE (.MLP)

The `.mlp` file contains raw ordered coordinates of vertices for linear and polygonal objects (unified polyline/polygon storage). Coordinates are represented in a fixed-precision integer format.

### 2.1. Local Geometry Header (Geometry Record Header)
Immediately following the 32 bytes of the global YZL header is an array of geometry records. Each record is prefaced by its own 8-byte mini-header.

| Offset | Size | Data Type | Endianness | Field Description |
| :--- | :--- | :--- | :--- | :--- |
| `0x00` | 4 | `uint32` | **Big-Endian** (`>I`) | Geometry record sequence number (starting from 1) |
| `0x04` | 4 | `uint32` | **Little-Endian** (`<I`) | Geometry record body length in bytes (Content Length) |

### 2.2. Geometry Record Body
The record body size in bytes is strictly equal to the `Content Length` field value.

| Offset | Size | Data Type | Field Description / Specifics |
| :--- | :--- | :--- | :--- |
| `0x00` | 16 | `int32[4]` | `bbox_int` = `[minx, miny, maxx, maxy]` |
| `0x10` | 4 | `int32` | `num_parts` — total number of segments/rings in the object |
| `0x14` | 4 | `int32` | `num_points` — total number of points (vertices) in the object |
| `0x18` | `num_parts * 4` | `uint32[]` | `parts` array. Point indices (offsets) where each segment begins (starting from 0). *These are indices, not contour lengths.* |
| `0x18 + (num_parts * 4)` | `num_points * 8` | `int32[2][]` | `points` array (pairs of `[X, Y]`). Conversion to Float: `Coordinate = Value / 1000000.0` |

### 2.3. Polygon Topology (Multipolygons)
The hardware triangulator supports complex polygon structures (e.g., lakes with islands), similar to the ESRI Shapefile format:

* All points of all rings (both outer contours and inner "holes") are flattened into a single `points` array.
* The `num_parts` field indicates the total number of rings in the multipolygon.
* The `parts` array contains the start indices of each ring (e.g., `[0, 41]`).
* Rings must be closed (the first and last coordinate of the contour must match).
* Single closed polygons are always classified as Outer and are wound clockwise.

**Winding Rules:**
* **Outer (External contours):** Strictly **Clockwise (CW)**.
* **Inner (Internal holes):** Strictly **Counter-Clockwise (CCW)**.

---

## 3. SPATIAL INDEX STRUCTURE (.IDX)

The `.idx` file organizes data by Levels of Detail (LOD) and clusters them for fast invisible geometry discarding (BBox Culling).

### 3.1. Flat List Architecture and Hardware State Machine
Within a LOD, objects are packed into flat groups (clusters) of a fixed size (maximum of 14 data objects). The engine uses a unified 28-byte frame for both Navigation Nodes (Nav Node) and Data Nodes (Data Node).

Fundamental rule: the last 8 bytes of any node (offset `+20` and `+24`) are always `v1` and `v2` pointers. This allows the hardware parser to traverse the memory graph without analyzing the node type.

### 3.2. Node Type Specification (C-Union Pattern)

```c
struct UnifiedNode {
    union {
        struct { float xmin, ymin, xmax, ymax; uint32_t type; } data; // 20 bytes
        struct { uint32_t v3_jump; float xmin, ymin, xmax, ymax; } nav;  // 20 bytes
    } payload;
    uint32_t v1;  // offset 20
    uint32_t v2;  // offset 24
};
```

#### 1. Data Node
Contains the cartographic primitive itself. Unpacking format (Python): `<ffffIII`.

| Offset | Size | Type | Description |
| :--- | :--- | :--- | :--- |
| `0x00 - 0x0F` | 16 | `float[4]` | BBox (xmin, ymin, xmax, ymax) |
| `0x10 - 0x13` | 4 | `uint32` | **Type** (OSM object class code, e.g., `5114` — secondary road) |
| `0x14 - 0x17` | 4 | `uint32` | **v1** (Pointer to Payload in `.mlp`). Must skip the 8-byte geometry header |
| `0x18 - 0x1B` | 4 | `uint32` | **v2** (Index in `.db`). `1` — empty record (unnamed), `>=2` — unique names |

> **POI Layer Anomaly (`pois.idx`):**
> * The `pois.mlp` file does not exist.
> * **Offset `0x00 - 0x0F` (Point Injection):** 32-bit Float coordinates are written directly into the BBox. Strict duplication is required: `minX == maxX` and `minY == maxY`.
> * **Offset `0x14 - 0x17` (v1):** Ignored by the graphics parser when the topology marker is `0x01000000`. In factory maps, these are increasing values.

#### 2. Nav Node (Navigation Node / Macro Node)**
Открывает геометрию (Mode 0x00) или ветку иерархического R-дерева (Mode > 0x01).
Формат распаковки (Python): `<IffffII`.

| Offset | Size | Type | Description |
| ------ | ------ | ------ | ------ |
| 0x00 - 0x03 | 4 | uint32 | `v3_jump` (Early Exit jump). Размер всего дочернего поддерева в байтах + 8 байт компенсации префетча. |
| 0x04 - 0x13 | 16 | float[4] | Cluster BBox (охватывает все вложенные дочерние BBox) |
| 0x14 - 0x17 | 4 | uint32 | `v1` (Tree Depth / Высота поддерева). `0` = потомками являются `Data Node`. `>0` = потомками являются макро-узлы (`Nav Node` с уровнем `v1 - 1`). |
| 0x18 - 0x1B | 4 | uint32 | `v2` (Количество дочерних узлов непосредственно внутри текущего кластера). |

> **Warning:** The `v3_jump` field requires mandatory hardware prefetch compensation (`+8` bytes to the jump length).

---

## 4. ATTRIBUTE DATABASE STRUCTURE (.DB)

The `.db` file stores text names (`name` tags). The format is a standard **dBase III (DBF)** DBMS encapsulated within a YZL container.

### 4.1. DBF Header Specification (Size: 161 bytes)
Starts at offset `0x20` (immediately after the YZL header).
* **dBase III Magic Byte:** `0x03` (Offset `0x20`).
* **Number of Records:** 4 bytes (LE) at offset `0x24`. `(Named objects + 1)`.
* **Database Header Size:** Strictly `161` (`0xA1 0x00`) at offset `0x28`.
* **Record Size:** Strictly `145` (`0x91 0x00`) at offset `0x2A`.

### 4.2. Record Fields Mapping
Each field description length is 32 bytes. Field description terminator is `0x0D`. 
1. `osm_id` (Type `C`, 12 bytes).
2. `code` (Type `C`, 4 bytes).
3. `fclass` (Type `C`, 28 bytes).
4. `name` (Type `C`, 100 bytes).

### 4.3. Importance of Record 0
The first physical data record in `.db` (145 bytes) **must strictly be filled with zeros** (`\x00`).
* Unnamed map objects point to it (pointer `v2 = 1`).
* Named records are packed subsequently (`v2 = 2`, `v2 = 3`, etc.).
* Each record begins with a `0x20` byte (dBase validity indicator).

> **Exception for the POI layer:**
> In `pois.db`, the zero dummy record is absent. The first data record starts immediately after the `0x0D` terminator. 1-based indexing is applied: `v2 = 1` points to the first physical record. Unnamed POIs receive `v2 = 0`.

---

## 5. OPERATION ALGORITHMS (PSEUDOCODE)

### 5.1. Spatial Index (IDX) Compilation Algorithm

**INPUT:** List of objects with computed `v1` (from MLP), `v2` (from DB), BBox, and Code.
**OUTPUT:** Binary map layer file `.idx`.

1. Initialize an empty byte buffer `idx_body`.
2. Write the signature of the first LOD: `idx_body.extend(b"SQT\x01" + pack("<I", 1))`.
3. Divide the input list of objects into spatial groups (Spatial Quad Tree) of up to 14 elements (depending on density).
4. For each group of objects:
   * Compute the common group BBox (min/max X and Y among all objects in the group).
   * Compute `nav_v1`: for the 1st cluster = `0`. For subsequent ones = `v1` pointing to the absolute end of the previous cluster's geometry.
   * Compute `nav_v2` = number of objects in the cluster.
   * Compute `jump_v3` = `(number_of_objects_in_group * 28) + 8` (the `+8` offset compensates for prefetch).
   * Pack Nav Node: `pack("<IffffII", jump_v3, MinX, MinY, MaxX, MaxY, nav_v1, nav_v2) -> idx_body`.
   * For each object, pack a Data Node consecutively: `pack("<ffffIII", obj.MinX, obj.MinY, obj.MaxX, obj.MaxY, obj.code, obj.v1, obj.v2) -> idx_body`.
5. Compile LOD 1 and LOD 2 sections.
   * Navigation nodes are omitted if the section consists of <=14 objects.
   * Nodes are always arranged consecutively without padding.
   * If a LOD is empty, the section consists of exactly 16 bytes: `[SQT\x01] [00 00 00 00] [00 00 00 00] [00 00 00 00]`.
6. Calculate `lod2_size` (exact size of the LOD 2 section).
7. Generate YZL Header: `b"YZL\x0C" + pack("<I", len(idx_body)) + b"\x01\x00\x00\x04" + pack(">I", lod2_size) + MD5_Hash(16 bytes)`.
8. Save YZL Header + `idx_body` to the file `LAYER_NAME.idx`.

### 5.2. Hardware Parsing Algorithm via State Machine

```python
# 1. Read SQT header (16 bytes)
magic, res, mode, count = struct.unpack("<IIII", file.read(16))

if count == 0:
    return # Empty LOD, proceed to the next

if mode == 0x00: # Flat List Mode
    for _ in range(count):
        node_bytes = file.read(28)
        xmin, ymin, xmax, ymax, obj_type, v1, v2 = struct.unpack("<ffffIII", node_bytes)
        render_object(xmin, ymin, xmax, ymax, obj_type, v1, v2)

elif mode == 0x01: # Clustered Mode
    for _ in range(count):
        nav_bytes = file.read(28)
        v3_jump, c_xmin, c_ymin, c_xmax, c_ymax, nav_v1, obj_count = struct.unpack("<IffffII", nav_bytes)
        
        if not is_in_screen(c_xmin, c_ymin, c_xmax, c_ymax):
            # Culling: Jump to the next Nav Node
            # v3_jump ALREADY includes the +8 bytes of hardware pipeline prefetch compensation!
            file.seek(v3_jump - 8, 1) # Equivalent to SEEK_CUR
            continue
            
        # Cluster is visible, read nested Data Nodes
        for _ in range(obj_count):
            data_bytes = file.read(28)
            xmin, ymin, xmax, ymax, obj_type, v1, v2 = struct.unpack("<ffffIII", data_bytes)
            render_object(xmin, ymin, xmax, ymax, obj_type, v1, v2)
```

---

## 6. INTERNAL STYLE TABLE (LUT) AND HARDWARE Z-CULLING

The `code` field is hardcoded to an internal Look-Up Table (LUT) style table corresponding to the **Geofabrik GIS** standard. The watch does not render all objects constantly, employing **hardware Z-Culling**. 

**Hardcoded Visibility Thresholds (Display Scale):**
* **Scale 1000+ m:** Only major highways (`5111`, `5112`, `5113`, `5114`). Polygons (landuse, water) are hardware-blocked by the GPU.
* **Scale 500 m:** Minor roads and slip roads (`5115`, `513x`). Rendering of water bodies and forests is permitted.
* **Scale 100 m:** Local streets (`512x`).
* **Scale 50 m:** Agricultural and technical roads (`514x`).
* **Scale 20 m (Max zoom):** Pedestrian and bicycle paths, steps (`515x`).

> **Engineering Conclusion:** Unknown codes or minor roads at large scales are ignored. To force rendering (e.g., of a `5152` cycleway from afar), its `code` must be remapped during compilation to a code with a lower threshold (e.g., `5141`).

---

## 7. MULTI-LEVEL SQT SCALING (LOD 0, LOD 1, LOD 2)

The `.idx` file contains three consecutive spatial index lists separated by `SQT\x01` signatures.
* **LOD 0 (Section 1):** Maximum detail level. Covers all primitives.
* **LOD 1 (Section 2):** Medium detail level.
* **LOD 2 (Section 3):** Overview detail level.

Each LOD level begins with a 16-byte header (state machine):

| Offset | Field Description | Value / Format |
| :--- | :--- | :--- |
| `0x00 - 0x03` | **Signature** | `SQT\x01` (`53 51 54 01`) |
| `0x04 - 0x07` | **Topology Marker** | `0x00000000` — Vector (read `.mlp`)<br>`0x01000000` — Point (read from BBox) |
| `0x08 - 0x0B` | LOD Mode Switch | `0x00` = Flat List<br>`0x01` = Одноуровневые кластеры<br>`>0x01` = Глубина R-дерева (Например, `0x05` означает 5 уровней вложенности Nav Nodes). |
| `0x0C - 0x0F` | Count | Количество корневых узлов верхнего уровня (внутри текущего SQT-блока). |

The boundary between detail levels is determined by the state machine logic (completion of batch reading). An empty section consists strictly of 16 bytes with `Mode = 0` and `Count = 0`. The POI layer is single-level (LOD 0 only); empty sections for it are not generated.

**LOD Connection to Z-Culling:** Objects are distributed across LODs according to thresholds. Objects with a `500 m` threshold are written to LOD 1, and `1000 m` to LOD 2.

---

## 8. VALIDATION BYPASS (SYSTEM DUMMIES) AND CONFIGURATION

### 8.1. Empty Layers (System Dummies)
The watch parser strictly requires the presence of at least the `landuse` layer. Factory dummy layers are used for empty areas.

### 8.2. Configuration File map.name
Serves as an entry point for the firmware and camera centering. Format is strict JSON.
* **Structure:** `{"centerLat":float,"centerLon":float,"mapName":"string"}`
* The center is calculated from the global BBox of all map layers.
* **Critical Requirement:** The built-in JSON parser does not tolerate whitespaces. During compilation, apply minification: `json.dumps(data, separators=(',', ':'))`.

---

## 9. HARDWARE RENDERING AND STYLE TABLE (LUT)

Visualization relies on a Look-Up Table hardcoded into ROM, where color and thickness are determined by the `Type` field (Offset `0x10` in `Data Node`).

### 9.1. System Aliases
* `8200` (Water Layer): ID for blue fill (rivers, lakes, reservoirs).
* `5111` (Roads Layer): Wide orange line (Motorway).
* `5112` - `5114` (Roads Layer): Yellow line (Trunk/Primary/Secondary).
* `>= 5124` (Roads Layer): Basic gray line (Local/Pedestrian).

### 9.2. Hardware Z-Culling (LOD Mapping)
The compiler must distribute objects into `.idx` blocks based on thresholds:
* **LOD 2 (Offset `0x0E` in YZL):** Basic routing (Motorways, Water, Major Landuse). Visibility threshold `>= 1000`.
* **LOD 1 (Flat SQT list):** Secondary geometry. Visibility threshold `500`.
* **LOD 0:** All geometry (incl. paths and sidewalks). Visibility threshold `< 500`.