# -*- coding: utf-8 -*-
"""Дневник сна и оценка циркадного ритма: запись, ось ночи, оценка, советы."""
import importlib.util
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

spec = importlib.util.spec_from_file_location(
    "a", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssb_alarm.py"))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + ("   " + detail if detail else ""))
    if not cond:
        fails.append(name)


# ── ось ночи ──
check("ось: 18:00 → 0", a.tpos(18 * 60) == 0)
check("ось: 03:00 → 540", a.tpos(3 * 60) == 540)
check("ось: 15:00 позже 07:00", a.tpos(15 * 60) > a.tpos(7 * 60))
check("формат 540 → 03:00", a.fmt_pos(540) == "03:00")
check("«≈05:00» читается", a.hhmm_min("≈05:00") == 300)
check("мусор → None", a.hhmm_min("утром") is None)

# ── дневник: запись не затирает уже известное ──
path = os.path.join(tempfile.gettempdir(), "ssb_alarm_test_diary.csv")
if os.path.exists(path):
    os.remove(path)
a.diary_upsert(date(2026, 1, 2), path=path, lay="02:40")
a.diary_upsert(date(2026, 1, 2), path=path, woke="11:00", alarm="11:00", lay="")
row = a.diary_load(path)[0]
check("дневник: lay сохранился после второй записи", row["lay"] == "02:40", str(row))
check("дневник: woke дописан", row["woke"] == "11:00")

# ── оценка ритма на сдвигающемся режиме: засыпание 06:00 → 02:00 за 10 ночей ──
rows = []
start = date(2026, 1, 1)
for i in range(10):
    on = (6 * 60 - i * 24) % 1440            # каждый день на 24 мин раньше
    off = (on + 8 * 60) % 1440
    rows.append({"date": (start + timedelta(days=i)).isoformat(),
                 "asleep": "%02d:%02d" % divmod(on, 60),
                 "woke": "%02d:%02d" % divmod(off, 60),
                 "alarm": "%02d:%02d" % divmod(off, 60), "feel": "3"})
ns = a.rhythm(rows)
check("ритм: все 10 ночей распознаны", len(ns) == 10, str(len(ns)))
check("ритм: минимум температуры = мелатонин + 7 ч",
      all(abs(n["tmin"] - n["dlmo"] - 420) < 1e-6 for n in ns))
check("ритм: оценка сдвигается раньше", ns[-1]["tmin"] < ns[0]["tmin"],
      "%s → %s" % (a.fmt_pos(ns[0]["tmin"]), a.fmt_pos(ns[-1]["tmin"])))
tips = a.rhythm_advice(rows, datetime(2026, 1, 11, 10, 0))
titles = [t[0] for t in tips]
check("советы: есть блок «Твой ритм сейчас»", titles[0] == "Твой ритм сейчас")
check("советы: сдвиг виден", any(t.startswith("Сдвиг за") for t in titles), str(titles))

# ── одна плохая ночь не ломает оценку (медиана) ──
bad = rows + [{"date": "2026-01-11", "asleep": "07:00", "woke": "15:00", "alarm": "11:00"}]
before, after = a.rhythm(rows)[-1]["tmin"], a.rhythm(bad)[-1]["tmin"]
check("медиана: одна ночь в 07:00 сдвигает оценку меньше чем на час",
      abs(after - before) < 60, "%d мин" % (after - before))

# ── будильник раньше минимума температуры → совет про свет позже ──
late = [{"date": "2026-02-%02d" % (i + 1), "asleep": "05:00", "woke": "13:00",
         "alarm": "11:00"} for i in range(5)]
tips = a.rhythm_advice(late, datetime(2026, 2, 6, 9, 0))   # минимум ≈10:00, подъём в 9
check("свет: подъём раньше минимума — советует выйти на свет позже",
      any("Утренний свет — после" in t[0] for t in tips), str([t[0] for t in tips]))
check("цель: засыпание позже цели — «пока не двигай»",
      any("Пока не двигай" in t[0] for t in tips))

check("мало данных → одна подсказка", len(a.rhythm_advice(late[:2], datetime(2026, 2, 3, 11))) == 1)

print("\nИТОГ:", "всё прошло" if not fails else "ПРОВАЛ: " + ", ".join(fails))
sys.exit(1 if fails else 0)
