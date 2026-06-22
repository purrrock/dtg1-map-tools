#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import osmium as o
import sys
import os
import tempfile
import shutil
from xml.sax.saxutils import escape

try:
    import numpy as np
    from numba import njit
    import lxml.etree as ET
except ImportError:
    import xml.etree.ElementTree as ET

# ==========================================
# Core Algorithm: Numba Machine-Code Level RDP Line Simplification
# ==========================================
@njit
def douglas_peucker_indices_fast(pts, epsilon):
    n = len(pts)
    if n < 3:
        arr = np.empty(n, dtype=np.int64)
        for i in range(n): 
            arr[i] = i
        return arr

    epsilon_sq = epsilon * epsilon
    stack_start = np.zeros(n, dtype=np.int64)
    stack_end = np.zeros(n, dtype=np.int64)
    stack_ptr = 0

    stack_start[0] = 0
    stack_end[0] = n - 1
    stack_ptr += 1

    keep_indices = np.zeros(n, dtype=np.bool_)
    keep_indices[0] = True
    keep_indices[n - 1] = True

    while stack_ptr > 0:
        stack_ptr -= 1
        start = stack_start[stack_ptr]
        end = stack_end[stack_ptr]

        if end - start <= 1:
            continue

        p1_x, p1_y = pts[start, 0], pts[start, 1]
        p2_x, p2_y = pts[end, 0], pts[end, 1]

        dx = p2_x - p1_x
        dy = p2_y - p1_y
        l2 = dx*dx + dy*dy

        dmax_sq = 0.0
        index = start

        for i in range(start + 1, end):
            px, py = pts[i, 0], pts[i, 1]
            if l2 == 0.0:
                vx, vy = px - p1_x, py - p1_y
                d_sq = vx*vx + vy*vy
            else:
                cross = dy * px - dx * py + p2_x * p1_y - p2_y * p1_x
                d_sq = (cross * cross) / l2

            if d_sq > dmax_sq:
                dmax_sq = d_sq
                index = i

        if dmax_sq > epsilon_sq:
            keep_indices[index] = True

            stack_start[stack_ptr] = start
            stack_end[stack_ptr] = index
            stack_ptr += 1

            stack_start[stack_ptr] = index
            stack_end[stack_ptr] = end
            stack_ptr += 1

    return np.nonzero(keep_indices)[0]

