# validate.py — проверка базы Энтузиастов перед коммитом (v2)
# Изменения к v1: отсутствие img — "внимание", не ошибка;
# добавлена проверка "одна картинка у разных предметов";
# пути нормализованы (работает одинаково на Windows и Mac/Linux).
import json, re, sys
from pathlib import Path
from collections import defaultdict

ALLOWED_RARITY = ["Особый", "Редкий", "Эпический", "Легендарный"]
REPO = Path(__file__).parent
errors, warnings = defaultdict(list), defaultdict(list)

def err(f, msg):  errors[f].append(msg)
def warn(f, msg): warnings[f].append(msg)
def norm(s):      return re.sub(r"\s+", " ", s.strip().lower())

# 1. Все JSON читаются? (ключи — с прямыми слэшами, как в секциях)
parsed = {}
for p in sorted(REPO.rglob("*.json")):
    rel = p.relative_to(REPO).as_posix()
    try:
        parsed[rel] = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(rel, f"битый JSON: строка {e.lineno}, столбец {e.colno}")

# 2. races.json: структура и существование файлов
item_registry, img_registry, apostrophes = defaultdict(list), defaultdict(list), defaultdict(set)

def check_name(name, f):
    if name != name.strip():
        warn(f, f"имя '{name}': пробелы по краям")
    if "  " in name:
        warn(f, f"имя '{name}': двойной пробел внутри")
    for ch in name:
        if ch in "'’":
            apostrophes[ch].add(name)

def check_item(it, rel, idx):
    if not isinstance(it, dict):
        err(rel, f"предмет #{idx}: не объект"); return
    label = it.get("n", f"#{idx}")
    if not it.get("n"):
        err(rel, f"предмет #{idx}: нет поля n (имя)")
    if "r" not in it:
        err(rel, f"предмет '{label}': нет поля r (редкость)")
    elif it["r"] not in ALLOWED_RARITY:
        err(rel, f"предмет '{label}': редкость '{it['r']}' не из {ALLOWED_RARITY}")
    if "m" not in it:
        warn(rel, f"предмет '{label}': нет поля m (моб/условие)")
    if it.get("n"):
        check_name(it["n"], rel)
        item_registry[norm(it["n"])].append((rel, it["n"]))
    img = it.get("img")
    if img:
        path = REPO / "images" / "items" / img
        if not path.exists():
            warn(rel, f"предмет '{label}': картинка images/items/{img} не найдена")
        else:
            img_registry[norm(img)].append((rel, label, img))
    else:
        warn(rel, f"предмет '{label}': нет поля img (картинка ещё не добавлена)")

if "races.json" in parsed:
    rj = parsed["races.json"].get("races", {})
    if not isinstance(rj, dict) or not rj:
        err("races.json", "нет объекта races или он пуст")
    for key, race in rj.items():
        if not isinstance(race, dict):
            err("races.json", f"раса {key}: не объект"); continue
        for field in ["name", "icon", "bg", "tagline", "data"]:
            if not race.get(field):
                err("races.json", f"раса {key}: нет поля {field}")
        for pf in ["bg", "data"]:
            if race.get(pf) and not (REPO / race[pf]).exists():
                err("races.json", f"раса {key}: файл {race[pf]} не найден")
else:
    err("races.json", "файл не найден или не читается")

# 3. Файлы данных: скелет, секции.
# Важно: каждый JSON сканируется сам по себе ровно один раз,
# поэтому предметы из секций здесь повторно не проверяем —
# в секции сверяем только существование файла и совпадение id.
for rel, obj in parsed.items():
    if rel == "races.json":
        continue
    if not isinstance(obj, dict):
        err(rel, "файл не является объектом"); continue
    for field in ["id", "name", "sections", "items"]:
        if field not in obj:
            err(rel, f"нет поля {field}")
    for field in ["sections", "items"]:
        if field in obj and not isinstance(obj[field], list):
            err(rel, f"поле {field} не список")
    for i, sec in enumerate(obj.get("sections", [])):
        if not isinstance(sec, dict):
            err(rel, f"секция #{i}: не объект"); continue
        for field in ["name", "data"]:
            if not sec.get(field):
                err(rel, f"секция #{i}: нет поля {field}")
        if sec.get("data") and not (REPO / sec["data"]).exists():
            err(rel, f"секция '{sec.get('name', i)}': файл {sec['data']} не найден")
        sub = parsed.get(sec.get("data", ""))
        if isinstance(sub, dict) and sec.get("id") and sub.get("id") and sub["id"] != sec["id"]:
            warn(rel, f"секция '{sec['name']}': id в списке '{sec['id']}' не совпадает с id в файле '{sub['id']}'")
        check_name(sec.get("name", ""), rel)
    for j, it in enumerate(obj.get("items", [])):
        check_item(it, rel, j)

# 4. Дубликаты имён
for key, places in item_registry.items():
    if len(places) > 1:
        files = ", ".join(sorted(set(f for f, _ in places)))
        warn(places[0][0], f"дубликат имени '{places[0][1]}' встречается в: {files}")

# 5. Одна картинка у разных предметов
for key, places in img_registry.items():
    if len(places) > 1:
        files = ", ".join(sorted(set(f for f, _, _ in places)))
        warn(places[0][0], f"картинка '{places[0][2]}' используется у разных предметов: {files}")

# 6. Апострофы вперемешку
if len(apostrophes) > 1:
    detail = "; ".join(f"'{ch}': {len(names)} шт." for ch, names in sorted(apostrophes.items()))
    warn("все data-файлы", f"смешаны апострофы ({detail}) — дедуп считает их разными символами")

# Отчёт
for f in sorted(set(errors) | set(warnings)):
    for e in errors[f]:  print(f"  ОШИБКА   {f}: {e}")
    for w in warnings[f]: print(f"  внимание {f}: {w}")
total_e, total_w = sum(len(v) for v in errors.values()), sum(len(v) for v in warnings.values())
print(f"\nИтог: ошибок {total_e}, предупреждений {total_w}")
sys.exit(1 if total_e else 0)
