#!/bin/bash
# Файл: build_region.sh
# Использование: ./build_region.sh <region_name>

set -e

REGION_NAME=$1

echo "1. Querying Geofabrik API for region: $REGION_NAME..."
# Используем jq для парсинга GeoJSON индекса серверов Geofabrik и извлечения PBF URL
PBF_URL=$(curl -sS https://download.geofabrik.de/index-v1.json | jq -r ".features[] | select(.properties.id == \"$REGION_NAME\") | .properties.urls.pbf")

if [ -z "$PBF_URL" ] || [ "$PBF_URL" == "null" ]; then
    echo "CRITICAL ERROR: Could not find PBF URL for region '$REGION_NAME' in Geofabrik index."
    exit 1
fi

echo "2. Downloading PBF data from $PBF_URL..."
wget -qO raw.osm.pbf "$PBF_URL"

echo "3. Hardware Pre-culling (osmium tags-filter)..."
# КРИТИЧЕСКИ ВАЖНО: Обрезаем все лишнее до конвертации в XML, чтобы не получить Out-Of-Memory в Python
osmium tags-filter raw.osm.pbf \
    w/highway w/landuse w/waterway w/natural=water w/building \
    n/amenity n/shop n/tourism n/leisure \
    -o filtered.osm.pbf -f pbf --overwrite

echo "4. Binary to XML Serialization..."
# Конвертируем уже легковесный PBF в XML
osmium cat filtered.osm.pbf -o map.osm -f osm --overwrite

echo "5. Topology Optimization (osm_optimizer.py)..."
python osm_optimizer.py

echo "6. Environment Preparation..."
# Компилятор ожидает файл "map.osm", поэтому подменяем исходник оптимизированной версией
mv map_optimized.osm map.osm

echo "7. Compiling ATS3085S Binaries..."
# Запуск без параметров, I/O операции происходят в корне
python dtg1_map_compiler.py

echo "8. Packaging Distribution Archive..."
zip -r "${REGION_NAME}_dtg1_map.zip" *.mlp *.idx *.db map.name

echo "Cleanup..."
rm -f raw.osm.pbf filtered.osm.pbf map.osm *.mlp *.idx *.db map.name
echo "SUCCESS: ${REGION_NAME}_dtg1_map.zip generated."