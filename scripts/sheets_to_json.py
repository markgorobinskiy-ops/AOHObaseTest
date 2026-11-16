# -*- coding: utf-8 -*-
"""
Таблица -> JSON, версия 3.5 (склейка предмета из нескольких строк)

Принцип единого хранения:
  - предмет с привязкой живёт ТОЛЬКО в файле своей локации;
  - в файле расы хранятся только предметы без привязки (сироты);
  - при прогоне предметы, получившие локацию, из файла расы убираются.

Новое в 3.5:
  - несколько строк таблицы с одним и тем же предметом (в рамках одной расы)
    склеиваются в ОДИН объект: суммируются локации и мобы;
  - поле "m" хранит всех мобов одной строкой через запятую — так карточка
    предмета и вкладки локаций остаются читаемыми без правок сайта.
"""
import csv
import io
import json
import os
import re
import sys
import urllib.request

# НАСТРОЙКИ: перенесите сюда строку SHEET_URL из предыдущей версии скрипта
# (публичная ссылка «Опубликовать в сети» вашей таблицы, output=csv).
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYV-0pQvs_2bMuK5SKgVqYvcFxJq7g4in95Gz5RMIVMrNydVVySJ64NoJGXQAQbCCY4vZ-w8sg4M11/pub?output=csv"
RACES_PATH = "races.json"
IMAGES_DIR = "images/items"

warnings = []
created_count = 0
used_ids = set()
groups = {}


def warn(msg):
    warnings.append(msg)
    print("  ! " + msg)


def note(msg):
    print("  > " + msg)


def norm(s):
    if s is None:
        return ""
    s = str(s).replace("\u00a0", " ")
    s = s.replace("\u2019", "'").replace("\u02bc", "'")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def norm_key(s):
    return norm(s).lower()


TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(name):
    s = norm(name).lower()
    out = []
    for ch in s:
        if ch in TRANSLIT:
            out.append(TRANSLIT[ch])
        elif ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            out.append(ch)
        elif ch in " -_":
            out.append("-")
    slug = re.sub(r"-+", "-", "".join(out)).strip("-")
    return slug or "loc"


def path_from_data(data):
    """'data/humans/perekreste.json' -> 'humans/perekreste'"""
    p = norm(data).replace("\\", "/")
    p = re.sub(r"^data/", "", p)
    p = re.sub(r"\.json$", "", p)
    return p.strip("/")


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception as e:
                warn("%s: не удалось прочитать json (%s), файл будет перезаписан" % (path, e))
                return default
    return default


def save_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def normalize_races(raw_cfg):
    """Приводит races.json к списку словарей рас, какой бы формы ни был файл."""
    if isinstance(raw_cfg, dict):
        for k in ("races", "расы"):
            if k in raw_cfg and isinstance(raw_cfg[k], dict):
                raw_cfg = raw_cfg[k]
                break
    entries = []
    if isinstance(raw_cfg, dict):
        for k, v in raw_cfg.items():
            if isinstance(v, dict):
                e = dict(v)
                e["_key"] = k
                entries.append(e)
            elif isinstance(v, str):
                entries.append({"_key": k, "id": norm(k) or slugify(v), "name": norm(v)})
    elif isinstance(raw_cfg, list):
        for rc in raw_cfg:
            if isinstance(rc, dict):
                entries.append(dict(rc))
            elif isinstance(rc, str):
                entries.append({"id": slugify(rc), "name": norm(rc)})
            else:
                warn("races.json: непонятная запись расы, пропускаю")
    else:
        warn("races.json: неожиданный формат файла")
    return entries


def race_id_of(e):
    """id расы: из поля data, затем из id, затем из латинского ключа, затем из имени."""
    if norm(e.get("data")):
        rid = path_from_data(e["data"])
        if rid:
            return rid.rsplit("/", 1)[-1]
    if norm(e.get("id")):
        return norm(e["id"])
    key = norm(e.get("_key", ""))
    if key and re.fullmatch(r"[a-z0-9_-]+", key):
        return key
    return slugify(e.get("name", "")) or slugify(e.get("_key", ""))


# ---------- дерево расы ----------

def make_node(name, path):
    file_path = os.path.join("data", *path.split("/")) + ".json"
    return {
        "name": name,
        "key": norm_key(name),
        "path": path,
        "file_path": file_path,
        "file": load_json(file_path, None),
        "dirty": False,
        "children": None,
    }


def node_children(node):
    if node["children"] is None:
        kids = []
        f = node["file"] or {}
        for sec in (f.get("sections") or []):
            if not isinstance(sec, dict):
                continue
            sname = norm(sec.get("name", ""))
            path = ""
            if norm(sec.get("data")):
                path = path_from_data(sec["data"])
            elif norm(sec.get("id")):
                path = norm(sec["id"])
            if not path:
                warn("%s: у секции «%s» нет ни data, ни id — пропущена"
                     % (node["file_path"].replace("\\", "/"), sname or "?"))
                continue
            if not sname:
                sname = path.rsplit("/", 1)[-1]
            if not norm(sec.get("data")):
                sec["data"] = "data/" + path + ".json"
                node["dirty"] = True
            kids.append(make_node(sname, path))
        node["children"] = kids
    return node["children"]