# ==========================================
# Phase 1: PyOsmium Handler (Pure C++ Way Parsing)
# ==========================================
class WayOptimizer(o.SimpleHandler):
    def __init__(self, temp_ways_file, temp_nodes_file, max_nodes_per_way, epsilon_deg):
        super().__init__()
        self.tmp_f = open(temp_ways_file, 'wb')
        self.tmp_nodes_f = open(temp_nodes_file, 'wb') # Временный файл для виртуальных узлов
        self.max_nodes = max_nodes_per_way
        self.epsilon_deg = epsilon_deg

        self.used_node_ids = set()
        self.ways_count = 0
        self.converted_pois_count = 0 # Счетчик извлеченных POI

        # Триггеры на удаление объекта
        self.drop_way_triggers = {'building', 'power'}
        # Безусловное удаление (коридоры внутри зданий)
        self.drop_way_kv = {'highway': {'corridor', 'elevator'}}
        
        # Ключи выживания (если геометрия нужна компилятору)
        self.survival_keys = {
            'landuse', 'natural', 'amenity', 'leisure', 'tourism', 
            'shop', 'sport', 'highway', 'waterway', 'barrier',
            'railway', 'aeroway', 'man_made', 'historic', 'route'
        }

        # [НОВОЕ] Ключи, маркирующие объект как Point of Interest
        self.poi_keys = {
            'amenity', 'shop', 'leisure', 'tourism', 'sport', 
            'historic', 'craft', 'office', 'healthcare', 'emergency'
        }

        self.drop_tag_keys = {
            'wikidata', 'wikipedia', 'phone', 'website', 'url', 
            'opening_hours', 'email', 'maxspeed', 'lanes', 'oneway', 
            'note', 'source', 'fixme', 'building', 'power',
            'operator', 'start_date'
        }
        self.drop_tag_prefixes = ('addr:', 'contact:', 'payment:', 'source:', 'generator:', 'plant:')

    def way(self, w):
        has_drop_trigger = False
        has_survival_tag = False
        has_poi_tag = False
        is_linear_highway = False
        valid_tags = []

        for tag in w.tags:
            # 1. Безусловные фатальные совпадения
            if tag.k in self.drop_way_kv and tag.v in self.drop_way_kv[tag.k]:
                return 

            # 2. Триггеры возможного удаления (building, power)
            if tag.k in self.drop_way_triggers:
                has_drop_trigger = True
                
            # 3. Триггеры выживания 
            if tag.k in self.survival_keys:
                has_survival_tag = True

            # 4. Триггеры POI
            if tag.k in self.poi_keys:
                has_poi_tag = True

            if tag.k == 'highway':
                is_linear_highway = True

            # 5. Сбор чистых тегов (за исключением мусора)
            if tag.k not in self.drop_tag_keys and not tag.k.startswith(self.drop_tag_prefixes):
                valid_tags.append((tag.k, tag.v))

        # Собираем геометрию
        pts = []
        valid_nds = []
        for n in w.nodes:
            try:
                pts.append((n.location.lon, n.location.lat))
                valid_nds.append(n.ref)
            except o.InvalidLocationError:
                pass

        # Если геометрия битая или тегов не осталось — удаляем полностью
        if not valid_tags or len(pts) == 0:
            return

        # [НОВОЕ] Извлечение POI (Centroid Injection)
        # Если это здание и в нем заложен POI-объект (магазин, храм и т.д.)
        if has_drop_trigger and has_poi_tag:
            # Вычисляем математический центроид здания
            center_lon = sum(p[0] for p in pts) / len(pts)
            center_lat = sum(p[1] for p in pts) / len(pts)
            
            # Смещение ID на 20 миллиардов гарантирует отсутствие коллизий
            node_id = 20000000000 + w.id
            xml_str = f'  <node id="{node_id}" version="1" visible="true" lat="{center_lat:.6f}" lon="{center_lon:.6f}">\n'
            
            for k, v in valid_tags:
                v_esc = escape(v, entities={'"': "&quot;"})
                xml_str += f'    <tag k="{k}" v="{v_esc}"/>\n'
            xml_str += '  </node>\n'
            
            self.tmp_nodes_f.write(xml_str.encode('utf-8'))
            self.converted_pois_count += 1
            # Прерываем обработку: мы спасли объект как точку, сам полигон-здание нам больше не нужен.
            return

        # Старая логика: Удаляем объект ТОЛЬКО если у него сработал триггер (например, building), 
        # но при этом нет ни одного ценного тега.
        if has_drop_trigger and not has_survival_tag:
            return

        # ---- Логика чанкинга и записи линий (way) ----
        is_polygon = w.is_closed() and not is_linear_highway
        simplified_nds = valid_nds
        simplified_pts = pts

        if is_polygon and simplified_nds[0] != simplified_nds[-1]:
            simplified_nds.append(simplified_nds[0])
            simplified_pts.append(simplified_pts[0])

        lons = [p[0] for p in simplified_pts]
        lats = [p[1] for p in simplified_pts]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        original_id = w.id
        chunks = []
        
        if is_polygon:
            chunks = [simplified_nds]
        else:
            step = max(1, self.max_nodes - 1)
            chunks = [simplified_nds[i:i + self.max_nodes] for i in range(0, len(simplified_nds), step)]

        for index, chunk in enumerate(chunks):
            wid = original_id * 1000 + index if len(chunks) > 1 else original_id

            xml_str = f'  <way id="{wid}" version="1" visible="true" '
            xml_str += f'min_lon="{min_lon:.6f}" max_lon="{max_lon:.6f}" '
            xml_str += f'min_lat="{min_lat:.6f}" max_lat="{max_lat:.6f}">\n'

            for nd_ref in chunk:
                xml_str += f'    <nd ref="{nd_ref}"/>\n'
                self.used_node_ids.add(nd_ref)

            for k, v in valid_tags:
                v_esc = escape(v, entities={'"': "&quot;"})
                xml_str += f'    <tag k="{k}" v="{v_esc}"/>\n'

            xml_str += '  </way>\n'
            self.tmp_f.write(xml_str.encode('utf-8'))
            self.ways_count += 1

    def close(self):
        self.tmp_f.close()
        self.tmp_nodes_f.close()


