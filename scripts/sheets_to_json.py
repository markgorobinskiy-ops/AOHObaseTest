#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Таблица (Google Sheets) -> data/*.json

Читает опубликованную таблицу (CSV или TSV), сопоставляет строки с расами из
races.json и раскладывает предметы по файлам локаций (sections) каждой расы —
в том формате, который ждёт сайт:
  n/name  — название,  r/rarity — редкость,  img — картинка,
  locs    — ПУТИ локаций (например "demons/uohatumh"),  m — мобы.
Файл расы держит items: [] (или «сироты» без локаций из sections) —
сайт считает предметы по файлам локаций, иначе всё задваивается.
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


def clean_data(p):
    """data/demons/uohatumh.json -> demons/uohatumh (как cleanData на сайте)."""
    return re.sub(r"^data/", "", str(p)).replace(".json", "")


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

    race_by_name = {}
    for race_key, r in race_entries:
        race_id = norm_key(r.get("data", "")) or norm_key(race_key)
        for variant in (race_key, r.get("name")):
            if variant:
                race_by_name[norm_key(variant)] = (race_id, r)

    items = {}  # (race_id, name, rarity, img) -> {"m": моб, "locs": [имена]}
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

    touched = set(key[0] for key in row_order)
    updated_races = 0
    updated_locs = 0
    for race_key, r in race_entries:
        race_id = norm_key(r.get("data", "")) or norm_key(race_key)
        if race_id not in touched:
            continue
        path = r.get("data")
        if not path:
            warn("У расы «%s» не указан файл data — пропущена" % r.get("name", race_id))
            continue
        if not os.path.isfile(path):
            warn("Файл расы не найден, пропущен: %s" % path)
            continue
        doc = load_json(path)
        if not isinstance(doc, dict):
            warn("Файл %s: неожиданная структура, пропущен" % path)
            continue

        # каноничные имена и ПУТИ локаций из sections расы
        loc_canon, loc_path = {}, {}
        for sec in doc.get("sections", []) or []:
            if isinstance(sec, dict) and sec.get("name"):
                loc_canon[norm_key(sec["name"])] = sec["name"]
                if sec.get("data"):
                    loc_path[norm_key(sec["name"])] = clean_data(sec["data"])

        # раскладка предметов по путям локаций
        by_loc = {}   # путь -> [предметы]
        orphans = []  # предметы без известной локации — остаются в файле расы
        for key in row_order:
            r_id, name, rarity, img = key
            if r_id != race_id:
                continue
            v = items[key]
            paths = []
            for l in v["locs"]:
                p = loc_path.get(norm_key(l))
                if p is None:
                    warn("Предмет «%s»: локация «%s» не найдена в sections расы «%s» — предмет не попадёт в файл локации" % (name, l, r.get("name", race_id)))
                    continue
                if p not in paths:
                    paths.append(p)
            entry = {
                "n": name, "name": name,
                "r": rarity, "rarity": rarity,
                "img": img,
                "locs": paths,
                "m": v["m"],
            }
            if paths:
                for p in paths:
                    by_loc.setdefault(p, []).append(entry)
            else:
                orphans.append(entry)

        # файлы локаций
        for sec in doc.get("sections", []) or []:
            if not (isinstance(sec, dict) and sec.get("data")):
                continue
            p = clean_data(sec["data"])
            sec_path = sec["data"]
            if p not in by_loc:
                continue  # в этой локации предметов нет — файл не трогаем
            if not os.path.isfile(sec_path):
                warn("Файл локации не найден, пропущен: %s" % sec_path)
                continue
            loc_doc = load_json(sec_path)
            if isinstance(loc_doc, dict):
                loc_doc["items"] = by_loc[p]
            elif isinstance(loc_doc, list):
                loc_doc = by_loc[p]
            else:
                warn("Файл %s: неожиданная структура, пропущен" % sec_path)
                continue
            save_json(sec_path, loc_doc)
            updated_locs += 1

        # файл расы: сайт берёт предметы из файлов локаций, поэтому items
        # держим пустым — иначе счётчики и таблицы задваиваются
        old = doc.get("items") or []
        doc["items"] = orphans
        save_json(path, doc)
        updated_races += 1
        print("  · %s: локаций с предметами %d, без привязки %d, прежних записей очищено %d"
              % (path, len(by_loc), len(orphans), len(old)))

    print("Обновлено файлов рас: %d" % updated_races)
    print("Обновлено файлов локаций: %d" % updated_locs)
    if warnings:
        print("Предупреждений: %d (см. выше)" % len(warnings))
    else:
        print("Предупреждений: 0")
    print("Синхронизировано предметов: %d" % len(items))


if __name__ == "__main__":
    main()
