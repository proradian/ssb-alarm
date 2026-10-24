# -*- coding: utf-8 -*-
"""Проверка фрагментов пауз на «железе как у пользователя»: драйвер пишет
8 бит / 11025 Гц, и куски выходят чуть короче заявленного (как 2,97 с вместо 3)."""
import importlib.util
import math
import os
import tempfile
import shutil
import wave

spec = importlib.util.spec_from_file_location(
    "a", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssb_alarm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

TMP = os.path.join(tempfile.gettempdir(), "ssb_alarm_test", 'recs')
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
m.REC_DIR = TMP

RATE = 11025
m.CHUNK_SEC = 2                  # заявлено 2 с ...
REAL = 1.9                       # ... а реально пишется 1,9 с (на 5% короче)
N = 40


def freq_of(k):
    return 200 + 25 * k          # у каждого куска свой тон — по нему узнаём кусок


chunks = []
for k in range(N):
    p = os.path.join(TMP, "c%02d.wav" % k)
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)        # 8 бит, как у драйвера пользователя
        w.setframerate(RATE)
        w.writeframes(bytes(128 + int(90 * math.sin(2 * math.pi * freq_of(k) * i / RATE))
                            for i in range(int(REAL * RATE))))
    chunks.append(p)

r = m.Recorder(m.load_cfg(), lambda w: None)
r.chunks = chunks
r.env = []
frames = []
for p in chunks:
    e = m.wav_envelope(p)
    frames.append(len(e))
    r.env.extend(e)
if hasattr(r, "chunk_frames"):
    r.chunk_frames = frames
    from datetime import datetime, timedelta
    t = datetime(2026, 10, 8, 0, 0, 0)
    r.chunk_starts = [t + timedelta(seconds=(REAL + 0.4) * i) for i in range(N)]

TARGET = 35                       # клип должен начаться в середине 35-го куска
SPAN = 1.0
# save_clips начинает клип за span/2 до паузы — компенсируем ровно это
at = sum(frames[:TARGET]) * m.FRAME_SEC + REAL / 2 + SPAN / 2
clips = r.save_clips([{"at": at, "len": 12, "gasp": True}], span=SPAN)
assert clips, "клип не сохранён"
path = os.path.join(TMP, clips[0])

with wave.open(path, "rb") as w:
    width, rate, n = w.getsampwidth(), w.getframerate(), w.getnframes()
    raw = w.readframes(n)
print("клип: заявлено %d бит, %d Гц, %.2f с" % (width * 8, rate, n / float(rate)))

if width == 1:
    vals = [b - 128 for b in raw]
else:
    import array
    a = array.array("h")
    a.frombytes(raw[:len(raw) // 2 * 2])
    vals = list(a)
zc = sum(1 for i in range(1, len(vals)) if (vals[i - 1] < 0) != (vals[i] < 0))
f = zc / 2.0 / (len(vals) / float(rate))
got = min(range(N), key=lambda k: abs(freq_of(k) - f))
print("тон в клипе: %.0f Гц -> это кусок №%d (нужен №%d)" % (f, got, TARGET))

ok_width = width == 1
ok_chunk = got == TARGET
print()
print("разрядность клипа совпадает с исходником:", "ДА" if ok_width else "НЕТ — клип битый")
print("клип вырезан из нужного куска:          ", "ДА" if ok_chunk else
      "НЕТ — промах на %d кусков" % abs(got - TARGET))
shutil.rmtree(TMP, ignore_errors=True)
raise SystemExit(0 if ok_width and ok_chunk else 1)
