import argparse
import os

def hex_dump(file_path):
    # Проверяем существование файла
    if not os.path.isfile(file_path):
        print(f"Ошибка: Файл не найден или это не файл - '{file_path}'")
        return

    try:
        # Открываем файл для бинарного чтения ('rb')
        with open(file_path, 'rb') as f:
            offset = 0
            while True:
                # Читаем по 16 байт
                chunk = f.read(16)
                if not chunk:
                    break  # Конец файла
                
                # Форматируем каждый байт в двузначное hex-число (например, '0A', 'FF')
                hex_values = ' '.join(f'{b:02X}' for b in chunk)
                
                # Выводим адрес (8 символов) и сами байты
                print(f'{offset:08X}:  {hex_values}')
                
                offset += 16
                
    except PermissionError:
        print(f"Ошибка: Нет прав для чтения файла '{file_path}'")
    except Exception as e:
        print(f"Произошла непредвиденная ошибка: {e}")

if __name__ == '__main__':
    # Настраиваем обработку аргументов командной строки
    parser = argparse.ArgumentParser(description="Генерация hex-дампа файла без ASCII-части.")
    parser.add_argument("filepath", help="Путь к входному файлу")
    
    args = parser.parse_args()
    hex_dump(args.filepath)