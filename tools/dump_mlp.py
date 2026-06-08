#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import struct
import argparse


HEADER_SIZE = 32


# ============================================================
# Helpers
# ============================================================

def coord_i_to_deg(v: int) -> float:
    return v / 1_000_000.0


# ============================================================
# Record parser
# ============================================================

def parse_record(body: bytes):

    if len(body) < 24:
        return None

    min_x_i, min_y_i, max_x_i, max_y_i, num_parts, num_points = struct.unpack(
        "<4iII",
        body[:24]
    )

    parts_offset = 24
    parts_size = num_parts * 4

    if len(body) < parts_offset + parts_size:
        return None

    parts = []

    if num_parts > 0:
        parts = list(struct.unpack(
            f"<{num_parts}I",
            body[parts_offset:parts_offset + parts_size]
        ))

    points_offset = parts_offset + parts_size

    points = []

    for i in range(num_points):

        off = points_offset + i * 8

        if off + 8 > len(body):
            break

        x_i, y_i = struct.unpack_from("<ii", body, off)

        points.append({
            "x_int": x_i,
            "y_int": y_i,
            "lon": coord_i_to_deg(x_i),
            "lat": coord_i_to_deg(y_i)
        })

    return {
        "bbox_int": (
            min_x_i,
            min_y_i,
            max_x_i,
            max_y_i
        ),

        "bbox_deg": (
            coord_i_to_deg(min_x_i),
            coord_i_to_deg(min_y_i),
            coord_i_to_deg(max_x_i),
            coord_i_to_deg(max_y_i)
        ),

        "num_parts": num_parts,
        "num_points": num_points,
        "parts": parts,
        "points": points
    }


# ============================================================
# Main dump
# ============================================================

def dump_mlp(mlp_path: str, out_path: str):

    with open(mlp_path, "rb") as f:
        data = f.read()

    size = len(data)

    with open(out_path, "w", encoding="utf-8") as out:

        out.write("========================================\n")
        out.write(" DT G1 MLP DUMP\n")
        out.write("========================================\n\n")

        out.write(f"File: {mlp_path}\n")
        out.write(f"Size: {size} bytes\n\n")

        offset = HEADER_SIZE
        rec_num = 0

        while offset + 8 <= size:

            rec_header = data[offset:offset + 8]

            if len(rec_header) < 8:
                break

            # mixed endian
            record_number = struct.unpack(">I", rec_header[:4])[0]
            content_length = struct.unpack("<I", rec_header[4:])[0]

            body_offset = offset + 8
            body_end = body_offset + content_length

            if body_end > size:
                out.write(
                    f"[{rec_num}] BROKEN RECORD\n"
                )
                break

            body = data[body_offset:body_end]

            parsed = parse_record(body)

            if parsed is None:

                out.write(
                    f"[{rec_num}] FAILED TO PARSE\n"
                )

                offset = body_end
                rec_num += 1
                continue

            minx, miny, maxx, maxy = parsed["bbox_deg"]

            out.write(
                f"[{rec_num}] "
                f"rec={record_number} "
                f"ofs=0x{offset:08X} "
                f"len={content_length} "
                f"parts={parsed['num_parts']} "
                f"pts={parsed['num_points']}\n"
            )

            out.write(
                f"bbox=({minx:.6f}, {miny:.6f}) - "
                f"({maxx:.6f}, {maxy:.6f})\n"
            )

            if parsed["parts"]:
                out.write(
                    f"parts={parsed['parts']}\n"
                )

            out.write("points:\n")

            for i, pt in enumerate(parsed["points"]):

                out.write(
                    f"  [{i:03d}] "
                    f"({pt['lon']:.6f}, {pt['lat']:.6f})\n"
                )

            out.write("\n")

            offset = body_end
            rec_num += 1

        out.write("========================================\n")
        out.write(f"Total records: {rec_num}\n")


# ============================================================
# Main
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Compact DT G1 MLP dump"
    )

    parser.add_argument(
        "mlp_file",
        help="Path to *.mlp"
    )

    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output txt"
    )

    args = parser.parse_args()

    mlp_path = args.mlp_file

    if not os.path.exists(mlp_path):
        print("File not found:", mlp_path)
        return

    if args.output:
        out_path = args.output
    else:
        out_path = os.path.splitext(mlp_path)[0] + "_compact_dump.txt"

    dump_mlp(mlp_path, out_path)

    print("Done.")
    print(out_path)


if __name__ == "__main__":
    main()