def collect_nodes(node, acc):
    acc.append(node)
    for ch in node_children(node):
        collect_nodes(ch, acc)
    return acc


def get_file(node):
    if node["file"] is None:
        node["file"] = {"id": node["path"], "name": node["name"], "sections": [], "items": []}
        node["dirty"] = True
    return node["file"]


def find_child(node, key):
    for ch in node_children(node):
        if ch["key"] == key:
            return ch
    return None


def create_child(parent, race, name):
    global created_count
    pf = get_file(parent)
    base = slugify(name)
    sid = parent["path"] + "/" + base
    n = 2
    while sid in used_ids or any(ch["path"] == sid for ch in node_children(parent)):
        sid = parent["path"] + "/" + base + "-" + str(n)
        n += 1
    pf.setdefault("sections", []).append({
        "name": name,
        "id": sid,
        "data": "data/" + sid + ".json",
    })
    parent["dirty"] = True

    child = make_node(name, sid)
    child["file"] = {"id": sid, "name": name, "sections": [], "items": []}
    child["dirty"] = True
    node_children(parent).append(child)
    used_ids.add(sid)
    race["index"].setdefault(child["key"], []).append(child)
    created_count += 1
    note("создана локация «%s» -> %s" % (name, child["file_path"].replace("\\", "/")))
    return child


def resolve(race, cell):
    """Разбирает ячейку «Локация». Возвращает (узел|None, путь_строкой)."""
    raw = norm(cell)
    if not raw:
        warn("не указана локация — предмет попадёт в файл расы без привязки")
        return None, ""
    segs = [norm(x) for x in raw.split("/") if norm(x)]
    if not segs:
        return None, ""
    # необязательный префикс расы
    if norm_key(segs[0].lstrip(" +")) == race["key"]:
        segs = segs[1:]
    if not segs:
        warn("в ячейке указано имя расы «%s», а не локация" % race["name"])
        return None, ""
    cur = race["root"]
    creating = False
    for seg in segs:
        if seg.startswith("+"):
            creating = True
            name = seg[1:].strip()
        else:
            name = seg
        if not name:
            warn("пустое звено в пути «%s»" % raw)
            return None, ""
        key = norm_key(name)
        if creating:
            existing = find_child(cur, key)
            cur = existing if existing is not None else create_child(cur, race, name)
        else:
            child = find_child(cur, key)
            if child is None:
                alts = race["index"].get(key, [])
                if len(alts) == 1:
                    child = alts[0]
                elif len(alts) > 1:
                    warn("локация «%s» неоднозначна, варианты: %s — укажите полный путь"
                         % (name, ", ".join(n["path"] for n in alts)))
                    return None, ""
                else:
                    warn("локация «%s» не найдена в дереве расы «%s»" % (name, race["name"]))
                    return None, ""
            cur = child
    return cur, cur["path"]


# ---------- предметы ----------

def make_item(g):
    mobs = g["mob"]
    if isinstance(mobs, str):
        mobs = [mobs] if mobs else []
    return {
        "n": g["name"], "name": g["name"],
        "r": g["rarity"], "rarity": g["rarity"],
        "img": g["img"],
        "locs": sorted(set(g["locs"])),
        "m": ", ".join(mobs),
    }


def merge_items(node, incoming):
    """Вливает новые предметы в файл узла, сохраняя прежние записи.
    Возвращает количество влитых предметов."""
    f = get_file(node)
    old = f.get("items", []) or []
    inc = dict(incoming)
    result = []
    seen = set()
    # сначала проходим по старым записям: совпавшие по имени обновляем из incoming,
    # несовпавшие — бережно сохраняем, а повторы с тем же именем выбрасываем
    for it in old:
        k = norm_key(it.get("name") or it.get("n") or "")
        if k in inc:
            result.append(inc.pop(k))
            seen.add(k)
        elif k not in seen:
            result.append(it)
            seen.add(k)
    # оставшиеся в incoming — новые предметы, дописываем в конец
    for it in inc.values():
        result.append(it)
    if json.dumps(result, ensure_ascii=False, sort_keys=True) != \
       json.dumps(old, ensure_ascii=False, sort_keys=True):
        f["items"] = result
        node["dirty"] = True
    return len(result)


def items_for_node(race, node):
    inc = {}
    for g in groups.values():
        if g["race"]["id"] != race["id"]:
            continue
        if node["path"] in g["locs"]:
            # предмет добавляется ЦЕЛИКОМ: со всеми своими локациями и мобами,
            # поэтому в каждой локации он будет одной строкой, без дублей
            inc[norm_key(g["name"])] = make_item(g)
    return inc


