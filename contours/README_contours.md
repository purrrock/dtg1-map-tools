# Integrating the Contour Map into the Offline Map

## How to Create Custom Offline Maps
Dedicated offline map format for smartwatches based on the Actions Technology ATS3085S platform chip.

### 🛠️ Prerequisites

1. Install **Python 3.8+** and **GDAL** (Can be used via OSGeo4W or the built-in terminal in QGIS).
2. Install the required Python packages for the compiler and optimization scripts:
   ```bash
   pip install pyshp osmium numba numpy lxml
   ```
3. Download and extract the open-source tool: `dtg1-map-tools`

	#### ⚙️ Detailed Guide: Installing GDAL

	Because GDAL relies on complex C++ binaries, standard `pip install gdal` will fail on most systems. Use the following pre-compiled distribution methods based on your OS:

	**For Windows (OSGeo4W Method):**
	1. Download the **OSGeo4W Network Installer** from `https://trac.osgeo.org/osgeo4w/`.
	2. Run the installer, select **Express Install**, and ensure the **GDAL** package is checked.
	3. Once the installation is complete, do not use the standard CMD. Open your Windows Start menu, search for **OSGeo4W Shell**, and run it. Use this specialized terminal (via the `cd` command) to navigate to your map folder and execute all `gdal_contour` commands.
	*(Alternative: If you already have QGIS installed, it includes GDAL. Just find and launch the "OSGeo4W Shell" from the QGIS folder in your Start menu).*

	**For Linux (Debian/Ubuntu/Mint):**
	The compiled GDAL binaries are available in the stable repositories. Open your terminal and execute:
	```bash
	sudo apt update && sudo apt install gdal-bin
	```
	
	To verify the installation on any OS, type `gdalinfo --version` in your terminal.

---

### 📥 Step 1: Download Base Data

1. **Download OSM Base Map**: Go to BBBike Extract, select your area (e.g., Nantou), choose OSM XML as the format. After downloading, extract it and rename the file to `base.osm`.

---

2. **Download DEM Elevation Data**: Go to OpenTopography, select the exact same area, download it in GeoTIFF format, and rename the file to `dem.tif`.

#### 🌐 Detailed Guide: Downloading DEM Elevation Data

To generate contour lines, you need a Digital Elevation Model (DEM) raster file.
	1. Go to [OpenTopography](https://portal.opentopography.org/datasets) and select **SRTM GL1 (30m)** or **Copernicus DEM GLO-30** under the Global & 	Regional DEM section.
	2. Click **Select a Region** and draw a bounding box around your target area. 
	> ⚠️ **Technical Requirement:** Make this bounding box 1–2 km larger than your `base.osm` area. If they are exactly the same size, contour lines might abruptly clip or generate artifacts at the edges of the smartwatch screen.
	3. Scroll down to Data Output Format and strictly select **GeoTiff**. Uncheck all Visualization options (Hillshade, Color Relief, etc.)—we only need the raw mathematical matrix, not image renderings.
	4. Enter your email in the Job Description section and click **Submit**.
	5. Once the job finishes, extract the downloaded archive, find the generated `.tif` file, rename it to `dem.tif`, and place it in the same directory as your Python scripts.

---

### 📥 Step 1.5: Optimize Base Data

Run the PyOsmium and Numba-based optimizer to chunk geometry and reduce nodes to avoid Out Of Memory (OOM) errors on the watch:
```bash
python osm_optimizer.py base.osm base_map.osm
```

---

### ⛰️ Step 2: Generate and Process Contour Lines (GDAL)

Open Command Prompt (CMD) or Terminal, ensure you are in the same directory as `dem.tif`, and execute the following two commands in order:

```bash
# 1. Generate a 10m interval contour Shapefile (attribute name set to 'ele')
gdal_contour -i 10 -a ele dem.tif contours.shp

# 2. Force coordinate system conversion to WGS84 (EPSG:4326)
ogr2ogr -t_srs EPSG:4326 contours_wgs84.shp contours.shp
```

### 🔄 Step 3: Convert Shapefile to OSM XML

Translate the contour Shapefile into an OSM XML format while applying safe chunking (MAX_POINTS_PER_WAY = 200) to prevent hardware Watchdog resets:

```bash
python shp2osm.py contours_wgs84.shp contours.osm
```

### 🔀 Step 4: High-Speed File Merge

Merge the base map and contours using the binary concatenation script:

```bash
python merge.py
```
*(Ensure `base_map.osm` and `contours.osm` are in the same directory before executing this script).*

---

### 📝 Step 5: Update features.csv

⚠️ **Warning:** You must add these lines to the **absolute bottom** of the file! Because the compiler reads from top to bottom, later rules will overwrite earlier ones.

1. Scroll down to the absolute bottom of `features.csv` (press Enter to create a new empty line after the very last line).
2. Paste the following two lines:

```csv
9901; contour_major ;Yellow;1000; roads ; highway=contour_major_test ;Contour Major;5113;Yellow;1000;1;
9902; contour_minor ;Gray;100; roads ; highway=contour_minor_test ;Contour Minor;5124;Gray;100;1;
```

### ⚙️ Step 6: Compilation

Place the resulting merged `map.osm` into the `dtg1-map-tools` folder and execute the compiler in your terminal:

```bash
python dtg1_map_compiler.py
```
*(Check that the output displays: "Assembled: 26950 roads" or a similar number, which means your 5000+ contour lines have been successfully added into the binary graph).*

---

### 📱 Step 7: Real-Device Testing on the Watch

🔥 **Clearing the Cache is Extremely Important!**

1. Connect the watch to your PC via USB.
2. Enter the `MAP` folder inside the watch's internal storage.
3. Create a brand new, clean folder (for example, `Topo_Final`).
4. Copy all newly generated files (`roads.mlp`, `roads.idx`, `roads.db`, `landuse.mlp`, `landuse.idx`, `landuse.db`, `water.mlp`, `water.idx`, `water.db`, `map.name`) into your new folder.
5. Safely eject the watch, open the map app, and verify hardware rendering.
6. Testing the results: When you open the map, you should directly see thick black lines (100m major contours). When you zoom in slightly, the thin gray lines (10m minor contours) will smoothly appear. When you zoom out to a 100m or 200m scale, these lines will absolutely not disappear, and panning operations will remain smooth.
