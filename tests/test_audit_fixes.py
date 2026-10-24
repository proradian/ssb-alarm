# -*- coding: utf-8 -*-
"""Исправления по независимому аудиту перед релизом: каждая проверка — сценарий,
который раньше ломался."""
import importlib.util
import os
import sys
import tempfile
import types
from datetime import date, datetime

spec = importlib.util.spec_from_file_location(
    "a", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssb_alarm.py"))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + ("   " + detail if detail else ""))
    if not cond:
        fails.append(name)


# №3 — «дыры» в плане по датам больше не теряют звонок
now = datetime(2026, 10, 24, 18, 0)
once, daily = a.wake_schedule(10, 0, {"2026-10-26": "09:30"}, now)
check("план с пропуском: 25.10 звонит в базовое 10:00",
      [w.isoformat() for w in once] == ["2026-10-25T10:00:00", "2026-10-26T09:30:00"],
      str([w.isoformat() for w in once]))
check("ежедневный — со дня после плана", daily == datetime(2026, 10, 27, 10, 0))

# №7 — сдвиг через полночь меняет и дату
cfg = {"hh": 10, "mm": 0, "plan": {"2026-10-25": "00:05"}}
c2 = a.shift_wake(cfg, -15, only_next=True, now=datetime(2026, 10, 24, 20, 0))
check("−15 от 00:05 → 23:50 того же вечера", c2["plan"] == {"2026-10-24": "23:50"}, str(c2["plan"]))

# №12 — сон через 18:00 не выпадает
ns = a.sleep_nights([{"date": "2026-10-24", "asleep": "10:00", "woke": "18:30", "alarm": ""}])
check("сон 10:00–18:30 попадает в ритм", len(ns) == 1 and ns[0]["dur"] == 510, str(ns))

# №5 — анкета закрывается сама, первая проверка — через 10 минут от решения
calls = {"after": None, "sleep": []}


class FakeForm:
    def __init__(self, cfg):
        self.root = types.SimpleNamespace(after=lambda ms, fn: calls.__setitem__("after", ms))

    def close(self):
        pass

    def run(self):
        pass


solved = types.SimpleNamespace(solved=True, rang_at=datetime.now(), started=0, attempts=1)
a.ring = lambda cfg: solved
a.DiaryForm = FakeForm
a.diary_upsert = lambda *x, **k: True
a.quiet_check = lambda cfg, title="": True
a.time.sleep = lambda s: calls["sleep"].append(s)
a.take_up_flag = lambda: False
a.wake_up(a.load_cfg())
check("анкета закрывается сама через %d с" % a.DIARY_TIMEOUT, calls["after"] == a.DIARY_TIMEOUT * 1000)
check("первая проверка ≈ через 10 минут от решения", calls["sleep"] and 590 <= calls["sleep"][0] <= 600,
      str(calls["sleep"]))

# №4 — громкость устройства по умолчанию читается (без изменения громкости)
st = a.speaker_volume("*default*")
check("устройство по умолчанию найдено", st.get("found") is True, str(st))

print("\nИТОГ:", "всё прошло" if not fails else "ПРОВАЛ: " + ", ".join(fails))
sys.exit(1 if fails else 0)
