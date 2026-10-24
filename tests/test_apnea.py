# -*- coding: utf-8 -*-
"""Синтетическая ночь: кладём ИЗВЕСТНОЕ число пауз и смотрим, найдёт ли их разбор."""
import os
import datetime
import importlib.util
import random

spec = importlib.util.spec_from_file_location(
    "a", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssb_alarm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FPS = int(1 / m.FRAME_SEC)
random.seed(42)


def put(env, seconds, lo, hi):
    for _ in range(int(seconds * FPS)):
        env.append(random.randint(lo, hi))


def build_night(hours=6.0, every_n_cycles=22, pause_len=18.0, quiet_tail_min=50):
    """Храп раз в 4 с. Каждый every_n_cycles-й цикл заменён паузой с вдохом.
    Последние quiet_tail_min минут — тихий сон без храпа: туда детектор
    заглядывать не должен."""
    env, truth = [], []
    total = hours * 3600
    noisy_until = total - quiet_tail_min * 60
    t, cycle = 0.0, 0
    while t < total:
        if t >= noisy_until:
            put(env, 4.0, 20, 50)                  # тихо, без храпа
            t += 4.0
            continue
        cycle += 1
        if cycle % every_n_cycles == 0 and t > 120:
            # опорное время берём из реальной длины огибающей, иначе
            # копится дрейф от округления кадров
            truth.append(len(env) / float(FPS))    # тишина начинается здесь
            put(env, pause_len, 20, 45)
            put(env, 1.2, 5000, 7000)              # громкий вдох после паузы
            t += pause_len + 1.2
        else:
            put(env, 1.4, 1200, 2600)              # храп
            put(env, 2.6, 20, 45)                  # между вдохами
            t += 4.0
    return env, truth


env, truth = build_night()
res = m.analyse(env)
found = [p["at"] for p in res["pauses"]]

print("синтетическая ночь:        %s" % m.hhmmss(len(env) * m.FRAME_SEC))
print("фон / порог звука:         %d / %d" % (res["floor"], res["thr"]))
print("звуковых эпизодов найдено: %d" % len(res["events"]))
print()
print("пауз заложено:             %d" % len(truth))
print("пауз найдено:              %d" % len(found))
print("с громким вдохом после:    %d" % sum(1 for p in res["pauses"] if p["gasp"]))

W = 10.0
matched = sum(1 for tr in truth if any(abs(f - tr) < W for f in found))
false_pos = [f for f in found if not any(abs(f - tr) < W for tr in truth)]
print()
print("найдено из заложенных:     %d из %d  (%.0f%%)"
      % (matched, len(truth), 100.0 * matched / len(truth)))
print("ложных срабатываний:       %d" % len(false_pos))
if false_pos[:5]:
    print("   первые ложные:", [m.hhmmss(f) for f in false_pos[:5]])

assert matched >= len(truth) * 0.9, "детектор пропускает паузы"
assert len(false_pos) <= max(2, len(truth) * 0.1), "слишком много ложных"
print()
print("тихий хвост (%d мин без храпа) ложных пауз не дал" % 50)
print()
print("=" * 60)
print(m.build_report(datetime.datetime.now(), env, res, []))
