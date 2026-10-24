# -*- coding: utf-8 -*-
"""Каждое исправление — по сценарию из находок Codex."""
import importlib.util
import math
import os
import tempfile
import shutil
import sys
import types
import wave
from datetime import datetime

spec = importlib.util.spec_from_file_location("a", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssb_alarm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SP = os.path.join(tempfile.gettempdir(), "ssb_alarm_test")
ok_all = True


def check(name, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print("%-4s %-60s %s" % ("OK" if cond else "FAIL", name, detail))


# №9 — клавиши без символа
class Ev:
    def __init__(self, keysym, char):
        self.keysym, self.char = keysym, char


w = object.__new__(m.AlarmWindow)
w.options, w.buf, w.locked_until = None, "", 0.0
w.draw_input = lambda: None
w.check_numeric = lambda: None
for ch in "37":
    m.AlarmWindow.on_key(w, Ev(ch, ch))
for ks in ("Shift_L", "Shift_R", "Left", "Up", "Control_L", "Alt_L", "Caps_Lock"):
    m.AlarmWindow.on_key(w, Ev(ks, ""))
check("№9  Shift/стрелки/Ctrl не меняют знак", w.buf == "37", "буфер: %r" % w.buf)
m.AlarmWindow.on_key(w, Ev("minus", "-"))
check("№9  минус по-прежнему меняет знак", w.buf == "-37", "буфер: %r" % w.buf)
m.AlarmWindow.on_key(w, Ev("KP_Subtract", "-"))
check("№9  минус на цифровом блоке тоже", w.buf == "37", "буфер: %r" % w.buf)

# №11 — «сколько поспишь» перед самым будильником
cfg = m.load_cfg()
h = m.sleep_hours_if_now(dict(cfg, hh=13, mm=45), datetime(2026, 10, 14, 13, 40))
check("№11 за 5 мин до 13:45 — не 23 ч 53 мин", h == 0.0, "%.2f ч" % h)
h = m.sleep_hours_if_now(dict(cfg, hh=13, mm=45), datetime(2026, 10, 14, 5, 0))
check("№11 обычный случай не сломан (05:00 → 13:45)", abs(h - 8.55) < 0.01, "%.2f ч" % h)

# №16 — 20 с громкого шума внутри «тишины»
fps, env = 4, []


def put(sec, v):
    env.extend([v] * int(sec * fps))


for _ in range(80):
    put(1.4, 2000); put(2.6, 30)
put(5, 30); put(20, 3000); put(5, 30); put(1, 6000)
for _ in range(20):
    put(1.4, 2000); put(2.6, 30)
lens = [round(p["len"], 1) for p in m.analyse(env)["pauses"]]
check("№16 громкий шум больше не «пауза»", not any(x > 25 for x in lens), "паузы: %s" % lens)

# №3, №4 — запись настроек
m.CONF = SP  # это папка: открыть на запись нельзя
check("№3  ошибка записи возвращает False", m.save_cfg({"a": 1}) is False)
check("№4  временный файл не остаётся", not os.path.exists(SP + ".tmp"))
m.CONF = os.path.join(SP, "cfg_test.json")
check("№4  запись в норме даёт True", m.save_cfg({"hh": 7}) is True)
check("№4  после записи нет .tmp", not os.path.exists(m.CONF + ".tmp"))
os.remove(m.CONF)

# №2 — тест поверх идущего звонка
m.TEST_FLAG = os.path.join(SP, "flag_t")
calls = []
real_ps = m.run_ps
m.run_ps = lambda s: (calls.append(s) or (True, "Running"))
res = m.task_run_now()
check("№2  тест не стартует поверх идущего звонка", res[0] is False, res[1])
check("№2  флаг при этом не создаётся", not os.path.exists(m.TEST_FLAG))
check("№2  Start-ScheduledTask не вызывался", not any("Start-ScheduledTask" in c for c in calls))
check("№2  флаг живёт 2 минуты", m.TEST_FLAG_TTL == 120)
m.run_ps = real_ps

# №6, №7, №8 — звук
fake_ws = types.SimpleNamespace(SND_FILENAME=1, SND_ASYNC=2, SND_LOOP=4, SND_PURGE=8, log=[])
fake_ws.PlaySound = lambda p, f: fake_ws.log.append((p, f))
sys.modules["winsound"] = fake_ws


class FakePlayer:
    made, alive = [], []

    def __init__(self, dev):
        self.dev = dev
        FakePlayer.made.append(self)

    def play_loop(self, path):
        if self.dev == "boom":
            raise IOError("файл пропал")
        FakePlayer.alive.append(self)
        return True

    def stop(self):
        if self in FakePlayer.alive:
            FakePlayer.alive.remove(self)


m.DevicePlayer = FakePlayer
s = m.Siren(dict(cfg, speaker="x"))
s._targets = lambda: ([0, "boom"], "test")
s._play("x.wav")
check("№8  при ошибке открытое устройство закрыто", FakePlayer.alive == [],
      "висит плееров: %d" % len(FakePlayer.alive))

s2 = m.Siren(dict(cfg, speaker="x"))
s2._targets = lambda: ([], "none")
s2._play("a.wav")                       # запасной путь winsound
s2._targets = lambda: ([1], "колонка")
fake_ws.log.clear()
s2._play("b.wav")                       # колонка нашлась
check("№7  запасной звук гасится, когда нашлась колонка",
      any(p is None and f == fake_ws.SND_PURGE for p, f in fake_ws.log))
s2.stop()
check("№6  stop() глушит всё", FakePlayer.alive == [])
s2._play("c.wav")                       # стадия пришла уже после остановки
check("№6  после stop() новая стадия не стартует", FakePlayer.alive == [] and s2.players == [])
check("№6  у звонка есть замок", hasattr(s2, "lock"))

# №18 — стерео-кусок
tmp = os.path.join(SP, "recs2")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp)
m.REC_DIR = tmp
src = os.path.join(tmp, "st.wav")
with wave.open(src, "wb") as wv:
    wv.setnchannels(2); wv.setsampwidth(1); wv.setframerate(8000)
    wv.writeframes(bytes(128 + int(80 * math.sin(i / 3.0)) for i in range(8000 * 2 * 10)))
r = m.Recorder(cfg, lambda e: None)
r.chunks, r.chunk_frames = [src], [40]
r.chunk_starts = [datetime(2026, 10, 14, 1, 0)]
clips = r.save_clips([{"at": 5.0, "len": 12, "gasp": True}], span=2.0)
with wave.open(os.path.join(tmp, clips[0]), "rb") as wv:
    chs, dur = wv.getnchannels(), wv.getnframes() / float(wv.getframerate())
check("№18 стерео-клип: 2 канала и верная длина", chs == 2 and abs(dur - 2.0) < 0.01,
      "каналов %d, %.2f с" % (chs, dur))

# №15, №17 — папка записи и неразобранный кусок
check("№15 у записи своя папка", os.path.basename(os.path.dirname(r.tmp_dir)) == "tmp"
      and str(os.getpid()) in r.tmp_dir, os.path.basename(r.tmp_dir))
r2 = m.Recorder(dict(cfg, hh=23, mm=59), lambda e: None)
orig_sys = m.sys.platform
made = []


def fake_chunk(path, stop_at=None):
    open(path, "wb").write(b"not a wav")
    made.append(path)
    return True


r2.record_chunk = fake_chunk
r2.run()
check("№17 битый кусок удалён, сырой звук не остался", made and not os.path.exists(made[0]),
      r2.err or "")
shutil.rmtree(tmp, ignore_errors=True)

print()
print("ИТОГ:", "всё прошло" if ok_all else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok_all else 1)
