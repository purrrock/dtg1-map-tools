# shp2osm.py (Extreme slim final edition - designed for smartwatches)
import shapefile
import sys
import os
import tempfile
import shutil

try:
    import numpy as np
    from numba import njit
    HAS_JIT = True
    print("⚡ Numba hardware-level JIT acceleration engine enabled!")
except ImportError:
    HAS_JIT = False
    print("⚠️ Numba not detected — running in pure Python mode.")

if HAS_JIT:
    @njit(cache=True)
    def douglas_peucker_jit(pts, epsilon):
        n = len(pts)
        if n < 3:
            keep = np.zeros(n, dtype=np.bool_)
            keep[0], keep[-1] = True, True
            return keep
        epsilon_sq = epsilon * epsilon
        stack_start, stack_end = np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64)
        stack_ptr = 0
        stack_start[0], stack_end[0] = 0, n - 1
        stack_ptr += 1
        keep = np.zeros(n, dtype=np.bool_)
        keep[0], keep[n - 1] = True, True

        while stack_ptr > 0:
            stack_ptr -= 1
            start, end = stack_start[stack_ptr], stack_end[stack_ptr]
            if end - start <= 1: continue
            
            p1x, p1y = pts[start, 0], pts[start, 1]
            p2x, p2y = pts[end, 0], pts[end, 1]
            dx, dy = p2x - p1x, p2y - p1y
            l2 = dx*dx + dy*dy
            dmax_sq, index = 0.0, start

            for i in range(start + 1, end):
                px, py = pts[i, 0], pts[i, 1]
                if l2 == 0.0:
                    vx, vy = px - p1x, py - p1y
                    d_sq = vx*vx + vy*vy
                else:
                    cross = dy * px - dx * py + p2x * p1y - p2y * p1x
                    d_sq = (cross * cross) / l2
                if d_sq > dmax_sq: dmax_sq, index = d_sq, i

            if dmax_sq > epsilon_sq:
                keep[index] = True
                stack_start[stack_ptr], stack_end[stack_ptr] = start, index
                stack_ptr += 1
                stack_start[stack_ptr], stack_end[stack_ptr] = index, end
                stack_ptr += 1
        return keep
else:
    def douglas_peucker_jit(pts, epsilon): pass 