# ---------- главный поток ----------

def main():
    global created_count, groups
    if "PASTE_" in SHEET_URL:
        print("! Не задан SHEET_URL — впишите ссылку на опубликованную таблицу")
        sys.exit(1)

    print("Загружаем таблицу...")
    raw = urllib.request.urlopen(SHEET_URL, timeout=30).read().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(raw)))
    rows = [r for r in rows if norm(r.get("Раса")) and norm(r.get("Предмет"))]
    print("Загружено строк из таблицы: %d" % len(rows))

    races_cfg = normalize_races(load_json(RACES_PATH, []))
    race_map = {}
    for rc in races_cfg:
        rid = race_id_of(rc)
        rname = norm(rc.get("name", "")) or rid
        if not rid:
            warn("races.json: у одной из рас не удалось определить id — пропускаю")
            continue
        root = make_node(rname, rid)
        race = {"id": rid, "name": rname, "key": norm_key(rname),
                "root": root, "index": {}}
        for nd in collect_nodes(root, []):
            race["index"].setdefault(nd["key"], []).append(nd)
            used_ids.add(nd["path"])
        race_map[race["key"]] = race
    print("Локаций найдено в деревьях рас: %d" % sum(len(r["index"]) for r in race_map.values()))

    # --- проход 1: разбор строк, сборка групп предметов ---
    # строки с одним и тем же предметом (в рамках одной расы) склеиваются:
    # суммируются локации и мобы, берётся первая непустая редкость/картинка
    groups = {}
    for r in rows:
        race = race_map.get(norm_key(r.get("Раса")))
        if race is None:
            warn("раса «%s» не найдена в races.json — строка пропущена" % norm(r.get("Раса")))
            continue
        name = norm(r.get("Предмет"))
        gkey = (race["id"], norm_key(name))
        if gkey not in groups:
            groups[gkey] = {
                "race": race,
                "name": name,
                "rarity": norm(r.get("Редкость")),
                "img": norm(r.get("Картинка")),
                "mob": [],
                "locs": [],
            }
        g = groups[gkey]
        if not g["rarity"]:
            g["rarity"] = norm(r.get("Редкость"))
        if not g["img"]:
            g["img"] = norm(r.get("Картинка"))
        mob = norm(r.get("Моб"))
        if mob and mob not in g["mob"]:
            g["mob"].append(mob)
        node, path = resolve(race, r.get("Локация"))
        if node is not None and path not in g["locs"]:
            g["locs"].append(path)

    # проверка картинок
    for g in groups.values():
        if g["img"] and not os.path.exists(os.path.join(IMAGES_DIR, g["img"])):
            warn("картинка «%s» не найдена в %s" % (g["img"], IMAGES_DIR))

    print("Уникальных предметов: %d" % len(groups))

    # --- проход 2: запись в файлы ---
    updated_races = 0
    updated_locs = 0
    cleaned_total = 0
    for race in race_map.values():
        nodes = collect_nodes(race["root"], [])

        # локации: вливаем предметы
        for nd in nodes[1:]:
            inc = items_for_node(race, nd)
            if inc:
                merge_items(nd, inc)
                print("  - %s: записано предметов %d (%s)"
                      % (nd["file_path"].replace("\\", "/"), len(inc), nd["name"]))

        # файл расы: только сироты; всё с привязкой — вычищаем
        orphans = {}
        for g in groups.values():
            if g["race"]["id"] == race["id"] and not g["locs"]:
                orphans[norm_key(g["name"])] = make_item(g)
        f = get_file(race["root"])
        if orphans:
            merge_items(race["root"], orphans)
            f = get_file(race["root"])
        items = f.get("items") or []
        kept = [it for it in items if not (it.get("locs") or [])]
        removed = len(items) - len(kept)
        cleaned_total += removed
        if removed:
            f["items"] = kept
            race["root"]["dirty"] = True
        print("  - data/%s.json: сирот %d, вычищено дубликатов с привязкой %d"
              % (race["id"], len(kept), removed))

    for race in race_map.values():
        for nd in collect_nodes(race["root"], []):
            if nd["dirty"]:
                save_json(nd["file_path"], nd["file"])
                if nd["path"] == race["id"]:
                    updated_races += 1
                else:
                    updated_locs += 1

    print("Обновлено файлов рас: %d" % updated_races)
    print("Обновлено файлов локаций: %d" % updated_locs)
    print("Создано локаций: %d" % created_count)
    print("Вычищено дубликатов из файлов рас: %d" % cleaned_total)
    print("Предупреждений: %d" % len(warnings))
    print("Синхронизировано предметов: %d" % len(groups))


if __name__ == "__main__":
    main()
