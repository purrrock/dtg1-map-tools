import os
import shutil

base_file = 'base_map.osm'
contour_file = 'contours.osm'
output_file = 'map.osm'

print("🚀 Executing high-speed merge (OS-level copy and binary truncation)...")
try:
    # Step 1: Use underlying OS directly copy base_map
    shutil.copyfile(base_file, output_file)

    with open(output_file, 'r+b') as f_out:

        # --- Step 2: Safely remove the trailing </osm> from base_map ---
        f_out.seek(0, os.SEEK_END)
        file_size = f_out.tell()

        # Expand search range to 64KB in case of trailing spaces or comments
        chunk_size = min(file_size, 64 * 1024)
        f_out.seek(file_size - chunk_size)
        tail_data = f_out.read()

        pos = tail_data.rfind(b'</osm>')
        if pos != -1:
            # Truncate directly once found
            f_out.truncate(file_size - chunk_size + pos)
        else:
            print("⚠️ Warning: </osm> tag not found at the end of base_map.osm! (File may be corrupted or incomplete)")

        # Move pointer to the new end of file ready for writing
        f_out.seek(0, os.SEEK_END)

        # --- Step 3: Process contours.osm ---
        with open(contour_file, 'rb') as f_in:
            # Read first 64KB to find and skip the XML header and <osm ...> tag
            header_chunk = f_in.read(64 * 1024)

            osm_tag_start = header_chunk.find(b'<osm')
            if osm_tag_start != -1:
                # Find the closing '>' of the <osm ...> tag
                osm_tag_end = header_chunk.find(b'>', osm_tag_start)
                if osm_tag_end != -1:
                    # Move read pointer to the character right after '>' (start of actual data)
                    f_in.seek(osm_tag_end + 1)
                else:
                    print("⚠️ Warning: <osm> tag in contours.osm is unusually long, cannot skip automatically!")
                    f_in.seek(0)
            else:
                print("⚠️ Warning: <osm> tag not found in contours.osm!")
                f_in.seek(0)

            # Dump the remaining content at high speed
            # 💡 Note: This will copy over the trailing </osm> from contours.osm,
            # so output_file naturally gets a perfect XML ending without extra work!
            shutil.copyfileobj(f_in, f_out, length=8*1024*1024)

    size_mb = os.path.getsize(output_file) / (1024*1024)
    print(f"✅ Merge successful! Size of map.osm: {size_mb:.2f} MB")

except Exception as e:
    print(f"❌ Merge failed: {e}")