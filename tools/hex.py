import argparse
import os

def hex_dump(file_path):
    # Check if the file exists
    if not os.path.isfile(file_path):
        print(f"Error: File not found or not a file - '{file_path}'")
        return

    try:
        # Open file for binary reading ('rb')
        with open(file_path, 'rb') as f:
            offset = 0
            while True:
                # Read 16 bytes at a time
                chunk = f.read(16)
                if not chunk:
                    break  # End of file
                
                # Format each byte into a two-digit hex number (e.g. '0A', 'FF')
                hex_values = ' '.join(f'{b:02X}' for b in chunk)
                
                # Output address (8 characters) and the bytes themselves
                print(f'{offset:08X}:  {hex_values}')
                
                offset += 16
                
    except PermissionError:
        print(f"Error: No permission to read file '{file_path}'")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == '__main__':
    # Set up command line argument processing
    parser = argparse.ArgumentParser(description="Generate hex dump of a file without ASCII part..")
    parser.add_argument("filepath", help="Path to input file")
    
    args = parser.parse_args()
    hex_dump(args.filepath)