rem Установка компилятора
pip install pyinstaller

rem Сборка монолитного исполняемого файла
pyinstaller --noconfirm --onefile --console --name "dtg1_map_compiler" --clean dtg1_map_compiler.py
