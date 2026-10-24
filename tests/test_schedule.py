# -*- coding: utf-8 -*-
"""Лестница по датам → триггеры Планировщика; кнопки −15/+15 и «шаг раньше»."""
import importlib.util
import os
import sys
from datetime import datetime

spec = importlib.util.spec_from_file_location(
    "a", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssb_alarm.py"))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + ("   " + detail if detail else ""))
    if not cond:
        fails.append(name)


now = datetime(2026, 10, 22, 18, 0)
plan = {"2026-10-21": "11:30", "2026-10-23": "10:30", "2026-10-24": "10:00"}
once, daily = a.wake_schedule(11, 0, plan, now)
check("прошлые даты плана не ставятся", [w.isoformat() for w in once] ==
      ["2026-10-23T10:30:00", "2026-10-24T10:00:00"])
check("ежедневный — со дня после плана", daily == datetime(2026, 10, 25, 11, 0), daily.isoformat())
once, daily = a.wake_schedule(7, 15, {}, now)
check("без плана — просто ежедневный", once == [] and (daily.hour, daily.minute) == (7, 15))
ps = a.triggers_ps(*a.wake_schedule(11, 0, plan, now))
check("в PowerShell два разовых и один ежедневный",
      ps.count("-Once") == 2 and ps.count("-Daily") == 1)

c = a.shift_wake({"hh": 11, "mm": 0, "plan": {"2026-10-23": "10:30"}}, 15, True, now)
check("+15 двигает дату плана, а не базу", (c["hh"], c["mm"]) == (11, 0)
      and c["plan"] == {"2026-10-23": "10:45"})
c = a.shift_wake({"hh": 11, "mm": 0, "plan": {}}, -15, True, now)
check("−15 без плана двигает базу", (c["hh"], c["mm"]) == (10, 45))
c = a.shift_wake({"hh": 11, "mm": 0, "plan": {"2026-10-23": "10:30", "2026-10-20": "12:00"}},
                 -30, False, now)
check("шаг раньше: база и будущие даты, прошлое удалено",
      (c["hh"], c["mm"]) == (10, 30) and c["plan"] == {"2026-10-23": "10:00"}, str(c["plan"]))
check("склонение", [a.plural_ru(n, "задача", "задачи", "задач") for n in (1, 3, 5, 11, 21)]
      == ["задача", "задачи", "задач", "задач", "задача"])

print("\nИТОГ:", "всё прошло" if not fails else "ПРОВАЛ: " + ", ".join(fails))
sys.exit(1 if fails else 0)
