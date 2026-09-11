from __future__ import annotations

import ctypes
import locale
import sys

LANGS = ("en", "de", "ua", "ru")
LANG_NAMES: dict[str, str] = {
    "en": "English",
    "de": "Deutsch",
    "ua": "Українська",
    "ru": "Русский",
}
NAME_TO_LANG = {name: code for code, name in LANG_NAMES.items()}

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "app_title": "SVT → PSdZData Export",
        "paths": "Paths",
        "path_psdz": "PSdZData",
        "path_svt": "SVT XML",
        "path_out": "Export",
        "browse": "Browse…",
        "export_types": "Export types",
        "export_layout": "Export layout",
        "layout_psdzdata": "psdzdata/swe/...  (for E-Sys)",
        "layout_swe": "swe/btld, swe/swfl, ...",
        "layout_ecu": "by ECU",
        "layout_flat": "all files in one folder",
        "search": "Find files",
        "export_selected": "Export selected",
        "open_export": "Open export folder",
        "blocks": "Blocks:",
        "select_all": "All",
        "select_none": "None",
        "select_found": "Found only",
        "language": "Language",
        "col_ecu": "ECU / part",
        "col_class": "Class",
        "col_id": "ID",
        "col_ver": "Version",
        "col_status": "Status",
        "col_files": "Files",
        "status_ready": "Select PSdZData and an SVT, then click Find.",
        "status_reading": "Reading SVT and indexing PSdZData…",
        "status_index": "Index {kind}: {count} files…",
        "status_search_done": "Search finished. {detail}",
        "status_search_error": "Search failed.",
        "status_copying": "Copying…",
        "status_copy_file": "Copying {done}/{total}: {name}",
        "status_selection": "Selected blocks: {blocks}/{total_blocks}  •  parts: {parts}/{total_parts}  •  to export: {ready}",
        "summary": "Found {found} / {parts}  •  files {files}  •  missing {missing}",
        "summary_other": "  •  other versions {other}",
        "log_ecus": "ECUs: {ecus}, parts: {parts}",
        "log_index": "Indexed {count} files ({counts})",
        "log_empty": "empty",
        "log_lite": "Nothing found. Lite packs often have CAFD only, without BTLD/SWFL/SWFK.",
        "log_folder": "Folder: {path}",
        "log_err": "ERR {detail}",
        "status_found": "found ({count})",
        "status_found_ecu": "{found} found",
        "status_other": "other versions exist",
        "status_missing": "not in PSdZData",
        "browse_psdz": "PSdZData folder",
        "browse_svt": "SVT XML",
        "browse_out": "Export folder",
        "filetypes_svt": "SVT XML",
        "filetypes_all": "All files",
        "err_psdz": "Select an existing PSdZData folder.",
        "err_svt": "Select an SVT XML file.",
        "err_out_missing": "The export folder has not been created yet.",
        "err_out_empty": "Select an export folder.",
        "err_no_type": "Check at least one type to export.",
        "err_no_blocks": "Check blocks: All, None, or specific ECUs.",
        "err_no_found": "No matching files among the checked blocks.",
        "err_no_ecu": "No ECUs with partIdentification were found in the SVT.",
        "err_no_swe": "No swe folder found in {path}",
        "export_done": "Done. Copied: {copied}, skipped (already there): {skipped}, errors: {errors}",
    },
    "de": {
        "app_title": "SVT → PSdZData Export",
        "paths": "Pfade",
        "path_psdz": "PSdZData",
        "path_svt": "SVT-XML",
        "path_out": "Export",
        "browse": "Durchsuchen…",
        "export_types": "Typen exportieren",
        "export_layout": "Exportstruktur",
        "layout_psdzdata": "psdzdata/swe/...  (für E-Sys)",
        "layout_swe": "swe/btld, swe/swfl, ...",
        "layout_ecu": "nach ECU",
        "layout_flat": "alle Dateien in einen Ordner",
        "search": "Dateien suchen",
        "export_selected": "Auswahl exportieren",
        "open_export": "Exportordner öffnen",
        "blocks": "Blöcke:",
        "select_all": "Alle",
        "select_none": "Keine",
        "select_found": "Nur gefundene",
        "language": "Sprache",
        "col_ecu": "ECU / Teil",
        "col_class": "Klasse",
        "col_id": "ID",
        "col_ver": "Version",
        "col_status": "Status",
        "col_files": "Dateien",
        "status_ready": "PSdZData und SVT wählen, dann auf Suchen klicken.",
        "status_reading": "SVT wird gelesen, PSdZData indexiert…",
        "status_index": "Index {kind}: {count} Dateien…",
        "status_search_done": "Suche abgeschlossen. {detail}",
        "status_search_error": "Suche fehlgeschlagen.",
        "status_copying": "Kopieren…",
        "status_copy_file": "Kopieren {done}/{total}: {name}",
        "status_selection": "Blöcke: {blocks}/{total_blocks}  •  Teile: {parts}/{total_parts}  •  Export: {ready}",
        "summary": "Gefunden {found} / {parts}  •  Dateien {files}  •  fehlt {missing}",
        "summary_other": "  •  andere Versionen {other}",
        "log_ecus": "ECU: {ecus}, Teile: {parts}",
        "log_index": "Index: {count} Dateien ({counts})",
        "log_empty": "leer",
        "log_lite": "Nichts gefunden. Lite-Pakete enthalten oft nur CAFD, ohne BTLD/SWFL/SWFK.",
        "log_folder": "Ordner: {path}",
        "log_err": "ERR {detail}",
        "status_found": "gefunden ({count})",
        "status_found_ecu": "{found} gefunden",
        "status_other": "andere Versionen vorhanden",
        "status_missing": "nicht in PSdZData",
        "browse_psdz": "PSdZData-Ordner",
        "browse_svt": "SVT-XML",
        "browse_out": "Exportordner",
        "filetypes_svt": "SVT-XML",
        "filetypes_all": "Alle Dateien",
        "err_psdz": "Wählen Sie einen vorhandenen PSdZData-Ordner.",
        "err_svt": "Wählen Sie eine SVT-XML-Datei.",
        "err_out_missing": "Der Exportordner wurde noch nicht erstellt.",
        "err_out_empty": "Wählen Sie einen Exportordner.",
        "err_no_type": "Aktivieren Sie mindestens einen Typ für den Export.",
        "err_no_blocks": "Blöcke markieren: Alle, Keine oder einzelne ECU.",
        "err_no_found": "Unter den markierten Blöcken gibt es keine gefundenen Dateien.",
        "err_no_ecu": "In der SVT wurden keine ECU mit partIdentification gefunden.",
        "err_no_swe": "Kein swe-Ordner in {path} gefunden",
        "export_done": "Fertig. Kopiert: {copied}, übersprungen (schon vorhanden): {skipped}, Fehler: {errors}",
    },
    "ua": {
        "app_title": "SVT → PSdZData Export",
        "paths": "Шляхи",
        "path_psdz": "PSdZData",
        "path_svt": "SVT XML",
        "path_out": "Експорт",
        "browse": "Огляд…",
        "export_types": "Типи для експорту",
        "export_layout": "Структура експорту",
        "layout_psdzdata": "psdzdata/swe/...  (для E-Sys)",
        "layout_swe": "swe/btld, swe/swfl, ...",
        "layout_ecu": "за ECU",
        "layout_flat": "усі файли в одну папку",
        "search": "Знайти файли",
        "export_selected": "Експорт вибраних",
        "open_export": "Відкрити папку експорту",
        "blocks": "Блоки:",
        "select_all": "Усі",
        "select_none": "Нічого",
        "select_found": "Лише знайдені",
        "language": "Мова",
        "col_ecu": "ECU / деталь",
        "col_class": "Клас",
        "col_id": "ID",
        "col_ver": "Версія",
        "col_status": "Статус",
        "col_files": "Файли",
        "status_ready": "Оберіть PSdZData і SVT, потім натисніть «Знайти».",
        "status_reading": "Читання SVT та індекс PSdZData…",
        "status_index": "Індекс {kind}: {count} файлів…",
        "status_search_done": "Пошук завершено. {detail}",
        "status_search_error": "Помилка пошуку.",
        "status_copying": "Копіювання…",
        "status_copy_file": "Копіювання {done}/{total}: {name}",
        "status_selection": "Вибрано блоків: {blocks}/{total_blocks}  •  деталей: {parts}/{total_parts}  •  до експорту: {ready}",
        "summary": "Знайдено {found} / {parts}  •  файлів {files}  •  немає {missing}",
        "summary_other": "  •  інші версії {other}",
        "log_ecus": "ECU: {ecus}, деталей: {parts}",
        "log_index": "В індексі {count} файлів ({counts})",
        "log_empty": "порожньо",
        "log_lite": "Нічого не знайдено. Lite-збірки часто містять лише CAFD, без BTLD/SWFL/SWFK.",
        "log_folder": "Папка: {path}",
        "log_err": "ERR {detail}",
        "status_found": "знайдено ({count})",
        "status_found_ecu": "{found} знайдено",
        "status_other": "є інші версії",
        "status_missing": "немає в PSdZData",
        "browse_psdz": "Папка PSdZData",
        "browse_svt": "SVT XML",
        "browse_out": "Папка експорту",
        "filetypes_svt": "SVT XML",
        "filetypes_all": "Усі файли",
        "err_psdz": "Вкажіть наявну папку PSdZData.",
        "err_svt": "Вкажіть файл SVT XML.",
        "err_out_missing": "Папку експорту ще не створено.",
        "err_out_empty": "Вкажіть папку експорту.",
        "err_no_type": "Позначте хоча б один тип для експорту.",
        "err_no_blocks": "Позначте блоки: Усі, Нічого або конкретні ECU.",
        "err_no_found": "Серед позначених блоків немає знайдених файлів.",
        "err_no_ecu": "У SVT не знайдено ECU з partIdentification.",
        "err_no_swe": "Не знайдено каталог swe в {path}",
        "export_done": "Готово. Скопійовано: {copied}, пропущено (вже є): {skipped}, помилок: {errors}",
    },
    "ru": {
        "app_title": "SVT → PSdZData Export",
        "paths": "Пути",
        "path_psdz": "PSdZData",
        "path_svt": "SVT XML",
        "path_out": "Экспорт",
        "browse": "Обзор…",
        "export_types": "Экспортировать типы",
        "export_layout": "Структура экспорта",
        "layout_psdzdata": "psdzdata/swe/...  (для E-Sys)",
        "layout_swe": "swe/btld, swe/swfl, ...",
        "layout_ecu": "по ECU",
        "layout_flat": "все файлы в одну папку",
        "search": "Найти файлы",
        "export_selected": "Экспорт выбранных",
        "open_export": "Открыть папку экспорта",
        "blocks": "Блоки:",
        "select_all": "Все",
        "select_none": "Ничего",
        "select_found": "Только найденные",
        "language": "Язык",
        "col_ecu": "ECU / деталь",
        "col_class": "Класс",
        "col_id": "ID",
        "col_ver": "Версия",
        "col_status": "Статус",
        "col_files": "Файлы",
        "status_ready": "Выберите PSdZData и SVT, затем нажмите «Найти».",
        "status_reading": "Чтение SVT и индекс PSdZData…",
        "status_search_done": "Поиск завершён. {detail}",
        "status_search_error": "Ошибка поиска.",
        "status_index": "Индекс {kind}: {count} файлов…",
        "status_copying": "Копирование…",
        "status_copy_file": "Копирование {done}/{total}: {name}",
        "status_selection": "Выбрано блоков: {blocks}/{total_blocks}  •  деталей: {parts}/{total_parts}  •  к экспорту: {ready}",
        "summary": "Найдено {found} / {parts}  •  файлов {files}  •  нет {missing}",
        "summary_other": "  •  другие версии {other}",
        "log_ecus": "ECU: {ecus}, деталей: {parts}",
        "log_index": "В индексе {count} файлов ({counts})",
        "log_empty": "пусто",
        "log_lite": "Ничего не найдено. Lite-сборки часто содержат только CAFD, без BTLD/SWFL/SWFK.",
        "log_folder": "Папка: {path}",
        "log_err": "ERR {detail}",
        "status_found": "найден ({count})",
        "status_found_ecu": "{found} найдено",
        "status_other": "есть другие версии",
        "status_missing": "нет в PSdZData",
        "browse_psdz": "Папка PSdZData",
        "browse_svt": "SVT XML",
        "browse_out": "Папка экспорта",
        "filetypes_svt": "SVT XML",
        "filetypes_all": "Все файлы",
        "err_psdz": "Укажите существующую папку PSdZData.",
        "err_svt": "Укажите файл SVT XML.",
        "err_out_missing": "Папка экспорта ещё не создана.",
        "err_out_empty": "Укажите папку экспорта.",
        "err_no_type": "Отметьте хотя бы один тип для экспорта.",
        "err_no_blocks": "Отметьте блоки галочками: Все, Ничего или конкретные ECU.",
        "err_no_found": "Среди отмеченных блоков нет найденных файлов.",
        "err_no_ecu": "В SVT не найдено ECU с partIdentification.",
        "err_no_swe": "Не найден каталог swe в {path}",
        "export_done": "Готово. Скопировано: {copied}, пропущено (уже есть): {skipped}, ошибок: {errors}",
    },
}

