# -*- coding: utf-8 -*-
"""Поддержка наушников: тихий сигнал до подъёма, переподключение, конец по времени."""
import importlib.util
import math
import os
import struct
import wave
from datetime import datetime, timedelta

spec = importlib.util.spec_from_file_location("a", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssb_alarm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.log = lambda msg: None
ok_all = True


def check(name, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print("%-4s %-60s %s" % ("OK" if cond else "FAIL", name, detail))


# сигнал: секунда, целое число периодов, уровень около −45 дБ
path = m.keepalive_wav(-45)
with wave.open(path, "rb") as w:
    n = w.getnframes()
    s = struct.unpack("<%dh" % n, w.readframes(n))
peak = max(abs(x) for x in s)
check("сигнал длится ровно секунду", n == m.RATE, "кадров: %d" % n)
check("пик около −45 дБ", abs(20 * math.log10(peak / 32767.0) + 45) < 0.5, "пик %d" % peak)
check("петля без щелчка: начало и конец сходятся", abs(s[0]) <= 1 and abs(s[-1] - s[0]) < peak * 0.02)

# прогон по часам: наушники есть → пропали → вернулись под другим номером
t0 = datetime(2026, 10, 29, 23, 0)
clock = [t0]
seen = [(3, "Razer Barracuda"), (3, "Razer Barracuda"), None, (5, "Razer Barracuda"), (5, "Razer Barracuda")]
events = []


class FakePlayer:
    def __init__(self, dev):
        self.dev = dev

    def play_loop(self, p):
        events.append(("play", self.dev))
        return True

    def finished(self):
        return False

    def stop(self):
        events.append(("stop", self.dev))


def find(_pattern):
    i = min(len(seen) - 1, int((clock[0] - t0).total_seconds() // m.KEEPALIVE_CHECK_SEC))
    return seen[i]


def sleep(sec):
    clock[0] += timedelta(seconds=sec)


cfg = dict(m.load_cfg(), speaker="Barracuda", keepalive=True)
m.run_keepalive(cfg, t0 + timedelta(seconds=m.KEEPALIVE_CHECK_SEC * 5),
                now=lambda: clock[0], sleep=sleep, find=find, player=FakePlayer)
check("играет, отпускает пропавшие, подхватывает новый номер, гасит в конце",
      events == [("play", 3), ("stop", 3), ("play", 5), ("stop", 5)], str(events))

check("без speaker поддержка не запускается", not m.start_keepalive(dict(cfg, speaker="")))
check("выключенная поддержка не запускается", not m.start_keepalive(dict(cfg, keepalive=False)))
check("по умолчанию выключено", m.DEFAULTS["keepalive"] is False)

print("\nИТОГ:", "всё прошло" if ok_all else "есть ошибки")
raise SystemExit(0 if ok_all else 1)
