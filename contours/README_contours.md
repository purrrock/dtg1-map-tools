# ⌚ Ultimate Guide: Build Your Own Offline Maps for a Smartwatch
**Applicable platform**: An offline map format exclusive to smartwatches built on the **Actions Technology ATS3085S** chip platform.

## 🛠️ Preparation
1. **Environment requirements**: Install **Python 3.8+** and the **GDAL** toolset (you can get this by installing OSGeo4W or QGIS and using its built-in terminal environment).
2. **Install the required package**: Open a terminal and run:
   ```bash
   pip install pyshp
   ```
3. **Download the build tool**: Download and extract the open-source tool: [dtg1-map-tools](https://github.com/purrrock/dtg1-map-tools)

---

## 📥 Step 1: Download the Base Data
We need to separately download a "flat base map" and "elevation data (for generating contour lines)."

1. **Download the OSM base map**:
   * Go to [BBBike Extract](https://extract.bbbike.org/) (or [Geofabrik – Taiwan region](https://download.geofabrik.de/asia/taiwan.html)).
   * Select your target area (e.g., Nantou).
   * Choose the **Protocolbuffer (PBF)** format.
   * After downloading, extract it and rename the file to `base.osm.pbf`.
2. **Download the DEM elevation data**:
   * Go to [OpenTopography](https://portal.opentopography.org/raster?opentopoID=OTSRTM.082015.4326.1).
   * Select the same area as above.
   * Download in **GeoTIFF** format.
   * Rename the file to `dem.tif`.

---

## ⚙️ Step 2: Optimize the Base Data (OSM Optimizer)
The raw map data is far too large for the watch to handle directly. We need to put it through extreme optimization.

1. **Run the script**: Make sure `base.osm.pbf` is in the same folder as this script, then run:
   ```bash
   python osm_optimizer.py
   ```
   *Once it finishes, the optimized `base_map.osm` will be generated.*

---

## ⛰️ Step 2: Generate and Process Contour Lines (GDAL)
Open Command Prompt (CMD) or a terminal, make sure you're in the same directory as the `dem.tif` you just downloaded, and run the following two commands in order:

```bash
# 1. Generate a contour-line Shapefile with 10m spacing (attribute name set to ele)
gdal_contour -i 10 -a ele dem.tif contours.shp

# 2. Force-convert the coordinate system to WGS84 (EPSG:4326), and simplify the lines so the watch doesn't lag
ogr2ogr -t_srs EPSG:4326 -simplify 0.00008 contours_final.shp contours.shp	
```
*(After running this, your folder should now contain `contours_final.shp` along with its companion files `.shx`, `.dbf`, etc.)*

---

## 🐍 Step 3: Generate a Spec-Compliant OSM Contour File
We need to convert the Shapefile generated above into OSM format, and put it through extreme slimming.

1. **Run shp2osm.py:**
   ```bash
   python shp2osm.py contours_final.shp contours.osm
   ```

---

## 🔗 Step 4: Safely Merge the Maps
We now have a base map (`base_map.osm`) and a contour map (`contours.osm`). This step merges the two into a single map file.

2. **Run merge.py:**
   ```bash
   python merge.py
   ```
   *(This generates `map.osm`, which now contains everything.)*

---

## 🪓 Step 4.5: Automatically Split the Map File (Getting It Ready for the Compiler)
Since the merged `map.osm` may be huge, the compiler could run into memory bottlenecks while processing it. So we need to cut it into smaller chunks first — and output those chunks directly into the compiler's folder, so the compiler can run through them automatically in batch.

2. **Run split_osm.py:**
   We'll output the split `.osm` files directly into the folder where you keep the map compiler (`dtg1-map-tools`). (If the compiler and this script are in the same directory, you can just point the output at the current directory `.` — or you can output them elsewhere first and move them manually afterward.)
   ```bash
   python split_osm.py map.osm
   ```
   *(Once this runs, the original, huge `map.osm` will be split into multiple files — `tile_000.osm`, `tile_001.osm`, and so on — placed alongside the compiler in the same directory.)*

---

## 🎨 Step 5: Edit `features.csv` (🚨 The Single Most Important Step)
For the watch to know how to "draw" these contour lines (color, thickness, when to display them), we need to add rules into the compiler's style/dictionary file.

1. Open the `features.csv` file inside your `dtg1-map-tools` folder (use Notepad or VS Code — don't save it with Excel, or the formatting will get scrambled).
2. **⚠️ Do not, under any circumstances,** add the rules at the beginning or middle of the file! The compiler reads top to bottom, so later rules override earlier ones.
3. Scroll to the **absolute end** of `features.csv` (after the last line, press `Enter` to start a new one).
4. Paste in the following three contour-rendering rules and save:

```csv
9901; contour_major ;Black;1000; roads ; highway=c1 ;Contour Major;5113;Black;1000;1;
9902; contour_medium ;DarkGray;500; roads ; highway=c2 ;Contour Medium;5113;DarkGray;500;1;
9903; contour_minor ;Gray;100; roads ; highway=c3 ;Contour Minor;5124;Gray;100;1;
```
*(These three lines mean: the major contour line (c1) is rendered black, the secondary contour lines (c2, c3) are rendered dark gray/gray, and they all stay visible even at smaller map scales.)*

---

## ⚙️ Step 6: Copy Over the Split Files and Run the Automated Batch Compile
Back in **Step 4.5**, after running `split_osm.py`, the script automatically created a folder called `osm` in the current directory, filled with the split `tile_XXX.osm` files.

1. **Move the folder**: copy that entire `osm` folder over into your `dtg1-map-tools` (compiler) directory.
2. **Run the build**: open a terminal, make sure your current path is inside the `dtg1-map-tools` directory, and run:
   ```bash
   python dtg1_map_compiler.py
   ```
   *(The compiler will automatically detect and read every map tile inside the `osm` folder and merge/compile them.)*
3. **Verify the result**: wait for it to finish, then check whether the final terminal output shows a noticeably higher `roads` count (e.g., something like `Assembled: 26950 roads`, or another number in the thousands or tens of thousands). A large number means your contour lines were successfully packed in!

---

## 📱 Step 7: On-Device Testing on the Watch (🔥 Clearing the Cache Matters a Lot)
Last step — get the freshly baked map onto your watch! To stop the watch from reading leftover data from the old map, be sure to follow the steps below and set up a brand-new folder.

1. Connect the watch to your computer with a USB cable.
2. Open the watch's internal storage and go into the `MAP` folder.
3. Copy every folder under the `osm` directory into it.
4. Unplug the USB cable, open the map app on the watch, and switch over to the new map you just added.

### 🎯 On-Device Checks to Confirm Success:
* After opening the map, you should immediately see thick black lines (the 100m major contours).
* As you zoom in a bit, thin gray lines (the 10m secondary contours) should smoothly fade in.
* When you zoom out to a 100m or 200m scale, these lines should never disappear.
* Try panning and zooming around — the watch should stay smooth and responsive throughout (thanks to the extreme RDP slimming we applied earlier).
