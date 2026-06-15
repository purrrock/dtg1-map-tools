import shapefile
import sys
import os
import tempfile
import shutil

def convert_shp_to_osm_ultimate(shp_file, osm_file):
    base_name = os.path.splitext(shp_file)[0]

    # Failsafe check: ensure all three necessary files exist
    missing_files = [ext for ext in [".shp", ".shx", ".dbf"] if not os.path.exists(base_name + ext)]
    if missing_files:
        print(f"❌ Missing necessary files: {', '.join(missing_files)} (Shapefile must contain shp, shx, dbf)")
        return

    print(f"📖 Reading Shapefile: {base_name}.shp ...")
    print(f"⚠️ Please ensure this Shapefile is in the WGS84 (EPSG:4326) coordinate system.")

    # 🚨 Core Fix: Watch hardware limits the number of vertices per object, preventing corrupt artifact lines
    MAX_POINTS_PER_WAY = 200

    try:
        with shapefile.Reader(base_name) as sf:
            total_shapes = len(sf)

            fields = [f[0].upper() for f in sf.fields[1:]]
            ele_idx = next((fields.index(k) for k in ['ELE', 'ELEVATION', 'Z'] if k in fields), None)

            # Использование отрицательных ID, чтобы избежать коллизий с базовой картой OSM
            node_id = -1000000
            way_id = -1000000

            print("💾 Phase 1: Processing geometries (Simultaneously writing Nodes and splitting Ways)...")

            # Буферизация 8 МБ для ускорения I/O операций на диске
            with open(osm_file, "w", encoding="utf-8", buffering=8*1024*1024) as f_osm, \
                 tempfile.TemporaryFile("w+", encoding="utf-8") as f_ways_temp:

                f_osm.write('<?xml version="1.0" encoding="UTF-8"?>\n')
                f_osm.write('<osm version="0.6" generator="pyshp2osm_ultimate">\n')

                for i, shapeRec in enumerate(sf.iterShapeRecords()):
                    raw_ele = shapeRec.record[ele_idx] if ele_idx is not None else 0

                    if isinstance(raw_ele, (int, float)): 
                        ele_val = int(raw_ele)
                    else:
                        try: 
                            ele_val = int(float(raw_ele))
                        except (ValueError, TypeError): 
                            ele_val = 0

                    ele_str = str(ele_val)
                    # Присвоение тегов для Fallback-механизма (major каждые 100 метров, остальные minor)
                    hw_value = "contour_major_test" if ele_val % 100 == 0 else "contour_minor_test"

                    shape = shapeRec.shape
                    parts = shape.parts
                    points = shape.points
                    num_parts = len(parts)
                    total_points = len(points)

                    tags_xml = f'    <tag k="highway" v="{hw_value}"/>\n    <tag k="ele" v="{ele_str}"/>\n    <tag k="area" v="no"/>\n'

                    for p in range(num_parts):
                        start_idx = parts[p]
                        end_idx = parts[p+1] if p + 1 < num_parts else total_points
                        part_points = points[start_idx:end_idx]
                        num_points = len(part_points)

                        if num_points < 2: continue

                        # 🚨 Split ultra-long contours into safe short segments
                        # Step forward by MAX_POINTS_PER_WAY - 1 each time to ensure segments connect seamlessly
                        for chunk_start in range(0, num_points, MAX_POINTS_PER_WAY - 1):
                            chunk_points = part_points[chunk_start : chunk_start + MAX_POINTS_PER_WAY]
                            chunk_len = len(chunk_points)

                            # If the last segment only has one point, ignore it (cannot form a line)
                            if chunk_len < 2: continue

                            # Прямая строковая генерация узлов
                            node_lines = "".join([
                                f'  <node id="{n_id}" lat="{y:.7f}" lon="{x:.7f}" visible="true" version="1"/>\n'
                                for n_id, (x, y) in zip(range(node_id, node_id - chunk_len, -1), chunk_points)
                            ])
                            f_osm.write(node_lines)

                            # Прямая строковая генерация ссылок на узлы для Ways
                            nd_lines = "".join([
                                f'    <nd ref="{nid}"/>\n'
                                for nid in range(node_id, node_id - chunk_len, -1)
                            ])

                            # Запись Ways во временный файл
                            f_ways_temp.write(f'  <way id="{way_id}" visible="true" version="1">\n{tags_xml}{nd_lines}  </way>\n')

                            node_id -= chunk_len
                            way_id -= 1

                    if i % 5000 == 0 and i > 0:
                        print(f"    ...Processed {i}/{total_shapes} geometries...")

                print("💾 Phase 2: Writing cached Ways into the main file...")
                f_ways_temp.seek(0)
                shutil.copyfileobj(f_ways_temp, f_osm)

                f_osm.write('</osm>\n')

            total_ways = abs(way_id + 1000000)
            print(f"✅ Conversion complete! Contours have been safely split, generating a total of {total_ways} continuous short segments.")

    except Exception as e:
        print(f"❌ An unknown error occurred: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python shp2osm.py <input.shp> <output.osm>")
    else:
        convert_shp_to_osm_ultimate(sys.argv[1], sys.argv[2])