def clean_element_metadata(elem):
    """ Очистка мусорных метаданных и тегов для Node и Relation """
    for attr in ['timestamp', 'changeset', 'uid', 'user']:
        elem.attrib.pop(attr, None)
        
    if 'version' not in elem.attrib:
        elem.set('version', '1')
    if 'visible' not in elem.attrib:
        elem.set('visible', 'true')

    drop_keys = {
        'wikidata', 'wikipedia', 'building', 'power', 
        'phone', 'website', 'url', 'opening_hours', 'email',
        'maxspeed', 'lanes', 'oneway', 
        'note', 'source', 'fixme',
        'operator', 'start_date'
    }
    drop_prefixes = ('addr:', 'contact:', 'payment:', 'source:', 'generator:', 'plant:')

    for tag in elem.findall('tag'):
        k = tag.get('k', '')
        if k in drop_keys or k.startswith(drop_prefixes):
            elem.remove(tag)

def optimize_osm_pyosmium(input_file, output_file, max_nodes_per_way=100, epsilon_deg=0.00005):
    temp_ways = tempfile.NamedTemporaryFile(delete=False, mode='wb')
    temp_ways_name = temp_ways.name
    temp_ways.close()
    
    temp_nodes = tempfile.NamedTemporaryFile(delete=False, mode='wb')
    temp_nodes_name = temp_nodes.name
    temp_nodes.close()

    print(f"[*] Phase 1: PyOsmium starting C++ engine to read coordinates and optimize ways...")
    handler = WayOptimizer(temp_ways_name, temp_nodes_name, max_nodes_per_way, epsilon_deg)

    handler.apply_file(input_file, locations=True, idx='flex_mem')
    handler.close()

    used_node_ids = handler.used_node_ids
    ways_count = handler.ways_count
    converted_pois = handler.converted_pois_count
    
    print(f"    ... PyOsmium analysis complete! Found {len(used_node_ids)} valid nodes, {ways_count} ways.")
    print(f"    ... Extracted {converted_pois} POIs from building polygons.")
    print(f"[*] Phase 2: Using iterparse to reconstruct the final XML file at high speed...")

    with open(output_file, 'wb') as out:
        out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n')

        context = ET.iterparse(input_file, events=('start', 'end'))
        context = iter(context)
        _, root = next(context)

        ways_written = False

        for event, elem in context:
            if event == 'end':
                if elem.tag == 'bounds':
                    out.write(ET.tostring(elem, encoding='utf-8') + b'\n')

                elif elem.tag == 'node':
                    clean_element_metadata(elem)
                    if int(elem.get('id')) in used_node_ids or len(elem.findall('tag')) > 0:
                        out.write(ET.tostring(elem, encoding='utf-8') + b'\n')

                elif elem.tag in ('way', 'relation'):
                    if not ways_written:
                        # Строго по стандарту OSM: сперва сливаем сгенерированные виртуальные узлы (Nodes)
                        print(f"    ... Injecting extracted POI nodes...")
                        with open(temp_nodes_name, 'rb') as tn:
                            shutil.copyfileobj(tn, out)
                            
                        # Затем сливаем оптимизированные полигоны (Ways)
                        print(f"    ... Seamlessly merging optimized ways...")
                        with open(temp_ways_name, 'rb') as tw:
                            shutil.copyfileobj(tw, out)
                            
                        ways_written = True

                    # Если это relation — пишем его в хвост (уже после внедрения наших темп-файлов)
                    if elem.tag == 'relation':
                        clean_element_metadata(elem)
                        if len(elem.findall('tag')) > 0:
                            out.write(ET.tostring(elem, encoding='utf-8') + b'\n')

                if elem.tag in ('node', 'way', 'relation', 'bounds'):
                    elem.clear()
                    root.clear()

        # Fallback (если в файле отсутствовали way и relation)
        if not ways_written:
            with open(temp_nodes_name, 'rb') as tn:
                shutil.copyfileobj(tn, out)
            with open(temp_ways_name, 'rb') as tw:
                shutil.copyfileobj(tw, out)

        out.write(b'</osm>\n')

    os.remove(temp_ways_name)
    os.remove(temp_nodes_name)
    
    print(f"[*] Optimization Summary:")
    print(f"    - Nodes kept: {len(used_node_ids)} (plus standalone/extracted POIs)")
    print(f"    - Extracted POIs: {converted_pois}")
    print(f"    - Optimized Ways: {ways_count}")
    print("[+] Massive file processing complete! Performance and memory usage have reached optimal levels.")

if __name__ == "__main__":
    input_osm = "map.osm"
    output_osm = "map_optimized.osm"
    
    if len(sys.argv) == 3:
        input_osm = sys.argv[1]
        output_osm = sys.argv[2]

    if not os.path.exists(input_osm):
        print(f"[-] File not found: {input_osm}")
        sys.exit(1)

    optimize_osm_pyosmium(input_osm, output_osm)