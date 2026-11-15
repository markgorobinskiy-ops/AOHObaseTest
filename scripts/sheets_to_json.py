#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Таблица (Google Sheets) -> data/*.json  ·  версия с поддержкой подлокаций

Что умеет:
  - Читает опубликованную таблицу (CSV/TSV) с колонками:
    Раса | Локация | Предмет | Редкость | Картинка | Моб
  - Рекурсивно обходит дерево локаций расы на ЛЮБУЮ глубину
    (файл расы -> локации -> подлокации -> ...), собирая пары «имя -> путь».
  - В колонке «Локация» можно писать:
      * просто имя:      Пещеры            — если такое имя одно на всю расу
      * полный путь:     Корнейл/Пещеры    — если имён-близнецов несколько
    Регистр, апострофы и лишние пробелы не важны.
  - Предметы пишет в формате сайта: n/name, r/rarity, img, locs (ПУТИ), m.
  - Предметы живут ТОЛЬКО в файлах локаций; в файле расы items очищается,
    иначе сайт задваивает счётчики.
  - «Сироты» остаются в файле расы с пустым locs и попадают в предупреждения.
"""

import csv
import io
import json
import os
import re
import sys
import urllib.request

# ─────────────────────────────────────────────────────────────────────────────
# НАСТРОЙКИ: перенесите сюда строку SHEET_URL из предыдущей версии скрипта
# (публичная ссылка «Опубликовать в сети» вашей таблицы, output=csv).
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYV-0pQvs_2bMuK5SKgVqYvcFxJq7g4in95Gz5RMIVMrNydVVySJ64NoJGXQAQbCCY4vZ-w8sg4M11/pub?output=csv"
RACES_PATH = "races.json"
IMAGES_DIR = "images/items"
# ─────────────────────────────────────────────────────────────────────────────

warnings = []


def warn(msg):
    warnings.append(msg)
    print("  ! " + msg)


def norm(s):
    if s is None:
        return ""
    s = str(s).replace("\u00a0", " ")
    s = s.replace("\u2019", "'").replace("\u02bc", "'")
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
    text = text.replace("\r\n", "\n").replace("\r", "\n")
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


def collect_locations(file_path, visited=None):
    """Рекурсивный обход дерева локаций.
    Возвращает список пар (цепочка_имён, путь):
        ("Корнейл", "humans/korneil"),
        ("Корнейл/Пещеры", "humans/korneil/pesheri"), ...
    """
    if visited is None:
        visited = set()
    key = str(file_path).replace("\\", "/")
    if key in visited:
        return []
    visited.add(key)
    out = []
    if not os.path.isfile(file_path):
        return out
    try:
        node = load_json(file_path)
    except Exception as e:
        warn("Не удалось прочитать %s: %s" % (file_path, e))
        return out
    if not isinstance(node, dict):
        return out
    for sec in node.get("sections", []) or []:
        if not (isinstance(sec, dict) and sec.get("name") and sec.get("data")):
            continue
        child_id = clean_data(sec["data"])
        out.append((sec["name"], child_id))
        out.extend(collect_locations(sec["data"], visited))
    return out


def all_data_files():
    out = []
    for root, _dirs, files in os.walk("data"):
        for f in files:
            if f.endswith(".json"):
                out.append(os.path.join(root, f))
    return out


def main():
    if "PASTE_YOUR" in SHEET_URL:
        sys.exit(
            "Не задана ссылка на таблицу (SHEET_URL). "
            "Перенесите строку SHEET_URL из предыдущей версии скрипта."
        )

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

    # ── читаем таблицу ──────────────────────────────────────────────────────
    items = {}   # (race_id, name, rarity, img) -> {"m": моб, "locs": [строки]}
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

    # ── раскладываем предметы по дереву локаций ─────────────────────────────
    updated_races = 0
    updated_locs = 0
    total_locations = 0

    for race_key, r in race_entries:
        race_id = norm_key(r.get("data", "")) or norm_key(race_key)
        if race_id not in touched:
            continue
        race_file = r.get("data")
        if not race_file:
            warn("У расы «%s» не указан файл data — пропущена" % r.get("name", race_id))
            continue
        if not os.path.isfile(race_file):
            warn("Файл расы не найден, пропущен: %s" % race_file)
            continue
        doc = load_json(race_file)
        if not isinstance(doc, dict):
            warn("Файл %s: неожиданная структура, пропущен" % race_file)
            continue

        entries = collect_locations(race_file)
        total_locations += len(entries)

        by_name = {}
        by_chain = {}
        for chain, p in entries:
            last = chain.split("/")[-1]
            by_name.setdefault(norm_key(last), []).append((chain, p))
            by_chain.setdefault(norm_key(chain), []).append(p)

        def resolve(loc_str, item_name):
            if "/" in loc_str:
                paths = by_chain.get(norm_key(loc_str), [])
                if len(paths) == 1:
                    return paths[0]
                if len(paths) > 1:
                    warn("Предмет «%s»: путь «%s» неоднозначен" % (item_name, loc_str))
                    return None
                warn("Предмет «%s»: путь «%s» не найден в дереве расы «%s»"
                     % (item_name, loc_str, r.get("name", race_id)))
                return None
            cands = by_name.get(norm_key(loc_str), [])
            if len(cands) == 1:
                return cands[0][1]
            if len(cands) > 1:
                opts = " или ".join(c[0] for c in cands)
                warn("Предмет «%s»: имя «%s» встречается несколько раз (%s). "
                     "Укажите в таблице полный путь через «/»"
                     % (item_name, loc_str, r.get("name", race_id), opts))
                return None
            warn("Предмет «%s»: локация «%s» не найдена в дереве расы «%s»"
                 % (item_name, loc_str, r.get("name", race_id)))
            return None

        by_loc = {}
        orphans = []
        for key in row_order:
            r_id, name, rarity, img = key
            if r_id != race_id:
                continue
            v = items[key]
            paths = []
            for l in v["locs"]:
                p = resolve(l, name)
                if p and p not in paths:
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

        # файлы локаций: только те, куда попали предметы
        sec_files = all_data_files()
        sec_by_id = {}
        for sf in sec_files:
            sec_by_id[clean_data(sf)] = sf
        for chain, p in entries:
            if p not in by_loc:
                continue
            sec_file = sec_by_id.get(p)
            if not sec_file:
                warn("Файл локации не найден для пути «%s» (%s)" % (chain, p))
                continue
            loc_doc = load_json(sec_file)
            if isinstance(loc_doc, dict):
                loc_doc["items"] = by_loc[p]
            elif isinstance(loc_doc, list):
                loc_doc = by_loc[p]
            else:
                warn("Файл %s: неожиданная структура, пропущен" % sec_file)
                continue
            save_json(sec_file, loc_doc)
            updated_locs += 1
            print("  · %s: записано предметов %d (%s)"
                  % (sec_file, len(by_loc[p]), chain))

        # файл расы: items только для сирот
        old = doc.get("items") or []
        doc["items"] = orphans
        save_json(race_file, doc)
        updated_races += 1
        print("  · %s: локаций с предметами %d, без привязки %d, прежних записей очищено %d"
              % (race_file, len(by_loc), len(orphans), len(old)))

    print("Обновлено файлов рас: %d" % updated_races)
    print("Обновлено файлов локаций: %d" % updated_locs)
    print("Локаций найдено в деревьях рас: %d" % total_locations)
    if warnings:
        print("Предупреждений: %d (см. выше)" % len(warnings))
    else:
        print("Предупреждений: 0")
    print("Синхронизировано предметов: %d" % len(items))


if __name__ == "__main__":
    main()
