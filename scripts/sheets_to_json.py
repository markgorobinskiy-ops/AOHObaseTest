#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Таблица (Google Sheets) -> data/*.json

Читает опубликованную таблицу (CSV или TSV), сопоставляет строки с расами из
races.json и перезаписывает json-файлы рас (поле "data" у каждой расы),
а также раскладывает предметы по файлам локаций из sections расы.
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
RACES_PATH = "races.json"
IMAGES_DIR = "images/items"

warnings = []


def warn(msg):
    warnings.append(msg)
    print("  ! " + msg)


def norm(s):
    if s is None:
        return ""
    s = str(s).replace("\u00a0", " ")
    s = s.replace("\u2019", "'").replace("\u02bc", "'")  # ’ -> ' (апострофы)
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
    text = text.replace("\r\n", "\n").replace("\r", "\n")
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
    """Достаёт расы из races.json: список пар (ключ, словарь расы)."""
    if isinstance(races_doc, dict):
        for key in ("расы", "races"):
            v = races_doc.get(key)
            if isinstance(v, dict):
                return list(v.items())
            if isinstance(v, list):
                return [("", r) for r in v if isinstance(r, dict)]
        return [(k, v) for k, v in races_doc.items() if isinstance(v, dict)]
    if isinstance(races_doc, list):
        return [("", r) for r in races_doc if isinstance(r, dict)]
    return []


def main():
    rows = fetch_sheet()
    print("Загружено строк из таблицы: %d" % len(rows))
    if not rows:
        sys.exit("Таблица пустая — обновление не выполнялось.")

    race_entries = iter_races(load_json(RACES_PATH))
    if not race_entries:
        sys.exit("Не нашёл список рас в races.json — проверь структуру файла.")

    # индекс: нормализованное имя (ключ или name) -> (идентификатор, словарь расы)
    race_by_name = {}
    for race_key, r in race_entries:
        race_id = norm_key(r.get("data", "")) or norm_key(race_key)
        for variant in (race_key, r.get("name")):
            if variant:
                race_by_name[norm_key(variant)] = (race_id, r)

    # группировка строк таблицы
    items = {}  # (race_id, name, rarity, img) -> {"m": моб, "locs": [...]}
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
        found = race_by_name.get(norm_key(race_n))
        if found is None:
            warn("Строка %d: раса «%s» не найдена в races.json" % (i, race_n))
            continue
        race_id, race = found
        if img and not os.path.isfile(os.path.join(IMAGES_DIR, img)):
            warn("Строка %d: картинки нет в %s: %s" % (i, IMAGES_DIR, img))
        key = (race_id, name, rarity, img)
        if key not in items:
            items[key] = {"m": mob, "locs": []}
            row_order.append(key)
        if mob and not items[key]["m"]:
            items[key]["m"] = mob
        if loc_n not in items[key]["locs"]:
            items[key]["locs"].append(loc_n)

    print("Уникальных предметов: %d" % len(items))

    # раскладываем предметы по файлам
    touched = set(key[0] for key in row_order)
    updated_races = 0
    updated_locs = 0
    for race_key, r in race_entries:
        race_id = norm_key(r.get("data", "")) or norm_key(race_key)
        if race_id not in touched:
            continue  # расы нет в таблице — её файл не трогаем
        path = r.get("data")
        if not path:
            warn("У расы «%s» не указан файл data — пропущена" % r.get("name", race_id))
            continue
        doc = load_json(path) if os.path.isfile(path) else {}
        new_items = []
        for key in row_order:
            r_id, name, rarity, img = key
            if r_id != race_id:
                continue
            v = items[key]
            new_items.append(
                {
                    "name": name,
                    "rarity": rarity,
                    "img": img,
                    "locs": list(v["locs"]),
                    "m": v["m"],
                }
            )
        if not isinstance(doc, dict):
            warn("Файл %s: неожиданная структура, пропущен" % path)
            continue

        # каноничные имена локаций из sections (чтобы ' и ’ не различались)
        loc_canon = {}
        for sec in doc.get("sections", []) or []:
            if isinstance(sec, dict) and sec.get("name"):
                loc_canon[norm_key(sec["name"])] = sec["name"]
        for it in new_items:
            it["locs"] = [loc_canon.get(norm_key(l), l) for l in it["locs"]]

        doc["items"] = new_items
        save_json(path, doc)
        updated_races += 1

        # файлы локаций
        for sec in doc.get("sections", []) or []:
            if not isinstance(sec, dict) or not sec.get("data") or not sec.get("name"):
                continue
            sec_path = sec["data"]
            loc_n = sec["name"]
            loc_items = [
                it for it in new_items
                if any(norm_key(l) == norm_key(loc_n) for l in it["locs"])
            ]
            if not loc_items:
                continue  # локация пустая в таблице — файл не трогаем
            if not os.path.isfile(sec_path):
                warn("Файл локации не найден, пропущен: %s" % sec_path)
                continue
            loc_doc = load_json(sec_path)
            if isinstance(loc_doc, dict) and "items" in loc_doc:
                loc_doc["items"] = loc_items
                save_json(sec_path, loc_doc)
                updated_locs += 1
            elif isinstance(loc_doc, list):
                save_json(sec_path, loc_items)
                updated_locs += 1
            else:
                warn("Файл %s: неожиданная структура, пропущен" % sec_path)

    print("Обновлено файлов рас: %d" % updated_races)
    print("Обновлено файлов локаций: %d" % updated_locs)
    if warnings:
        print("Предупреждений: %d (см. выше)" % len(warnings))
    else:
        print("Предупреждений: 0")
    print("Синхронизировано предметов: %d" % len(items))


if __name__ == "__main__":
    main()
