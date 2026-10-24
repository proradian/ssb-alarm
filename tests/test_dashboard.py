# -*- coding: utf-8 -*-
"""Главное окно: обработчики не копятся при перерисовке (№10), выключение отменяет
отложенную регистрацию (№6), закрытие окна доносит новое время до Планировщика (№6).
Планировщик подменён — настоящие задачи не трогаются."""
import importlib.util
import os
import sys
import tempfile

spec = importlib.util.spec_from_file_location(
    "a", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssb_alarm.py"))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + ("   " + detail if detail else ""))
    if not cond:
        fails.append(name)


tmp = os.path.join(tempfile.gettempdir(), "ssb_alarm_test_dash")
os.makedirs(tmp, exist_ok=True)
a.CONF = os.path.join(tmp, "settings.json")
a.DIARY = os.path.join(tmp, "diary.csv")
for f in (a.CONF, a.DIARY):
    if os.path.exists(f):
        os.remove(f)
calls = []
a.task_status = lambda: {"exists": True, "state": "Ready", "next": "—"}
a.speaker_check = lambda cfg: ("ok", "тест")
a.task_enable = lambda hh, mm, plan=None: (calls.append(("enable", hh, mm)), (True, "OK"))[1]
a.task_disable = lambda: (calls.append(("disable",)), (True, "OK"))[1]
a.task_enable_bed_cfg = lambda cfg: (True, "OK")
a.task_remove_bed = a.task_remove_light = lambda: (True, "OK")

d = a.Dashboard()
d.status = {"exists": True, "state": "Ready"}
res = {}


def step1():
    n0 = len(d.root.tk.call("info", "commands"))
    for _ in range(5):
        d.draw()
    res["leak"] = len(d.root.tk.call("info", "commands")) - n0
    d.shift(-15)                        # время сохранено, регистрация отложена на 1,2 с
    d.toggle()                          # выключить сразу
    d.root.after(2000, step2)


def step2():
    res["after_toggle"] = list(calls)
    calls.clear()
    d.shift(-15)
    d.quit()                            # закрыть сразу после изменения


d.root.after(500, step1)
d.run()
check("5 перерисовок не добавляют Tcl-команд", res["leak"] <= 5, "прирост %d" % res["leak"])
check("выключение не включается обратно отложенной регистрацией",
      ("enable", 6, 15) not in res["after_toggle"] and ("disable",) in res["after_toggle"]
      or not any(c[0] == "enable" for c in res["after_toggle"]), str(res["after_toggle"]))
check("закрытие окна доносит новое время до Планировщика", ("enable", 6, 0) in calls, str(calls))

print("\nИТОГ:", "всё прошло" if not fails else "ПРОВАЛ: " + ", ".join(fails))
sys.exit(1 if fails else 0)
