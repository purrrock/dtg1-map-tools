import sys
import csv
from typing import Dict, Tuple, Set

class HWConstants:
    """Hardware reserved constants for Fallback mechanics."""
    WATER_CODE = 8200

class LookupTables:
    """
    Таблицы стилей (LUT) и реестры маршрутизации для парсера.
    Включает хэш-таблицы для Advanced Key-Value Tag Routing и 
    изоляцию реестров Blacklist по слоям (Namespace Collisions prevention).
    """
    HIGHWAY_CODES: Dict[str, int] = {}
    POLYGON_CODES: Dict[str, int] = {}
    POI_CODES: Dict[str, int] = {}
    DISPLAY_SCALES: Dict[int, int] = {}
    POI_SHAPES: Dict[str, str] = {}
    
    # Изоляция реестров Blacklist по слоям (Software Culling)
    DISABLED_ROADS: Set[str] = set()
    DISABLED_LANDUSE: Set[str] = set()
    DISABLED_POIS: Set[str] = set()
    DISABLED_WATER: Set[str] = set()
    
    # Advanced Key-Value Tag Routing: layer -> (key, value) -> fclass
    TAG_ROUTING: Dict[str, Dict[Tuple[str, str], str]] = {
        'pois': {}, 
        'roads': {}, 
        'landuse': {}, 
        'water': {}
    }

    @classmethod
    def load_from_csv(cls, filepath: str = "features.csv") -> None:
        """
        Метод парсинга конфигурации маршрутизации (LUT).
        Выполняет загрузку правил маппинга, заполнение таблиц для Early Exit
        и динамическую привязку алиасов (LOD/Цвет/Шейп).
        """
        print(f"[>] Загрузка таблицы стилей LUT из {filepath}...")
        
        try:
            with open(filepath, mode="r", encoding="utf-8") as f:
                reader = csv.reader(f, delimiter=";")
                next(reader, None)  # Пропуск заголовка
                
                loaded_records = 0
                for row in reader:
                    # Проверка минимальной длины строки конфигурации
                    if len(row) < 11:
                        continue
                        
                    fclass = row[1].strip()
                    layer = row[4].strip()
                    osm_tag = row[5].strip()
                    enabled_flag = row[10].strip().lower()
                    
                    # === Механизм Software Culling (Early Exit парсинг) ===
                    # Изоляция реестров для предотвращения Namespace Collisions
                    if enabled_flag in ("0", "false", "no", "off", ""):
                        if layer == "roads":
                            cls.DISABLED_ROADS.add(fclass)
                        elif layer == "pois":
                            cls.DISABLED_POIS.add(fclass)
                        elif layer == "water":
                            cls.DISABLED_WATER.add(fclass)
                        else:
                            cls.DISABLED_LANDUSE.add(fclass)
                        continue
                        
                    # === Advanced Key-Value Tag Routing ===
                    # Парсинг сложных OSM тегов (например, amenity=hospital, place=city)
                    if osm_tag and "=" in osm_tag:
                        for tag_pair in osm_tag.split(","):
                            if "=" in tag_pair:
                                k, v = tag_pair.split("=", 1)
                                if layer not in cls.TAG_ROUTING:
                                    cls.TAG_ROUTING[layer] = {}
                                cls.TAG_ROUTING[layer][(k.strip(), v.strip())] = fclass
                                
                    # Парсинг параметров ремаппинга (аппаратные ID и уровни SQT-индексации)
                    try:
                        remap_code = int(row[7].strip())
                        remap_lod = int(row[9].strip())
                    except ValueError:
                        continue
                        
                    # === Заполнение таблиц LUT для генератора бинарных графов ===
                    if layer == "roads":
                        cls.HIGHWAY_CODES[fclass] = remap_code
                        cls.DISPLAY_SCALES[remap_code] = remap_lod
                    elif layer in ("landuse", "water"):
                        cls.POLYGON_CODES[fclass] = remap_code
                        cls.DISPLAY_SCALES[remap_code] = remap_lod
                    elif layer == "pois":
                        cls.POI_CODES[fclass] = remap_code
                        cls.DISPLAY_SCALES[remap_code] = remap_lod
                        # Fallback-механизм для отсутствующего шейпа POI
                        shape_val = row[11].strip().lower() if len(row) > 11 else "rhombus"
                        cls.POI_SHAPES[fclass] = shape_val if shape_val else "rhombus"
                        
                    loaded_records += 1
                    
            print(f"    Успешно импортировано правил: {loaded_records}")
            print(f"[i] LUT загружен. Дороги: {len(cls.HIGHWAY_CODES)}, Полигоны: {len(cls.POLYGON_CODES)}, POI: {len(cls.POI_CODES)}")
            
            # Страховка для водного слоя (должен присутствовать в LOD2)
            if HWConstants.WATER_CODE not in cls.DISPLAY_SCALES:
                cls.DISPLAY_SCALES[HWConstants.WATER_CODE] = 1000
                
        except FileNotFoundError:
            print(f"[-] Ошибка: Файл конфигурации LUT {filepath} не найден.")
            sys.exit(1)
        except Exception as e:
            print(f"[-] Критическая ошибка парсинга {filepath}: {e}")
            sys.exit(1)
