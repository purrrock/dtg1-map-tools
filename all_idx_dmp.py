import struct
import csv
import os

LAYERS = ['pois', 'roads', 'landuse', 'water']
YZL_SIZE = 32
NODE_SIZE = 28

def process_file(layer_name):
    input_file = f"{layer_name}.idx"
    output_file = f"{layer_name}.idx_dump.csv"

    if not os.path.exists(input_file):
        print(f"[-] Пропуск {input_file} (файл не найден).")
        return

    print(f"[+] Дамп {input_file} -> {output_file}...")

    with open(input_file, 'rb') as f, open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        
        writer.writerow([
            'Offset_Hex', 'NodeType', 'TreeIndex',
            'v1', 'v2', 'v3', 'Code',
            'MinX', 'MinY', 'MaxX', 'MaxY', 'Raw_Hex'
        ])

        data = f.read()
        size = len(data)

        if size < YZL_SIZE or not data.startswith(b'YZL'):
            print(f"  [!] Внимание: Сигнатура YZL не найдена в {input_file}.")
            return

        offset = YZL_SIZE
        tree_counter = -1

        while offset < size:
            # Ищем ближайшую сигнатуру SQT
            sqt_idx = data.find(b"SQT\x01", offset)

            if sqt_idx == -1:
                if offset < size:
                    padding_len = size - offset
                    writer.writerow([
                        hex(offset), f'EOF Padding ({padding_len} bytes)', '',
                        '', '', '', '', '', '', '', '', data[offset:size].hex()
                    ])
                break

            # Если между текущей позицией и началом дерева есть мусор
            if sqt_idx > offset:
                padding_len = sqt_idx - offset
                writer.writerow([
                    hex(offset), f'Padding ({padding_len} bytes)', '',
                    '', '', '', '', '', '', '', '', data[offset:sqt_idx].hex()
                ])

            tree_counter += 1
            tree_start = sqt_idx

            # Читаем заголовок дерева (8 байт)
            header_raw = data[sqt_idx:sqt_idx+8]
            sig, param = struct.unpack('<4sI', header_raw)

            writer.writerow([
                hex(tree_start), 'Tree Header', tree_counter,
                '', '', f"Param:{param}", '',
                '', '', '', '', header_raw.hex()
            ])

            offset = sqt_idx + 8

            # Читаем узлы внутри дерева
            while offset + NODE_SIZE <= size:
                next_sqt = data.find(b"SQT\x01", offset)
                
                # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ:
                # Если до следующего дерева осталось меньше 28 байт,
                # значит это padding. Прерываем чтение узлов!
                if next_sqt != -1 and (next_sqt - offset) < NODE_SIZE:
                    break

                raw = data[offset:offset+NODE_SIZE]
                v1, v2, val_0x08 = struct.unpack("<III", raw[:12])

                # Эвристика: если val_0x08 < 1 000 000, это v3 (Branch). Иначе это float MinX (Leaf)
                if val_0x08 < 1000000:
                    v3, branch_minx, branch_miny, branch_maxx, branch_maxy = struct.unpack('<Iffff', raw[8:28])
                    writer.writerow([
                        hex(offset), 'Branch', tree_counter,
                        v1, v2, v3, '',
                        branch_minx, branch_miny, branch_maxx, branch_maxy, raw.hex()
                    ])
                else:
                    leaf_minx, leaf_miny, leaf_maxx, leaf_maxy, code = struct.unpack('<ffffI', raw[8:28])
                    writer.writerow([
                        hex(offset), 'Leaf', tree_counter,
                        v1, v2, '', code,
                        leaf_minx, leaf_miny, leaf_maxx, leaf_maxy, raw.hex()
                    ])

                offset += NODE_SIZE

def main():
    print("Начинаю создание чистых дампов...\n")
    for layer in LAYERS:
        process_file(layer)
    print("\nГотово. Проверьте CSV файлы.")

if __name__ == '__main__':
    main()