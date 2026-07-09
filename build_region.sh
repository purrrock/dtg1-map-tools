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
# Используем префикс nwr/ (Node, Way, Relation), чтобы не потерять полигональные POI.
# Тег building намеренно исключен: osmium аппаратно отсечет миллионы простых домов,
# но пропустит те здания, которые имеют теги из списков amenity, shop и т.д.
osmium tags-filter raw.osm.pbf \
    nwr/highway nwr/landuse nwr/waterway nwr/natural nwr/barrier \
    nwr/railway nwr/aeroway nwr/man_made nwr/route \
    nwr/amenity nwr/shop nwr/leisure nwr/tourism nwr/sport \
    nwr/historic nwr/craft nwr/office nwr/healthcare nwr/emergency \
    -o filtered.osm.pbf -f pbf --overwrite
rm -f raw.osm.pbf

echo "4. Binary to XML Serialization..."
# Конвертируем уже легковесный PBF в XML
osmium cat filtered.osm.pbf -o map.osm -f osm --overwrite
rm -f filtered.osm.pbf
# echo "5. Topology Optimization (osm_optimizer.py)..."
# python -u osm_optimizer.py

echo "6. Environment Preparation..."
# Компилятор ожидает файл "map.osm", поэтому подменяем исходник оптимизированной версией
mv map_optimized.osm map.osm

echo "7. Compiling ATS3085S Binaries..."
# Запуск без параметров, I/O операции происходят в корне
python dtg1_map_compiler.py
rm -f map.osm

echo "8. Packaging Distribution Archive..."
zip -r "${REGION_NAME}_dtg1_map.zip" *.mlp *.idx *.db map.name

echo "Cleanup..."
rm -f raw.osm.pbf filtered.osm.pbf map.osm *.mlp *.idx *.db map.name
echo "SUCCESS: ${REGION_NAME}_dtg1_map.zip generated."