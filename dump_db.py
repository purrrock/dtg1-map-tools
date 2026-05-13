#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import csv
import struct
import argparse


# ============================================================
# Helpers
# ============================================================

def clean_text(data: bytes) -> str:
    return (
        data.decode("utf-8", errors="ignore")
        .replace("\x00", "")
        .strip()
    )


# ============================================================
# DB dump
# ============================================================

def dump_db_table(db_path: str, out_path: str):

    with open(db_path, "rb") as f:

        # ----------------------------------------------------
        # Read DBF-like header
        # ----------------------------------------------------

        f.seek(32 + 8)

        header_len, record_len = struct.unpack(
            "<HH",
            f.read(4)
        )

        records_offset = 32 + header_len

        # ----------------------------------------------------
        # File size
        # ----------------------------------------------------

        f.seek(0, os.SEEK_END)
        file_size = f.tell()

        # ----------------------------------------------------
        # Read records
        # ----------------------------------------------------

        f.seek(records_offset)

        rows = []

        rec_num = 0

        while True:

            rec_offset = f.tell()

            rec = f.read(record_len)

            if len(rec) < record_len:
                break

            deleted_flag = rec[0]

            payload = rec[1:145].ljust(144, b"\x00")

            try:

                osm_id_b, code_b, fclass_b, name_b = struct.unpack(
                    "<12s4s28s100s",
                    payload
                )

                osm_id = clean_text(osm_id_b)
                code = clean_text(code_b)
                fclass = clean_text(fclass_b)
                name = clean_text(name_b)

                has_payload = any([
                    osm_id,
                    code,
                    fclass,
                    name
                ])

            except Exception:

                osm_id = ""
                code = ""
                fclass = ""
                name = ""

                has_payload = False

            rows.append({
                "Record": rec_num,
                "Offset": f"0x{rec_offset:08X}",
                "DeletedFlag": f"0x{deleted_flag:02X}",
                "HasPayload": has_payload,
                "osm_id": osm_id,
                "code": code,
                "fclass": fclass,
                "name": name
            })

            rec_num += 1

    # --------------------------------------------------------
    # Save CSV
    # --------------------------------------------------------

    with open(out_path, "w", newline="", encoding="utf-8-sig") as csvfile:

        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "Record",
                "Offset",
                "DeletedFlag",
                "HasPayload",
                "osm_id",
                "code",
                "fclass",
                "name"
            ],
            delimiter=";"
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(row)

    print("Done.")
    print(out_path)
    print(f"Records: {len(rows)}")
    print(f"File size: {file_size} bytes")
    print(f"Header len: {header_len}")
    print(f"Record len: {record_len}")


# ============================================================
# Main
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="DT G1 DB table dump"
    )

    parser.add_argument(
        "db_file",
        help="Path to *.db"
    )

    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output CSV file"
    )

    args = parser.parse_args()

    db_path = args.db_file

    if not os.path.exists(db_path):
        print("File not found:", db_path)
        return

    if args.output:
        out_path = args.output
    else:
        out_path = os.path.splitext(db_path)[0] + "_table.csv"

    dump_db_table(db_path, out_path)


if __name__ == "__main__":
    main()