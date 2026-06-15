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
    """
    Реализация алгоритма Дугласа-Пекера с использованием JIT-компиляции Numba.
    Обеспечивает высокую скорость работы за счет компиляции в машинный код (LLVM).
    """
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
    """
    Обработчик PyOsmium. Извлекает геометрию на уровне C++.
    """
    def __init__(self, temp_ways_file, max_nodes_per_way, epsilon_deg):
        super().__init__()
        self.tmp_f = open(temp_ways_file, 'wb')
        self.max_nodes = max_nodes_per_way
        self.epsilon_deg = epsilon_deg

        self.used_node_ids = set()
        self.ways_count = 0

        self.ignore_highway_types = {'corridor', 'elevator'}
        
        # [ОБНОВЛЕНО] В Whitelist добавлен тег 'barrier' для сохранения линейной геометрии препятствий
        self.keep_tags = {
            'highway', 'waterway', 'natural', 'name', 'landuse', 
            'amenity', 'leisure', 'tourism', 'shop', 'sport', 'barrier'
        }

    def way(self, w):
        is_ignored = False
        valid_tags = []

        for tag in w.tags:
            if tag.k == 'highway' and tag.v in self.ignore_highway_types:
                is_ignored = True
                break
            if tag.k in self.keep_tags:
                valid_tags.append((tag.k, tag.v))

        if is_ignored or not valid_tags or len(w.nodes) == 0:
            return

        pts = []
        valid_nds = []
        for n in w.nodes:
            try:
                pts.append((n.location.lon, n.location.lat))
                valid_nds.append(n.ref)
            except o.InvalidLocationError:
                pass

        if len(pts) == 0:
            return

        pts_array = np.array(pts, dtype=np.float64)
        kept_indices = douglas_peucker_indices_fast(pts_array, self.epsilon_deg)

        simplified_nds = [valid_nds[i] for i in kept_indices]
        simplified_pts = [pts[i] for i in kept_indices]

        if not simplified_pts:
            return

        lons = [p[0] for p in simplified_pts]
        lats = [p[1] for p in simplified_pts]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        original_id = w.id
        step = max(1, self.max_nodes - 1)
        chunks = [simplified_nds[i:i + self.max_nodes] for i in range(0, len(simplified_nds), step)]

        for index, chunk in enumerate(chunks):
            wid = original_id * 1000 + index if len(chunks) > 1 else original_id

            xml_str = f'  <way id="{wid}" '
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


def clean_element_metadata(elem):
    """
    Мутация XML-узла in-place.
    """
    for attr in ['version', 'timestamp', 'changeset', 'uid', 'user', 'visible']:
        elem.attrib.pop(attr, None)

    # [ОБНОВЛЕНО] Исключены 'barrier', 'int_name', 'old_name', 'alt_name'
    drop_keys = {
        'wikidata', 'wikipedia', 'building', 'power', 
        'phone', 'website', 'url', 'opening_hours', 'email',
        'maxspeed', 'lanes', 'oneway', 'surface', 'tracktype', 'smoothness',
        'note', 'source', 'fixme'
    }
    
    # [ОБНОВЛЕНО] Исключен префикс 'name:'
    drop_prefixes = ('addr:', 'contact:', 'payment:', 'source:')

    for tag in elem.findall('tag'):
        k = tag.get('k', '')
        if k in drop_keys or k.startswith(drop_prefixes):
            elem.remove(tag)

def optimize_osm_pyosmium(input_file, output_file, max_nodes_per_way=100, epsilon_deg=0.00005):
    temp_ways = tempfile.NamedTemporaryFile(delete=False, mode='wb')
    temp_ways_name = temp_ways.name
    temp_ways.close()

    print(f"[*] Phase 1: PyOsmium starting C++ engine to read coordinates and optimize ways...")
    handler = WayOptimizer(temp_ways_name, max_nodes_per_way, epsilon_deg)

    handler.apply_file(input_file, locations=True, idx='flex_mem')
    handler.close()

    used_node_ids = handler.used_node_ids
    ways_count = handler.ways_count
    print(f"    ... PyOsmium analysis complete! Found {len(used_node_ids)} valid nodes and {ways_count} ways.")
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
                    if int(elem.get('id')) in used_node_ids:
                        clean_element_metadata(elem)
                        out.write(ET.tostring(elem, encoding='utf-8') + b'\n')

                elif elem.tag == 'way':
                    if not ways_written:
                        print(f"    ... Seamlessly merging {ways_count} optimized ways...")
                        with open(temp_ways_name, 'rb') as tw:
                            shutil.copyfileobj(tw, out)
                        ways_written = True

                elif elem.tag == 'relation':
                    if not ways_written:
                        with open(temp_ways_name, 'rb') as tw:
                            shutil.copyfileobj(tw, out)
                        ways_written = True
                        
                    clean_element_metadata(elem)
                    out.write(ET.tostring(elem, encoding='utf-8') + b'\n')

                elem.clear()
                root.clear()

        out.write(b'</osm>\n')

    os.remove(temp_ways_name)
    print(f"[*] Optimization Summary:")
    print(f"    - Nodes kept: {len(used_node_ids)}")
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