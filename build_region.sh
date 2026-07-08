#!/bin/bash
# Файл: build_region.sh
# Использование: ./build_region.sh <region_name> <geofabrik_url>
# Пример: ./build_region.sh monaco https://download.geofabrik.de/europe/monaco-latest.osm.pbf

set -e

REGION_NAME=$1
PBF_URL=$2
WORKSPACE_DIR="build_tmp_${REGION_NAME}"

mkdir -p "$WORKSPACE_DIR"
cd "$WORKSPACE_DIR"

echo "Downloading ${REGION_NAME} PBF data..."
wget -qO raw.osm.pbf "$PBF_URL"

echo "Filtering OSM data (Pre-culling via osmium)..."
# Оставляем только теги, которые есть в нашем features.csv для экономии RAM
osmium tags-filter raw.osm.pbf \
    w/highway w/landuse w/waterway w/natural=water w/building \
    n/amenity n/shop n/tourism n/leisure \
    -o filtered.osm.pbf -f pbf --overwrite

echo "Converting to XML..."
osmium cat filtered.osm.pbf -o map.osm -f osm --overwrite

echo "Compiling binary map formats for ATS3085S..."
# Возвращаемся в корень для запуска компилятора, передавая путь к map.osm
cd ..
python dtg1_map_compiler.py "$WORKSPACE_DIR/map.osm" -o "output_${REGION_NAME}"

echo "Packaging map archive..."
cd "output_${REGION_NAME}"
zip -r "../${REGION_NAME}_dtg1_map.zip" roads.mlp roads.idx landuse.db map.name

echo "Cleanup..."
cd ..
rm -rf "$WORKSPACE_DIR" "output_${REGION_NAME}"
echo "Done: ${REGION_NAME}_dtg1_map.zip generated."