def convert_shp_to_osm_ultimate(shp_file, osm_file):
    base_name = os.path.splitext(shp_file)[0]
    
    missing_files = [ext for ext in [".shp", ".shx", ".dbf"] if not os.path.exists(base_name + ext)]
    if missing_files:
        print(f"❌ Error: Missing required files: {', '.join(missing_files)}")
        return

    # --- 🌟 Core parameter configuration (enhanced slimming and noise filtering) ---
    MAX_POINTS = 35        # Increased nodes per way to greatly reduce <way> tag count
    RDP_EPSILON = 0.00001  # Line simplification tolerance (~4.4 m)
    MIN_STUB = 2           # Minimum point count (segments below this are discarded)
    MIN_SPAN = 0.0001      # Minimum span for open segments (~40 m, aggressively filters stub lines)
    MIN_LOOP_SPAN = 0.0001 # Minimum diameter for closed contours (~20 m, aggressively filters false-hill noise)
    BATCH_SIZE = 8000      # Memory batch write size
    
    # 🌟 Short ID numbering system to reduce character count
    node_id, way_id = -1, -1
    
    try:
        with shapefile.Reader(base_name) as sf, \
             open(osm_file, "w", encoding="utf-8", buffering=16*1024*1024) as f_osm, \
             tempfile.TemporaryFile("w+", encoding="utf-8") as f_ways:
            
            total_shapes = len(sf)
            f_osm.write('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="pyshp2osm_final">\n')
            
            fields = [f[0].upper() for f in sf.fields[1:]]
            ele_idx = next((fields.index(k) for k in ['ELE', 'ELEVATION', 'Z'] if k in fields), None)
            
            node_buffer, way_buffer = [], []
            print("💾 Phase 1: Geometry and topology processing (extreme noise filtering, stripping invalid tags)...")
            
            for i, shapeRec in enumerate(sf.iterShapeRecords()):
                raw_ele = shapeRec.record[ele_idx] if ele_idx is not None else 0
                if isinstance(raw_ele, (int, float)): 
                    ele_val = int(raw_ele)
                else:
                    try: ele_val = int(float(raw_ele))
                    except (ValueError, TypeError): continue
                
                # 🌟 Character budget squeeze: use ultra-short tags (c1, c2, c3) and strip ele/area attributes
                hw_val = "c1" if ele_val % 100 == 0 else ("c2" if ele_val % 50 == 0 else "c3")
                tags_xml = f'<tag k="highway" v="{hw_val}"/>\n'
                
                pts = shapeRec.shape.points
                for p in range(len(shapeRec.shape.parts)):
                    start = shapeRec.shape.parts[p]
                    end = shapeRec.shape.parts[p+1] if p + 1 < len(shapeRec.shape.parts) else len(pts)
                    seg = pts[start:end]
                    
                    if len(seg) < MIN_STUB: continue
                    
                    xs, ys = [pt[0] for pt in seg], [pt[1] for pt in seg]
                    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
                    max_span = max(span_x, span_y)
                    
                    is_closed = (seg[0] == seg[-1]) 
                    if is_closed and max_span < MIN_LOOP_SPAN: continue 
                    elif not is_closed and max_span < MIN_SPAN: continue 
                    
                    if HAS_JIT:
                        keep = douglas_peucker_jit(np.array(seg, dtype=np.float64), RDP_EPSILON)
                        seg = [seg[idx] for idx in range(len(seg)) if keep[idx]]
                        if len(seg) < 2: continue
                    
                    current_node_ids = list(range(node_id, node_id - len(seg), -1))
                    node_id -= len(seg)
                    
                    for nid, (x, y) in zip(current_node_ids, seg):
                        # 🌟 Strip indentation, visible, version; reduce precision to .5f
                        node_buffer.append(f'<node id="{nid}" lat="{y:.5f}" lon="{x:.5f}"/>\n')
                        
                    for c_start in range(0, len(current_node_ids), MAX_POINTS - 1):
                        chunk_ids = current_node_ids[c_start : c_start + MAX_POINTS]
                        if len(chunk_ids) < 2: continue
                        
                        # 🌟 Strip indentation, visible, version
                        w_lines = "".join([f'<nd ref="{nid}"/>\n' for nid in chunk_ids])
                        way_buffer.append(f'<way id="{way_id}">\n{tags_xml}{w_lines}</way>\n')
                        way_id -= 1

                if len(node_buffer) > BATCH_SIZE:
                    f_osm.write("".join(node_buffer))
                    node_buffer.clear()
                if len(way_buffer) > BATCH_SIZE:
                    f_ways.write("".join(way_buffer))
                    way_buffer.clear()
                    
                if i % 5000 == 0 and i > 0:
                    print(f"    ...Processed {i} / {total_shapes} contour lines...")

            if node_buffer: f_osm.write("".join(node_buffer))
            if way_buffer: f_ways.write("".join(way_buffer))
                        
            print("💾 Phase 2: Merging cached ways into main file...")
            f_ways.seek(0)
            shutil.copyfileobj(f_ways, f_osm)
            f_osm.write('</osm>\n')
            
        final_size = os.path.getsize(osm_file) / (1024 * 1024)
        total_ways_gen = abs(way_id + 1)
        print(f"✅ Extreme slim conversion complete! Generated {total_ways_gen} short segments. OSM size: {final_size:.2f} MB")

    except Exception as e:
        print(f"❌ Unknown error occurred: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python shp2osm.py <input_contours.shp> <output_contours.osm>")
    else:
        convert_shp_to_osm_ultimate(sys.argv[1], sys.argv[2])