# Windows primary language IDs: https://learn.microsoft.com/windows/win32/intl/language-identifier-constants-and-strings
_WIN_PRIMARY = {
    0x07: "de",
    0x09: "en",
    0x19: "ru",
    0x22: "ua",
}


def system_language() -> str:
    if sys.platform == "win32":
        try:
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            mapped = _WIN_PRIMARY.get(langid & 0xFF)
            if mapped:
                return mapped
        except Exception:
            pass
    candidates: list[str] = []
    for getter in (locale.getlocale, locale.getdefaultlocale):
        try:
            value = getter()[0]
        except (TypeError, ValueError):
            value = None
        if value:
            candidates.append(value)
    for raw in candidates:
        low = raw.replace("-", "_").lower()
        if low.startswith(("uk", "ua")):
            return "ua"
        if low.startswith("ru"):
            return "ru"
        if low.startswith("de"):
            return "de"
        if low.startswith("en"):
            return "en"
    return "en"


def normalize_language(code: str | None) -> str:
    if code in LANGS:
        return code
    return system_language()


def translate(lang: str, key: str, **kwargs: object) -> str:
    table = STRINGS.get(lang) or STRINGS["en"]
    text = table.get(key) or STRINGS["en"].get(key) or key
    if kwargs:
        return text.format(**kwargs)
    return text
