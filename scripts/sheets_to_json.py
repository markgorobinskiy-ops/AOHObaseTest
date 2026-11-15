#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Таблица (Google Sheets) -> data/*.json

Читает опубликованную таблицу (CSV или TSV), сопоставляет строки с расами и
локациями из data/races.json и перезаписывает json-файлы локаций.
Один предмет в нескольких локациях = несколько строк с одинаковым названием.
"""

import csv
import io
import json
import os
import re
import sys
import urllib.request

SHEET_URL = os.environ.get(
    "SHEET_URL",
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYV-0pQvs_2bMuK5SKgVqYvcFxJq7g4in95Gz5RMIVMrNydVVySJ64NoJGXQAQbCCY4vZ-w8sg4M11/pub?output=csv",
)
RACES_PATH = "data/races.json"
IMAGES_DIR = "images/items"

warnings = []


def warn(msg):
    warnings.append(msg)
    print("  ! " + msg)


def norm(s):
    if s is None:
        return ""
    s = str(s).replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def norm_key(s):
    return norm(s).lower()


def fetch_sheet():
    req = urllib.request.Request(
        SHEET_URL, headers={"User-Agent": "Mozilla/5.0 (sheets-sync)"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    text = raw.decode("utf-8-sig", errors="replace")
    delim = "\t" if "\t" in text.splitlines()[0] else ","
    return list(csv.DictReader(io.StringIO(text), delimiter=delim))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def iter_races(races_doc):
    """Достаёт список рас из data/races.json независимо от точной структуры."""
    if isinstance(races_doc, list):
        return races_doc
    if isinstance(races_doc, dict):
        for key in ("races", "items", "data"):
            if isinstance(races_doc.get(key), list):
                return races_doc[key]
        # словарь вида {"dwarves": {...}, ...}
        return [v for v in races_doc.values() if isinstance(v, dict)]
    return []


def main():
    rows = fetch_sheet()
    print("Загружено строк из таблицы: %d" % len(rows))
    if not rows:
        sys.exit("Таблица пустая — обновление не выполнялось.")

    races_doc = load_json(RACES_PATH)
    races = iter_races(races_doc)
    if not races:
        sys.exit("Не нашёл список рас в data/races.json — проверь структуру файла.")

    # индекс: нормализованное имя расы -> запись расы
    race_by_name = {}
    for r in races:
        if isinstance(r, dict) and r.get("name"):
            race_by_name[norm_key(r["name"])] = r

    # индекс: нормализованное имя локации -> (запись расы, секция)
    loc_index = {}
    for r in races:
        for sec in r.get("sections", []) or []:
            if isinstance(sec, dict) and sec.get("name"):
                loc_index[(norm_key(r.get("name", "")), norm_key(sec["name"]))] = (
                    r,
                    sec,
                )

    # группировка строк таблицы
    items = {}  # key=(race_key, name, rarity, img) -> {"locs": [имена локаций], "m": моб}
    row_order = []
    for i, row in enumerate(rows, start=2):
        race_n = norm(row.get("Раса"))
        loc_n = norm(row.get("Локация"))
        name = norm(row.get("Предмет"))
        rarity = norm(row.get("Редкость"))
        img = norm(row.get("Картинка"))
        mob = norm(row.get("Моб"))
        if not (race_n or loc_n or name or rarity or img or mob):
            continue  # пустая строка
        if not (race_n and loc_n and name):
            warn("Строка %d: пустые Раса/Локация/Предмет — пропущена" % i)
            continue
        race = race_by_name.get(norm_key(race_n))
        if race is None:
            warn("Строка %d: раса «%s» не найдена в races.json" % (i, race_n))
            continue
        if (norm_key(race_n), norm_key(loc_n)) not in loc_index:
            warn(
                "Строка %d: локация «%s» не найдена у расы «%s» — проверь написание"
                % (i, loc_n, race_n)
            )
            continue
        if img and not os.path.isfile(os.path.join(IMAGES_DIR, img)):
            warn("Строка %d: картинки нет в %s: %s" % (i, IMAGES_DIR, img))
        key = (norm_key(race_n), name, rarity, img)
        if key not in items:
            items[key] = {"m": mob, "locs": []}
            row_order.append(key)
        if mob and not items[key]["m"]:
            items[key]["m"] = mob
        if loc_n not in items[key]["locs"]:
            items[key]["locs"].append(loc_n)

    print("Уникальных предметов: %d" % len(items))

    # собираем json по локациям
    loc_names_by_race = {}
    for (race_key, name, rarity, img), v in items.items():
        for loc_n in v["locs"]:
            loc_names_by_race.setdefault(race_key, set()).add(loc_n)

    updated_files = 0
    for r in races:
        race_key = norm_key(r.get("name", ""))
        wanted = loc_names_by_race.get(race_key, set())
        for sec in r.get("sections", []) or []:
            if not isinstance(sec, dict) or not sec.get("data"):
                continue
            loc_n = norm(sec.get("name", ""))
            if loc_n and loc_n not in wanted:
                continue  # локация пустая в таблице — не трогаем её json
            path = sec["data"]
            if not os.path.isfile(path):
                warn("Файл локации не найден, пропущен: %s" % path)
                continue
            doc = load_json(path)
            new_items = []
            for key in row_order:
                race_k, name, rarity, img = key
                if race_k != race_key:
                    continue
                v = items[key]
                if loc_n in v["locs"]:
                    new_items.append(
                        {
                            "name": name,
                            "rarity": rarity,
                            "img": img,
                            "locs": list(v["locs"]),
                            "m": v["m"],
                        }
                    )
            if isinstance(doc, dict) and "items" in doc:
                doc["items"] = new_items
            elif isinstance(doc, list):
                doc = new_items
            else:
                warn("Файл %s: неожиданная структура, пропущен" % path)
                continue
            save_json(path, doc)
            updated_files += 1

    print("Обновлено файлов локаций: %d" % updated_files)
    if warnings:
        print("Предупреждений: %d (см. выше)" % len(warnings))
    else:
        print("Предупреждений: 0")
    print("Синхронизировано предметов: %d" % len(items))


if __name__ == "__main__":
    main()
