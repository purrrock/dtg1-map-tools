#!/bin/bash
# =========================================================================
# DT G1 Map Downloader & Preprocessor Script
# Supports cumulative batch builds with country-region prefix naming
# =========================================================================

# Перехватываем полный префикс региона из параметров матрицы (напр., poland-dolnoslaskie)
FULL_REGION_NAME=$1
export REGION_NAME=$FULL_REGION_NAME

echo "[>] Starting map pipeline for target archive: ${FULL_REGION_NAME}_dtg1_map.zip"

echo "1. Downloading Geofabrik Spatial Index..."
curl -s https://download.geofabrik.de/index-v1.json -o index.json

echo "2. Resolving Geofabrik Download URL (3-Tier Cascade Lookup)..."

# Ступень 1: Поиск по точному совпадению (напр., russia-central-fed-district или belarus)
PBF_URL=$(jq -r '.features[] | select(.properties.id == "'$FULL_REGION_NAME'") | .properties.urls.pbf' index.json)

# Ступень 2: Поиск с заменой первого дефиса на слэш (напр., для макрорегионов Канады: canada-alberta -> canada/alberta)
if [ -z "$PBF_URL" ] || [ "$PBF_URL" == "null" ]; then
    SLASH_NAME=$(echo "$FULL_REGION_NAME" | sed 's/-/\//')
    echo "  [~] Exact ID '$FULL_REGION_NAME' not found. Trying slash substitution: '$SLASH_NAME'..."
    PBF_URL=$(jq -r '.features[] | select(.properties.id == "'$SLASH_NAME'") | .properties.urls.pbf' index.json)
fi

# Ступень 3: Поиск со срезом префикса до первого дефиса (напр., для Польши: poland-dolnoslaskie -> id: dolnoslaskie)
if [ -z "$PBF_URL" ] || [ "$PBF_URL" == "null" ]; then
    STRIPPED_NAME=$(echo "$FULL_REGION_NAME" | cut -d'-' -f2-)
    echo "  [~] Transformed ID not found. Trying stripped fallback ID: '$STRIPPED_NAME'..."
    PBF_URL=$(jq -r '.features[] | select(.properties.id == "'$STRIPPED_NAME'") | .properties.urls.pbf' index.json)
fi

# Окончательная проверка: если URL так и не найден, останавливаем пайплайн
if [ -z "$PBF_URL" ] || [ "$PBF_URL" == "null" ]; then
    echo "[-] CRITICAL ERROR: Could not resolve download URL for region '$FULL_REGION_NAME' in Geofabrik index."
    exit 1
fi

echo "[SUCCESS] Found target PBF source: $PBF_URL"

echo "3. Fetching raw pbf chunk..."
wget -nv -O source.osm.pbf "$PBF_URL"

echo "4. Environment Optimization & Extraction via Osmium..."
# Вырезаем только те тэги, которые прописаны в LUT, отсекая здания и мусор
osmium tags-filter source.osm.pbf \
    nwr/highway nwr/landuse nwr/waterway nwr/natural nwr/barrier \
    nwr/railway nwr/aeroway nwr/man_made nwr/route \
    nwr/amenity nwr/shop nwr/leisure nwr/tourism nwr/sport \
    nwr/historic nwr/craft nwr/office nwr/healthcare nwr/emergency \
  -o filtered.osm.pbf --overwrite

echo "5. Binary to XML Serialization..."
osmium cat filtered.osm.pbf -o map.osm -f osm --overwrite

echo "-> Counting nodes for progress tracking..."
export TOTAL_NODES=$(grep -c '^[[:space:]]*<node' map.osm || echo 0)
echo "-> Total nodes to process: $TOTAL_NODES"

echo "6. Launching Python Compiler Engine..."
# Запускаем компилятор. Оркестратор сам подхватит REGION_NAME и сделает красивый mapName в map.name
python3 dtg1_map_compiler.py -p landuse

echo "7. Packaging Target Hardware Binary Package..."
# Создаем архив, имя которого строго начинается с FULL_REGION_NAME
zip -r "${FULL_REGION_NAME}_dtg1_map.zip" roads.mlp roads.idx landuse.mlp landuse.idx water.mlp water.idx map.name roads.db landuse.db water.db

echo "[SUCCESS] Build loop complete for $FULL_REGION_NAME"