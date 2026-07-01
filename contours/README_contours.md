# ⌚ Ultimate Guide: Build Your Own Offline Maps for a Smartwatch
**Applicable platform**: An offline map format exclusive to smartwatches built on the **Actions Technology ATS3085S** chip platform.

## 🛠️ Preparation
1. **Environment requirements**: Install **Python 3.8+** and the **GDAL** toolset (you can easily get this by installing OSGeo4W or QGIS and using its built-in terminal/command prompt).
2. **Install the required package**: Open a terminal and run:
   ```bash
   pip install pyshp
   ```
3. **Download the build tools**: Download and extract the open-source tool repository: [dtg1-map-tools](https://github.com/purrrock/dtg1-map-tools)

---

## 📥 Step 1: Download the Base Data
We need to separately download a "flat base map" and "elevation data (for generating contour lines)."

1. **Download the OSM base map**:
   * Go to [BBBike Extract](https://extract.bbbike.org/) (or [Geofabrik](https://download.geofabrik.de/)).
   * Select your target area (e.g., your local city or region).
   * Choose the **Protocolbuffer (PBF)** format.
   * After downloading, extract it and rename the file to `base.osm.pbf`.
2. **Download the DEM elevation data**:
   * Go to [OpenTopography](https://portal.opentopography.org/raster?opentopoID=OTSRTM.082015.4326.1) (or your local government's open data platform for 20m grid DEMs).
   * Select the exact same bounding box/area as above.
   * Download the data in **GeoTIFF** format.
   * Rename the file to `dem.tif`.

---

## ⚙️ Step 2: Optimize the Base Data (OSM Optimizer V20.7)
The raw map data is far too large for the watch to handle directly. We need to put it through our extreme optimizer (V20.7 Ultimate Edition), which automatically extracts POI centroids, purifies metadata, and unlocks building rendering.

1. **Run the script**: Make sure `base.osm.pbf` is in the same folder as `osm_optimizer.py`, then run:
   ```bash
   python osm_optimizer.py
   ```
   *Once it finishes, the highly optimized `base_map.osm` will be generated.*

---

## ⛰️ Step 3: Generate and Process Contour Lines (GDAL)
Open Command Prompt (CMD) or a terminal, make sure you're in the same directory as the `dem.tif` you just downloaded, and run the following commands in order:

```bash
# 1. Force-convert to WGS84 coordinate system and smooth the terrain using cubic spline interpolation
gdalwarp -t_srs EPSG:4326 -tr 0.00015 0.00015 -r cubicspline dem.tif dem_super.tiff

# 2. Generate a contour-line Shapefile with 10m spacing (attribute name set to ele)
gdal_contour -i 10 -a ele dem_super.tiff contours.shp

# 3. Perform extreme line simplification so the watch doesn't lag
ogr2ogr -simplify 0.00003 contours_final.shp contours.shp	
```
*(After running this, your folder should now contain `contours_final.shp` along with its companion files `.shx`, `.dbf`, etc.)*

---

## 🐍 Step 4: Generate a Spec-Compliant OSM Contour File
We need to convert the simplified Shapefile generated above into OSM format using a high-speed streaming converter.

1. **Run shp2osm.py:**
   ```bash
   python shp2osm.py contours_final.shp contours.osm
   ```

---

## 🔗 Step 5: Safely Merge the Maps
We now have an optimized base map (`base_map.osm`) and a contour map (`contours.osm`). This step merges the two into a single, unified map file using OS-level binary truncation.

1. **Run merge.py:**
   ```bash
   python merge.py
   ```
   *(This generates `map.osm`, which now contains absolutely everything.)*

---

## 🪓 Step 6: Automatically Split the Map File (Tile Generation)
Since the merged `map.osm` is likely huge, the compiler could run into memory bottlenecks. We need to automatically cut it into smaller spatial tiles.

1. **Run split_osm.py:**
   ```bash
   python split_osm.py map.osm
   ```
   *(Once this runs, the script will automatically create an `osm` folder in your current directory, filled with optimally split files like `tile_000.osm`, `tile_001.osm`, etc.)*

---

## 🎨 Step 7: Edit `features.csv` (🚨 The Single Most Important Step)
For the watch to know how to "draw" these contour lines and the newly unlocked buildings (color, thickness, display scale), we need to add rules into the compiler's dictionary file.

1. Open the `features.csv` file inside your `dtg1-map-tools` folder (use Notepad or VS Code — **do not save it with Excel**, or the formatting will break).
2. **⚠️ Do not, under any circumstances,** add the rules at the beginning or middle of the file! The compiler reads top to bottom, so later rules override earlier ones.
3. Scroll to the **absolute end** of `features.csv` (after the last line, press `Enter` to start a new one).
4. Paste in the following five rules (for contours, buildings, and boundaries) and save:

```csv
9901; contour_major ;Black;1000; roads ; highway=c1 ;Contour Major;5113;Black;1000;1;
9902; contour_medium ;DarkGray;500; roads ; highway=c2 ;Contour Medium;5113;DarkGray;500;1;
9903; contour_minor ;Gray;100; roads ; highway=c3 ;Contour Minor;5124;Gray;100;1;
9904; building_generic ;Gray;100; landuse ; building=yes ;Building;7210;Gray;100;1;
9905; admin_boundary ;DashLine;1000; roads ; boundary=administrative ;Boundary;5125;Red;1000;1;
```
*(These rules define exactly how the compiler should translate raw OSM tags into the hardware's binary rendering engine.)*

---

## ⚙️ Step 8: Copy Over the Split Files and Run the Automated Batch Compiler
Back in **Step 6**, the script created an `osm` folder filled with `tile_XXX.osm` files.

1. **Move the folder**: Copy that entire `osm` folder over into your `dtg1-map-tools` (compiler) directory.
2. **Inject custom GPX routes (Optional)**: If you have your own `.gpx` tracks, place them inside the `routes` folder in the compiler directory.
3. **Run the build**: Open a terminal, ensure your current path is inside the `dtg1-map-tools` directory, and run:
   ```bash
   python dtg1_map_compiler.py --poi-mode landuse
   ```
   *(The `--poi-mode landuse` flag tells the compiler to automatically bake POI icons directly into the map logic. The compiler will automatically detect, read, and compile every map tile inside the `osm` folder.)*

---

## 📱 Step 9: On-Device Testing on the Watch (🔥 Clearing the Cache Matters a Lot)
Last step — get the freshly baked maps onto your watch! To prevent the watch from reading corrupted leftover data from old maps, be sure to follow these steps precisely.

1. Connect the watch to your computer with a USB cable.
2. Open the watch's internal storage and go into the `MAP` folder.
3. Open the `osm` folder inside your compiler directory. Inside, you will see subfolders named `tile_000`, `tile_001`, etc.
4. **Copy all of these tile folders** directly into the watch's `MAP` directory. *(Do not copy the raw `.osm` files, only the folders containing the compiled `.mlp`, `.idx`, and `.db` files!)*
5. Unplug the USB cable, open the map app on the watch, and switch over to your new maps.

### 🎯 On-Device Checks to Confirm Success:
* After opening the map, you should immediately see thick black lines (the 100m major contours).
* As you zoom in a bit, thin gray lines (the 10m secondary contours) should smoothly fade in.
* You should now see grey shapes representing buildings (`building=yes`) in urban areas.
* When you zoom out to a 100m or 200m scale, the contour lines should never disappear.
* Try panning and zooming around — the watch should stay smooth and highly responsive, entirely thanks to the extreme multi-stage optimization pipeline we just applied!