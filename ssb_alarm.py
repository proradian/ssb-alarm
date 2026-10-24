#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSB ALARM — самый сложный будильник (ssb-alarm).

Будильник, построенный на исследованиях сна. Звенит на весь экран поверх всех
окон и замолкает только после нескольких правильных ответов ПОДРЯД — математика
и логика. Через 10 минут проверяет, не уснул ли ты обратно. Ведёт дневник сна,
оценивает циркадный ритм и рисует его на графике с рекомендациями.

Нужен только Python со стандартной библиотекой (tkinter). Windows 10/11.

Запуск:
    pythonw ssb_alarm.py           — единая панель: подъём, ночь, ритм сна, советы
    pythonw ssb_alarm.py --settings — только настройки звонка (сложность, задачи, звук)
    pythonw ssb_alarm.py --now     — сразу звенеть (так его запускает Планировщик задач)
    pythonw ssb_alarm.py --up      — «Я встал»: проснулся раньше — решаешь 10 задач
    pythonw ssb_alarm.py --bedtime — тихое напоминание, что пора спать
    pythonw ssb_alarm.py --record  — записать ночь с микрофона и разобрать утром
    pythonw ssb_alarm.py --light   — рассвет через умный дом Яндекса (по желанию)
    python  ssb_alarm.py --check   — проверить колонку
    python  ssb_alarm.py --devices — список устройств вывода звука

Это не медицинский прибор. Подробности и источники — README.md и docs/.
"""

import base64
import csv
import faulthandler
import gc
import io
import itertools
import json
import math
import os
import queue
import random
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import wave
from datetime import datetime, timedelta

import tkinter as tk


# ═══════════════════════════ НАСТРОЙКИ НА ДИСКЕ ═══════════════════════════

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, "settings.json")

DEFAULTS = {"hh": 6, "mm": 30, "level": 2, "need": 3,
            "reset": True, "penalty": True, "kinds": "mix",
            "sound": "melody", "escalate": True, "speaker": "",
            "speaker_volume": 80,
            "bedtime": True, "bed_before_min": 495, "bed_fixed": "",
            "plan": {}}


def load_cfg():
    """Настройки из settings.json; чего нет — берётся из DEFAULTS."""
    cfg = dict(DEFAULTS)
    try:
        with io.open(CONF, encoding="utf-8") as f:
            saved = json.load(f)
        for k in cfg:
            if k in saved and type(saved[k]) is type(cfg[k]):
                cfg[k] = saved[k]
    except Exception:
        pass
    if cfg["kinds"] not in ("math", "logic", "mix"):
        cfg["kinds"] = "mix"
    if cfg["sound"] not in ("melody", "t3", "beep"):
        cfg["sound"] = "melody"
    cfg["level"] = max(1, min(3, cfg["level"]))
    cfg["need"] = max(1, min(12, cfg["need"]))
    cfg["speaker_volume"] = max(10, min(100, cfg["speaker_volume"]))
    return cfg


# ─────────────── журнал ───────────────
# Без журнала причину тихого звонка пришлось искать вслепую. Теперь каждый запуск
# пишет, куда играл звук, какая была громкость и чем закончился.

LOG = os.path.join(HERE, "alarm.log")
_LOG_LOCK = threading.Lock()
_LOG_MAX = 400 * 1024


def log(msg):
    try:
        with _LOG_LOCK:
            if os.path.exists(LOG) and os.path.getsize(LOG) > _LOG_MAX:
                with io.open(LOG, encoding="utf-8", errors="replace") as f:
                    tail = f.read()[-_LOG_MAX // 2:]
                with io.open(LOG, "w", encoding="utf-8") as f:
                    f.write(tail)
            with io.open(LOG, "a", encoding="utf-8") as f:
                f.write("%s [%d] %s\n" % (datetime.now().strftime("%d.%m %H:%M:%S"),
                                          os.getpid(), msg))
    except Exception:                                        # noqa: BLE001
        pass


def save_cfg(cfg):
    """Пишет настройки атомарно: во временный файл, затем подмена. Иначе звонок,
    стартующий в момент сохранения, прочитал бы пустой файл и молча взял DEFAULTS.
    Возвращает True, если записалось."""
    tmp = CONF + ".tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONF)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


# ═══════════════════════════════ ТЕМА ═══════════════════════════════
# Пиксельный стиль (с 22.10.2026): палитра в духе PICO-8 и свой растровый
# шрифт 5×7. Tk на Windows сглаживает любой TTF, поэтому буквы рисуются
# прямоугольниками на канве с целым масштабом — пиксели остаются чёткими.

class T:
    BG_TOP   = "#161B3D"
    BG_BOT   = "#07081A"
    INK      = "#05060F"
    CARD     = "#1D2B53"
    CARD_HI  = "#2B3C70"
    LINE     = "#3A4A80"
    TEXT     = "#FFF1E8"
    MUTED    = "#A3A9D1"
    DIM      = "#5A6396"
    ACCENT   = "#FF004D"
    ACCENT_2 = "#FF77A8"
    ACCENT_D = "#7E2553"
    OK       = "#00E436"
    WARN     = "#FFA300"
    GOLD     = "#FFEC27"
    SKY      = "#29ADFF"
    STAR     = "#C2C3C7"


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb2hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c)


def mix(c1, c2, t):
    a, b = hex2rgb(c1), hex2rgb(c2)
    return rgb2hex([a[i] + (b[i] - a[i]) * t for i in range(3)])


# ─────────────── растровый шрифт ───────────────
# символ: (сдвиг от верха заглавной, строки через пробел). Заглавные — 7 строк,
# строчные начинаются со 2-й строки, хвосты (д, р, у, ц…) уходят ниже на 1–2.

FONT_SRC = {
    "А": (0, ".###. #...# #...# ##### #...# #...# #...#"),
    "Б": (0, "##### #.... #.... ####. #...# #...# ####."),
    "В": (0, "####. #...# #...# ####. #...# #...# ####."),
    "Г": (0, "##### #.... #.... #.... #.... #.... #...."),
    "Д": (0, ".####. .#..#. .#..#. .#..#. .#..#. .#..#. ###### #....#"),
    "Е": (0, "##### #.... #.... ####. #.... #.... #####"),
    "Ё": (-2, ".#.#. ..... ##### #.... #.... ####. #.... #.... #####"),
    "Ж": (0, "#..#..# #..#..# .#.#.#. ..###.. .#.#.#. #..#..# #..#..#"),
    "З": (0, ".###. #...# ....# ..##. ....# #...# .###."),
    "И": (0, "#...# #...# #..## #.#.# ##..# #...# #...#"),
    "Й": (-2, ".###. ..... #...# #..## #.#.# ##..# #...# #...# #...#"),
    "К": (0, "#...# #..#. #.#.. ##... #.#.. #..#. #...#"),
    "Л": (0, "..### .#..# .#..# .#..# .#..# .#..# #...#"),
    "М": (0, "#...# ##.## #.#.# #.#.# #...# #...# #...#"),
    "Н": (0, "#...# #...# #...# ##### #...# #...# #...#"),
    "О": (0, ".###. #...# #...# #...# #...# #...# .###."),
    "П": (0, "##### #...# #...# #...# #...# #...# #...#"),
    "Р": (0, "####. #...# #...# ####. #.... #.... #...."),
    "С": (0, ".###. #...# #.... #.... #.... #...# .###."),
    "Т": (0, "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#.."),
    "У": (0, "#...# #...# #...# .#### ....# #...# .###."),
    "Ф": (0, "..#.. .###. #.#.# #.#.# #.#.# .###. ..#.."),
    "Х": (0, "#...# #...# .#.#. ..#.. .#.#. #...# #...#"),
    "Ц": (0, "#...#. #...#. #...#. #...#. #...#. #...#. ###### .....#"),
    "Ч": (0, "#...# #...# #...# .#### ....# ....# ....#"),
    "Ш": (0, "#..#..# #..#..# #..#..# #..#..# #..#..# #..#..# #######"),
    "Щ": (0, "#..#..#. #..#..#. #..#..#. #..#..#. #..#..#. #..#..#. ######## .......#"),
    "Ъ": (0, "##.... .#.... .#.... .####. .#...# .#...# .####."),
    "Ы": (0, "#.....# #.....# #.....# ####..# #...#.# #...#.# ####..#"),
    "Ь": (0, "#.... #.... #.... ####. #...# #...# ####."),
    "Э": (0, ".###. #...# ....# ..### ....# #...# .###."),
    "Ю": (0, "#..###. #.#...# #.#...# ###...# #.#...# #.#...# #..###."),
    "Я": (0, ".#### #...# #...# .#### ..#.# .#..# #...#"),

    "а": (2, ".###. ....# .#### #...# .####"),
    "б": (0, ".#### #.... ####. #...# #...# #...# .###."),
    "в": (2, "####. #...# ####. #...# ####."),
    "г": (2, "##### #.... #.... #.... #...."),
    "д": (2, ".####. .#..#. .#..#. .#..#. ###### #....#"),
    "е": (2, ".###. #...# ##### #.... .###."),
    "ё": (0, ".#.#. ..... .###. #...# ##### #.... .###."),
    "ж": (2, "#.#.# #.#.# .###. #.#.# #.#.#"),
    "з": (2, "####. ....# .###. ....# ####."),
    "и": (2, "#...# #..## #.#.# ##..# #...#"),
    "й": (0, ".###. ..... #...# #..## #.#.# ##..# #...#"),
    "к": (2, "#..#. #.#.. ##... #.#.. #..#."),
    "л": (2, "..### .#..# .#..# .#..# #...#"),
    "м": (2, "#...# ##.## #.#.# #...# #...#"),
    "н": (2, "#...# #...# ##### #...# #...#"),
    "о": (2, ".###. #...# #...# #...# .###."),
    "п": (2, "##### #...# #...# #...# #...#"),
    "р": (2, "####. #...# #...# #...# ####. #.... #...."),
    "с": (2, ".###. #...# #.... #...# .###."),
    "т": (2, "##### ..#.. ..#.. ..#.. ..#.."),
    "у": (2, "#...# #...# #...# #...# .#### ....# .###."),
    "ф": (0, "..#.. ..#.. .###. #.#.# #.#.# #.#.# .###. ..#.. ..#.."),
    "х": (2, "#...# .#.#. ..#.. .#.#. #...#"),
    "ц": (2, "#..#. #..#. #..#. #..#. ##### ....#"),
    "ч": (2, "#...# #...# .#### ....# ....#"),
    "ш": (2, "#.#.# #.#.# #.#.# #.#.# #####"),
    "щ": (2, "#.#.#. #.#.#. #.#.#. #.#.#. ###### .....#"),
    "ъ": (2, "##... .#... .###. .#..# .###."),
    "ы": (2, "#....# #....# ###..# #..#.# ###..#"),
    "ь": (2, "#... #... ###. #..# ###."),
    "э": (2, ".###. #...# ..### #...# .###."),
    "ю": (2, "#..##. #.#..# ###..# #.#..# #..##."),
    "я": (2, ".#### #...# .#### .#..# #...#"),

    "D": (0, "####. #...# #...# #...# #...# #...# ####."),
    "F": (0, "##### #.... #.... ####. #.... #.... #...."),
    "G": (0, ".###. #...# #.... #.### #...# #...# .####"),
    "I": (0, "### .#. .#. .#. .#. .#. ###"),
    "J": (0, "..### ...#. ...#. ...#. ...#. #..#. .##.."),
    "L": (0, "#.... #.... #.... #.... #.... #.... #####"),
    "N": (0, "#...# #...# ##..# #.#.# #..## #...# #...#"),
    "Q": (0, ".###. #...# #...# #...# #.#.# #..#. .##.#"),
    "R": (0, "####. #...# #...# ####. #.#.. #..#. #...#"),
    "S": (0, ".#### #.... #.... .###. ....# ....# ####."),
    "U": (0, "#...# #...# #...# #...# #...# #...# .###."),
    "V": (0, "#...# #...# #...# #...# #...# .#.#. ..#.."),
    "W": (0, "#...# #...# #...# #.#.# #.#.# #.#.# .#.#."),
    "Y": (0, "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#.."),
    "Z": (0, "##### ....# ...#. ..#.. .#... #.... #####"),

    "b": (0, "#.... #.... ####. #...# #...# #...# ####."),
    "d": (0, "....# ....# .#### #...# #...# #...# .####"),
    "f": (0, "..##. .#... ####. .#... .#... .#... .#..."),
    "g": (2, ".#### #...# #...# #...# .#### ....# .###."),
    "h": (0, "#.... #.... ####. #...# #...# #...# #...#"),
    "i": (0, "# . # # # # #"),
    "j": (0, "..# ... ..# ..# ..# ..# ..# #.# .#."),
    "l": (0, "#. #. #. #. #. #. .#"),
    "m": (2, "####. #.#.# #.#.# #.#.# #.#.#"),
    "n": (2, "####. #...# #...# #...# #...#"),
    "q": (2, ".#### #...# #...# #...# .#### ....# ....#"),
    "r": (2, "#.##. ##..# #.... #.... #...."),
    "s": (2, ".#### #.... .###. ....# ####."),
    "t": (0, ".#... .#... ####. .#... .#... .#... ..##."),
    "u": (2, "#...# #...# #...# #...# .####"),
    "v": (2, "#...# #...# #...# .#.#. ..#.."),
    "w": (2, "#...# #...# #.#.# #.#.# .#.#."),
    "z": (2, "##### ...#. ..#.. .#... #####"),

    "0": (0, ".###. #...# #...# #...# #...# #...# .###."),
    "1": (0, "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###."),
    "2": (0, ".###. #...# ....# ...#. ..#.. .#... #####"),
    "3": (0, "##### ...#. ..#.. ...#. ....# #...# .###."),
    "4": (0, "...#. ..##. .#.#. #..#. ##### ...#. ...#."),
    "5": (0, "##### #.... ####. ....# ....# #...# .###."),
    "6": (0, "..##. .#... #.... ####. #...# #...# .###."),
    "7": (0, "##### ....# ...#. ..#.. .#... .#... .#..."),
    "8": (0, ".###. #...# #...# .###. #...# #...# .###."),
    "9": (0, ".###. #...# #...# .#### ....# ...#. .##.."),

    ".": (6, "#"),
    ",": (6, ".# #."),
    ":": (2, "# . . #"),
    ";": (2, ".# .. .. .# #."),
    "!": (0, "# # # # # . #"),
    "?": (0, ".###. #...# ....# ...#. ..#.. ..... ..#.."),
    "-": (3, "####"),
    "−": (3, "#####"),
    "–": (3, "#####"),
    "—": (3, "#######"),
    "_": (7, "#####"),
    "+": (1, "..#.. ..#.. ##### ..#.. ..#.."),
    "×": (1, "#...# .#.#. ..#.. .#.#. #...#"),
    "✕": (1, "#...# .#.#. ..#.. .#.#. #...#"),
    "÷": (1, "..#.. ..... ##### ..... ..#.."),
    "=": (2, "##### ..... #####"),
    "%": (0, "##..# ##..# ...#. ..#.. .#... #..## #..##"),
    "(": (0, "..# .#. #.. #.. #.. .#. ..#"),
    ")": (0, "#.. .#. ..# ..# ..# .#. #.."),
    "[": (0, "### #.. #.. #.. #.. #.. ###"),
    "]": (0, "### ..# ..# ..# ..# ..# ###"),
    "/": (0, "....# ....# ...#. ..#.. .#... #.... #...."),
    "|": (0, "# # # # # # #"),
    "<": (0, "...# ..#. .#.. #... .#.. ..#. ...#"),
    ">": (0, "#... .#.. ..#. ...# ..#. .#.. #..."),
    "*": (1, "#.#.# .###. ##### .###. #.#.#"),
    "#": (1, ".#.#. ##### .#.#. ##### .#.#."),
    "«": (2, "..#.# .#.#. #.#.. .#.#. ..#.#"),
    "»": (2, "#.#.. .#.#. ..#.# .#.#. #.#.."),
    "\"": (0, "#.# #.#"),
    "'": (0, "# #"),
    "·": (3, "#"),
    "…": (6, "#.#.#"),
    "²": (0, "##. ..# .#. ###"),
    "√": (0, "..### ..#.. ..#.. ..#.. #.#.. .##.. ..#.."),
    "≈": (2, ".##..# #..##. ...... .##..# #..##."),
    "±": (0, "..#.. ..#.. ##### ..#.. ..#.. ..... #####"),
    "→": (1, "...#.. ....#. ###### ....#. ...#.."),
    "⌫": (1, "..####### .#..#.#.# #....#..# .#..#.#.# ..#######"),
    "✓": (1, "......# .....#. #...#.. .#.#... ..#...."),
    "●": (1, ".###. ##### ##### ##### .###."),
    "○": (1, ".###. #...# #...# #...# .###."),
    "■": (1, "##### ##### ##### ##### #####"),
    "▲": (2, "..#.. .###. #####"),
    "▼": (2, "##### .###. ..#.."),
    "►": (1, "#... ##.. ###. ##.. #..."),
    "▌": (0, "## ## ## ## ## ## ##"),
    "█": (0, "##### ##### ##### ##### ##### ##### #####"),
    "☐": (0, "######### #.......# #.......# #.......# #.......# #.......# #########"),
    "☑": (0, "######### #......## #.....#.# ##...#..# #.#.#...# #..#....# #########"),
}
# латиница, похожая на кириллицу, рисуется теми же буквами
FONT_ALIAS = dict(zip("ABCEHKMOPTXaceopxyk", "АВСЕНКМОРТХасеорхук"))
LINE_U = 12               # шаг строки в пикселях шрифта: 2 над буквой + 7 + 2 хвост + 1
_FONT = {}
_ids = itertools.count(1)


def _build_font():
    for ch, (top, src) in FONT_SRC.items():
        rows = src.split()
        runs = []
        for y, row in enumerate(rows):
            x = 0
            while x < len(row):
                if row[x] != "#":
                    x += 1
                    continue
                s = x
                while x < len(row) and row[x] == "#":
                    x += 1
                runs.append((s, top + y, x - s))
        _FONT[ch] = (max(len(r) for r in rows), runs)
    _FONT[" "] = (3, [])


_build_font()


def glyph(ch):
    g = _FONT.get(ch) or _FONT.get(FONT_ALIAS.get(ch, ch.upper()))
    return g or _FONT["?"]


def text_w(s, sc):
    """Ширина строки в экранных пикселях."""
    if not s:
        return 0
    return (sum(glyph(c)[0] for c in s) + len(s) - 1) * sc


def wrap(text, sc, width):
    """Перенос по словам под ширину в пикселях."""
    out = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            cand = word if not line else line + " " + word
            if line and text_w(cand, sc) > width:
                out.append(line)
                line = word
            else:
                line = cand
        out.append(line)
    return out


def px_rect(cv, x1, y1, x2, y2, fill, tags=(), state="normal"):
    return cv.create_rectangle(int(x1), int(y1), int(x2), int(y2), fill=fill,
                               outline="", width=0, tags=tags, state=state)


class PText:
    """Надпись пиксельным шрифтом. anchor: center, w, e — y это середина блока;
    n, nw, ne — y это верх первой строки. Перерисовывается, только если текст сменился."""

    def __init__(self, cv, x, y, text="", sc=2, fill=T.TEXT, anchor="center",
                 width=None, tags=(), shadow=None, justify=None, state="normal"):
        self.cv, self.x, self.y = cv, x, y
        self.sc = max(1, int(round(sc)))
        self.fill, self.anchor, self.width, self.shadow = fill, anchor, width, shadow
        self.justify = justify or {"w": "left", "nw": "left",
                                   "e": "right", "ne": "right"}.get(anchor, "center")
        self.tag = "pt%d" % next(_ids)
        self.tags, self.state, self.text = tuple(tags), state, None
        self.set(text)

    def set(self, text=None, fill=None):
        if fill is not None and fill != self.fill:
            self.fill = fill
            if text is None or text == self.text:
                self.cv.itemconfig(self.tag + "m", fill=fill)
        if text is not None and text != self.text:
            self.text = text
            self.draw()

    def place(self, x, y):
        if (x, y) != (self.x, self.y):
            self.x, self.y = x, y
            self.draw()

    def show(self, on=True):
        self.state = "normal" if on else "hidden"
        self.cv.itemconfig(self.tag, state=self.state)

    def draw(self):
        cv, sc = self.cv, self.sc
        cv.delete(self.tag)
        lines = wrap(self.text, sc, self.width) if self.width else self.text.split("\n")
        widths = [text_w(s, sc) for s in lines]
        block = max(widths)
        h = ((len(lines) - 1) * LINE_U + 7) * sc
        a = self.anchor
        left = (self.x if a in ("w", "nw") else
                self.x - block if a in ("e", "ne") else self.x - block / 2)
        top = self.y if a in ("n", "nw", "ne") else self.y - h / 2
        self.bottom = top + h + 2 * sc        # низ блока с хвостами букв — для раскладки
        layers = ([(self.shadow, max(1, int(round(sc / 3.0))), "s")] if self.shadow else []) + [(self.fill, 0, "m")]
        for color, off, kind in layers:
            tg = (self.tag, self.tag + kind) + self.tags
            for i, line in enumerate(lines):
                dx = {"left": 0, "right": block - widths[i]}.get(
                    self.justify, (block - widths[i]) // 2)
                lx = int(round(left + dx)) + off
                ly = int(round(top + i * LINE_U * sc)) + off
                for ch in line:
                    w, runs = glyph(ch)
                    for gx, gy, gn in runs:
                        px_rect(cv, lx + gx * sc, ly + gy * sc, lx + (gx + gn) * sc,
                                ly + (gy + 1) * sc, color, tg, self.state)
                    lx += (w + 1) * sc


# ─────────────── спрайты ───────────────

SPR_CLOCK = [
    ".KKK.......KKK.",
    "KRRRK.....KRRRK",
    "KRRK.KKKKK.KRRK",
    ".KK.KWWWWWK.KK.",
    "...KWWWKWWWK...",
    "..KWWWWKWWWWK..",
    "..KWWWWKWWWWK..",
    "..KWWWWKKKWWK..",
    "..KWWWWWWWWWK..",
    "..KWWWWWWWWWK..",
    "...KWWWWWWWK...",
    "....KKKKKKK....",
    "...KK.....KK...",
]
CLOCK_PAL = {"K": T.INK, "R": T.ACCENT, "W": T.TEXT}

SPR_STAR = [
    "....K....",
    "...KYK...",
    "...KYK...",
    "KKKKYKKKK",
    "KYWYYYYYK",
    ".KYYYYYK.",
    "..KYYYK..",
    ".KYYKYYK.",
    ".KYK.KYK.",
    ".KK...KK.",
]
STAR_ON = {"K": T.INK, "Y": T.GOLD, "W": "#FFFFFF"}
STAR_OFF = {"K": T.INK, "Y": T.CARD_HI, "W": T.LINE}


def disc(r, cut=None):
    """Круг (или серп, если cut=(dx, dy)) из «Y» с обводкой «K»."""
    n = 2 * r + 3
    c = r + 1
    grid = [["." for _ in range(n)] for _ in range(n)]
    for y in range(n):
        for x in range(n):
            if (x - c) ** 2 + (y - c) ** 2 <= r * r + r * 0.6:
                if cut and (x - c - cut[0]) ** 2 + (y - c - cut[1]) ** 2 <= r * r:
                    continue
                grid[y][x] = "Y"
    for y in range(n):
        for x in range(n):
            if grid[y][x] == "." and any(
                    0 <= y + dy < n and 0 <= x + dx < n and grid[y + dy][x + dx] == "Y"
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                grid[y][x] = "K"
    return ["".join(r_) for r_ in grid]


def sprite(cv, x, y, rows, pal, sc, tags=()):
    """Рисует спрайт с центром в (x, y). Цвет каждого символа можно потом
    перекрасить: cv.itemconfig(имя + "_" + символ, fill=...)."""
    name = "sp%d" % next(_ids)
    w = max(len(r) for r in rows)
    x0 = int(round(x - w * sc / 2))
    y0 = int(round(y - len(rows) * sc / 2))
    for ry, row in enumerate(rows):
        rx = 0
        while rx < len(row):
            ch = row[rx]
            s = rx
            while rx < len(row) and row[rx] == ch:
                rx += 1
            if ch != ".":
                px_rect(cv, x0 + s * sc, y0 + ry * sc, x0 + rx * sc, y0 + (ry + 1) * sc,
                        pal[ch], (name, name + "_" + ch) + tuple(tags))
    return name


# ─────────────── плашки, фон, кнопки ───────────────

def notch(cv, x1, y1, x2, y2, u, fill, tags=()):
    """Прямоугольник со срезанными углами — пиксельное «скругление»."""
    px_rect(cv, x1 + u, y1, x2 - u, y2, fill, tags)
    px_rect(cv, x1, y1 + u, x2, y2 - u, fill, tags)


def pbox(cv, x1, y1, x2, y2, u, fill=T.CARD, border=T.INK, shadow=T.INK,
         inset=False, tags=()):
    """Плашка: тень, рамка, заливка и блик сверху (inset — утопленная, тень внутри).
    Рамку можно перекрасить: cv.itemconfig(имя + "o", fill=...)."""
    name = "pb%d" % next(_ids)
    x1, y1, x2, y2 = (int(round(v)) for v in (x1, y1, x2, y2))
    tg = (name,) + tuple(tags)
    if shadow:
        notch(cv, x1 + u, y1 + u, x2 + u, y2 + u, u, shadow, tg)
    notch(cv, x1, y1, x2, y2, u, border, tg + (name + "o",))
    notch(cv, x1 + u, y1 + u, x2 - u, y2 - u, u, fill, tg + (name + "f",))
    if inset:
        px_rect(cv, x1 + 2 * u, y1 + u, x2 - 2 * u, y1 + 2 * u, mix(fill, "#000000", 0.5), tg)
    else:
        px_rect(cv, x1 + 2 * u, y1 + u, x2 - 2 * u, y1 + 2 * u, mix(fill, "#FFFFFF", 0.1), tg)
    return name


_BG_CACHE = {}


def paint_bg(cv, w, h, top=T.BG_TOP, bot=T.BG_BOT, u=4, bands=7, stars=60, seed=7,
             star_h=0.8, live=8):
    """Ночное небо: полосы с дизерингом на стыках и пиксельные звёзды. Всё — на нижний слой.
    Небо со звёздами — одна картинка, увеличенная без сглаживания: тысячи
    прямоугольников тормозили анимацию звонка до 6–9 кадров в секунду.
    Отдельными фигурами остаются только live звёзд — те, что мерцают."""
    cv.delete("bg")
    key = (cv.tk, w, h, top, bot, u, bands, stars, seed, star_h, live)   # картинка живёт в своём Tk
    cache = _BG_CACHE.get(key)
    if cache:                              # панель перерисовывается целиком — небо не пересобираем
        img, live_px = cache
        for x1, y1, col in live_px:
            px_rect(cv, x1, y1, x1 + u, y1 + u, col, ("bg", "star"))
        cv.create_image(0, 0, image=img, anchor="nw", tags=("bg",))
        cv.tag_lower("bg")
        cv.tag_raise("star", "bg")
        return
    live_px = []
    cols = [mix(top, bot, i / float(max(1, bands - 1))) for i in range(bands)]
    gw, gh = -(-w // u), -(-h // u)
    bh = gh / float(bands)
    grid = []
    for y in range(gh):
        i = min(bands - 1, int(y / bh))
        k = y - int(i * bh)
        if i and k < 2:                    # стык полос: шахматка 50 %, потом 25 %
            step = 2 if k == 0 else 4
            grid.append([cols[i - 1] if (x - k) % step == 0 else cols[i] for x in range(gw)])
        else:
            grid.append([cols[i]] * gw)
    rnd = random.Random(seed)
    for n in range(stars):
        x = rnd.randrange(0, gw)
        y = rnd.randrange(0, max(1, int(gh * star_h)))
        col = mix(T.STAR, top, rnd.random() * 0.65)
        cells = ([(x, y), (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]
                 if rnd.random() < 0.12 else [(x, y)])
        for cx, cy in cells:
            if 0 <= cx < gw and 0 <= cy < gh:
                if n < live:
                    live_px.append((cx * u, cy * u, col))
                    px_rect(cv, cx * u, cy * u, cx * u + u, cy * u + u, col, ("bg", "star"))
                else:
                    grid[cy][cx] = col
    img = tk.PhotoImage(master=cv, width=gw, height=gh)
    img.put(" ".join("{" + " ".join(row) + "}" for row in grid))
    if u > 1:
        img = img.zoom(u)
    _BG_CACHE[key] = (img, live_px)        # заодно держит ссылку: иначе картинку соберёт GC
    cv.create_image(0, 0, image=img, anchor="nw", tags=("bg",))
    cv.tag_lower("bg")
    cv.tag_raise("star", "bg")             # живые звёзды созданы раньше картинки — поверх неё


def twinkle(cv, top=T.BG_TOP, n=3):
    """Пара случайных звёзд меняет яркость — небо «дышит»."""
    stars = cv.find_withtag("star")
    for item in random.sample(stars, min(n, len(stars))):
        cv.itemconfig(item, fill=mix(T.STAR, top, random.random() * 0.8))


def frame(cv, w, h, u, color=T.CARD_HI):
    """Рамка окна без системного заголовка."""
    for x1, y1, x2, y2 in ((0, 0, w, u), (0, h - u, w, h), (0, 0, u, h), (w - u, 0, w, h)):
        px_rect(cv, x1, y1, x2, y2, color, ("frame",))


def draggable(root, cv, tag):
    """Окно без заголовка таскается за фон."""
    st = {}

    def start(e):
        st["dx"] = e.x_root - root.winfo_x()
        st["dy"] = e.y_root - root.winfo_y()

    def move(e):
        if "dx" in st:
            root.geometry("+%d+%d" % (e.x_root - st["dx"], e.y_root - st["dy"]))

    cv.tag_bind(tag, "<Button-1>", start)
    cv.tag_bind(tag, "<B1-Motion>", move)


def tk_gc_guard():
    """Сборщик мусора — только в главном потоке.
    20.10.2026 окно проверки падало посреди решения (0x80000003 в tcl86t.dll):
    автоматический GC сработал в потоке сирены и удалил Tcl-интерпретатор прошлого
    окна не из того потока → Tcl_AsyncDelete. Поэтому автоматический GC выключаем,
    а собираем мусор сами: из окон по таймеру (keep_gc) и между окнами."""
    gc.disable()


def keep_gc(root, every_ms=30000):
    """Периодическая сборка мусора из главного (Tk) потока."""
    def tick():
        try:
            gc.collect()
            root.after(every_ms, tick)
        except tk.TclError:
            pass
    root.after(every_ms, tick)


def close_window(root):
    """Снимает отложенные after-задачи, иначе Tcl ругается после destroy."""
    try:
        for aid in root.tk.call("after", "info"):
            try:
                root.after_cancel(aid)
            except Exception:
                pass
    except Exception:
        pass
    root.destroy()


def bind_tracked(cv, tag, seq, fn):
    """tag_bind с учётом: clear_canvas() потом снимет обработчик вместе с рисунком."""
    fid = cv.tag_bind(tag, seq, fn)
    cv.__dict__.setdefault("_tracked", []).append((tag, seq, fid))
    return fid


def clear_canvas(cv):
    """delete("all") удаляет рисунок, но не tag_bind: Python-обработчики кнопок оставались
    висеть в Tcl и копились с каждой перерисовкой. Снимаем их сами."""
    for tag, seq, fid in cv.__dict__.get("_tracked", []):
        try:
            cv.tag_unbind(tag, seq, fid)
        except tk.TclError:
            pass
    cv.__dict__["_tracked"] = []
    cv.delete("all")


class CBtn:
    """Пиксельная кнопка на канве: рамка, объём (блик и тень) и «нажатие» на пиксель вниз."""

    def __init__(self, cv, x, y, w, h, text, cmd=None, u=4, sc=2,
                 fill=T.CARD, hover=None, fg=T.TEXT, outline=T.INK, align="center"):
        self.cv, self.cmd, self.u = cv, cmd, u
        self.fill, self.hover, self.fg = fill, hover or mix(fill, "#FFFFFF", 0.12), fg
        self.enabled, self.pressed = True, False
        self.tag = t = "cb%d" % next(_ids)
        x1, y1 = int(round(x - w / 2)), int(round(y - h / 2))
        x2, y2 = x1 + int(round(w)), y1 + int(round(h))
        notch(cv, x1, y1 + u, x2, y2 + u, u, T.INK, (t,))
        notch(cv, x1, y1, x2, y2, u, outline, (t, t + "o", t + "mv"))
        notch(cv, x1 + u, y1 + u, x2 - u, y2 - u, u, fill, (t, t + "f", t + "mv"))
        px_rect(cv, x1 + 2 * u, y1 + u, x2 - 2 * u, y1 + 2 * u, fill, (t, t + "hi", t + "mv"))
        px_rect(cv, x1 + 2 * u, y2 - 2 * u, x2 - 2 * u, y2 - u, fill, (t, t + "lo", t + "mv"))
        tx = x1 + 4 * u if align == "left" else x
        self.txt = PText(cv, tx, y, text, sc, fg, anchor="w" if align == "left" else "center",
                         tags=(t, t + "mv"))
        self._paint(fill)
        bind_tracked(cv, t, "<Enter>", self._enter)
        bind_tracked(cv, t, "<Leave>", self._leave)
        bind_tracked(cv, t, "<Button-1>", self._down)
        bind_tracked(cv, t, "<ButtonRelease-1>", self._up)

    def _paint(self, face):
        self.cv.itemconfig(self.tag + "f", fill=face)
        self.cv.itemconfig(self.tag + "hi", fill=mix(face, "#FFFFFF", 0.22))
        self.cv.itemconfig(self.tag + "lo", fill=mix(face, "#000000", 0.32))

    def _press(self, on):
        if on != self.pressed:
            self.pressed = on
            self.cv.move(self.tag + "mv", 0, self.u if on else -self.u)

    def _enter(self, _=None):
        if self.enabled:
            self._paint(self.hover)
            self.cv.config(cursor="hand2")

    def _leave(self, _=None):
        self._press(False)
        self._paint(self.fill)
        self.cv.config(cursor="")

    def _down(self, _=None):
        if self.enabled:
            self._press(True)

    def _up(self, _=None):
        self._press(False)
        if not self.enabled:
            return
        self._paint(self.hover)
        if self.cmd:
            self.cmd()

    def style(self, fill=None, fg=None, hover=None, outline=None):
        if fill:
            self.fill = fill
            self._paint(fill)
        if hover:
            self.hover = hover
        if outline:
            self.cv.itemconfig(self.tag + "o", fill=outline)
        if fg:
            self.fg = fg
            self.txt.set(fill=fg)

    def set_text(self, s):
        self.txt.set(s)
        if self.pressed:                       # новые пиксели текста рисуются без сдвига
            self.cv.move(self.txt.tag, 0, self.u)

    def show(self):
        self.txt.state = "normal"
        self.cv.itemconfig(self.tag, state="normal")
        self.enabled = True

    def hide(self):
        self._press(False)
        self.txt.state = "hidden"
        self.cv.itemconfig(self.tag, state="hidden")
        self.enabled = False


# ═══════════════════════ ПЛАНИРОВЩИК ЗАДАЧ WINDOWS ═══════════════════════

TASK_NAME = "Будильник"
SCRIPT = os.path.abspath(__file__)
TEST_FLAG = os.path.join(HERE, ".testrun")
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def psq(s):
    """Экранирует строку для одинарных кавычек PowerShell."""
    return str(s).replace("'", "''")


def run_ps(script):
    """PowerShell через -EncodedCommand — не зависит от кодовой страницы."""
    if sys.platform != "win32":
        return False, "Планировщик задач есть только в Windows"
    head = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8\n"
            "$ErrorActionPreference='Stop'\n")
    enc = base64.b64encode((head + script).encode("utf-16-le")).decode("ascii")
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=45, creationflags=NO_WINDOW)
    except Exception as e:                                   # noqa: BLE001
        return False, str(e)
    out = p.stdout.decode("utf-8", "replace").strip()
    err = p.stderr.decode("utf-8", "replace").strip()
    if p.returncode != 0:
        return False, (err or out or "код возврата %d" % p.returncode)
    return True, out


_PS_STATUS = """
$n = '<<TASK>>'
$t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
if (-not $t) { '{"exists":false}'; exit }
$i = Get-ScheduledTaskInfo -TaskName $n
$hh = -1; $mm = -1
$sb = ''
try { $sb = [string]$t.Triggers[0].StartBoundary } catch {}
if ($sb -match 'T(\\d{2}):(\\d{2})') { $hh = [int]$Matches[1]; $mm = [int]$Matches[2] }
$next = ''
if ($i.NextRunTime) { $next = $i.NextRunTime.ToString('dd.MM  HH:mm') }
$last = ''
if ($i.LastRunTime -and $i.LastRunTime.Year -gt 1999) { $last = $i.LastRunTime.ToString('dd.MM  HH:mm') }
[pscustomobject]@{
  exists = $true
  state  = [string]$t.State
  wake   = [bool]$t.Settings.WakeToRun
  next   = $next
  last   = $last
  hh     = $hh
  mm     = $mm
} | ConvertTo-Json -Compress
"""

# Без предела по времени (PT0S): звонок гаснет только после правильных ответов,
# как и обещано. Уезжаешь — выключай будильник в панели.
_PS_ENABLE = """
$n = '<<TASK>>'
$a = New-ScheduledTaskAction -Execute '<<EXE>>' -Argument '"<<SCRIPT>>" --now' -WorkingDirectory '<<DIR>>'
<<TRIGGERS>>
$s = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $n -Action $a -Trigger $g -Settings $s -Principal $p `
        -Description 'Будильник, который нельзя выключить лёжа.' -Force | Out-Null
Enable-ScheduledTask -TaskName $n | Out-Null
'OK'
"""


def task_status():
    ok, out = run_ps(_PS_STATUS.replace("<<TASK>>", psq(TASK_NAME)))
    if not ok:
        return {"error": out}
    try:
        return json.loads(out)
    except ValueError:
        return {"error": out or "пустой ответ PowerShell"}


def wake_schedule(hh, mm, plan=None, now=None):
    """Будущие подъёмы для Планировщика: разовый звонок на КАЖДЫЙ день от сегодня до
    последней даты плана (время из плана, а в днях без записи — обычное), дальше
    ежедневный. Иначе в «дырах» плана звонка не было, хотя панель его показывала."""
    now = now or datetime.now()
    days = {}
    for d, t in (plan or {}).items():
        m = hhmm_min(t)
        try:
            day = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        if m is not None:
            days[day] = m
    last = max(days) if days else None
    once = []
    if last is not None and last >= now.date():
        day = now.date()
        while day <= last:
            w = datetime(day.year, day.month, day.day) + timedelta(minutes=days.get(day, hh * 60 + mm))
            if w > now:
                once.append(w)
            day += timedelta(days=1)
        base = datetime(last.year, last.month, last.day) + timedelta(days=1)
    else:
        base = now
    daily = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return once, daily


def triggers_ps(once, daily, end_after_h=3):
    """PowerShell-массив триггеров $g: разовые (с концом) + ежедневный."""
    lines = ["$g = @()"]
    for w in once:
        lines.append("$t = New-ScheduledTaskTrigger -Once -At ([datetime]'%s'); "
                     "$t.EndBoundary = ([datetime]'%s').ToString('s'); $g += $t"
                     % (w.isoformat(), (w + timedelta(hours=end_after_h)).isoformat()))
    lines.append("$g += New-ScheduledTaskTrigger -Daily -At ([datetime]'%s')" % daily.isoformat())
    return "\n".join(lines)


def task_enable(hh, mm, plan=None):
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    script = (_PS_ENABLE
              .replace("<<TASK>>", psq(TASK_NAME))
              .replace("<<EXE>>", psq(exe))
              .replace("<<SCRIPT>>", psq(SCRIPT))
              .replace("<<DIR>>", psq(HERE))
              .replace("<<TRIGGERS>>", triggers_ps(*wake_schedule(hh, mm, plan))))
    return run_ps(script)


def task_disable():
    return run_ps("Disable-ScheduledTask -TaskName '%s' | Out-Null; 'OK'" % psq(TASK_NAME))


# Тест стартует за секунды, поэтому флагу хватает 2 минут — чем короче его жизнь,
# тем уже окно, в котором забытый флаг мог бы упростить настоящий звонок.
TEST_FLAG_TTL = 120


def task_run_now():
    """Тестовый прогон: флаг, чтобы звонок гасился одним ответом.
    Поверх идущего звонка тест не запускаем: при IgnoreNew Планировщик молча
    проигнорирует новый экземпляр, а флаг остался бы лежать."""
    ok, state = run_ps("(Get-ScheduledTask -TaskName '%s').State" % psq(TASK_NAME))
    if ok and state.strip() == "Running":
        return False, "звонок уже идёт — дождись, пока он закончится"
    try:
        with io.open(TEST_FLAG, "w", encoding="utf-8") as f:
            f.write(repr(time.time()))
    except OSError:
        pass
    ok, out = run_ps("Start-ScheduledTask -TaskName '%s'; 'OK'" % psq(TASK_NAME))
    if not ok:
        try:
            os.remove(TEST_FLAG)
        except OSError:
            pass
    return ok, out


def take_test_flag():
    """True, если это тестовый запуск. Флаг одноразовый и протухает:
    если тест почему-то не стартовал, оставшийся флаг иначе превратил бы
    настоящий утренний звонок в «один ответ и спи дальше»."""
    if not os.path.exists(TEST_FLAG):
        return False
    try:
        with io.open(TEST_FLAG, encoding="utf-8") as f:
            age = time.time() - float(f.read().strip())
    except (OSError, ValueError):
        age = None                    # битый или старого формата — не верим
    try:
        os.remove(TEST_FLAG)
    except OSError:
        pass
    return age is not None and 0 <= age <= TEST_FLAG_TTL


# ═══════════════════════════ ЗАДАЧИ: МАТЕМАТИКА ═══════════════════════════
# Каждый генератор возвращает (вопрос, ответ, варианты).
# варианты = None  → ответ вводится числом с клавиатуры.
# варианты = список → ответ это индекс правильного варианта.

def task_multiply(level):
    if level == 1:
        a, b = random.randint(2, 9), random.randint(2, 9)
    elif level == 2:
        a, b = random.randint(6, 19), random.randint(3, 9)
    else:
        a, b = random.randint(13, 49), random.randint(7, 19)
    return "%d × %d" % (a, b), a * b, None


def task_chain(level):
    """Цепочка с приоритетом операций — заставляет реально думать."""
    if level == 1:
        a, b, c = random.randint(2, 9), random.randint(2, 6), random.randint(2, 6)
        return "%d + %d × %d" % (a, b, c), a + b * c, None
    if level == 2:
        a, b, c = random.randint(2, 12), random.randint(2, 9), random.randint(2, 9)
        d = random.randint(2, 12)
        return "%d + %d × %d − %d" % (a, b, c, d), a + b * c - d, None
    a, b, c = random.randint(4, 15), random.randint(3, 12), random.randint(2, 9)
    d, e = random.randint(10, 40), random.randint(2, 9)
    return "%d × %d − %d × %d + %d" % (a, b, c, e, d), a * b - c * e + d, None


def task_square(level):
    n = random.randint(*{1: (2, 12), 2: (8, 20), 3: (15, 40)}[level])
    return "%d²" % n, n * n, None


def task_divmod(level):
    if level == 1:
        b, q = random.randint(2, 5), random.randint(2, 6)
    elif level == 2:
        b, q = random.randint(3, 9), random.randint(4, 15)
    else:
        b, q = random.randint(6, 17), random.randint(8, 30)
    r = random.randint(1, b - 1)
    return "остаток %d ÷ %d" % (b * q + r, b), r, None


def task_percent(level):
    table = {1: ([10, 50], (40, 200, 20)),
             2: ([10, 20, 25, 50], (40, 400, 20)),
             3: ([15, 35, 45, 60, 75], (40, 800, 20))}
    for _ in range(40):
        ps, rng = table[level]
        p, n = random.choice(ps), random.randrange(*rng)
        v = p * n / 100.0
        if v == int(v):
            return "%d%% от %d" % (p, n), int(v), None
    return task_multiply(level)


def task_equation(level):
    if level == 1:
        a, x, b = random.randint(2, 5), random.randint(2, 9), random.randint(1, 10)
    elif level == 2:
        a, x, b = random.randint(2, 9), random.randint(2, 12), random.randint(1, 20)
    else:
        a, x, b = random.randint(3, 15), random.randint(3, 25), random.randint(10, 60)
    if level == 3 and random.random() < 0.5:
        return "%dx − %d = %d,   x" % (a, b, a * x - b), x, None
    return "%dx + %d = %d,   x" % (a, b, a * x + b), x, None


def task_root(level):
    n = random.randint(*{1: (2, 9), 2: (4, 15), 3: (12, 30)}[level])
    return "√%d" % (n * n), n, None


def task_clock(level):
    """Сколько минут между двумя моментами времени."""
    step = {1: 5, 2: 5, 3: 1}[level]
    gap = {1: random.randrange(10, 60, step),
           2: random.randrange(20, 120, step),
           3: random.randrange(40, 260, step)}[level]
    h = random.randint(6, 20)
    m = random.randrange(0, 60, 5)
    t1 = h * 60 + m
    t2 = t1 + gap
    return ("сколько минут с %02d:%02d до %02d:%02d?"
            % (t1 // 60, t1 % 60, (t2 // 60) % 24, t2 % 60)), gap, None


MATH_GENERATORS = [task_multiply, task_chain, task_square, task_divmod,
                   task_percent, task_equation, task_root, task_clock]


# ═══════════════════════════ ЗАДАЧИ: ЛОГИКА ═══════════════════════════

GROUPS = [
    ["кот", "собака", "лошадь", "корова", "волк", "лиса", "заяц", "медведь"],
    ["дуб", "берёза", "сосна", "клён", "тополь", "ель"],
    ["стол", "стул", "шкаф", "диван", "кровать", "полка"],
    ["чашка", "тарелка", "ложка", "вилка", "кастрюля", "чайник"],
    ["машина", "автобус", "поезд", "самолёт", "велосипед", "трамвай"],
    ["яблоко", "груша", "банан", "слива", "персик", "вишня"],
    ["морковь", "картофель", "огурец", "свёкла", "капуста", "редиска"],
    ["молоток", "отвёртка", "пила", "рубанок", "дрель"],
    ["рубашка", "брюки", "куртка", "шапка", "носки", "шарф"],
    ["река", "озеро", "море", "ручей", "пруд"],
]

# имя в именительном и родительном: «Боря выше Ани», а не «выше Аня»
NAMES = [("Аня", "Ани"), ("Боря", "Бори"), ("Витя", "Вити"), ("Гена", "Гены"),
         ("Даша", "Даши"), ("Женя", "Жени"), ("Костя", "Кости"), ("Лена", "Лены")]

DAYS = ["понедельник", "вторник", "среда", "четверг",
        "пятница", "суббота", "воскресенье"]

RELATIONS = [
    ("Брат твоей матери тебе кто?", "дядя", ["племянник", "двоюродный брат", "тесть", "шурин"]),
    ("Сестра твоего отца тебе кто?", "тётя", ["племянница", "сестра", "тёща", "кузина"]),
    ("Сын твоего сына тебе кто?", "внук", ["племянник", "брат", "правнук", "зять"]),
    ("Дочь твоей сестры тебе кто?", "племянница", ["внучка", "кузина", "тётя", "золовка"]),
    ("Мать твоей жены тебе кто?", "тёща", ["свекровь", "тётя", "сватья", "невестка"]),
    ("Сын твоей сестры тебе кто?", "племянник", ["внук", "кузен", "шурин", "деверь"]),
]

ANALOGIES = [
    ("птица — перья,   рыба — ?", "чешуя", ["шерсть", "лапы", "хвост", "крылья"]),
    ("день — ночь,   свет — ?", "тьма", ["лампа", "утро", "тень", "солнце"]),
    ("нож — резать,   ручка — ?", "писать", ["читать", "держать", "рисовать", "чертить"]),
    ("врач — больница,   учитель — ?", "школа", ["класс", "книга", "урок", "тетрадь"]),
    ("рука — локоть,   нога — ?", "колено", ["ступня", "пятка", "бедро", "палец"]),
    ("машина — дорога,   поезд — ?", "рельсы", ["вокзал", "мост", "тоннель", "шпалы"]),
    ("холодно — зима,   жарко — ?", "лето", ["весна", "осень", "юг", "пустыня"]),
    ("буква — слово,   слово — ?", "предложение", ["книга", "звук", "страница", "абзац"]),
]

RIDDLES = [
    ("Сколько месяцев в году имеют 28 дней?", 12),
    ("В комнате 4 угла, в каждом углу кошка, напротив каждой кошки 3 кошки. "
     "Сколько всего кошек?", 4),
    ("Два землекопа копают 2 метра канавы за 2 часа. "
     "Сколько метров выкопают 4 землекопа за 4 часа?", 8),
    ("Пять свечей горело, две погасли. Сколько свечей осталось?", 2),
    ("Бревно распилили на 4 части. Сколько сделали распилов?", 3),
]

CHOICE_RIDDLES = [
    ("У отца Маши пять дочерей: Чача, Чече, Чичи, Чочо. Как зовут пятую?",
     "Маша", ["Чучу", "Чача", "Маруся", "Чичи"]),
    ("Что тяжелее: килограмм ваты или килограмм железа?",
     "одинаково", ["вата", "железо", "смотря где", "нельзя сравнить"]),
]


def _choice_task(question, correct, wrongs, count=4):
    """Собирает вопрос с вариантами и перемешивает их."""
    opts = [correct] + random.sample(wrongs, min(count - 1, len(wrongs)))
    random.shuffle(opts)
    return question, opts.index(correct), opts


def logic_odd_one_out(level):
    n = 4 if level < 3 else 5
    a, b = random.sample(GROUPS, 2)
    odd = random.choice(b)
    opts = random.sample(a, n - 1) + [odd]
    random.shuffle(opts)
    return "Что здесь лишнее?", opts.index(odd), opts


def logic_sequence(level):
    kind = random.choice(["arith", "geom", "square", "grow"])
    if kind == "arith":
        start = random.randint(1, 9)
        step = random.randint(2, 5) if level == 1 else random.randint(3, 9)
        seq = [start + step * i for i in range(4)]
    elif kind == "geom":
        start = random.randint(1, 3)
        k = 2 if level == 1 else random.choice([2, 3])
        seq = [start * k ** i for i in range(4)]
    elif kind == "square":
        start = random.randint(1, 3 if level == 1 else 5)
        seq = [(start + i) ** 2 for i in range(4)]
    else:
        start = random.randint(1, 5)
        d = random.randint(1, 3)
        seq, cur, stp = [], start, d
        for _ in range(4):
            seq.append(cur)
            cur += stp
            stp += d
    nxt = seq[-1] + (seq[-1] - seq[-2]) if kind == "arith" else None
    if kind == "geom":
        nxt = seq[-1] * (seq[1] // seq[0])
    elif kind == "square":
        nxt = (int(round(seq[-1] ** 0.5)) + 1) ** 2
    elif kind == "grow":
        nxt = seq[-1] + (seq[-1] - seq[-2]) + d
    return ", ".join(str(x) for x in seq) + ",  ?", nxt, None


def plural_days(n):
    """2 дня, 5 дней, 21 день — иначе получается «через 5 дня»."""
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


def logic_days(level):
    today = random.randint(0, 6)
    n = random.randint(2, 4) if level == 1 else random.randint(3, 9)
    correct = DAYS[(today + n) % 7]
    wrongs = [d for d in DAYS if d != correct]
    return _choice_task("Сегодня %s. Какой день будет через %d %s?"
                        % (DAYS[today], n, plural_days(n)), correct, wrongs)


def logic_order(level):
    (a, _), (b, b_gen), (c, c_gen), (extra, _) = random.sample(NAMES, 4)
    word, lo, hi = random.choice([("выше", "самый низкий", "самый высокий"),
                                 ("старше", "самый младший", "самый старший")])
    ask_low = random.random() < 0.5
    question = "%s %s %s, %s %s %s. Кто %s?" % (
        a, word, b_gen, b, word, c_gen, lo if ask_low else hi)
    correct = c if ask_low else a
    return _choice_task(question, correct,
                        [x for x in (a, b, c, extra) if x != correct])


def logic_relation(level):
    q, correct, wrongs = random.choice(RELATIONS)
    return _choice_task(q, correct, wrongs)


def logic_analogy(level):
    q, correct, wrongs = random.choice(ANALOGIES)
    return _choice_task(q, correct, wrongs)


def logic_riddle(level):
    if random.random() < 0.5:
        q, a = random.choice(RIDDLES)
        return q, a, None
    q, correct, wrongs = random.choice(CHOICE_RIDDLES)
    return _choice_task(q, correct, wrongs)


def logic_count_back(level):
    """Простая задача в словах с числовым ответом."""
    if level == 1:
        had = random.randint(6, 14)
        gave = random.randint(2, had - 2)
        return ("Было %d яблок, отдал %d. Сколько осталось?" % (had, gave),
                had - gave, None)
    if level == 2:
        had = random.randrange(10, 30, 2)
        return ("Было %d конфет, отдал половину и ещё 2. Сколько осталось?" % had,
                had // 2 - 2, None)
    if random.random() < 0.5:
        total = random.randrange(21, 60, 3)          # кратно трём
        part = random.randint(2, 6)
        return ("В корзине %d яблок. Треть забрали, потом ещё %d. "
                "Сколько осталось?" % (total, part),
                total - total // 3 - part, None)
    total = random.randrange(20, 60, 4)              # кратно четырём
    return ("Было %d конфет, съел четверть. Сколько осталось?" % total,
            total * 3 // 4, None)


LOGIC_GENERATORS = [logic_odd_one_out, logic_sequence, logic_days, logic_order,
                    logic_relation, logic_analogy, logic_riddle, logic_count_back]

LEVEL_NAME = {1: "Разминка", 2: "Норма", 3: "Жёстко"}
KIND_NAME = {"math": "Математика", "logic": "Логика", "mix": "Вперемешку"}
SOUND_NAME = {"melody": "Мелодия", "t3": "Сигнал T-3", "beep": "Резкий писк"}


def new_task(level, kinds="mix"):
    """-> (вопрос, ответ, варианты). варианты=None → ответ вводится числом."""
    pool = []
    if kinds in ("math", "mix"):
        pool += MATH_GENERATORS
    if kinds in ("logic", "mix"):
        pool += LOGIC_GENERATORS
    return random.choice(pool)(level)


TAUNTS = ["Мимо. Мозг ещё спит.", "Не то. Соберись.", "Опять не угадал.",
          "Кровать тут не поможет.", "Ноль. Начинаем заново."]

PRAISE = ["Есть. Дальше.", "Верно.", "Хорошо, не расслабляйся.", "Точно. Ещё."]


# ═══════════════════════════════ ЗВУК ═══════════════════════════════
# Низкочастотный меандр ~520 Гц: у Bruck & Thomas он будил 92% против 56%
# у высокого писка при 75 дБА — это у взрослых со сниженным слухом, на пожарных
# сигналах; в других группах низкий сигнал тоже выигрывал. Мелодичность по
# McFarlane (RMIT, 2020) связана с меньшей *самооценкой* сонной инерции (опрос),
# нарастание по Yang/Cera/Pauletto (KTH, 18 чел.) — с лучшим настроением утром.

RATE = 22050

# Фраза в низком регистре, фундаментальная около 520 Гц (до пятой октавы).
MELODY = [(523, .20), (392, .20), (523, .20), (659, .26), (0, .16),
          (523, .20), (392, .28), (0, .55)]

# Злая стадия: пауз почти нет, добавлен резкий акцент сверху.
MELODY_FAST = [(523, .13), (392, .13), (523, .13), (1046, .16), (0, .05),
               (523, .13), (392, .13), (1046, .16), (0, .05),
               (523, .13), (659, .20), (0, .10)]

# Сигнал T-3 (ISO 8201): три посылки по 0,5 с, затем пауза 1,5 с.
T3 = [(520, .5), (0, .5), (520, .5), (0, .5), (520, .5), (0, 1.5)]

# Старый резкий писк — оставлен как вариант, хотя он худший из трёх.
BEEP = [(980, .13), (0, .12), (1310, .13), (0, .12)] * 4

STAGES = [(0, 0.12), (20, 0.22), (45, 0.38), (80, 0.58),
          (140, 0.80), (240, 1.00), (480, "angry")]


def _render(spec, amp):
    """Меандр по нотной схеме с плавными краями, чтобы не щёлкало."""
    out = []
    fade = max(1, int(RATE * 0.008))
    for freq, dur in spec:
        n = int(RATE * dur)
        for i in range(n):
            if freq <= 0:
                v = 0.0
            else:
                v = 1.0 if (i * freq / float(RATE)) % 1.0 < 0.5 else -1.0
            env = 1.0
            if i < fade:
                env = i / float(fade)
            elif i > n - fade:
                env = (n - i) / float(fade)
            out.append(struct.pack("<h", int(max(-1.0, min(1.0, v)) * env * amp * 26000)))
    return b"".join(out)


def stage_wav(kind, amp, angry=False):
    """Готовит (и кэширует) файл одной стадии громкости."""
    name = "alarm_%s_%03d%s.wav" % (kind, int(amp * 100), "_x" if angry else "")
    path = os.path.join(tempfile.gettempdir(), name)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    spec = {"melody": MELODY_FAST if angry else MELODY,
            "t3": T3, "beep": BEEP}[kind]
    data = _render(spec, amp)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(data)
    return path



# ─────── вывод звонка на конкретную колонку (Windows, waveOut) ───────
# Системное устройство по умолчанию не переключаем: иначе после звонка
# весь звук компа остался бы на колонке. Будильник сам открывает нужное
# устройство и крутит на нём WAV по кругу силами драйвера.

WHDR_BEGINLOOP, WHDR_ENDLOOP, WHDR_DONE = 0x4, 0x8, 0x1


def _winmm():
    import ctypes
    from ctypes import wintypes

    class WAVEOUTCAPSW(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                    ("vDriverVersion", wintypes.UINT), ("szPname", wintypes.WCHAR * 32),
                    ("dwFormats", wintypes.DWORD), ("wChannels", wintypes.WORD),
                    ("wReserved1", wintypes.WORD), ("dwSupport", wintypes.DWORD)]

    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                    ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                    ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                    ("cbSize", wintypes.WORD)]

    class WAVEHDR(ctypes.Structure):
        _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                    ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                    ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                    ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]

    return ctypes, ctypes.windll.winmm, WAVEOUTCAPSW, WAVEFORMATEX, WAVEHDR


def output_devices():
    """[(номер, имя)] устройств вывода. Имя winmm обрезает до 31 символа."""
    if sys.platform != "win32":
        return []
    ctypes, mm, CAPS, _f, _h = _winmm()
    out = []
    for i in range(mm.waveOutGetNumDevs()):
        caps = CAPS()
        if mm.waveOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            out.append((i, caps.szPname))
    return out


def find_speaker(pattern):
    """Номер устройства, в имени которого есть pattern (без учёта регистра).
    Несколько вариантов можно перечислить через «|»."""
    keys = [k.strip().lower() for k in (pattern or "").split("|") if k.strip()]
    for i, name in output_devices():
        low = name.lower()
        if any(k in low for k in keys):
            return i, name
    return None


class DevicePlayer:
    """Крутит WAV по кругу на одном устройстве вывода."""

    def __init__(self, dev_id):
        self.dev = dev_id
        self.h = None
        self.hdr = None
        self.buf = None              # держим ссылку, пока драйвер читает буфер
        self.lock = threading.Lock()

    def play_loop(self, path, loops=1000000):
        ctypes, mm, _c, FMT, HDR = _winmm()
        with wave.open(path, "rb") as w:
            ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            data = w.readframes(w.getnframes())
        with self.lock:
            self._close()
            fmt = FMT(1, ch, rate, rate * ch * width, ch * width, width * 8, 0)
            h = ctypes.c_void_p()
            if mm.waveOutOpen(ctypes.byref(h), self.dev, ctypes.byref(fmt), 0, 0, 0) != 0:
                return False
            buf = ctypes.create_string_buffer(data, len(data))
            hdr = HDR()
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = len(data)
            hdr.dwFlags = 0                       # до Prepare флаги обязаны быть нулём
            if mm.waveOutPrepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr)) != 0:
                mm.waveOutClose(h)
                return False
            if loops:
                hdr.dwFlags |= WHDR_BEGINLOOP | WHDR_ENDLOOP
                hdr.dwLoops = loops
            if mm.waveOutWrite(h, ctypes.byref(hdr), ctypes.sizeof(hdr)) != 0:
                mm.waveOutUnprepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
                mm.waveOutClose(h)
                return False
            self.h, self.hdr, self.buf = h, hdr, buf
            return True

    def finished(self):
        return self.hdr is None or bool(self.hdr.dwFlags & WHDR_DONE)

    def _close(self):
        if self.h is None:
            return
        ctypes, mm, _c, _f, _h = _winmm()
        mm.waveOutReset(self.h)
        mm.waveOutUnprepareHeader(self.h, ctypes.byref(self.hdr), ctypes.sizeof(self.hdr))
        mm.waveOutClose(self.h)
        self.h = self.hdr = self.buf = None

    def stop(self):
        with self.lock:
            self._close()



# ─────── громкость колонки (Windows Core Audio через PowerShell) ───────
# waveOut играет через громкость устройства в Windows: если у колонки там 0%
# или выключен звук, будильник «звенит» в тишину. Так и было 15.10.

_PS_VOL = r"""
$code = @'
using System;
using System.Globalization;
using System.Runtime.InteropServices;
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
  int EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
  int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
}
[Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceCollection { int GetCount(out int c); int Item(int i, out IMMDevice d); }
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
  int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o);
  int OpenPropertyStore(int access, out IPropertyStore store);
}
[Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
  int GetCount(out int c); int GetAt(int i, out PROPERTYKEY k); int GetValue(ref PROPERTYKEY k, out PROPVARIANT v);
}
[StructLayout(LayoutKind.Sequential)] struct PROPERTYKEY { public Guid fmtid; public int pid; }
[StructLayout(LayoutKind.Explicit)] struct PROPVARIANT { [FieldOffset(0)] public short vt; [FieldOffset(8)] public IntPtr p; }
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int RegisterControlChangeNotify(IntPtr n); int UnregisterControlChangeNotify(IntPtr n);
  int GetChannelCount(out int c);
  int SetMasterVolumeLevel(float l, ref Guid g); int SetMasterVolumeLevelScalar(float l, ref Guid g);
  int GetMasterVolumeLevel(out float l); int GetMasterVolumeLevelScalar(out float l);
  int SetChannelVolumeLevel(int c, float l, ref Guid g); int SetChannelVolumeLevelScalar(int c, float l, ref Guid g);
  int GetChannelVolumeLevel(int c, out float l); int GetChannelVolumeLevelScalar(int c, out float l);
  int SetMute(bool m, ref Guid g); int GetMute(out bool m);
}
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator {}
public static class SpkVol {
  public static string Run(string part, float level, int mute, int raiseOnly) {
    var en = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
    var key = new PROPERTYKEY { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 };
    bool byDefault = part == "*default*";        // устройство вывода по умолчанию
    IMMDeviceCollection col = null; int n = 1;
    if (!byDefault) { en.EnumAudioEndpoints(0, 1, out col); col.GetCount(out n); }
    for (int i = 0; i < n; i++) {
      IMMDevice d;
      if (byDefault) { if (en.GetDefaultAudioEndpoint(0, 1, out d) != 0 || d == null) break; }
      else col.Item(i, out d);
      IPropertyStore ps; d.OpenPropertyStore(0, out ps);
      PROPVARIANT v; ps.GetValue(ref key, out v);
      string name = Marshal.PtrToStringUni(v.p) ?? "";
      if (!byDefault && name.IndexOf(part, StringComparison.OrdinalIgnoreCase) < 0) continue;
      Guid iid = typeof(IAudioEndpointVolume).GUID; object o;
      d.Activate(ref iid, 23, IntPtr.Zero, out o);
      var ev = (IAudioEndpointVolume)o;
      Guid ctx = Guid.Empty;
      float prev; bool prevMute; ev.GetMasterVolumeLevelScalar(out prev); ev.GetMute(out prevMute);
      if (level >= 0) {
        float target = raiseOnly == 1 ? Math.Max(prev, level) : level;
        ev.SetMasterVolumeLevelScalar(target, ref ctx);
      }
      if (mute >= 0) ev.SetMute(mute == 1, ref ctx);
      float now; bool nowMute; ev.GetMasterVolumeLevelScalar(out now); ev.GetMute(out nowMute);
      var ci = CultureInfo.InvariantCulture;
      return "{\"found\":true,\"name\":\"" + name.Replace("\\", "\\\\").Replace("\"", "'") +
             "\",\"prev\":" + prev.ToString("0.000", ci) + ",\"prevMute\":" + (prevMute ? "true" : "false") +
             ",\"now\":" + now.ToString("0.000", ci) + ",\"nowMute\":" + (nowMute ? "true" : "false") + "}";
    }
    return "{\"found\":false}";
  }
}
'@
Add-Type -TypeDefinition $code
[SpkVol]::Run('<<NAME>>', <<LEVEL>>, <<MUTE>>, <<RAISE>>)
"""


def speaker_volume(part, level=None, mute=None, raise_only=False):
    """Читает (и при желании ставит) громкость и mute устройства вывода по части имени.
    Возвращает dict: found, name, prev, prevMute, now, nowMute — или error."""
    if sys.platform != "win32" or not part:
        return {"found": False}                # «*default*» — устройство по умолчанию
    script = (_PS_VOL.replace("<<NAME>>", psq(part))
              .replace("<<LEVEL>>", "%.3f" % (level if level is not None else -1))
              .replace("<<MUTE>>", str(-1 if mute is None else int(bool(mute))))
              .replace("<<RAISE>>", "1" if raise_only else "0"))
    ok, out = run_ps(script)
    if not ok:
        return {"error": out.splitlines()[0][:200] if out else "PowerShell не ответил"}
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": out[:200]}


class SpeakerVolumeGuard:
    """На время звонка поднимает громкость и включает звук — у выбранной колонки, а если
    её нет (не задана или не подключена) — у устройства по умолчанию. После звонка
    возвращает всё как было — как будильник в телефоне."""
    DEFAULT = "*default*"

    def __init__(self, cfg):
        self.part = cfg.get("speaker", "")
        self.target = cfg.get("speaker_volume", 80) / 100.0
        self.prev = {}               # устройство → (громкость, mute) до первого вмешательства

    def ensure(self, part=None):
        part = part or self.part or self.DEFAULT
        st = speaker_volume(part, level=self.target, mute=False, raise_only=True)
        if st.get("error"):
            log("громкость: не удалось — %s" % st["error"])
            return False
        if not st.get("found"):
            log("громкость: устройство «%s» не найдено" % part)
            return False
        self.prev.setdefault(part, (st["prev"], st["prevMute"]))
        log("громкость «%s»: было %d%%%s → стало %d%%%s" % (
            st["name"], round(st["prev"] * 100), ", звук выключен" if st["prevMute"] else "",
            round(st["now"] * 100), ", звук выключен" if st["nowMute"] else ""))
        return not st["nowMute"] and st["now"] > 0

    def ensure_default(self):
        return self.ensure(self.DEFAULT)

    def restore(self):
        for part, (level, mute) in list(self.prev.items()):
            speaker_volume(part, level=level, mute=mute)
            log("громкость «%s» возвращена: %d%%%s" % (
                part, round(level * 100), ", звук выключен" if mute else ""))
        self.prev.clear()


def speaker_check(cfg):
    """Проверка колонки заранее, пока человек ещё не спит.
    Громкость будильник поднимет сам, а вот неподключённую колонку — нет:
    это надо поймать до сна. Возвращает (уровень, текст): ok / warn / bad."""
    part = cfg.get("speaker", "")
    if not part:
        return "warn", "колонка в настройках не задана — звонок пойдёт во все устройства"
    if not find_speaker(part):
        return "bad", "колонка не подключена — подключи сейчас, иначе звонок уйдёт в наушники"
    st = speaker_volume(part)
    if not st.get("found"):
        return "warn", "колонка подключена, но громкость прочитать не удалось"
    level = round(st["now"] * 100)
    if st["nowMute"] or level < 5:
        return "warn", "колонка подключена, звук в Windows %s — будильник сам поднимет до %d%%" % (
            "выключен" if st["nowMute"] else "на %d%%" % level, cfg.get("speaker_volume", 80))
    return "ok", "колонка подключена, громкость %d%%" % level


class Siren:
    """Звонок с нарастанием: тихо на старте, максимум к 4 минутам,
    после 8 минут — злая стадия с октавой сверху.
    Играет в колонку из настройки speaker; если её нет — во все устройства
    сразу, чтобы звонок не ушёл тихо в одни наушники."""

    RECHECK_SEC = 15              # как часто заново искать колонку

    def __init__(self, cfg=None, guard=None, stages=None):
        cfg = cfg or {}
        self.guard = guard            # SpeakerVolumeGuard или None
        self.custom_stages = stages   # своя лестница громкости (тихая проверка)
        self.kind = cfg.get("sound", "melody")
        self.escalate = cfg.get("escalate", True)
        self.speaker = cfg.get("speaker", "")
        self.stop_flag = threading.Event()
        self.proc = None
        self.gen = 0
        self.players = []
        self.route = ""               # куда реально играет — для отладки и теста
        self.lock = threading.Lock()  # _play и stop() не должны пересекаться

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def join(self, timeout=8.0):
        """Дождаться потока сирены: иначе громкость могли поднять уже после restore()."""
        t = getattr(self, "thread", None)
        if t is not None and t is not threading.current_thread():
            t.join(timeout)

    def _stages(self):
        if self.custom_stages:
            return self.custom_stages
        if not self.escalate:
            return [(0, 1.00)]
        return STAGES

    def _prepare(self):
        """Перед игрой в колонку — поднять её громкость (вне замка: это 1–2 с)."""
        if self.guard is None:
            return
        if self.speaker and find_speaker(self.speaker):
            self.guard.ensure()
        else:                                  # колонки нет — звонок идёт во все устройства,
            self.guard.ensure_default()        # поднимаем хотя бы устройство по умолчанию

    def _run(self):
        begun = time.time()
        stages = list(self._stages())
        idx, path, last = 0, None, time.time()
        while not self.stop_flag.is_set():
            if idx < len(stages) and time.time() - begun >= stages[idx][0]:
                t, amp = stages[idx]
                idx += 1
                angry = amp == "angry"
                try:
                    path = stage_wav(self.kind, 1.0 if angry else amp, angry)
                except Exception as e:                       # noqa: BLE001
                    log("звонок: не собрался звук стадии: %s" % e)
                    return
                self._prepare()
                self._play(path)
                log("стадия %d с (%s): играет в %s" % (
                    t, "злая" if angry else "%d%%" % round(amp * 100), self.route))
                last = time.time()
                continue
            # Колонка могла подключиться позже или отвалиться посреди звонка —
            # проверяем всё время, а не только на смене стадий (раньше после
            # 8-й минуты поиск прекращался).
            if path and time.time() - last >= self.RECHECK_SEC:
                last = time.time()
                try:
                    want = self._targets()[1]
                except Exception:                            # noqa: BLE001
                    want = self.route
                if want != self.route:
                    self._prepare()
                    self._play(path)
                    log("переключение: теперь играет в %s" % self.route)
            time.sleep(0.2)

    def _targets(self):
        """Куда играть. Устройства ищем заново на каждой стадии: Bluetooth
        после выхода компа из сна может подключиться не сразу, и тогда
        следующая стадия уже уйдёт в колонку."""
        found = find_speaker(self.speaker) if self.speaker else None
        if found:
            return [found[0]], found[1]
        devs = output_devices()
        return [i for i, _n in devs], "все устройства (колонка «%s» не найдена)" % self.speaker

    def _play(self, path):
        self.gen += 1
        if sys.platform == "win32":
            # Под замком: stop() из окна не должен проскочить между проверкой
            # флага и запуском нового плеера — иначе звук пережил бы остановку.
            with self.lock:
                for p in self.players:
                    p.stop()
                self.players = []
                if self.stop_flag.is_set():
                    return
                started = []
                try:
                    ids, self.route = self._targets()
                    for i in ids:
                        p = DevicePlayer(i)
                        if p.play_loop(path):
                            started.append(p)
                except Exception:                            # noqa: BLE001
                    for p in started:                        # не бросаем открытые устройства
                        p.stop()
                    started = []
                self.players = started
                if self.players:
                    self._purge_winsound()                   # если до этого играл запасной путь
                    return
                self.route = "устройство по умолчанию (запасной путь)"
                try:
                    import winsound
                    winsound.PlaySound(path, winsound.SND_FILENAME
                                       | winsound.SND_ASYNC | winsound.SND_LOOP)
                except Exception:                            # noqa: BLE001
                    pass
            return
        my = self.gen
        player = "afplay" if sys.platform == "darwin" else "aplay"
        if self.proc:
            self.proc.terminate()

        def repeat():
            while not self.stop_flag.is_set() and my == self.gen:
                try:
                    self.proc = subprocess.Popen(
                        [player, path], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
                    self.proc.wait()
                except FileNotFoundError:
                    print("", end="", flush=True)
                    time.sleep(1)

        threading.Thread(target=repeat, daemon=True).start()

    @staticmethod
    def _purge_winsound():
        if sys.platform != "win32":
            return
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:                                    # noqa: BLE001
            pass

    def stop(self):
        with self.lock:
            self.stop_flag.set()
            self.gen += 1
            for p in self.players:
                try:
                    p.stop()
                except Exception:                            # noqa: BLE001
                    pass
            self.players = []
        if sys.platform == "win32":
            self._purge_winsound()
        elif self.proc:
            self.proc.terminate()


# ═══════════════════════════ ЭКРАН БУДИЛЬНИКА ═══════════════════════════

class AlarmWindow:
    def __init__(self, cfg, siren, need=None, title="П О Д Ъ Ё М", timeout=0,
                 label="ЗВЕНИТ"):
        """need/title/timeout/label — для тихих режимов: «Я встал» (10 задач)
        и проверки «не уснул ли обратно» (1 задача, timeout секунд на ответ)."""
        self.cfg, self.siren = cfg, siren
        self.need = need or cfg["need"]
        self.title_txt, self.timeout, self.label = title, timeout, label
        self.solved = False
        self.level = cfg["level"]
        self.kinds = cfg["kinds"]
        self.streak = self.attempts = self.wrong_row = 0
        self.started = time.time()
        self.buf = ""
        self.caret = True
        self.locked_until = 0.0
        self.phase = 0.0
        self.frame_n = 0
        self.qoff = 0
        self.alive = True
        self.question, self.answer, self.options = new_task(self.level, self.kinds)

        self.root = tk.Tk()
        keep_gc(self.root)
        self.root.title("ПОДЪЁМ")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=T.BG_BOT)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        self.W = self.root.winfo_screenwidth()
        self.H = self.root.winfo_screenheight()
        self.S = S = self.H / 1080.0
        self.u = max(2, int(round(4 * S)))          # «пиксель» рамок и полос

        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bg=T.BG_BOT)
        self.cv.pack(fill="both", expand=True)
        self.sky = mix(T.ACCENT_D, T.BG_BOT, 0.55)
        paint_bg(self.cv, self.W, self.H, top=self.sky, u=self.u, bands=9,
                 stars=140, star_h=0.9, live=0)   # живые звёзды тут роняют частоту кадров

        self._stripes()
        self._header()
        self._dots()
        self._card()
        self._input()
        self._keypad()
        self._options()
        self._footer()

        self.root.bind("<Key>", self.on_key)
        for seq in ("<Escape>", "<Alt-F4>", "<Control-w>"):
            self.root.bind(seq, lambda e: "break")

        self.render()
        self.animate()
        self.blink()
        self.keep_on_top()

    def sc(self, k):
        """Масштаб пиксельного шрифта под высоту экрана."""
        return max(2, int(round(k * self.S)))

    # ─────────────── оформление ───────────────

    def _stripes(self):
        """Бегущая «аварийная» лента сверху и снизу — лесенкой из пикселей.
        Каждая лента — одна картинка: сотни прямоугольников в движении
        роняли окно до 6 кадров в секунду."""
        u = self.u
        band = 4 * u
        self.period = 14 * u
        self.stripe_off = {"stop": 0, "sbot": 0}
        gw = (self.W + 4 * self.period) // u + 1
        rows = []
        for r in range(4):
            rows.append("{" + " ".join(
                T.ACCENT_D if (x - (3 - r)) % 14 < 7 else T.INK for x in range(gw)) + "}")
        img = tk.PhotoImage(master=self.cv, width=gw, height=4)
        img.put(" ".join(rows))
        self.stripe_img = img.zoom(u) if u > 1 else img
        for tag, y in (("stop", 0), ("sbot", self.H - band)):
            self.cv.create_image(-2 * self.period, y, image=self.stripe_img, anchor="nw",
                                 tags=(tag,))
            edge = y + band if tag == "stop" else y - u
            px_rect(self.cv, 0, edge, self.W, edge + u, T.ACCENT_D)

    def _header(self):
        S = self.S
        cy = 104 * S
        tsc = self.sc(8)
        self.title = PText(self.cv, self.W / 2, cy, self.title_txt, tsc,
                           fill=T.ACCENT, shadow=T.INK)
        csc = self.sc(5)
        dx = text_w(self.title_txt, tsc) / 2 + 40 * S + 8 * csc
        self.clk_l = sprite(self.cv, self.W / 2 - dx, cy, SPR_CLOCK, CLOCK_PAL, csc)
        self.clk_r = sprite(self.cv, self.W / 2 + dx, cy, SPR_CLOCK, CLOCK_PAL, csc)
        self.clock = PText(self.cv, self.W / 2, 176 * S, "", self.sc(3), fill=T.MUTED)

    def _dots(self):
        """Звёзды прогресса: правильный ответ зажигает очередную."""
        S = self.S
        n = self.need
        ssc = self.sc(5)
        while ssc > 2 and n * 12 * ssc > self.W * 0.6:
            ssc -= 1
        gap = 12 * ssc
        x0 = self.W / 2 - gap * (n - 1) / 2
        self.dots = [sprite(self.cv, x0 + i * gap, 242 * S, SPR_STAR, STAR_OFF, ssc)
                     for i in range(n)]
        self.progress = PText(self.cv, self.W / 2, 294 * S, "", self.sc(2), fill=T.MUTED)

    def _card(self):
        S, u = self.S, self.u
        w, h = min(self.W * 0.62, 1000 * S), 190 * S
        self.card_cy = 400 * S
        self.card_w, self.card_h = w, h
        self.card = pbox(self.cv, self.W / 2 - w / 2, self.card_cy - h / 2,
                         self.W / 2 + w / 2, self.card_cy + h / 2, u,
                         fill=T.CARD, border=T.LINE, tags=("qcard",))
        self.q_text = PText(self.cv, self.W / 2, self.card_cy, "", tags=("qcard",))

    def _input(self):
        S, u = self.S, self.u
        w, h = 490 * S, 88 * S
        self.in_cy = 548 * S
        pbox(self.cv, self.W / 2 - w / 2, self.in_cy - h / 2,
             self.W / 2 + w / 2, self.in_cy + h / 2, u,
             fill=T.INK, border=T.LINE, inset=True, tags=("numeric",))
        self.in_text = PText(self.cv, self.W / 2, self.in_cy, "", self.sc(6),
                             fill=T.TEXT, tags=("numeric",))
        self.status = PText(self.cv, self.W / 2, 620 * S, "", self.sc(3), fill=T.MUTED)

    def _keypad(self):
        S, u = self.S, self.u
        bw, bh, gap = 112 * S, 72 * S, 14 * S
        total = 4 * bw + 3 * gap
        x0 = self.W / 2 - total / 2 + bw / 2
        y0 = 676 * S
        self.pad = []
        rows = [("1", "2", "3", "⌫"), ("4", "5", "6", "±"), ("7", "8", "9", "C")]
        for ri, row in enumerate(rows):
            for ci, key in enumerate(row):
                side = ci == 3
                self.pad.append(CBtn(
                    self.cv, x0 + ci * (bw + gap), y0 + ri * (bh + gap), bw, bh, key,
                    lambda k=key: self.press(k), u=u,
                    sc=self.sc(3 if side else 4),
                    fill=T.CARD_HI if side else T.CARD,
                    fg=T.MUTED if side else T.TEXT))
        y4 = y0 + 3 * (bh + gap)
        self.pad.append(CBtn(self.cv, x0 + (bw + gap) / 2, y4, bw * 2 + gap, bh, "0",
                             lambda: self.press("0"), u=u, sc=self.sc(4), fill=T.CARD))
        self.pad.append(CBtn(self.cv, x0 + 2 * (bw + gap) + (bw + gap) / 2, y4,
                             bw * 2 + gap, bh, "ОТВЕТИТЬ", lambda: self.press("\r"),
                             u=u, sc=self.sc(3), fill=T.ACCENT, fg=T.TEXT))

    def _options(self):
        S, u = self.S, self.u
        w, h = min(self.W * 0.50, 820 * S), 80 * S
        self.opt_btns = []
        for i in range(5):
            b = CBtn(self.cv, self.W / 2, 596 * S + i * (h + 16 * S), w, h, "",
                     lambda k=i: self.choose(k), u=u, sc=self.sc(3),
                     fill=T.CARD, fg=T.TEXT, align="left")
            b.hide()
            self.opt_btns.append(b)

    def _footer(self):
        S = self.S
        y = self.H - 50 * S
        fs = self.sc(2)
        self.f_left = PText(self.cv, 46 * S, y, "", fs, fill=T.DIM, anchor="w")
        self.f_mid = PText(self.cv, self.W / 2, y, "", fs, fill=T.MUTED)
        PText(self.cv, self.W - 46 * S, y,
              "%s · %s" % (LEVEL_NAME[self.level], KIND_NAME[self.kinds]),
              fs, fill=T.DIM, anchor="e")

    # ─────────────── отрисовка вопроса ───────────────

    def render(self):
        S, u = self.S, self.u
        q = self.question
        if self.options is None and not q.rstrip().endswith("?"):
            text = q + "  = ?"
        else:
            text = q
        base = 8 if len(q) <= 18 else 6 if len(q) <= 34 else 5 if len(q) <= 62 else 4
        sc = self.sc(base)
        width = self.card_w - 16 * u
        while sc > 2:                       # длинный вопрос — шрифт мельче, пока не влезет
            lines = wrap(text, sc, width)
            if (((len(lines) - 1) * LINE_U + 7) * sc <= self.card_h - 10 * u
                    and max(text_w(s, sc) for s in lines) <= width):
                break
            sc -= 1
        self.q_text.sc, self.q_text.width = sc, width
        self.q_text.x = self.W / 2 + self.qoff
        self.q_text.text = None
        self.q_text.set(text)

        if self.options is None:
            for b in self.opt_btns:
                b.hide()
            self.cv.itemconfig("numeric", state="normal")
            self.in_text.state = "normal"
            self.status.place(self.W / 2, 620 * S)
            for b in self.pad:
                b.show()
        else:
            self.status.place(self.W / 2, 530 * S)
            self.cv.itemconfig("numeric", state="hidden")
            self.in_text.state = "hidden"
            for b in self.pad:
                b.hide()
            for i, b in enumerate(self.opt_btns):
                if i < len(self.options):
                    b.show()
                    b.set_text("%d  %s" % (i + 1, self.options[i]))
                    b.style(fill=T.CARD, fg=T.TEXT, outline=T.INK,
                            hover=mix(T.CARD, "#FFFFFF", 0.12))
                else:
                    b.hide()

        left = self.need - self.streak
        self.progress.set("ОСТАЛОСЬ ПРАВИЛЬНЫХ ПОДРЯД: %d" % left)
        for i, d in enumerate(self.dots):
            pal = STAR_ON if i < self.streak else STAR_OFF
            for k in ("Y", "W"):
                self.cv.itemconfig(d + "_" + k, fill=pal[k])
        self.draw_input()

    def draw_input(self):
        if self.options is not None:
            return
        cur = "▌" if (self.caret and time.time() >= self.locked_until) else " "
        self.in_text.set(self.buf + cur, fill=T.TEXT if self.buf else T.DIM)

    def blink(self):
        if not self.alive:
            return
        self.caret = not self.caret
        self.draw_input()
        self.root.after(480, self.blink)

    def animate(self):
        if not self.alive:
            return
        u = self.u
        self.frame_n += 1
        el = int(time.time() - self.started)
        if self.timeout and el >= self.timeout and not self.solved:
            log("проверка: нет ответа за %d с" % self.timeout)
            self.alive = False
            close_window(self.root)
            return

        # Верх и низ экрана меняются в разных кадрах. Tk перерисовывает
        # прямоугольник, охватывающий все изменения кадра: заголовок сверху
        # плюс лента снизу — это весь экран, и звонок падал до 6–12 кадров в секунду.
        if self.frame_n % 2 == 0:
            self.phase += 0.18
            p = (math.sin(self.phase) + 1) / 2
            self.title.set(fill=mix(T.ACCENT, T.ACCENT_2, p))
            # будильники по бокам дребезжат, как настоящие
            d = u if (self.frame_n // 2) % 2 else -u
            self.cv.move(self.clk_l, d, 0)
            self.cv.move(self.clk_r, -d, 0)
            self._roll("stop", u)
            self.clock.set(datetime.now().strftime("%H:%M:%S"))
        else:
            self._roll("sbot", -u)
            self.f_left.set("ПОПЫТОК: %d" % self.attempts)
            if self.timeout:
                rest = max(0, self.timeout - el)
                self.f_mid.set("%s  %02d:%02d" % (self.label, rest // 60, rest % 60))
            else:
                self.f_mid.set("%s  %02d:%02d" % (self.label, el // 60, el % 60))

        if self.locked_until and self.frame_n % 2 == 0:
            rest = self.locked_until - time.time()
            if rest > 0:
                self.status.set("ПАУЗА  %.1f" % rest, T.WARN)
            else:
                self.locked_until = 0.0
                self.status.set("", T.MUTED)
        self.root.after(45, self.animate)

    def _roll(self, tag, d):
        off = self.stripe_off[tag] + abs(d)
        self.cv.move(tag, d, 0)
        if off >= self.period:
            self.cv.move(tag, -self.period if d > 0 else self.period, 0)
            off = 0
        self.stripe_off[tag] = off

    def keep_on_top(self):
        if not self.alive:
            return
        self.root.attributes("-topmost", True)
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass
        self.root.after(500, self.keep_on_top)

    # ─────────────── ввод ───────────────

    def press(self, key):
        if self.options is not None or time.time() < self.locked_until:
            return
        if key == "⌫":
            self.buf = self.buf[:-1]
        elif key == "C":
            self.buf = ""
        elif key == "±":
            self.buf = self.buf[1:] if self.buf.startswith("-") else "-" + self.buf
        elif key == "\r":
            self.check_numeric()
            return
        elif len(self.buf) < 9:
            self.buf += key
        self.draw_input()

    def choose(self, idx):
        if self.options is None or time.time() < self.locked_until:
            return
        if idx >= len(self.options):
            return
        self.judge(idx == self.answer)

    def on_key(self, e):
        if self.options is not None:
            if e.char.isdigit() and e.char != "0":
                self.choose(int(e.char) - 1)
            return "break"
        if e.keysym in ("Return", "KP_Enter"):
            self.press("\r")
        elif e.keysym == "BackSpace":
            self.press("⌫")
        elif e.keysym in ("Delete", "Escape"):
            self.press("C")
        elif e.char in ("-", "−") or e.keysym in ("minus", "KP_Subtract"):
            # не e.char in "-−": у Shift, стрелок и Ctrl символ пустой,
            # а "" in "-−" в Python — истина, и ответ менял знак сам
            self.press("±")
        elif e.char.isdigit():
            self.press(e.char)
        return "break"

    def check_numeric(self):
        raw = self.buf.strip().replace(",", ".")
        self.buf = ""
        self.draw_input()
        if not raw or raw == "-":
            return
        try:
            given = int(float(raw))
        except ValueError:
            self.status.set("Введи число.", T.WARN)
            return
        self.judge(given == self.answer)

    # ─────────────── проверка ───────────────

    def judge(self, correct):
        if not self.alive:
            return
        self.attempts += 1
        if correct:
            self.wrong_row = 0
            self.streak += 1
            self.flash_card(T.OK)
            if self.streak >= self.need:
                self.render()
                self.root.after(260, self.finish)
                return
            self.status.set(random.choice(PRAISE), T.OK)
        else:
            self.wrong_row += 1
            self.shake()
            self.flash_card(T.ACCENT)
            if self.cfg["reset"]:
                self.streak = 0
                msg = random.choice(TAUNTS)
            else:
                msg = "Мимо. Новая задача."
            if self.wrong_row >= 3:
                msg = "Три мимо подряд. Встань и умойся."
            self.status.set(msg, T.ACCENT)
            if self.cfg["penalty"]:
                self.locked_until = time.time() + 3.0

        self.question, self.answer, self.options = new_task(self.level, self.kinds)
        self.render()

    def flash_card(self, color):
        self.cv.itemconfig(self.card + "o", fill=color)
        self.root.after(420, lambda: self.alive and
                        self.cv.itemconfig(self.card + "o", fill=T.LINE))

    def shake(self, n=8, dx=None):
        """Карточка дёргается на пиксели и в конце встаёт ровно на место."""
        if not self.alive:
            return
        if dx is None:
            dx = 3 * self.u
        if n <= 0 or abs(dx) < 1:
            self.cv.move("qcard", -self.qoff, 0)
            self.q_text.x -= self.qoff
            self.qoff = 0
            return
        self.cv.move("qcard", dx, 0)
        self.q_text.x += dx
        self.qoff += dx
        self.root.after(26, lambda: self.shake(n - 1, -int(dx * 0.82)))

    def finish(self):
        self.solved = True
        self.siren.stop()
        secs = int(time.time() - self.started)
        log("звонок: задачи решены за %d с, попыток %d" % (secs, self.attempts))
        self.title.set(self.title_txt + "  ✓")
        self.status.set("Готово. %d с, попыток: %d" % (secs, self.attempts), T.OK)
        for b in self.opt_btns + self.pad:
            b.hide()

        def done():
            self.alive = False
            close_window(self.root)

        self.root.after(900, done)

    def run(self):
        self.root.mainloop()


# ═══════════════════════════ ПАНЕЛЬ УПРАВЛЕНИЯ ═══════════════════════════

class Setup:
    W, H = 620, 1368
    U = 4                                   # «пиксель» рамок, полос и звёзд

    def __init__(self, parent=None):
        self.cfg = load_cfg()
        self.saved = dict(self.cfg)
        self.parent = parent
        self.status = None
        self.busy = ""
        self.polling = False
        self.alive = True
        self.queue = queue.Queue()

        self.root = tk.Tk() if parent is None else tk.Toplevel(parent)
        if parent is None:
            keep_gc(self.root)
        self.root.title("Настройки будильника")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        # На невысоком экране окно режется по экрану, а содержимое крутится
        # колесом мыши — иначе нижние кнопки оказались бы за краем.
        self.view_h = min(self.H, max(400, sh - 60))
        self.root.geometry("%dx%d+%d+%d" % (self.W, self.view_h, (sw - self.W) // 2,
                                            max(0, (sh - self.view_h) // 2 - 20)))
        self.root.configure(bg=T.BG_BOT)

        self.cv = tk.Canvas(self.root, width=self.W, height=self.view_h,
                            highlightthickness=0, bg=T.BG_BOT,
                            scrollregion=(0, 0, self.W, self.H), yscrollincrement=40)
        self.cv.pack(fill="both", expand=True)
        if self.view_h < self.H:
            self.root.bind("<MouseWheel>", lambda e: self.cv.yview_scroll(
                -1 if e.delta > 0 else 1, "units"))
        paint_bg(self.cv, self.W, self.H, u=self.U, bands=8, stars=55)
        draggable(self.root, self.cv, "bg")

        self._titlebar()
        self._head()
        self._status_card()
        self._time_block()
        self._level_block()
        self._kind_block()
        self._sound_block()
        self._need_block()
        self._toggles()
        self._buttons()
        frame(self.cv, self.W, self.H, self.U)

        self.refresh()
        self.pump()
        self.poll_status()
        self.sparkle()

    def _titlebar(self):
        u = self.U
        px_rect(self.cv, 0, 0, self.W, 40, T.CARD, ("bg", "bar"))
        px_rect(self.cv, 0, 40, self.W, 40 + u, T.INK, ("bg", "bar"))
        sprite(self.cv, 26, 21, SPR_CLOCK, CLOCK_PAL, 2, tags=("bg",))
        PText(self.cv, 48, 21, "SSB-ALARM", 2, fill=T.MUTED, anchor="w", tags=("bg",))
        CBtn(self.cv, self.W - 28, 20, 32, 28, "✕", self._quit, u=2, sc=2,
             fill=T.ACCENT_D, hover=T.ACCENT, fg=T.TEXT)

    def _head(self):
        tsc = 5
        tw = text_w("БУДИЛЬНИК", tsc)
        left = self.W / 2 - (tw + 76) / 2
        sprite(self.cv, left + 30, 96, SPR_CLOCK, CLOCK_PAL, 4)
        PText(self.cv, left + 76, 98, "БУДИЛЬНИК", tsc, fill=T.TEXT, anchor="w",
              shadow=T.ACCENT)
        PText(self.cv, self.W / 2, 142, "который нельзя выключить лёжа", 2, fill=T.MUTED)

    def _section(self, y, label):
        PText(self.cv, 46, y, label, 2, fill=T.SKY, anchor="w")
        for x in range(46, self.W - 46, 4 * self.U):     # пунктир
            px_rect(self.cv, x, y + 14, x + 2 * self.U, y + 14 + self.U // 2, T.CARD_HI)

    def _status_card(self):
        u = self.U
        pbox(self.cv, 46, 166, self.W - 46, 294, u, fill=T.CARD)
        px_rect(self.cv, 68, 182, 88, 202, T.INK)
        self.s_dot = px_rect(self.cv, 72, 186, 84, 198, T.DIM)
        self.s_state = PText(self.cv, 100, 192, "", 3, fill=T.TEXT, anchor="w")
        self.s_next = PText(self.cv, 70, 224, "", 2, fill=T.MUTED, anchor="w")
        self.s_hint = PText(self.cv, 70, 244, "", 2, fill=T.WARN, anchor="nw",
                            width=self.W - 46 * 2 - 48)

    def _time_block(self):
        self._section(318, "ВРЕМЯ ПОДЪЁМА")
        cy = 416
        pbox(self.cv, self.W / 2 - 150, cy - 46, self.W / 2 + 150, cy + 46, self.U,
             fill=T.INK, border=T.CARD_HI, inset=True)
        self.hh_t = PText(self.cv, self.W / 2 - 70, cy, "00", 9, fill=T.TEXT)
        PText(self.cv, self.W / 2, cy, ":", 9, fill=T.DIM)
        self.mm_t = PText(self.cv, self.W / 2 + 70, cy, "00", 9, fill=T.TEXT)
        for dx, key, step in ((-70, "hh", 1), (70, "mm", 5)):
            CBtn(self.cv, self.W / 2 + dx, cy - 70, 64, 28, "▲",
                 lambda k=key, s=step: self._bump(k, s), u=3, sc=2,
                 fill=T.CARD, fg=T.MUTED)
            CBtn(self.cv, self.W / 2 + dx, cy + 66, 64, 28, "▼",
                 lambda k=key, s=step: self._bump(k, -s), u=3, sc=2,
                 fill=T.CARD, fg=T.MUTED)

    def _bump(self, key, d):
        self.cfg[key] = (self.cfg[key] + d) % (24 if key == "hh" else 60)
        self.refresh()

    def _row3(self, y, items, store, key):
        w, gap = 168, 12
        x0 = self.W / 2 - (w * 3 + gap * 2) / 2 + w / 2
        for i, (v, label) in enumerate(items):
            store[v] = CBtn(self.cv, x0 + i * (w + gap), y, w, 44, label,
                            lambda v=v: self._set(key, v), u=self.U, sc=2)

    def _level_block(self):
        self._section(518, "СЛОЖНОСТЬ")
        self.lvl_btns = {}
        self._row3(560, [(i, LEVEL_NAME[i]) for i in (1, 2, 3)], self.lvl_btns, "level")

    def _kind_block(self):
        self._section(604, "ТИП ЗАДАЧ")
        self.kind_btns = {}
        self._row3(646, [(k, KIND_NAME[k]) for k in ("math", "logic", "mix")],
                   self.kind_btns, "kinds")

    def _set(self, key, v):
        self.cfg[key] = v
        self.refresh()

    def _sound_block(self):
        self._section(690, "ЗВОНОК")
        self.snd_btns = {}
        self._row3(732, [(k, SOUND_NAME[k]) for k in ("melody", "t3", "beep")],
                   self.snd_btns, "sound")

    def _need_block(self):
        self._section(776, "ПРАВИЛЬНЫХ ОТВЕТОВ ПОДРЯД")
        self.need_t = PText(self.cv, self.W - 46, 776, "3", 2, fill=T.GOLD, anchor="e")
        cy = 822
        CBtn(self.cv, self.W / 2 - 214, cy, 52, 44, "−", lambda: self._bump_need(-1),
             u=self.U, sc=3, fill=T.CARD)
        CBtn(self.cv, self.W / 2 + 214, cy, 52, 44, "+", lambda: self._bump_need(1),
             u=self.U, sc=3, fill=T.CARD)
        self.need_cy = cy
        self.need_shown = None

    def _bump_need(self, d):
        self.cfg["need"] = max(1, min(12, self.cfg["need"] + d))
        self.refresh()

    def _draw_need(self):
        n = self.cfg["need"]
        if n == self.need_shown:
            return
        self.need_shown = n
        self.cv.delete("needstars")
        gap = 28
        x0 = self.W / 2 - gap * (n - 1) / 2
        for i in range(n):
            sprite(self.cv, x0 + i * gap, self.need_cy, SPR_STAR, STAR_ON, 2,
                   tags=("needstars",))

    def _toggles(self):
        self._section(866, "ОПЦИИ")
        w, y0, step = self.W - 92, 908, 42
        self.b_reset, self.b_pen, self.b_esc, self.b_bed = (
            CBtn(self.cv, self.W / 2, y0 + i * step, w, 34, "",
                 lambda k=k: self._flip(k), u=3, sc=2, align="left")
            for i, k in enumerate(("reset", "penalty", "escalate", "bedtime")))

    def _flip(self, key):
        self.cfg[key] = not self.cfg[key]
        self.refresh()

    def _buttons(self):
        u = self.U
        w, gap = 258, 12
        xl = self.W / 2 - (w + gap) / 2
        xr = self.W / 2 + (w + gap) / 2
        y1, y2, y3 = 1098, 1156, 1214
        self.b_save = CBtn(self.cv, xl, y1, w, 48, "СОХРАНИТЬ", self.do_save,
                           u=u, sc=2, fill=T.CARD_HI, fg=T.TEXT)
        self.b_on = CBtn(self.cv, xr, y1, w, 48, "ВКЛЮЧИТЬ", self.do_enable,
                         u=u, sc=2, fill=T.ACCENT, fg=T.TEXT)
        CBtn(self.cv, xl, y2, w, 48, "ВЫКЛЮЧИТЬ", self.do_disable,
             u=u, sc=2, fill=T.CARD, fg=T.MUTED)
        CBtn(self.cv, xr, y2, w, 48, "ТЕСТ ЗВОНКА", self.do_test,
             u=u, sc=2, fill=T.CARD, fg=T.MUTED)
        w3, g3 = 168, 12
        for dx, label, cmd in ((-(w3 + g3), "ЗАПИСЬ НОЧИ", self.do_record),
                               (0, "ОТЧЁТ", self.do_report),
                               (w3 + g3, "СВЕТ", self.do_light)):
            CBtn(self.cv, self.W / 2 + dx, y3, w3, 48, label, cmd,
                 u=u, sc=2, fill=T.CARD, fg=T.MUTED)
        CBtn(self.cv, self.W / 2, 1272, w * 2 + gap, 48, "РИТМ СНА", self.do_rhythm,
             u=u, sc=2, fill=T.CARD, fg=T.OK)
        self.msg_t = PText(self.cv, self.W / 2, 1308, "", 2, fill=T.MUTED,
                           anchor="n", width=self.W - 92)
        PText(self.cv, self.W / 2, self.H - 16,
              "звонит задача Планировщика «%s»" % TASK_NAME, 2, fill=T.DIM)

    def sparkle(self):
        if not self.alive:
            return
        twinkle(self.cv, n=2)
        self.root.after(400, self.sparkle)

    # ─────────────── фоновые операции ───────────────

    def run_async(self, label, fn, done, user=True):
        """Действия пользователя и фоновый опрос не мешают друг другу."""
        if user:
            if self.busy:
                self.set_msg("Подожди: " + self.busy.lower() + "…", T.WARN)
                return
            self.busy = label
            self.set_msg(label + "…", T.MUTED)
        else:
            if self.polling or self.busy:
                return
            self.polling = True

        def worker():
            try:
                res = fn()
            except Exception as e:                           # noqa: BLE001
                res = (False, str(e))
            self.queue.put((done, res, user))

        threading.Thread(target=worker, daemon=True).start()

    def pump(self):
        if not self.alive:
            return
        while True:
            try:
                done, res, user = self.queue.get_nowait()
            except queue.Empty:
                break
            if user:
                self.busy = ""
            else:
                self.polling = False
            done(res)
        self.root.after(100, self.pump)

    def poll_status(self):
        if not self.alive:
            return
        self.run_async("Проверяю", task_status, self._got_status, user=False)
        self.root.after(5000, self.poll_status)

    def _got_status(self, res):
        self.status = res
        self.refresh()

    def check_now(self):
        self.polling = False
        self.run_async("Проверяю", task_status, self._got_status, user=False)

    # ─────────────── действия ───────────────

    def set_msg(self, text, color=T.MUTED):
        self.msg_t.set(text, color)

    def do_save(self):
        if save_cfg(self.cfg):
            self.saved = dict(self.cfg)
            self.set_msg("Настройки сохранены в settings.json", T.OK)
        else:
            self.set_msg("Не удалось записать settings.json", T.ACCENT)
        self.refresh()

    def do_enable(self):
        if self.busy:                    # иначе отклонённое нажатие уже сохранило бы файл
            self.set_msg("Подожди: " + self.busy.lower() + "…", T.WARN)
            return
        cfg = dict(self.cfg)             # снимок: фон не должен видеть правки после нажатия
        if not save_cfg(cfg):
            self.set_msg("Не удалось записать settings.json — будильник не трогаю", T.ACCENT)
            return
        self.saved = dict(cfg)
        hh, mm = cfg["hh"], cfg["mm"]

        def work():
            ok, out = task_enable(hh, mm, cfg.get("plan"))
            if not ok:
                return ok, out
            notes, warn = [], False
            if cfg["bedtime"]:
                bh, bm = reminder_time(cfg)
                bok, bout = task_enable_bed_cfg(cfg)
                if bok:
                    notes.append("отбой %02d:%02d" % (bh, bm))
                else:
                    notes.append("напоминание не встало: " + (bout.splitlines() or [""])[0][:40])
                    warn = True
            elif not task_remove_bed()[0]:
                notes.append("старое напоминание не снялось")
                warn = True
            lc = load_lights()
            if lc["token"] and lc["device_id"]:
                lh, lm = light_time(hh, mm, lc["pre_minutes"])
                lok, lout = task_enable_light(hh, mm, lc["pre_minutes"])
                if lok:
                    notes.append("свет %02d:%02d" % (lh, lm))
                else:
                    notes.append("свет не встал: " + (lout.splitlines() or [""])[0][:40])
                    warn = True
            else:
                task_remove_light()
            return True, "  ·  ".join(notes), warn

        def done(res):
            ok, out = res[0], res[1]
            warn = len(res) > 2 and res[2]
            if ok:
                extra = ("  ·  " + out) if out else ""
                self.set_msg("Будильник на %02d:%02d%s" % (hh, mm, extra),
                             T.WARN if warn else T.OK)
            else:
                self.set_msg("Не вышло: " + out.splitlines()[0][:64], T.ACCENT)
            self.check_now()

        self.run_async("Ставлю задачу", work, done)

    def do_disable(self):
        if not (self.status or {}).get("exists"):
            self.set_msg("Задачи в Планировщике нет — выключать нечего", T.WARN)
            return
        def work():
            ok, out = task_disable()
            left = []                    # рассвет и напоминание без будильника не нужны
            if not task_remove_light()[0]:
                left.append("свет")
            if not task_remove_bed()[0]:
                left.append("напоминание")
            return ok, out, left

        def done(res):
            ok, out = res[0], res[1]
            left = res[2] if len(res) > 2 else []
            if ok and left:
                self.set_msg("Будильник отключён, но не снялось: " + ", ".join(left), T.ACCENT)
            elif ok:
                self.set_msg("Будильник, рассвет и напоминание отключены", T.WARN)
            else:
                self.set_msg("Не вышло: " + (out.splitlines() or [""])[0][:64], T.ACCENT)
            self.check_now()

        self.run_async("Отключаю", work, done)

    def do_test(self):
        if not (self.status or {}).get("exists"):
            self.set_msg("Сначала «ВКЛЮЧИТЬ» — тест запускает саму задачу", T.WARN)
            return

        def done(res):
            ok, out = res
            self.set_msg("Тест пошёл — гасится одним ответом" if ok
                         else "Не вышло: " + out.splitlines()[0][:64],
                         T.OK if ok else T.ACCENT)
            self.check_now()

        self.run_async("Запускаю тест", task_run_now, done)

    def do_record(self):
        """Запускает ночную запись отдельным процессом — панель можно закрыть."""
        exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(exe):
            exe = sys.executable
        try:
            subprocess.Popen([exe, SCRIPT, "--record"], cwd=HERE,
                             creationflags=NO_WINDOW)
            self.set_msg("Запись пошла — остановится сама в %02d:%02d"
                         % (self.cfg["hh"], self.cfg["mm"]), T.OK)
        except Exception as e:                               # noqa: BLE001
            self.set_msg("Не вышло: " + str(e)[:60], T.ACCENT)

    def do_light(self):
        """Открывает список устройств умного дома или подсказку про токен."""
        def work():
            return True, light_setup()

        def done(res):
            cfg = load_lights()
            if cfg["token"] and cfg["device_id"]:
                self.set_msg("Свет настроен: %s" % (cfg["device_name"]
                                                    or cfg["device_id"])[:40], T.OK)
            elif cfg["token"]:
                self.set_msg("Список устройств открыт — впиши device_id", T.WARN)
            else:
                self.set_msg("Открыл инструкцию: нужен токен в lights.json", T.WARN)

        self.run_async("Спрашиваю Яндекс", work, done)

    def do_rhythm(self):
        try:
            RhythmWindow(self.root, dict(self.cfg))
        except Exception as e:                               # noqa: BLE001
            log("ритм: " + traceback.format_exc())
            self.set_msg("Не открылось: " + str(e)[:60], T.ACCENT)

    def do_report(self):
        """Открывает самый свежий отчёт о ночи."""
        try:
            names = [n for n in os.listdir(REC_DIR)
                     if n.startswith("otchet_") and n.endswith(".txt")]
        except OSError:
            names = []
        if not names:
            self.set_msg("Отчётов пока нет — сначала «ЗАПИСЬ НОЧИ»", T.WARN)
            return
        newest = os.path.join(REC_DIR, sorted(names)[-1])
        try:
            os.startfile(newest)                             # noqa: S606
            self.set_msg("Открыл " + os.path.basename(newest), T.OK)
        except Exception as e:                               # noqa: BLE001
            self.set_msg("Не открылось: " + str(e)[:60], T.ACCENT)

    # ─────────────── перерисовка ───────────────

    def refresh(self):
        self.hh_t.set("%02d" % self.cfg["hh"])
        self.mm_t.set("%02d" % self.cfg["mm"])
        self.need_t.set(str(self.cfg["need"]))
        self._draw_need()

        for i, b in self.lvl_btns.items():
            self._pill(b, i == self.cfg["level"])
        for k, b in self.kind_btns.items():
            self._pill(b, k == self.cfg["kinds"])
        for k, b in self.snd_btns.items():
            self._pill(b, k == self.cfg["sound"])

        for btn, key, label in ((self.b_reset, "reset", "Ошибка обнуляет счётчик"),
                                (self.b_pen, "penalty", "Пауза 3 с после ошибки"),
                                (self.b_esc, "escalate",
                                 "Громкость нарастает за 4 минуты"),
                                (self.b_bed, "bedtime",
                                 "Напоминание об отбое в %02d:%02d"
                                 % reminder_time(self.cfg))):
            on = self.cfg[key]
            btn.set_text(("☑  " if on else "☐  ") + label)
            if on:
                btn.style(fill=T.CARD_HI, fg=T.TEXT, outline=T.INK,
                          hover=mix(T.CARD_HI, "#FFFFFF", 0.1))
            else:
                btn.style(fill=T.CARD, fg=T.DIM, outline=T.INK,
                          hover=mix(T.CARD, "#FFFFFF", 0.1))

        self.b_save.set_text("СОХРАНИТЬ" if self.cfg == self.saved else "СОХРАНИТЬ ●")
        self._render_status()

    @staticmethod
    def _pill(btn, active):
        if active:
            btn.style(fill=T.ACCENT_D, hover=mix(T.ACCENT_D, T.ACCENT, 0.3),
                      fg=T.TEXT, outline=T.ACCENT)
        else:
            btn.style(fill=T.CARD, hover=mix(T.CARD, "#FFFFFF", 0.12),
                      fg=T.MUTED, outline=T.INK)

    def _render_status(self):
        st = self.status
        hint = ""
        if st is None:
            dot, state, sub = T.DIM, "ПРОВЕРЯЮ…", ""
        elif st.get("error"):
            dot, state = T.WARN, "ПЛАНИРОВЩИК НЕ ОТВЕЧАЕТ"
            sub = st["error"].splitlines()[0][:40]
        elif not st.get("exists"):
            dot, state = T.DIM, "НЕ УСТАНОВЛЕН"
            sub = "Задачи в Планировщике нет"
            hint = "Нажми «ВКЛЮЧИТЬ» — будильник встанет на %02d:%02d" % (
                self.cfg["hh"], self.cfg["mm"])
        elif st.get("state") == "Disabled":
            dot, state = T.WARN, "ОТКЛЮЧЁН"
            sub = "Задача есть, но выключена — звонить не будет"
            hint = "«ВКЛЮЧИТЬ» поднимет её обратно"
        else:
            dot, state = T.OK, "ВКЛЮЧЁН"
            sub = "Звонок: %s" % (st.get("next") or "—")
            if st.get("last"):
                sub += "   был: %s" % st["last"]
            if st.get("hh", -1) >= 0 and (st["hh"], st["mm"]) != (self.cfg["hh"], self.cfg["mm"]):
                hint = "В задаче стоит %02d:%02d — «ВКЛЮЧИТЬ» применит %02d:%02d" % (
                    st["hh"], st["mm"], self.cfg["hh"], self.cfg["mm"])
            elif not st.get("wake", True):
                hint = "Задача не будит компьютер из сна"

        self.cv.itemconfig(self.s_dot, fill=dot)
        self.s_state.set(state, T.TEXT if dot != T.DIM else T.MUTED)
        self.s_next.set(sub)
        self.s_hint.set(hint, T.WARN)

    def _quit(self):
        self.alive = False
        if self.parent is None:
            close_window(self.root)
        else:
            self.root.destroy()          # close_window снял бы и таймеры главной панели

    def run(self):
        self.root.bind("<Escape>", lambda e: self._quit())
        self.root.after(60, lambda: (self.root.lift(), self.root.focus_force()))
        if self.parent is None:
            self.root.mainloop()
        else:
            self.root.wait_window()





# ═══════════════════════ НАПОМИНАНИЕ ОБ ОТБОЕ ═══════════════════════
# Отбой считается от подъёма, а не задаётся отдельно: иначе эти два
# времени разъезжаются. По умолчанию 8 ч 15 мин до подъёма — это время
# лечь, если перед сном ещё полчаса смотришь что-то.

BED_TASK = "Будильник-отбой"
LATENCY_MIN = 12          # ориентир: ночью взрослые засыпают обычно за 10–20 мин


def bed_time(hh, mm, before_min):
    t = datetime(2000, 1, 2, hh, mm) - timedelta(minutes=before_min)
    return t.hour, t.minute


def reminder_time(cfg):
    """Когда напоминать. Если задано bed_fixed ("21:30") — по нему: якорь
    тогда вечерняя волна сонливости, и перенос подъёма не должен утаскивать
    напоминание следом. Иначе — от подъёма, как раньше."""
    fixed = (cfg.get("bed_fixed") or "").strip()
    if fixed:
        try:
            h, m = (int(x) for x in fixed.split(":"))
            if 0 <= h < 24 and 0 <= m < 60:
                return h, m
        except ValueError:
            pass
    return bed_time(cfg["hh"], cfg["mm"], cfg["bed_before_min"])


def next_wake(cfg, now=None):
    """Ближайший подъём. В settings.json может быть "plan": {"2026-10-18": "12:30", …} —
    лестница по дням; на даты вне плана берётся обычное hh:mm."""
    now = now or datetime.now()
    plan = cfg.get("plan") or {}
    for d in range(3):
        day = (now + timedelta(days=d)).date()
        hh, mm = cfg["hh"], cfg["mm"]
        try:
            h, m = (int(x) for x in str(plan[day.isoformat()]).split(":"))
            if 0 <= h < 24 and 0 <= m < 60:
                hh, mm = h, m
        except (KeyError, ValueError):
            pass
        wake = datetime(day.year, day.month, day.day, hh, mm)
        if wake > now:               # сравниваем с «сейчас», а не с моментом засыпания:
            return wake              # иначе за 5 минут до звонка выходило «23 ч 53 мин»
    return now


def sleep_hours_if_now(cfg, now=None):
    """Сколько выйдет сна, если уснуть прямо сейчас."""
    now = now or datetime.now()
    asleep = now + timedelta(minutes=LATENCY_MIN)
    return max(0.0, (next_wake(cfg, now) - asleep).total_seconds() / 3600.0)


_PS_BED = """
$n = '<<TASK>>'
$a = New-ScheduledTaskAction -Execute '<<EXE>>' -Argument '"<<SCRIPT>>" --bedtime' -WorkingDirectory '<<DIR>>'
<<TRIGGERS>>
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $n -Action $a -Trigger $g -Settings $s -Principal $p `
        -Description 'Напоминание, что пора гасить экран.' -Force | Out-Null
Enable-ScheduledTask -TaskName $n | Out-Null
'OK'
"""


def task_enable_bed(hh, mm, before_min):
    return task_enable_bed_at(*bed_time(hh, mm, before_min))


def task_enable_bed_cfg(cfg):
    """Напоминание вслед за подъёмом — с учётом лестницы по датам."""
    fixed = hhmm_min(cfg.get("bed_fixed"))
    if fixed is not None:
        return task_enable_bed_at(*divmod(fixed, 60))
    before = timedelta(minutes=cfg["bed_before_min"])
    once, daily = wake_schedule(cfg["hh"], cfg["mm"], cfg.get("plan"))
    now = datetime.now()
    return _bed_register(triggers_ps([w - before for w in once if w - before > now],
                                     daily - before, end_after_h=2))


def task_enable_bed_at(bh, bm):
    return _bed_register(triggers_ps([], datetime.now().replace(
        hour=bh, minute=bm, second=0, microsecond=0)))


def _bed_register(triggers):
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    script = (_PS_BED
              .replace("<<TASK>>", psq(BED_TASK))
              .replace("<<EXE>>", psq(exe))
              .replace("<<SCRIPT>>", psq(SCRIPT))
              .replace("<<DIR>>", psq(HERE))
              .replace("<<TRIGGERS>>", triggers))
    return run_ps(script)


def task_remove_bed():
    return run_ps("Unregister-ScheduledTask -TaskName '%s' -Confirm:$false "
                  "-ErrorAction SilentlyContinue; 'OK'" % psq(BED_TASK))


class BedtimeWindow:
    """Мягкий толчок, а не будильник: окно без звука. Звук здесь вреден —
    если человек уже уснул раньше, сигнал его бы разбудил."""

    W, H = 580, 440
    SNOOZE_MIN = 15
    CHECK_SEC = 30                # как часто перепроверять колонку, пока окно открыто
    SKY = "#14275A"

    def __init__(self):
        self.cfg = load_cfg()
        self.alive = True
        self.snoozes = 0

        self.root = tk.Tk()
        keep_gc(self.root)
        self.root.title("Пора спать")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (self.W, self.H, (sw - self.W) // 2,
                                            (sh - self.H) // 3))
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bg=T.BG_BOT)
        self.cv.pack(fill="both", expand=True)
        paint_bg(self.cv, self.W, self.H, top=self.SKY, u=4, bands=6, stars=45, seed=3,
                 star_h=0.3)
        draggable(self.root, self.cv, "bg")

        tw = text_w("ПОРА СПАТЬ", 4)
        left = self.W / 2 - (tw + 64) / 2
        sprite(self.cv, left + 22, 62, disc(7, (-4, -3)), {"Y": T.GOLD, "K": T.INK}, 3,
               tags=("bg",))
        PText(self.cv, left + 64, 64, "ПОРА СПАТЬ", 4, fill="#8FB6FF", anchor="w",
              shadow=T.INK, tags=("bg",))
        PText(self.cv, self.W / 2, 104, "гаси экран", 2, fill=T.MUTED, tags=("bg",))
        self.t_hours = PText(self.cv, self.W / 2, 156, "", 7, fill=T.OK, shadow=T.INK)
        self.t_cap = PText(self.cv, self.W / 2, 212, "", 2, fill=T.MUTED, width=self.W - 60)
        self.t_note = PText(self.cv, self.W / 2, 262, "", 2, fill=T.DIM, width=self.W - 60)
        self.t_spk = PText(self.cv, self.W / 2, 314, "проверяю колонку…", 2, fill=T.MUTED,
                           width=self.W - 60)
        self.spk_state = None
        CBtn(self.cv, self.W / 2 - 128, 382, 240, 48, "ИДУ СПАТЬ", self.go,
             u=4, sc=2, fill="#2B4C9B", fg=T.TEXT)
        CBtn(self.cv, self.W / 2 + 128, 382, 240, 48,
             "ЕЩЁ %d МИНУТ" % self.SNOOZE_MIN, self.snooze,
             u=4, sc=2, fill=T.CARD, fg=T.MUTED)
        frame(self.cv, self.W, self.H, 4, color="#2B4C9B")

        self.tick()
        self.sparkle()
        self.root.after(150, self.check_speaker)

    def sparkle(self):
        if not self.alive:
            return
        twinkle(self.cv, self.SKY, n=2)
        self.root.after(500, self.sparkle)

    def check_speaker(self):
        """Колонку проверяем, пока человек не спит: подключить её во сне он не сможет."""
        if not self.alive:
            return
        try:
            level, text = speaker_check(self.cfg)
        except Exception as e:                               # noqa: BLE001
            level, text = "warn", "колонку проверить не удалось: %s" % e
        icon, col = {"ok": ("✓", T.OK), "warn": ("!", T.WARN), "bad": ("✕", T.ACCENT)}[level]
        self.t_spk.set("%s  %s" % (icon, text), col)
        if (level, text) != self.spk_state:
            log("проверка колонки перед сном: %s — %s" % (level, text))
            self.spk_state = (level, text)
        self.root.after(self.CHECK_SEC * 1000, self.check_speaker)

    def tick(self):
        if not self.alive:
            return
        # Перечитываем настройки: окно может висеть часами, а подъём за это время
        # переставили — иначе показывает устаревшее (было «подъём в 11:00» при 13:45).
        self.cfg = load_cfg()
        h = sleep_hours_if_now(self.cfg)
        if h < 7:
            col, note = T.ACCENT, "это уже меньше семи часов — ложись сейчас"
        elif datetime.now() < next_wake(self.cfg) - timedelta(hours=8):
            # то же правило, что в главном окне: ложиться не раньше подъёма − 8 ч
            col, note = T.WARN, "рано: ложиться не раньше %s, даже если клонит" % (
                (next_wake(self.cfg) - timedelta(hours=8)).strftime("%H:%M"))
        else:
            col, note = T.OK, "окно сна открыто — ложись"
        self.t_hours.set("%dч %02dм" % (int(h), int(round((h % 1) * 60))), col)
        self.t_cap.set("столько поспишь, если уснёшь сейчас "
                       "(подъём в %s)" % next_wake(self.cfg).strftime("%H:%M"))
        self.t_note.set(note)
        self.root.after(10000, self.tick)

    def snooze(self):
        self.snoozes += 1
        self.root.withdraw()
        self.root.after(self.SNOOZE_MIN * 60 * 1000, self.back)

    def back(self):
        if not self.alive:
            return
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.lift()

    def go(self):
        now = datetime.now()
        diary_upsert(next_wake(self.cfg, now).date(), lay=now.strftime("%H:%M"))
        self.alive = False
        close_window(self.root)

    def run(self):
        self.root.bind("<Escape>", lambda e: self.go())
        self.root.after(60, lambda: (self.root.lift(), self.root.focus_force()))
        self.root.mainloop()


# ═══════════════════ СВЕТ: УМНЫЙ ДОМ ЯНДЕКСА ═══════════════════
# Рассветная засветка перед подъёмом. Gabel и соавторы (17 чел., после ограничения
# сна): рассвет за 30 мин до подъёма улучшал самочувствие, настроение и
# когнитивные показатели по сравнению с тусклым светом и с синим светом через
# 2 ч после подъёма. Сравнения со светом в момент подъёма там не было. Если лампа умеет яркость —
# делаем настоящий плавный рассвет, если это простой выключатель —
# просто включаем заранее.
#
# Токен лежит в lights.json рядом со скриптом и в запросы уходит
# заголовком Authorization, а не в адресе.

IOT_BASE = "https://api.iot.yandex.net"
LIGHTS_CONF = os.path.join(HERE, "lights.json")
LIGHT_TASK = "Будильник-свет"
LIGHT_DEFAULTS = {"token": "", "device_id": "", "device_name": "",
                  "pre_minutes": 30, "dawn": True}


def load_lights():
    cfg = dict(LIGHT_DEFAULTS)
    try:
        with io.open(LIGHTS_CONF, encoding="utf-8") as f:
            saved = json.load(f)
        for k in cfg:
            if k in saved and type(saved[k]) is type(cfg[k]):
                cfg[k] = saved[k]
    except Exception:
        pass
    cfg["pre_minutes"] = max(5, min(90, cfg["pre_minutes"]))
    return cfg


def save_lights(cfg):
    try:
        with io.open(LIGHTS_CONF, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True, ""
    except OSError as e:
        return False, str(e)


def iot(method, path, token, body=None, timeout=15):
    """Запрос к API умного дома. Возвращает (успех, данные-или-ошибка)."""
    import urllib.error
    import urllib.request
    if not token:
        return False, {"error": "токен не задан"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(IOT_BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8") or "{}"
        return True, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:                                    # noqa: BLE001
            detail = ""
        hint = {401: "токен недействителен или истёк",
                403: "у токена нет прав iot:control",
                404: "устройство не найдено"}.get(e.code, "")
        return False, {"error": "HTTP %d %s" % (e.code, hint), "detail": detail}
    except Exception as e:                                   # noqa: BLE001
        return False, {"error": str(e)}


def iot_devices(token):
    ok, data = iot("GET", "/v1.0/user/info", token)
    if not ok:
        return False, data
    return True, data.get("devices", [])


def device_dims(dev):
    """Умеет ли устройство плавную яркость."""
    for cap in dev.get("capabilities", []):
        if cap.get("type") == "devices.capabilities.range":
            params = cap.get("parameters") or {}
            if params.get("instance") == "brightness":
                return True
    return False


def iot_on_off(token, device_id, value):
    body = {"devices": [{"id": device_id, "actions": [
        {"type": "devices.capabilities.on_off",
         "state": {"instance": "on", "value": bool(value)}}]}]}
    return iot("POST", "/v1.0/devices/actions", token, body)


def iot_brightness(token, device_id, percent):
    body = {"devices": [{"id": device_id, "actions": [
        {"type": "devices.capabilities.range",
         "state": {"instance": "brightness", "value": float(percent)}}]}]}
    return iot("POST", "/v1.0/devices/actions", token, body)


def action_failed(data):
    """Вытаскивает из ответа отказ, даже когда HTTP вернул 200."""
    bad = []
    for dev in (data or {}).get("devices", []):
        for cap in dev.get("capabilities", []):
            res = (cap.get("state") or {}).get("action_result") or {}
            if res.get("status") and res["status"] != "DONE":
                bad.append(res.get("error_message") or res.get("error_code")
                           or res["status"])
    return bad


def run_dawn(log=print):
    """Плавный рассвет до подъёма: яркость от 1% до 100%."""
    cfg = load_lights()
    if not cfg["token"] or not cfg["device_id"]:
        log("свет не настроен — заполни lights.json")
        return False
    token, dev = cfg["token"], cfg["device_id"]
    minutes = cfg["pre_minutes"]

    dimmable = False
    ok, devices = iot_devices(token)
    if ok:
        for d in devices:
            if d.get("id") == dev:
                dimmable = device_dims(d)
                break

    smooth = dimmable and cfg["dawn"]
    if smooth:
        # Яркость 1% и включение одним запросом. Если сначала включить,
        # лампа вспыхнет на последней запомненной яркости — посреди тёмной
        # комнаты, ровно то, от чего рассвет должен избавить.
        ok, data = iot("POST", "/v1.0/devices/actions", token, {"devices": [{
            "id": dev, "actions": [
                {"type": "devices.capabilities.range",
                 "state": {"instance": "brightness", "value": 1.0}},
                {"type": "devices.capabilities.on_off",
                 "state": {"instance": "on", "value": True}}]}]})
    else:
        ok, data = iot_on_off(token, dev, True)
    if not ok:
        log("не включился: %s" % data.get("error"))
        return False
    bad = action_failed(data)
    if bad:
        log("устройство отказало: %s" % "; ".join(str(b) for b in bad))
        return False

    if not smooth:
        log("свет включён (без плавности)")
        return True

    steps = max(1, int(minutes * 60 / 20))          # шаг раз в 20 секунд
    t0 = time.time()
    failed = 0
    for k in range(1, steps + 1):                   # 1% уже выставлен
        # ждём до отметки от старта, а не «20 с после прошлого запроса»:
        # иначе время сетевых запросов копится и к будильнику не успеваем
        delay = t0 + 20.0 * k - time.time()
        if delay > 0:
            time.sleep(delay)
        pct = 1 + 99.0 * k / steps
        ok, data = iot_brightness(token, dev, round(pct))
        if not ok or action_failed(data):
            failed += 1
    if failed:
        # к подъёму свет обязан быть полным — последний шаг пробуем ещё раз
        ok, data = iot_brightness(token, dev, 100)
        if not ok or action_failed(data):
            log("рассвет не довёлся до 100%%: ошибок %d из %d шагов" % (failed, steps))
            return False
    log("рассвет завершён")
    return True


# ── задача Планировщика для света ──

_PS_LIGHT = """
$n = '<<TASK>>'
$a = New-ScheduledTaskAction -Execute '<<EXE>>' -Argument '"<<SCRIPT>>" --light dawn' -WorkingDirectory '<<DIR>>'
$g = New-ScheduledTaskTrigger -Daily -At ([datetime]'<<TIME>>')
$s = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $n -Action $a -Trigger $g -Settings $s -Principal $p `
        -Description 'Рассвет перед будильником.' -Force | Out-Null
Enable-ScheduledTask -TaskName $n | Out-Null
'OK'
"""


def light_time(hh, mm, pre):
    """Во сколько включать свет, чтобы к подъёму он уже горел."""
    t = datetime(2000, 1, 1, hh, mm) - timedelta(minutes=pre)
    return t.hour, t.minute


def task_enable_light(hh, mm, pre):
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    lh, lm = light_time(hh, mm, pre)
    script = (_PS_LIGHT
              .replace("<<TASK>>", psq(LIGHT_TASK))
              .replace("<<EXE>>", psq(exe))
              .replace("<<SCRIPT>>", psq(SCRIPT))
              .replace("<<DIR>>", psq(HERE))
              .replace("<<TIME>>", "%02d:%02d" % (lh, lm)))
    return run_ps(script)


def task_remove_light():
    return run_ps("Unregister-ScheduledTask -TaskName '%s' -Confirm:$false "
                  "-ErrorAction SilentlyContinue; 'OK'" % psq(LIGHT_TASK))


HOWTO_TOKEN = """Свет ещё не настроен. Токен вписывается сюда:
  %s

Как его получить (делаешь ты, токен никуда кроме этого файла не уходит):

1. Зайти на oauth.yandex.ru и создать приложение.
2. Платформа — «Веб-сервисы».
   Redirect URI: https://oauth.yandex.ru/verification_codes
3. Права (scopes): iot:view и iot:control
4. Открыть в браузере, подставив свой Client ID:
   https://oauth.yandex.ru/authorize?response_type=token&client_id=CLIENT_ID
5. После подтверждения скопировать значение access_token
   из адресной строки и вставить его в поле "token" в lights.json.

Потом снова запустить:  pythonw ssb_alarm.py --light setup
— и здесь появится список устройств с их id.
"""


def light_setup():
    """Выгружает список устройств в файл и открывает его — чтобы было
    откуда скопировать id в lights.json."""
    cfg = load_lights()
    if not cfg["token"]:
        save_lights(cfg)                      # создаём заготовку файла
        text = HOWTO_TOKEN % LIGHTS_CONF
    else:
        ok, devices = iot_devices(cfg["token"])
        if not ok:
            text = "Не вышло получить список устройств:\n%s\n%s" % (
                devices.get("error", ""), devices.get("detail", ""))
        elif not devices:
            text = "Устройств в умном доме не найдено."
        else:
            rows = ["Устройства в твоём умном доме.",
                    'Скопируй нужный id в поле "device_id" файла:',
                    "  %s" % LIGHTS_CONF, "", "=" * 70]
            for d in devices:
                rows.append("имя:      %s" % d.get("name", "—"))
                rows.append("тип:      %s" % d.get("type", "—"))
                rows.append("яркость:  %s" % ("да" if device_dims(d) else "нет"))
                rows.append("id:       %s" % d.get("id", "—"))
                rows.append("-" * 70)
            text = "\n".join(rows)
    path = os.path.join(HERE, "ustroystva.txt")
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(text)
        os.startfile(path)                                   # noqa: S606
    except Exception:                                        # noqa: BLE001
        pass
    return text


def light_task_exists():
    ok, out = run_ps("if (Get-ScheduledTask -TaskName '%s' -ErrorAction "
                     "SilentlyContinue) { 'yes' } else { 'no' }" % psq(LIGHT_TASK))
    return ok and out.strip() == "yes"


# ═══════════════════════ ЗАПИСЬ НОЧИ (микрофон) ═══════════════════════
# Пишем звук кусками через MCI (winmm, только ctypes — никаких библиотек),
# считаем громкость кадрами по 0,25 с и ищем в огибающей шумные участки
# и паузы внутри них. Это НЕ диагностика апноэ — см. дисклеймер в отчёте.

REC_DIR = os.path.join(HERE, "records")
CHUNK_SEC = 60            # длина одного куска записи
FRAME_SEC = 0.25          # шаг огибающей
REC_RATE = 8000           # для храпа и дыхания хватает с запасом
PAUSE_SEC = 10.0          # тишина такой длины внутри храпа — кандидат
MAX_CLIPS = 12


def mci(cmd):
    """Одна команда MCI. Возвращает (код ошибки, ответ)."""
    from ctypes import windll, create_unicode_buffer
    buf = create_unicode_buffer(512)
    err = windll.winmm.mciSendStringW(cmd, buf, 512, 0)
    return err, buf.value


def mci_error(code):
    from ctypes import windll, create_unicode_buffer
    buf = create_unicode_buffer(256)
    windll.winmm.mciGetErrorStringW(code, buf, 256)
    return buf.value or ("код %d" % code)


def wav_envelope(path):
    """Громкость кадрами по FRAME_SEC. Модуль audioop в Python 3.13+ удалён,
    поэтому считаем вручную."""
    import array
    with wave.open(path, "rb") as w:
        rate, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        width = w.getsampwidth()
        raw = w.readframes(n)
    if width == 1:
        # 8-битный wav беззнаковый, середина 128 — приводим к шкале 16 бит
        data = array.array("h", ((b - 128) * 256 for b in raw))
    elif width == 2:
        data = array.array("h")
        data.frombytes(raw[:len(raw) // 2 * 2])
    else:
        raise ValueError("неподдерживаемая разрядность: %d бит" % (width * 8))
    if ch > 1:
        data = data[::ch]
    step = max(1, int(rate * FRAME_SEC))
    out = []
    for i in range(0, len(data) - step + 1, step):
        acc = 0
        for v in data[i:i + step]:
            acc += v * v
        out.append(int((acc / float(step)) ** 0.5))
    return out


def percentile(values, q):
    if not values:
        return 0
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(len(v) * q)))]


def analyse(env):
    """Из огибающей достаём события, шумные участки и паузы внутри них."""
    floor = max(percentile(env, 0.20), 40)
    thr = max(floor * 3, 150)
    fps = 1.0 / FRAME_SEC

    runs = []      # все громкие отрезки любой длины — любой из них рвёт тишину
    events = []    # только короткие (0,25–5 с): храп, вдохи — по ним шумность и вдох
    i = 0
    while i < len(env):
        if env[i] > thr:
            j, peak = i, 0
            while j < len(env) and env[j] > thr:
                peak = max(peak, env[j])
                j += 1
            runs.append((i, j, peak))
            if 0.25 <= (j - i) / fps <= 5.0:
                events.append((i, j, peak))
            i = j
        else:
            i += 1

    per_min = {}
    for a, _b, _pk in events:
        mn = int(a / fps / 60)
        per_min[mn] = per_min.get(mn, 0) + 1
    noisy = set(mn for mn, c in per_min.items() if c >= 5)

    peaks = sorted(pk for _a, _b, pk in events)
    loud_mark = peaks[int(len(peaks) * 0.75)] if peaks else 0

    pauses = []
    for k in range(len(runs) - 1):  # паузы — между ЛЮБЫМИ громкими отрезками
        end = runs[k][1]
        nxt_start, _ne, nxt_peak = runs[k + 1]
        gap = (nxt_start - end) / fps
        if gap < PAUSE_SEC:
            continue
        mn = int(end / fps / 60)
        if mn not in noisy and (mn + 1) not in noisy:
            continue          # тишина вне шумного участка — просто тихо спал
        pauses.append({"at": end / fps, "len": gap, "gasp": nxt_peak >= loud_mark})
    return {"floor": floor, "thr": thr, "events": events,
            "noisy_minutes": sorted(noisy), "pauses": pauses}


def hhmmss(sec):
    sec = int(sec)
    return "%d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


def build_report(started, env, res, clips, clock=None):
    if clock is None:
        def clock(sec):
            return started + timedelta(seconds=sec)
    total = len(env) * FRAME_SEC
    minutes = max(1, int(total / 60))
    noisy = len(res["noisy_minutes"])
    deep = [p for p in res["pauses"] if p["gasp"]]
    out = []
    add = out.append
    add("ОТЧЁТ О НОЧНОЙ ЗАПИСИ")
    add("=" * 60)
    add("Начало записи:       %s" % started.strftime("%d.%m.%Y  %H:%M"))
    add("Длительность:        %s" % hhmmss(total))
    add("")
    add("Звуковых эпизодов:   %d   (храп, дыхание, движения, шум)"
        % len(res["events"]))
    add("Шумных минут:        %d из %d   (%.0f%% ночи)"
        % (noisy, minutes, 100.0 * noisy / minutes))
    add("")
    add("ПАУЗЫ ВНУТРИ ШУМНЫХ УЧАСТКОВ (тишина от %d секунд)" % int(PAUSE_SEC))
    add("Всего:                         %d" % len(res["pauses"]))
    add("Из них с громким вдохом после: %d" % len(deep))
    if res["pauses"]:
        longest = max(res["pauses"], key=lambda x: x["len"])
        add("Самая длинная:                 %.0f с, в %s"
            % (longest["len"], clock(longest["at"]).strftime("%H:%M:%S")))
        add("")
        add("   время по часам      длина    громкий вдох после")
        for pz in res["pauses"][:15]:
            add("   %-18s  %5.0f с    %s"
                % (clock(pz["at"]).strftime("%H:%M:%S"), pz["len"],
                   "да" if pz["gasp"] else "нет"))
        if len(res["pauses"]) > 15:
            add("   ... и ещё %d" % (len(res["pauses"]) - 15))
    add("")
    if clips:
        add("СОХРАНЁННЫЕ ФРАГМЕНТЫ (по 8 секунд вокруг самых длинных пауз):")
        for c in clips:
            add("   " + c)
        add("")
    add("=" * 60)
    add("ЭТО НЕ ДИАГНОЗ И НЕ ЗАМЕНА ОБСЛЕДОВАНИЮ.")
    add("")
    add("Микрофон слышит только звук. Апноэ определяют по остановкам")
    add("дыхания и падению кислорода в крови — этого микрофон не видит.")
    add("Тихое ровное дыхание он считает паузой, а апноэ без храпа может")
    add("не заметить вовсе. Для сравнения: один проверенный акустический")
    add("алгоритм (SleepCheck) находил апноэ средней и тяжёлой степени")
    add("с чувствительностью 86% и специфичностью 83% — и это обученная")
    add("модель, а не подсчёт громкости, как здесь. Точность этого отчёта")
    add("неизвестна. Отсутствие пауз в нём не исключает апноэ.")
    add("")
    add("Отчёт годится ровно для одного: решить, идти ли к врачу.")
    add("Много пауз с громким вдохом после — повод записаться к сомнологу")
    add("на полисомнографию или домашний респираторный мониторинг.")
    return "\n".join(out)


class Recorder:
    """Пишет ночь кусками, в конце собирает отчёт."""

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.stop_flag = threading.Event()
        self.env = []
        self.chunks = []
        self.chunk_frames = []      # сколько кадров огибающей дал каждый кусок
        self.chunk_starts = []      # когда по часам начался каждый кусок
        self.started = datetime.now()
        self.err = None
        self.report_path = None
        # своя папка на каждую запись: две записи сразу не затрут куски друг друга
        self.tmp_dir = os.path.join(REC_DIR, "tmp", "%s_%d" % (
            self.started.strftime("%Y%m%d_%H%M%S"), os.getpid()))

    def locate(self, sec):
        """Секунда огибающей -> (номер куска, смещение внутри него).
        Куски на деле короче заявленных, а между ними есть зазоры, поэтому
        делить на CHUNK_SEC нельзя — к утру набегает несколько минут."""
        frame = int(sec / FRAME_SEC)
        acc = 0
        for i, n in enumerate(self.chunk_frames):
            if frame < acc + n:
                return i, (frame - acc) * FRAME_SEC
            acc += n
        last = len(self.chunk_frames) - 1
        return last, self.chunk_frames[last] * FRAME_SEC if last >= 0 else 0.0

    def wall(self, sec):
        """Секунда огибающей -> время по часам."""
        if not self.chunk_starts:
            return self.started + timedelta(seconds=sec)
        i, off = self.locate(sec)
        return self.chunk_starts[i] + timedelta(seconds=off)

    def stop(self):
        self.stop_flag.set()

    def alarm_dt(self):
        now = datetime.now()
        tgt = now.replace(hour=self.cfg["hh"], minute=self.cfg["mm"],
                          second=0, microsecond=0)
        if tgt <= now:
            tgt += timedelta(days=1)
        return tgt

    def run(self):
        if sys.platform != "win32":
            self.err = "Запись сделана через winmm — только Windows"
            self.log("error")
            return
        try:
            os.makedirs(self.tmp_dir, exist_ok=True)
        except OSError as e:
            self.err = str(e)
            self.log("error")
            return

        # Минутой раньше будильника: иначе последний кусок запишет сирену.
        stop_at = self.alarm_dt() - timedelta(minutes=1)
        idx = 0
        while not self.stop_flag.is_set() and datetime.now() < stop_at:
            path = os.path.join(self.tmp_dir, "chunk_%05d.wav" % idx)
            began = datetime.now()
            if not self.record_chunk(path, stop_at):
                break
            try:
                env = wav_envelope(path)
            except Exception as e:                           # noqa: BLE001
                self.err = "разбор куска: %s" % e
                try:
                    os.remove(path)        # сырой звук не оставляем, как и обещано
                except OSError:
                    pass
                break
            self.chunks.append(path)
            self.chunk_frames.append(len(env))
            self.chunk_starts.append(began)
            self.env.extend(env)
            idx += 1
            self.log("tick")
        self.finish()

    def record_chunk(self, path, stop_at=None):
        err, _ = mci("open new type waveaudio alias rec")
        if err:
            self.err = "микрофон недоступен: " + mci_error(err)
            return False
        try:
            # Порядок важен: без "format tag pcm" драйвер игнорирует
            # остальные set и пишет в своём формате (ловилось 11025/8 бит).
            for cmd in ("set rec time format ms",
                        "set rec format tag pcm",
                        "set rec channels 1",
                        "set rec bitspersample 16",
                        "set rec samplespersec %d" % REC_RATE,
                        "set rec alignment 2",
                        "set rec bytespersec %d" % (REC_RATE * 2)):
                mci(cmd)
            err, _ = mci("record rec")
            if err:
                self.err = "не удалось начать запись: " + mci_error(err)
                return False
            end = time.time() + CHUNK_SEC
            if stop_at is not None:
                end = min(end, time.mktime(stop_at.timetuple()))
            while time.time() < end and not self.stop_flag.is_set():
                time.sleep(0.25)
            mci("stop rec")
            err, _ = mci('save rec "%s"' % path)
            if err:
                self.err = "не удалось сохранить: " + mci_error(err)
                return False
        finally:
            mci("close rec")
        return os.path.exists(path)

    def finish(self):
        if not self.env:
            self.log("done")
            return
        res = analyse(self.env)
        clips = self.save_clips(res["pauses"])
        text = build_report(self.started, self.env, res, clips, clock=self.wall)
        self.report_path = os.path.join(
            REC_DIR, self.started.strftime("otchet_%Y-%m-%d_%H%M.txt"))
        try:
            with io.open(self.report_path, "w", encoding="utf-8") as f:
                f.write(text)
            env_path = os.path.join(REC_DIR, self.started.strftime(
                "ogibayushchaya_%Y-%m-%d_%H%M.json"))
            with io.open(env_path, "w", encoding="utf-8") as f:
                json.dump({"frame_sec": FRAME_SEC, "env": self.env}, f)
        except OSError as e:
            self.err = str(e)
        for c in self.chunks:                # сам звук ночи не храним
            try:
                os.remove(c)
            except OSError:
                pass
        try:
            os.rmdir(self.tmp_dir)
        except OSError:
            pass
        self.log("done")

    def save_clips(self, pauses, span=8.0):
        """Вырезает короткие куски вокруг самых длинных пауз.
        Формат клипа берётся из исходного куска: драйвер пишет 8 бит, и
        8-битные данные под 16-битным заголовком превращались в шум."""
        out = []
        if not self.chunks:
            return out
        if not self.chunk_frames:                # на всякий случай: старый путь
            self.chunk_frames = [int(CHUNK_SEC / FRAME_SEC)] * len(self.chunks)
        best = sorted(pauses, key=lambda p: -p["len"])[:MAX_CLIPS]
        for k, pz in enumerate(sorted(best, key=lambda p: p["at"])):
            ci, off = self.locate(max(0.0, pz["at"] - span / 2))
            try:
                data, width, rate, ch, need = b"", None, None, 1, 0
                while ci < len(self.chunks):     # клип может пересечь границу
                    with wave.open(self.chunks[ci], "rb") as w:
                        if width is None:
                            width, rate = w.getsampwidth(), w.getframerate()
                            ch = w.getnchannels()   # драйвер мог проигнорировать «моно»
                            need = int(span * rate)
                        w.setpos(min(w.getnframes(), int(off * rate)))
                        data += w.readframes(need - len(data) // (width * ch))
                    if len(data) // (width * ch) >= need:
                        break
                    ci, off = ci + 1, 0.0
                if not data:
                    continue
                dst = os.path.join(REC_DIR, "%s_pauza_%02d.wav" % (
                    self.started.strftime("%Y-%m-%d_%H%M"), k + 1))
                with wave.open(dst, "wb") as w2:
                    w2.setnchannels(ch)
                    w2.setsampwidth(width)
                    w2.setframerate(rate)
                    w2.writeframes(data)
                out.append(os.path.basename(dst))
            except Exception:                                # noqa: BLE001
                continue
        return out


class RecorderWindow:
    W, H = 560, 392
    SKY = mix(T.ACCENT_D, T.BG_BOT, 0.6)
    SEGS = 30

    def __init__(self):
        self.cfg = load_cfg()
        self.alive = True
        self.queue = queue.Queue()

        self.root = tk.Tk()
        keep_gc(self.root)
        self.root.title("Запись ночи")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (self.W, self.H, (sw - self.W) // 2,
                                            (sh - self.H) // 2))
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bg=T.BG_BOT)
        self.cv.pack(fill="both", expand=True)
        paint_bg(self.cv, self.W, self.H, top=self.SKY, u=4, bands=6, stars=30, seed=11,
                 star_h=0.25)
        draggable(self.root, self.cv, "bg")

        tw = text_w("ИДЁТ ЗАПИСЬ НОЧИ", 3)
        left = self.W / 2 - (tw + 40) / 2
        self.dot = sprite(self.cv, left + 12, 58, disc(3), {"Y": T.ACCENT, "K": T.INK}, 3)
        PText(self.cv, left + 40, 58, "ИДЁТ ЗАПИСЬ НОЧИ", 3, fill=T.TEXT, anchor="w",
              shadow=T.INK, tags=("bg",))
        self.t_elapsed = PText(self.cv, self.W / 2, 120, "0:00:00", 7, fill=T.TEXT,
                               shadow=T.ACCENT_D)
        sw_, gap = 12, 4
        x0 = self.W / 2 - (self.SEGS * (sw_ + gap) - gap) / 2
        pbox(self.cv, x0 - 8, 162, x0 + self.SEGS * (sw_ + gap) - gap + 8, 194, 4,
             fill=T.INK, border=T.CARD_HI, inset=True)
        self.segs = [px_rect(self.cv, x0 + i * (sw_ + gap), 170, x0 + i * (sw_ + gap) + sw_,
                             186, T.CARD) for i in range(self.SEGS)]
        self.lit = -1
        self.t_sub = PText(self.cv, self.W / 2, 226, "", 2, fill=T.MUTED, width=self.W - 60)
        self.t_msg = PText(self.cv, self.W / 2, 284, "", 2, fill=T.DIM, width=self.W - 60)
        CBtn(self.cv, self.W / 2, 348, 340, 48, "ОСТАНОВИТЬ И РАЗОБРАТЬ",
             self.finish_now, u=4, sc=2, fill=T.CARD, fg=T.TEXT)
        frame(self.cv, self.W, self.H, 4, color=T.ACCENT_D)

        self.rec = Recorder(self.cfg, self.queue.put)
        threading.Thread(target=self.rec.run, daemon=True).start()
        self.tick()
        self.pump()

    def pump(self):
        if not self.alive:
            return
        done = False
        while True:
            try:
                what = self.queue.get_nowait()
            except queue.Empty:
                break
            if what in ("done", "error"):
                done = True
        if done:
            self.show_result()
            return
        self.root.after(200, self.pump)

    def tick(self):
        if not self.alive:
            return
        el = (datetime.now() - self.rec.started).total_seconds()
        self.t_elapsed.set(hhmmss(el))
        self.cv.itemconfig(self.dot + "_Y",
                           fill=T.ACCENT if int(el) % 2 == 0 else T.ACCENT_D)
        n = 0
        if self.rec.env:
            tail = self.rec.env[-240:]
            lvl = sum(tail) / float(len(tail))
            n = max(0, min(self.SEGS, int(lvl / 100)))
        if n != self.lit:                       # индикатор громкости: зелёный → жёлтый → красный
            self.lit = n
            for i, seg in enumerate(self.segs):
                col = (T.CARD if i >= n else T.OK if i < 20 else
                       T.WARN if i < 26 else T.ACCENT)
                self.cv.itemconfig(seg, fill=col)
        self.t_sub.set("остановится сама в %02d:%02d, когда зазвонит будильник"
                       % (self.cfg["hh"], self.cfg["mm"]))
        self.t_msg.set("кусков записано: %d · звук ночи не сохраняется, "
                       "остаётся отчёт и короткие фрагменты пауз" % len(self.rec.chunks))
        self.root.after(500, self.tick)

    def finish_now(self):
        self.t_msg.set("останавливаю и разбираю запись…")
        self.rec.stop()

    def show_result(self):
        self.alive = False
        if self.rec.report_path and not self.rec.err:
            try:
                os.startfile(self.rec.report_path)           # noqa: S606
            except Exception:                                # noqa: BLE001
                pass
        print(self.rec.err or self.rec.report_path or "записывать было нечего")
        close_window(self.root)

    def run(self):
        self.root.mainloop()


# ═══════════════════════════════ ЗАПУСК ═══════════════════════════════

class _Mute:
    """Тишина вместо сирены — для окон «Я встал» и проверки."""
    def stop(self):
        pass


UP_FLAG = os.path.join(HERE, ".upearly")
UP_WINDOW_H = 2          # «Я встал» работает только за 2 ч до подъёма: не способ выключить будильник с вечера
UP_NEED = 10             # столько задач, чтобы засчитать ранний подъём
RECHECK_MIN = 10         # через сколько минут после решения спросить ещё раз
RECHECK_SEC = 120        # сколько ждать ответа на проверке, потом снова звонок
RECHECK_ROUNDS = 6
DIARY_TIMEOUT = 180      # анкета «во сколько уснул» закрывается сама через 3 минуты
CHECK_STAGES = [(0, 0.20), (40, 0.40), (80, 0.65)]   # проверка звучит мягко и нарастает




def quiet_check(cfg, title="ТЫ НЕ СПИШЬ?"):
    """Одна задача под мягкий нарастающий звук; не ответил за RECHECK_SEC — значит, уснул.
    Беззвучную проверку легко проспать — поэтому мягкий звук."""
    guard = SpeakerVolumeGuard(cfg)
    siren = Siren(cfg, guard=guard, stages=CHECK_STAGES)
    siren.start()
    try:
        w = AlarmWindow(cfg, siren, need=1, title=title, timeout=RECHECK_SEC,
                        label="ОСТАЛОСЬ")
        w.run()
    finally:
        siren.stop()
        siren.join()
        guard.restore()
        gc.collect()
    log("проверка «%s»: %s" % (title, "ответил" if w.solved else "НЕ ответил"))
    return w.solved


def up_early():
    """Кнопка «Я встал»: 10 задач — и сегодняшний звонок заменится тихой проверкой."""
    cfg = load_cfg()
    now = datetime.now()
    wake = next_wake(cfg, now)
    if (wake - now).total_seconds() > UP_WINDOW_H * 3600:
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showinfo("Я встал", "Рано: кнопка работает только за %d ч до подъёма.\n"
                            "Ближайший подъём — %s." % (UP_WINDOW_H, wake.strftime("%d.%m %H:%M")),
                            parent=root)
        root.destroy()
        log("«Я встал»: рано, подъём в %s" % wake.strftime("%d.%m %H:%M"))
        return
    w = AlarmWindow(cfg, _Mute(), need=UP_NEED, title="Я   В С Т А Л", label="РЕШАЮ")
    w.run()
    if w.solved:
        with io.open(UP_FLAG, "w", encoding="utf-8") as f:
            f.write(wake.isoformat())
        log("«Я встал»: %d задач решено, звонок %s заменён проверкой" % (UP_NEED, wake.strftime("%H:%M")))
        diary_upsert(now.date(), woke=datetime.now().strftime("%H:%M"),
                     alarm=wake.strftime("%H:%M"), note="встал раньше будильника")


def take_up_flag(now=None):
    """Был ли ранний подъём на этот звонок (±1 ч)? Флаг одноразовый."""
    now = now or datetime.now()
    try:
        with io.open(UP_FLAG, encoding="utf-8") as f:
            when = datetime.fromisoformat(f.read().strip())
        os.remove(UP_FLAG)
    except (OSError, ValueError):
        return False
    return abs((when - now).total_seconds()) <= 3600


def wake_up(cfg, test=False):
    """Звонок + повторные проверки: решил задачи и уснул обратно — звонит снова."""
    if not test and take_up_flag():
        log("звонок: встал раньше («Я встал») — вместо звонка проверка")
        if quiet_check(cfg, "ТЫ ВСТАЛ — ПРОВЕРКА"):
            return
    w = ring(cfg)
    if test:
        return
    solved_at = time.time()
    if w is not None and w.solved:
        now = datetime.now()
        diary_upsert(now.date(), woke=now.strftime("%H:%M"),
                     alarm=w.rang_at.strftime("%H:%M"),
                     solve_sec=int(now.timestamp() - w.started), attempts=w.attempts)
        try:
            form = DiaryForm(cfg)                # один вопрос: во сколько уснул
            form.root.after(DIARY_TIMEOUT * 1000, form.close)   # не ответил — сам закроется
            form.run()
        except Exception:                        # noqa: BLE001
            log("дневник: " + traceback.format_exc())
        gc.collect()
    for k in range(RECHECK_ROUNDS):
        # первая проверка — через RECHECK_MIN от решения задач, а не от закрытия анкеты
        wait = RECHECK_MIN * 60 - (time.time() - solved_at) if k == 0 else RECHECK_MIN * 60
        time.sleep(max(5, wait))
        if quiet_check(cfg):
            return
        log("проверка: не ответил — звоню снова")
        ring(cfg)


def ring(cfg):
    log("звонок: старт · ответов %d · сложность %d · звук %s · устройства: %s" % (
        cfg["need"], cfg["level"], cfg["sound"],
        "; ".join(n for _i, n in output_devices()) or "—"))
    guard = SpeakerVolumeGuard(cfg)
    siren = Siren(cfg, guard=guard)
    siren.start()
    w = None
    try:
        w = AlarmWindow(cfg, siren)
        w.rang_at = datetime.now()
        w.run()
    finally:
        siren.stop()
        siren.join()
        guard.restore()
        gc.collect()                      # мусор прошлого окна — здесь, в главном потоке
        log("звонок: завершён")
    return w


# ═══════════════════════════ ДНЕВНИК СНА ═══════════════════════════
# Одна строка — одна ночь, ключ — дата ПОДЪЁМА. Будильник сам пишет, когда звонил
# и за сколько решены задачи; кнопка «ИДУ СПАТЬ» — когда лёг; короткая анкета
# после звонка — когда примерно уснул, просыпался ли и как самочувствие.

DIARY = os.path.join(HERE, "diary.csv")
DIARY_COLS = ["date", "lay", "asleep", "woke", "alarm", "solve_sec", "attempts",
              "awakenings", "feel", "note"]


def diary_load(path=None):
    try:
        with io.open(path or DIARY, encoding="utf-8-sig", newline="") as f:
            return [{k: (r.get(k) or "").strip() for k in DIARY_COLS}
                    for r in csv.DictReader(f)]
    except OSError:
        return []


def diary_save(rows, path=None):
    path = path or DIARY
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DIARY_COLS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: r["date"]):
            w.writerow({k: r.get(k, "") for k in DIARY_COLS})
    os.replace(tmp, path)


def diary_upsert(day, path=None, **fields):
    """Дополняет запись за день подъёма; пустые значения не затирают записанное."""
    key = day.isoformat() if hasattr(day, "isoformat") else str(day)
    rows = diary_load(path)
    row = next((r for r in rows if r["date"] == key), None)
    if row is None:
        row = dict.fromkeys(DIARY_COLS, "")
        row["date"] = key
        rows.append(row)
    for k, v in fields.items():
        if k in DIARY_COLS and v not in (None, ""):
            row[k] = str(v)
    try:
        diary_save(rows, path)
        return True
    except OSError as e:
        log("дневник: не записал — %s" % e)
        return False


def hhmm_min(s):
    """«03:15», «≈03:15» → минуты от полуночи; мусор → None."""
    s = (s or "").strip().lstrip("≈~").strip()
    try:
        h, m = (int(x) for x in s.split(":"))
    except ValueError:
        return None
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


# Ось ночи: от 18:00 до 18:00 следующего дня. На ней и засыпание в 23:00, и в 07:00,
# и подъём в 06:00 или в 15:00 ложатся по порядку, без перехода через полночь.
AXIS_START = 18 * 60


def tpos(minutes):
    return (minutes - AXIS_START) % 1440


def fmt_pos(p):
    m = int(round(p + AXIS_START)) % 1440
    return "%02d:%02d" % (m // 60, m % 60)


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0) if n else None


# Оценка ритма по поведению (не по анализам):
#  • DLMO — начало вечернего мелатонина — в среднем ≈ 2 ч до привычного засыпания
#    (Burgess и др. 2003; Sletten и др. 2010; разброс у людей до нескольких часов);
#  • минимум температуры тела ≈ DLMO + 7 ч (Burgess, Eastman).
# «Привычное засыпание» — медиана последних RHYTHM_WIN ночей: одна плохая ночь
# оценку не сбивает, а устойчивый сдвиг виден за несколько дней.
DLMO_BEFORE_ONSET = 120
TMIN_AFTER_DLMO = 7 * 60
RHYTHM_WIN = 5


def sleep_nights(rows):
    """Ночи, где известны и засыпание, и подъём. Позиции — на оси от 18:00."""
    out = []
    for r in sorted(rows, key=lambda r: r["date"]):
        a, w = hhmm_min(r.get("asleep")), hhmm_min(r.get("woke"))
        if a is None or w is None:
            continue
        on, off = tpos(a), tpos(w)
        if off <= on:                          # сон через 18:00 — подъём уже на следующих сутках оси
            off += 1440
        dur = off - on
        if not 60 <= dur <= 16 * 60:          # опечатка или слишком долгий «сон»
            continue
        al = hhmm_min(r.get("alarm"))
        try:
            feel = int(r.get("feel") or 0) or None
        except ValueError:
            feel = None
        out.append({"date": r["date"], "on": on, "off": off, "dur": dur,
                    "alarm": tpos(al) if al is not None else None, "feel": feel})
    return out


def rhythm(rows, win=RHYTHM_WIN):
    """Ночи + оценка DLMO и минимума температуры на каждую дату (скользящая медиана)."""
    ns = sleep_nights(rows)
    for i, n in enumerate(ns):
        onset = _median([x["on"] for x in ns[max(0, i - win + 1):i + 1]])
        n["dlmo"] = onset - DLMO_BEFORE_ONSET
        n["tmin"] = n["dlmo"] + TMIN_AFTER_DLMO
    return ns


def rhythm_advice(rows, wake_at):
    """Советы по дневнику. wake_at — ближайший подъём (datetime). → [(заголовок, текст, источник)]."""
    ns = rhythm(rows)
    if len(ns) < 3:
        return [("Мало данных",
                 "Нужно хотя бы 3 ночи в дневнике (сейчас %d). Анкета появляется после "
                 "каждого звонка, ночь можно добавить и вручную." % len(ns), "")]
    last = ns[-1]
    recent = ns[-RHYTHM_WIN:]
    onset = _median([n["on"] for n in recent])
    wake = tpos(wake_at.hour * 60 + wake_at.minute)
    target_bed = wake - 8 * 60
    tips = [("Твой ритм сейчас",
             "Засыпаешь обычно около %s. Отсюда оценка: вечерний мелатонин с ≈%s, "
             "минимум температуры тела ≈%s. Это расчёт по дневнику, а не анализ — "
             "у разных людей разброс бывает в несколько часов."
             % (fmt_pos(onset), fmt_pos(last["dlmo"]), fmt_pos(last["tmin"])),
             "Burgess 2003; Sletten 2010")]

    early = last["tmin"] - wake
    if early > 20:
        tips.append(("Утренний свет — после %s" % fmt_pos(last["tmin"]),
                     "Подъём (%s) раньше минимума температуры примерно на %d мин. Свет ДО "
                     "минимума сдвигает часы позже — первые полчаса-час держи свет "
                     "умеренным, а на улицу выходи после %s."
                     % (fmt_pos(wake), early, fmt_pos(last["tmin"])),
                     "кривая ответа на свет, Khalsa 2003"))
    else:
        tips.append(("Утренний свет — сразу после подъёма",
                     "Подъём уже после минимума температуры: 30+ минут дневного света "
                     "сразу после будильника сдвигают часы раньше. Без света часы "
                     "догоняют будильник в разы медленнее.",
                     "Khalsa 2003; Burgess 2003"))

    tips.append(("Вечер — с %s" % fmt_pos(onset - 180),
                 "За 2–3 часа до сна — тусклый тёплый свет, экран потемнее: яркий свет "
                 "вечером сдвигает часы позже.", "Tzischinsky и Lavie 1997"))

    lag = _median([n["on"] for n in ns[-3:]]) - target_bed
    if lag <= 30:
        tips.append(("Можно двигать подъём раньше",
                     "Последние ночи засыпаешь у цели (ложиться не раньше %s). Можно "
                     "сдвинуть будильник на 30–60 минут раньше — быстрее часы не успевают."
                     % fmt_pos(target_bed), "Burgess 2003; Eastman 2005"))
    elif lag > 60:
        tips.append(("Пока не двигай подъём",
                     "Засыпаешь примерно на %d мин позже цели (%s): часы отстают от "
                     "будильника. Держи время подъёма и утренний свет, пока не догонят. "
                     "Ложиться раньше цели не стоит — сон на одной усталости может оборваться."
                     % (lag, fmt_pos(target_bed)), "Wilhelmsen-Langeland/Saxvig; КПТ-И"))
    else:
        tips.append(("Почти догнал",
                     "Засыпаешь рядом с целью (%s). Подержи этот подъём ещё 2–3 дня, "
                     "потом двигай дальше." % fmt_pos(target_bed), "Burgess 2003"))

    offs = [n["off"] for n in ns[-7:]]
    if len(offs) >= 3:
        mean = sum(offs) / len(offs)
        sd = (sum((x - mean) ** 2 for x in offs) / len(offs)) ** 0.5
        if sd > 60:
            tips.append(("Подъём гуляет",
                         "Время подъёма за неделю гуляет на ±%d мин. Регулярность "
                         "предсказывает здоровье сильнее, чем длительность сна." % sd,
                         "Windred 2024"))

    durs = [n["dur"] for n in recent]
    avg = sum(durs) / len(durs) / 60.0
    if avg > 9:
        tips.append(("Долгий сон",
                     "В среднем %.1f ч. Долгий сон часто сигнал (апноэ, настроение, плохое "
                     "качество сна) — стоит обсудить с врачом." % avg,
                     "AASM 2015; Grandner 2007"))
    elif avg < 7:
        tips.append(("Мало сна",
                     "В среднем %.1f ч — меньше 7. Недосып копится незаметно для самого "
                     "человека." % avg, "AASM 2015; Van Dongen 2003"))

    if len(ns) >= 6:
        half = len(ns) // 2
        a = _median([n["tmin"] for n in ns[:half]])
        b = _median([n["tmin"] for n in ns[half:]])
        shift = b - a
        if abs(shift) >= 30:
            tips.append(("Сдвиг за %d ночей" % len(ns),
                         "Оценка ритма сдвинулась на %d мин %s — это видно на графике "
                         "по синей линии." % (abs(shift), "раньше" if shift < 0 else "позже"),
                         ""))
    return tips


def default_asleep(day, cfg, rows=None):
    """Подсказка для анкеты: «лёг» + LATENCY_MIN, иначе подъём − 8 ч."""
    rows = diary_load() if rows is None else rows
    row = next((r for r in rows if r["date"] == day.isoformat()), {})
    lay = hhmm_min(row.get("lay"))
    if lay is not None:
        m = lay + LATENCY_MIN
    else:
        w = next_wake(cfg, datetime(day.year, day.month, day.day) - timedelta(hours=6))
        m = w.hour * 60 + w.minute - 8 * 60
    m = int(round(m / 15.0) * 15) % 1440
    return m


class DiaryForm:
    """Один вопрос после звонка: «во сколько уснул?». Подъём будильник знает сам —
    это момент, когда решены задачи. Вручную (из окна ритма, без звонка) — ещё и подъём."""
    W = 560

    def __init__(self, cfg, day=None, parent=None):
        self.cfg = cfg
        self.day = day or datetime.now().date()
        self.saved = False
        self.own = parent is None

        rows = diary_load()
        row = next((r for r in rows if r["date"] == self.day.isoformat()), {})
        self.asleep = hhmm_min(row.get("asleep"))
        if self.asleep is None:
            self.asleep = default_asleep(self.day, cfg, rows)
        self.woke = hhmm_min(row.get("woke"))
        self.ask_woke = self.woke is None          # звонка не было — подъём спросим
        if self.ask_woke:
            w = next_wake(cfg, datetime(self.day.year, self.day.month, self.day.day)
                          - timedelta(hours=6))
            self.woke = w.hour * 60 + w.minute
        self.H = 420 if self.ask_woke else 310

        self.root = tk.Tk() if self.own else tk.Toplevel(parent)
        if self.own:
            keep_gc(self.root)
        self.root.title("Сон")
        self.root.configure(bg=T.BG_BOT)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (self.W, self.H, (sw - self.W) // 2,
                                            max(0, (sh - self.H) // 3)))
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bg=T.BG_BOT)
        self.cv.pack()
        paint_bg(self.cv, self.W, self.H, u=4, bands=6, stars=40, seed=5, star_h=0.25)

        c, W = self.cv, self.W
        PText(c, W / 2, 38, "ВО СКОЛЬКО ПРИМЕРНО УСНУЛ?", 3, fill=T.TEXT, shadow=T.INK)
        PText(c, W / 2, 72, "ночь на %s · по этому строится график ритма"
              % self.day.strftime("%d.%m"), 2, fill=T.MUTED, width=W - 40)
        self.t_time = {}
        pickers = [("asleep", 156, "")]
        if self.ask_woke:
            pickers = [("asleep", 162, "уснул"), ("woke", 268, "проснулся")]
        for key, cy, label in pickers:
            if label:
                PText(c, W / 2, cy - 48, label, 2, fill=T.SKY)
            pbox(c, W / 2 - 96, cy - 32, W / 2 + 96, cy + 32, 4, fill=T.INK,
                 border=T.CARD_HI, inset=True)
            self.t_time[key] = PText(c, W / 2, cy, "", 6, fill=T.OK)
            for dx, lab, d in ((-218, "−1ч", -60), (-142, "−15", -15),
                               (142, "+15", 15), (218, "+1ч", 60)):
                CBtn(c, W / 2 + dx, cy, 64, 44, lab,
                     lambda d=d, k=key: self.bump(k, d), u=4, sc=2, fill=T.CARD)

        by = self.H - 52
        CBtn(c, W / 2 - 122, by, 228, 48, "СОХРАНИТЬ", self.save, u=4, sc=2,
             fill=T.ACCENT, fg=T.TEXT)
        CBtn(c, W / 2 + 122, by, 228, 48, "НЕ ПОМНЮ", self.close, u=4, sc=2,
             fill=T.CARD, fg=T.MUTED)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.render()

    def bump(self, key, d):
        setattr(self, key, (getattr(self, key) + d) % 1440)
        self.render()

    def render(self):
        for key, txt in self.t_time.items():
            txt.set("%02d:%02d" % divmod(getattr(self, key), 60))

    def save(self):
        fields = {"asleep": "%02d:%02d" % divmod(self.asleep, 60)}
        if self.ask_woke:
            fields["woke"] = "%02d:%02d" % divmod(self.woke, 60)
        self.saved = diary_upsert(self.day, **fields)
        log("дневник: ночь на %s — уснул %s" % (self.day.isoformat(), fields["asleep"]))
        self.close()

    def close(self):
        if self.own:
            close_window(self.root)
        else:
            self.root.destroy()

    def run(self):
        if self.own:
            self.root.mainloop()
        else:
            self.root.wait_window()


def block_h(text, sc, width):
    """Высота перенесённого текста в пикселях (с хвостами букв)."""
    n = len(wrap(text, sc, width)) if text else 0
    return ((n - 1) * LINE_U + 9) * sc if n else 0


class RhythmWindow:
    """График сна по дням + оценка ритма + советы. Открывается из панели."""
    W = 980
    ROW = 26
    DAYS = 14

    def __init__(self, parent, cfg):
        self.parent, self.cfg = parent, cfg
        self.top = tk.Toplevel(parent)
        self.top.title("Ритм сна")
        self.top.configure(bg=T.BG_BOT)
        self.top.resizable(False, True)
        sh = self.top.winfo_screenheight()
        self.H = min(1010, sh - 80)
        self.top.geometry("%dx%d+40+20" % (self.W, self.H))
        self.cv = tk.Canvas(self.top, width=self.W, height=self.H,
                            highlightthickness=0, bg=T.BG_BOT)
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<MouseWheel>",
                     lambda e: self.cv.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.draw()

    def x(self, pos):
        return self.x0 + pos * self.pxm

    def draw(self):
        c, W = self.cv, self.W
        clear_canvas(c)
        rows = diary_load()
        ns = rhythm(rows)[-self.DAYS:]
        self.x0, self.x1 = 110, W - 30
        self.pxm = (self.x1 - self.x0) / 1440.0

        PText(c, 30, 36, "РИТМ СНА", 4, fill=T.TEXT, anchor="w", shadow=T.INK)
        PText(c, 30, 62, "фиолетовая линия — засыпание · зелёная — подъём · закрашено — сон · "
              "пунктир — цель (лечь не раньше и будильник)", 2, fill=T.MUTED,
              anchor="nw", width=W - 300)
        CBtn(c, W - 130, 40, 200, 40, "ЗАПИСАТЬ НОЧЬ", self.add_night, u=4, sc=2,
             fill=T.CARD, fg=T.OK)

        top = 130
        h = 420
        if not ns:
            PText(c, W / 2, top + 30, "Дневник пуст. Ночи появятся после звонков будильника "
                  "или кнопкой «ЗАПИСАТЬ НОЧЬ».", 2, fill=T.MUTED, width=W - 120)
            h = 60
        else:
            pbox(c, 30, top - 16, W - 22, top + h + 12, 4, fill=CHART_BG, border=T.INK,
                 inset=True)
            draw_ribbon_chart(c, self.x0, top, self.x1, top + h, ns)
        y = top + h + 50
        PText(c, 30, y, "РЕКОМЕНДАЦИИ", 3, fill=T.TEXT, anchor="w", shadow=T.INK)
        y += 30
        for title, text, src in rhythm_advice(rows, next_wake(self.cfg)):
            t1 = PText(c, 30, y, title, 2, fill=T.OK, anchor="nw", width=W - 60)
            t2 = PText(c, 30, t1.bottom + 8, text + ("  [%s]" % src if src else ""), 2,
                       fill=T.TEXT, anchor="nw", width=W - 60)
            y = t2.bottom + 18
        t = PText(c, 30, y + 4, "Это не медицинский прибор. Оценка ритма — расчёт по "
                  "поведению (засыпание − 2 ч, +7 ч), точно его меряют по мелатонину в слюне. "
                  "Долгий сон, громкий храп, остановки дыхания, сонливость днём — повод "
                  "к сомнологу.", 2, fill=T.DIM, anchor="nw", width=W - 60)
        full = max(self.H, int(t.bottom) + 60)
        paint_bg(c, W, full, u=4, bands=8, stars=70, live=0)
        self.cv.configure(scrollregion=(0, 0, W, full))

    def add_night(self):
        DiaryForm(self.cfg, parent=self.top).run()
        self.draw()


# ═══════════════════════════ ЕДИНАЯ ПАНЕЛЬ ═══════════════════════════
# Всё, что нужно каждый день, — в одном окне: подъём, ночь, ритм, советы.
# Редкие настройки звонка (сложность, задачи, звук) — по кнопке «НАСТРОЙКИ».

DAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def plural_ru(n, one, few, many):
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def shift_wake(cfg, minutes, only_next=True, now=None):
    """Сдвиг подъёма. only_next — только ближайший (запись плана на эту дату или
    базовое время); иначе — базовое время и все будущие даты плана (шаг лестницы)."""
    now = now or datetime.now()
    cfg = dict(cfg)
    plan = dict(cfg.get("plan") or {})
    today = now.date().isoformat()

    def moved(hhmm):
        m = (hhmm_min(hhmm) + minutes) % 1440
        return "%02d:%02d" % divmod(m, 60)

    if only_next:
        w = next_wake(cfg, now)
        key = w.date().isoformat()
        if key in plan:
            nw = w + timedelta(minutes=minutes)        # дата могла смениться через полночь
            del plan[key]
            plan[nw.date().isoformat()] = nw.strftime("%H:%M")
        else:
            cfg["hh"], cfg["mm"] = divmod((cfg["hh"] * 60 + cfg["mm"] + minutes) % 1440, 60)
    else:
        cfg["hh"], cfg["mm"] = divmod((cfg["hh"] * 60 + cfg["mm"] + minutes) % 1440, 60)
        for k in plan:
            if k >= today:
                plan[k] = moved(plan[k])
    cfg["plan"] = {k: v for k, v in plan.items() if k >= today}   # прошлое не храним
    return cfg


CHART_BG = "#0B1024"        # «экран» под графиком
ON_C, OFF_C, SLEEP_FILL = "#B5A2FF", T.OK, "#1D2B53"
DLMO_C, TMIN_C = "#B07CFF", "#43B4FF"


def px_dot(c, x, y, col, r=4):
    """Точка графика — квадратик в чёрной обводке."""
    px_rect(c, x - r - 2, y - r - 2, x + r + 2, y + r + 2, T.INK)
    px_rect(c, x - r, y - r, x + r, y + r, col)


def draw_rhythm_chart(c, x0, x1, top, ns, row_h, labels=True, band=None):
    """Полосы сна по дням на оси 18:00→18:00 + линии оценки мелатонина и минимума
    температуры. band — (начало, конец) окна цели на оси. Возвращает нижнюю границу."""
    pxm = (x1 - x0) / 1440.0
    X = lambda p: x0 + p * pxm
    h = max(1, len(ns)) * row_h
    if band:
        b0, b1 = band
        if b1 < b0:
            b1 += 1440
        px_rect(c, X(b0), top - 4, X(min(b1, 1440)), top + h + 2, "#16213C")
    for k in range(0, 25, 3):
        xx = X(k * 60)
        c.create_line(xx, top - 4, xx, top + h + 2, fill=T.CARD_HI, dash=(2, 4))
        if labels:
            PText(c, xx, top - 14, fmt_pos(k * 60), 2, fill=T.DIM)
    line_d, line_t = [], []
    bar = max(4, row_h * 0.55)
    for i, n in enumerate(ns):
        y = top + i * row_h + row_h / 2
        d = datetime.strptime(n["date"], "%Y-%m-%d")
        PText(c, x0 - 10, y, d.strftime("%d.%m"), 2, fill=T.MUTED, anchor="e")
        col = mix("#5B6CFF", T.OK, ((n["feel"] or 3) - 1) / 4.0) if n["feel"] else "#5B6CFF"
        px_rect(c, X(n["on"]), y - bar / 2, X(n["off"]), y + bar / 2, col)
        if n["alarm"] is not None:
            px_rect(c, X(n["alarm"]) - 1, y - bar / 2 - 3, X(n["alarm"]) + 2, y + bar / 2 + 3,
                    T.ACCENT)
        line_d += [X(n["dlmo"] % 1440), y]
        line_t += [X(n["tmin"] % 1440), y]
    for pts, colr in ((line_d, DLMO_C), (line_t, TMIN_C)):
        if len(pts) >= 4:
            c.create_line(*pts, fill=colr, width=2)
        for j in range(0, len(pts), 2):
            px_dot(c, pts[j], pts[j + 1], colr, 2)
    return top + h


def draw_ribbon_chart(c, x0, y0, x1, y1, ns):
    """«Лента»: засыпание и подъём двумя линиями по дням, между ними закрашен сон,
    пунктиром — цель (ложиться не раньше = будильник − 8 ч, и сам будильник)."""
    n = len(ns)
    tgt = [(i, x["alarm"] - 480, x["alarm"]) for i, x in enumerate(ns) if x["alarm"] is not None]
    lo = min([x["on"] for x in ns] + [t[1] for t in tgt]) - 30
    hi = max([x["off"] for x in ns] + [t[2] for t in tgt]) + 30
    lo, hi = int(lo // 120) * 120, int(-(-hi // 120)) * 120
    pad_b = 30                                        # под подписи дат
    Y = lambda t: y0 + (t - lo) * (y1 - pad_b - y0) / float(hi - lo)
    px1 = x1 - 104                                   # справа место под подписи последней ночи
    X = (lambda i: (x0 + px1) / 2.0) if n == 1 else (lambda i: x0 + 16 + i * (px1 - x0 - 16) / (n - 1.0))

    for t in range(lo, hi + 1, 120):
        c.create_line(x0, Y(t), x1, Y(t), fill=T.CARD_HI, dash=(2, 4))
        PText(c, x0 - 10, Y(t), fmt_pos(t), 2, fill=T.DIM, anchor="e")

    if n >= 2:
        pts = []
        for i, x in enumerate(ns):
            pts += [X(i), Y(x["on"])]
        for i in range(n - 1, -1, -1):
            pts += [X(i), Y(ns[i]["off"])]
        c.create_polygon(*pts, fill=SLEEP_FILL, outline="")
    else:
        px_rect(c, X(0) - 14, Y(ns[0]["on"]), X(0) + 14, Y(ns[0]["off"]), SLEEP_FILL)

    for col, k in (("#8FB4FF", 1), (T.ACCENT, 2)):
        line = []
        for t in tgt:
            line += [X(t[0]), Y(t[k])]
        if len(line) >= 4:
            c.create_line(*line, fill=col, width=2, dash=(4, 4))

    for key, col in (("on", ON_C), ("off", OFF_C)):
        line = []
        for i, x in enumerate(ns):
            line += [X(i), Y(x[key])]
        if len(line) >= 4:
            c.create_line(*line, fill=col, width=4)
        for i, x in enumerate(ns):
            px_dot(c, X(i), Y(x[key]), col)

    last = ns[-1]
    for key, col, name in (("on", ON_C, "уснул"), ("off", OFF_C, "встал")):
        PText(c, X(n - 1) + 14, Y(last[key]) - 10, name, 2, fill=col, anchor="w")
        PText(c, X(n - 1) + 14, Y(last[key]) + 10, fmt_pos(last[key]), 2, fill=T.TEXT,
              anchor="w")

    step = max(1, -(-n // 8))                         # не больше ~8 подписей, чтобы не слипались
    for i, x in enumerate(ns):
        if (n - 1 - i) % step == 0:                     # от последней ночи назад
            PText(c, X(i), y1 - 10, datetime.strptime(x["date"], "%Y-%m-%d").strftime("%d.%m"),
                  2, fill=T.MUTED)


def register_all(cfg):
    """Будильник, напоминание и рассвет — по одному расписанию (план по датам + база)."""
    ok, out = task_enable(cfg["hh"], cfg["mm"], cfg.get("plan"))
    if not ok:
        return ok, out
    if cfg.get("bedtime"):
        task_enable_bed_cfg(cfg)
    lc = load_lights()
    if lc["token"] and lc["device_id"]:
        task_enable_light(cfg["hh"], cfg["mm"], lc["pre_minutes"])   # рассвет — по базовому времени
    return ok, out


class Dashboard:
    """Главное окно (вариант B): меню слева с будильником, справа страницы
    «Сегодня», «Ритм сна», «Ночи». Редкие настройки звонка — отдельным окном."""
    W, H = 1440, 860
    SB = 280                   # ширина меню
    X0, X1 = 308, 1416         # рабочая область

    BG = T.BG_BOT
    SIDE = "#0E1430"
    SOFT = T.MUTED             # вторичный текст
    LABEL = "#7F87B8"
    SLEEP = "#5B6CFF"
    BAND = "#16213C"

    def __init__(self):
        self.cfg = load_cfg()
        self.status = None
        self.spk = None
        self.page = "rhythm"
        self.period = 14
        self.alive = True
        self.pending = None
        self.queue = queue.Queue()

        self.root = tk.Tk()
        keep_gc(self.root)
        self.root.title("Самый сложный будильник")
        self.root.configure(bg=self.BG)
        self.root.resizable(False, False)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (self.W, self.H, max(0, (sw - self.W) // 2),
                                            max(0, (sh - self.H) // 2 - 30)))
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bg=self.BG)
        self.cv.pack(fill="both", expand=True)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.bind("<Escape>", lambda e: self.quit())

        self.draw()
        self.pump()
        self.poll()
        self.tick()

    # ─────────────── фон ───────────────

    def bg(self, fn, done):
        def worker():
            try:
                res = fn()
            except Exception as e:                           # noqa: BLE001
                res = e
            self.queue.put((done, res))
        threading.Thread(target=worker, daemon=True).start()

    def pump(self):
        if not self.alive:
            return
        while True:
            try:
                done, res = self.queue.get_nowait()
            except queue.Empty:
                break
            done(res)
        self.root.after(100, self.pump)

    def poll(self):
        if not self.alive:
            return
        self.bg(task_status, self._got_status)
        self.bg(lambda: speaker_check(self.cfg), self._got_spk)
        self.root.after(30000, self.poll)

    def _got_status(self, res):
        self.status = res if isinstance(res, dict) else {"error": str(res)}
        self.draw()

    def _got_spk(self, res):
        self.spk = res if isinstance(res, tuple) else ("warn", "колонку проверить не удалось")
        self.draw()

    def tick(self):
        if not self.alive:
            return
        self.draw()
        self.root.after(30000, self.tick)

    def enabled(self):
        st = self.status or {}
        return bool(st.get("exists")) and st.get("state") != "Disabled"

    # ─────────────── рисование ───────────────

    def card(self, x0, y0, x1, y1, fill=T.CARD, tags=()):
        return pbox(self.cv, x0, y0, x1, y1, 4, fill=fill, tags=tags)

    def label(self, x, y, text, anchor="w"):
        PText(self.cv, x, y, text, 2, fill=T.SKY, anchor=anchor)

    def hand(self, tag):
        c = self.cv
        bind_tracked(c, tag, "<Enter>", lambda e: c.config(cursor="hand2"))
        bind_tracked(c, tag, "<Leave>", lambda e: c.config(cursor=""))

    def draw(self):
        if not self.alive:
            return
        c = self.cv
        clear_canvas(c)
        paint_bg(c, self.W, self.H, u=4, bands=8, stars=110, seed=9, live=0)
        self.cfg = load_cfg()
        self.now = datetime.now()
        self.wake = next_wake(self.cfg, self.now)
        self.rows = diary_load()
        self._sidebar()
        {"today": self._page_today, "rhythm": self._page_rhythm,
         "nights": self._page_nights}[self.page]()

    def _sidebar(self):
        c, sb = self.cv, self.SB
        px_rect(c, 0, 0, sb, self.H, self.SIDE)
        px_rect(c, sb - 4, 0, sb, self.H, T.INK)
        sprite(c, 34, 38, SPR_CLOCK, CLOCK_PAL, 2)
        PText(c, 58, 38, "SSB-ALARM", 2, fill=T.TEXT, anchor="w")

        # будильник
        self.card(18, 68, sb - 22, 254)
        self.label(36, 94, "ПОДЪЁМ")
        on = self.enabled()
        sx = sb - 82
        notch(c, sx, 84, sx + 44, 104, 2, T.INK, ("sw",))
        notch(c, sx + 2, 86, sx + 42, 102, 2, T.OK if on else T.CARD_HI, ("sw",))
        kx = sx + 26 if on else sx + 6
        px_rect(c, kx, 88, kx + 12, 100, T.TEXT, ("sw",))
        PText(c, sx - 10, 94, "вкл" if on else "выкл", 2,
              fill=T.OK if on else self.LABEL, anchor="e", tags=("sw",))
        bind_tracked(c, "sw", "<Button-1>", lambda e: self.toggle())
        self.hand("sw")
        CBtn(c, 46, 152, 40, 44, "−", lambda: self.shift(-15), u=4, sc=3, fill=T.CARD_HI)
        PText(c, (sb - 4) / 2, 152, self.wake.strftime("%H:%M"), 4, fill=T.TEXT, shadow=T.INK)
        CBtn(c, sb - 50, 152, 40, 44, "+", lambda: self.shift(15), u=4, sc=3, fill=T.CARD_HI)
        left = max(0, int((self.wake - self.now).total_seconds() // 60))
        bed = self.wake - timedelta(hours=8)
        PText(c, (sb - 4) / 2, 210, "через %d ч %02d мин\nложиться с %s"
              % (left // 60, left % 60, bed.strftime("%H:%M")), 2, fill=self.LABEL)
        if self.status and self.status.get("error"):
            PText(c, (sb - 4) / 2, 240, "Планировщик не ответил", 2, fill=T.WARN)

        # меню
        items = [("today", "Сегодня"), ("rhythm", "Ритм сна"), ("nights", "Ночи"),
                 ("settings", "Настройки")]
        y = 296
        for key, name in items:
            act = key == self.page
            tag = "nav_" + key
            if act:
                pbox(c, 16, y - 20, sb - 20, y + 20, 4, fill=T.CARD_HI, tags=(tag,))
            else:
                px_rect(c, 16, y - 20, sb - 20, y + 20, self.SIDE, (tag,))
            PText(c, 36, y, ("► " if act else "") + name, 2,
                  fill=T.TEXT if act else self.SOFT, anchor="w", tags=(tag,))
            bind_tracked(c, tag, "<Button-1>", lambda e, k=key: self.go(k))
            self.hand(tag)
            y += 50

        lvl, txt = self.spk or ("wait", "проверяю колонку…")
        col = {"ok": T.OK, "warn": T.WARN, "bad": T.ACCENT, "wait": self.LABEL}[lvl]
        icon = {"ok": "✓", "warn": "!", "bad": "✕", "wait": "…"}[lvl]
        PText(c, 20, self.H - 86, "%s %s" % (icon, txt), 2, fill=col, anchor="nw",
              width=sb - 44)

    def _head(self, title):
        PText(self.cv, self.X0, 44, title, 4, fill=T.TEXT, anchor="w", shadow=T.INK)

    # ── страница «Ритм сна» ──

    def _page_rhythm(self):
        c, x0, x1 = self.cv, self.X0, self.X1
        self._head("Ритм сна")
        for i, d in enumerate((7, 14, 30)):
            act = d == self.period
            CBtn(c, x1 - 48 - (2 - i) * 104, 44, 96, 36, "%d дней" % d,
                 lambda d=d: self.set_period(d), u=4, sc=2,
                 fill=T.ACCENT_D if act else T.CARD, fg=T.TEXT if act else self.SOFT,
                 outline=T.ACCENT if act else T.INK)

        allns = rhythm(self.rows)
        ns = allns[-self.period:]
        tiles = self._metrics(ns)
        tw = (x1 - x0 - 36) / 4.0
        for i, (name, val, sub, colr) in enumerate(tiles):
            tx = x0 + i * (tw + 12)
            self.card(tx, 84, tx + tw, 186)
            PText(c, tx + 18, 108, name, 2, fill=self.LABEL, anchor="w")
            PText(c, tx + 18, 140, val, 4, fill=T.TEXT, anchor="w", shadow=T.INK)
            PText(c, tx + 18, 170, sub, 2, fill=colr, anchor="w")

        cx1 = x1 - 392
        pbox(c, x0, 202, cx1, self.H - 24, 4, fill=CHART_BG, border=T.INK, inset=True)
        if not ns:
            PText(c, (x0 + cx1) / 2, 460, "Здесь появится график. После каждого звонка "
                  "будильник спросит одно — во сколько ты уснул.", 2, fill=self.SOFT,
                  width=cx1 - x0 - 80)
        else:
            self._legend(x0 + 22, 228)
            draw_ribbon_chart(c, x0 + 70, 262, cx1 - 24, self.H - 40, ns)

        self._advice(cx1 + 16, x1, 202)

    def _legend(self, x, y):
        c = self.cv
        items = [("линия", ON_C, "засыпание"), ("линия", OFF_C, "подъём"),
                 ("бар", SLEEP_FILL, "сон"), ("пунктир", "#8FB4FF", "лечь не раньше"),
                 ("пунктир", T.ACCENT, "будильник")]
        for kind, col, name in items:
            if kind == "бар":
                px_rect(c, x - 2, y - 8, x + 20, y + 8, T.CARD_HI)
                px_rect(c, x, y - 6, x + 18, y + 6, col)
            elif kind == "линия":
                px_rect(c, x, y - 2, x + 18, y + 2, col)
            else:
                for dx in (0, 8, 16):
                    px_rect(c, x + dx, y - 1, x + dx + 4, y + 1, col)
            x += 24
            PText(c, x, y, name, 2, fill=self.SOFT, anchor="w")
            x += text_w(name, 2) + 12

    def _metrics(self, ns):
        if not ns:
            dash = ("—", "нет данных", self.LABEL)
            return [("Засыпание",) + dash, ("Сон в среднем",) + dash,
                    ("Подъём гуляет",) + dash, ("Сдвиг ритма",) + dash]
        recent = ns[-RHYTHM_WIN:]
        onset = _median([n["on"] for n in recent])
        bed = self.wake - timedelta(hours=8)
        lag = onset - tpos(bed.hour * 60 + bed.minute)
        t1 = ("Засыпание", "≈" + fmt_pos(onset),
              "у цели %s" % bed.strftime("%H:%M") if lag <= 30 else "позже цели на %d мин" % lag,
              T.OK if lag <= 30 else T.WARN)
        avg = sum(n["dur"] for n in ns) / len(ns)
        t2 = ("Сон в среднем", "%d:%02d" % divmod(int(avg), 60), "цель около 8 ч",
              T.WARN if avg > 9 * 60 or avg < 7 * 60 else self.SOFT)
        offs = [n["off"] for n in ns[-7:]]
        mean = sum(offs) / len(offs)
        sd = (sum((v - mean) ** 2 for v in offs) / len(offs)) ** 0.5
        t3 = ("Подъём гуляет", "±%d мин" % sd, "регулярно" if sd <= 30 else "нерегулярно",
              T.OK if sd <= 30 else T.WARN)
        shift = ns[-1]["tmin"] - ns[0]["tmin"] if len(ns) > 1 else 0
        sign = "−" if shift < 0 else "+"
        t4 = ("Сдвиг ритма", "%s%d:%02d" % ((sign,) + divmod(int(abs(shift)), 60)),
              ("раньше" if shift < 0 else "позже") + " за %d дней" % len(ns) if shift else "без изменений",
              self.SOFT)
        return [t1, t2, t3, t4]

    def _advice(self, x0, x1, y):
        c = self.cv
        self.label(x0, y + 8, "ЧТО ДЕЛАТЬ")
        y += 28
        tips = rhythm_advice(self.rows, self.wake)
        if len(tips) > 1:
            tips = tips[1:]
        step = ("Можно двигать", "Пока не двигай", "Почти догнал")
        tips.sort(key=lambda t: 0 if t[0].startswith(step) else 1)   # решение про подъём — первым
        w = x1 - x0 - 36
        for title, text, src in tips[:3]:
            green = title.startswith("Можно двигать")
            body = ". ".join(text.split(". ")[:2]).rstrip(".") + "."
            # сначала раскладка, потом рисование: не влезает — карточку не рисуем вовсе
            yt = y + 18
            yb = yt + block_h(title, 2, w) + 6
            ys = yb + block_h(body, 2, w) + 6
            bottom = ys + (block_h(src, 2, w) + 4 if src else 0)
            if green:
                bottom += 52
            bottom += 12
            if bottom > self.H - 24:
                break
            self.card(x0, y, x1, bottom)
            PText(c, x0 + 18, yt, title, 2, fill=T.OK if green else T.TEXT, anchor="nw", width=w)
            PText(c, x0 + 18, yb, body, 2, fill=self.SOFT, anchor="nw", width=w)
            if src:
                PText(c, x0 + 18, ys, src, 2, fill=self.LABEL, anchor="nw", width=w)
            if green:
                nxt = (self.wake - timedelta(minutes=30)).strftime("%H:%M")
                CBtn(c, (x0 + x1) / 2, bottom - 34, x1 - x0 - 32, 40, "Шаг раньше → " + nxt,
                     self.step_earlier, u=4, sc=2, fill="#12301F", fg=T.OK, outline="#2B6B47")
            y = bottom + 12

    # ── страница «Сегодня» ──

    def _page_today(self):
        c, x0, x1 = self.cv, self.X0, self.X1
        now, wake = self.now, self.wake
        bed = wake - timedelta(hours=8)
        self._head("Сегодня")
        PText(c, x1, 44, "сейчас " + now.strftime("%H:%M"), 2, fill=self.SOFT, anchor="e")
        mins = int((bed - now).total_seconds() // 60)
        if mins > 0:
            head = "До сна %d ч %02d мин" % divmod(mins, 60)
            sub = ("Ложиться не раньше %s — если лечь раньше на одной усталости, сон может "
                   "оборваться через несколько часов." % bed.strftime("%H:%M"))
        else:
            head = "Окно сна открыто"
            sub = "Можно ложиться. Не уснул за 20 минут — встань и вернись, когда клонит."
        self.card(x0, 80, x1, 470)
        PText(c, x0 + 26, 124, head, 5, fill=T.TEXT, anchor="w", shadow=T.INK)
        PText(c, x0 + 26, 158, sub, 2, fill=self.SOFT, anchor="nw", width=x1 - x0 - 52)

        dim = bed - timedelta(hours=3)
        plan = [(dim, T.WARN, "Приглушить свет, экран потеплее", "вечерний свет сдвигает часы позже")]
        if self.cfg.get("bedtime"):
            fixed = hhmm_min(self.cfg.get("bed_fixed"))
            rem = (datetime.combine(bed.date(), datetime.min.time()) + timedelta(minutes=fixed)
                   if fixed is not None else wake - timedelta(minutes=self.cfg["bed_before_min"]))
            plan.append((rem, ON_C, "Тихое напоминание и проверка колонки", "без звука"))
        plan.append((bed, "#8FB4FF", "Можно ложиться", "не уснул за 20 мин — встать"))
        ns_now = rhythm(self.rows)
        tmin = ns_now[-1]["tmin"] if len(ns_now) >= 3 else None
        if tmin is not None and tmin - tpos(wake.hour * 60 + wake.minute) > 20:
            # подъём раньше минимума температуры: яркий свет сразу сдвинул бы часы позже
            plan.append((wake, T.OK, "Подъём, свет умеренный; на улицу после %s" % fmt_pos(tmin),
                         "свет до минимума сдвигает часы позже"))
        else:
            plan.append((wake, T.OK, "Подъём → сразу на улицу, 30+ мин света",
                         "часы двигаются раньше"))
        y = 236
        for when, col, what, why in sorted(plan, key=lambda p: p[0]):
            past = when < now
            hi = what == "Можно ложиться"
            pbox(c, x0 + 20, y - 22, x1 - 20, y + 22, 4,
                 fill=T.CARD_HI if hi else "#18244A", border=T.LINE if hi else T.INK, shadow=None)
            PText(c, x0 + 44, y, when.strftime("%H:%M"), 3,
                  fill=self.LABEL if past else col, anchor="w")
            PText(c, x0 + 150, y, ("► " if hi else "") + what, 2,
                  fill=self.LABEL if past else T.TEXT, anchor="w")
            PText(c, x1 - 44, y, why, 2, fill=self.LABEL, anchor="e")
            y += 56

        # полоса суток
        top = 494
        self.card(x0, top, x1, top + 170)
        self.label(x0 + 22, top + 26, "СУТКИ ПО ТВОЕМУ РИТМУ")
        ns = rhythm(self.rows)
        bx0, bx1 = x0 + 26, x1 - 26
        X = lambda p: bx0 + (p % 1440) * (bx1 - bx0) / 1440.0
        P = lambda dt: tpos(dt.hour * 60 + dt.minute)
        sy0, sy1 = top + 70, top + 102
        px_rect(c, bx0 - 4, sy0 - 4, bx1 + 4, sy1 + 4, T.INK)
        px_rect(c, bx0, sy0, bx1, sy1, "#18244A")
        px_rect(c, X(P(dim)), sy0, X(P(bed)), sy1, "#4A3A14")
        px_rect(c, X(P(bed)), sy0, X(P(wake)), sy1, "#3B4BA8")
        px_rect(c, X(P(wake)), sy0, X(P(wake) + 60), sy1, "#5A5418")
        sleep_txt = "сон %s–%s" % (bed.strftime("%H:%M"), wake.strftime("%H:%M"))
        lx = X(P(bed)) + 12
        if ns and -14 < X(ns[-1]["dlmo"]) - lx < text_w(sleep_txt, 2) + 14:
            lx = X(ns[-1]["dlmo"]) + 14              # подпись не налезает на точку мелатонина
        PText(c, lx, (sy0 + sy1) / 2, sleep_txt, 2, fill="#C8D3FF", anchor="w")
        if ns:
            px_dot(c, X(ns[-1]["dlmo"]), (sy0 + sy1) / 2, DLMO_C)
            px_dot(c, X(ns[-1]["tmin"]), (sy0 + sy1) / 2, TMIN_C)
            PText(c, x0 + 330, top + 26, "мелатонин ≈%s · минимум температуры ≈%s"
                  % (fmt_pos(ns[-1]["dlmo"]), fmt_pos(ns[-1]["tmin"])), 2,
                  fill=self.SOFT, anchor="w")
        nx = X(P(now))
        px_rect(c, nx - 2, sy0 - 14, nx + 2, sy1 + 10, T.ACCENT)
        PText(c, nx, sy0 - 26, "сейчас", 2, fill=T.ACCENT_2)
        for k in range(0, 25, 3):
            PText(c, X(k * 60) if k < 24 else bx1, sy1 + 24, fmt_pos(k * 60)[:2], 2,
                  fill=self.LABEL)

        bw = (x1 - x0 - 24) / 3.0
        for i, (lab, cmd) in enumerate((("ТЕСТ ЗВОНКА", self.test),
                                        ("ЗАПИСЬ НОЧИ", self.record),
                                        ("ОТЧЁТ О НОЧИ", self.report))):
            CBtn(c, x0 + bw / 2 + i * (bw + 12), top + 222, bw, 48, lab, cmd, u=4, sc=2,
                 fill=T.CARD, fg=self.SOFT)

    # ── страница «Ночи» ──

    def _page_nights(self):
        c, x0, x1 = self.cv, self.X0, self.X1
        self._head("Ночи")
        CBtn(c, x1 - 104, 44, 200, 40, "ЗАПИСАТЬ НОЧЬ", self.add_night, u=4, sc=2,
             fill=T.CARD, fg=T.OK)
        self.card(x0, 80, x1, self.H - 24)
        cols = [("Дата", 0), ("Лёг", 160), ("Уснул", 280), ("Проснулся", 410),
                ("Сон", 570), ("Будильник", 690), ("Решено за", 860)]
        for name, dx in cols:
            PText(c, x0 + 26 + dx, 112, name, 2, fill=T.SKY, anchor="w")
        for x in range(x0 + 18, x1 - 18, 16):                # пунктир под шапкой
            px_rect(c, x, 130, x + 8, 132, T.CARD_HI)
        rows = sorted(self.rows, key=lambda r: r["date"], reverse=True)[:22]
        if not rows:
            PText(c, (x0 + x1) / 2, 300, "Пока пусто — ночи появятся после звонков будильника.",
                  2, fill=self.SOFT)
        y = 154
        for r in rows:
            a, w = hhmm_min(r.get("asleep")), hhmm_min(r.get("woke"))
            dur = ""
            if a is not None and w is not None:
                d = tpos(w) - tpos(a)
                if 0 < d < 16 * 60:
                    dur = "%d:%02d" % divmod(d, 60)
            try:
                sec = int(r.get("solve_sec") or 0)
            except ValueError:
                sec = 0
            dd = datetime.strptime(r["date"], "%Y-%m-%d")
            vals = ["%s, %s" % (dd.strftime("%d.%m"), DAY_NAMES[dd.weekday()]),
                    r.get("lay") or "—", r.get("asleep") or "—", r.get("woke") or "—",
                    dur or "—", r.get("alarm") or "—",
                    ("%d:%02d" % divmod(sec, 60)) if sec else "—"]
            for (name, dx), v in zip(cols, vals):
                PText(c, x0 + 26 + dx, y, v, 2, fill=T.TEXT if v != "—" else T.DIM, anchor="w")
            y += 30


    # ─────────────── действия ───────────────

    def go(self, page):
        if page == "settings":
            self.open_settings()
            return
        self.page = page
        self.draw()

    def set_period(self, d):
        self.period = d
        self.draw()

    def shift(self, d):
        self._apply(shift_wake(load_cfg(), d, only_next=True))

    def step_earlier(self):
        self._apply(shift_wake(load_cfg(), -30, only_next=False))

    def _apply(self, cfg):
        if not save_cfg(cfg):
            return
        self.cfg = cfg
        self.draw()
        if self.enabled():
            if self.pending:
                self.root.after_cancel(self.pending)
            self.pending = self.root.after(1200, self.register)

    def cancel_pending(self):
        if self.pending:
            self.root.after_cancel(self.pending)
            self.pending = None

    def register(self):
        self.cancel_pending()
        cfg = load_cfg()
        self.bg(lambda: register_all(cfg),
                lambda res: (log("панель: задача обновлена — %s" % (res,)),
                             self.bg(task_status, self._got_status)))

    def toggle(self):
        self.cancel_pending()            # иначе отложенная регистрация включила бы будильник снова
        if self.enabled():
            self.bg(lambda: (task_disable(), task_remove_bed(), task_remove_light()),
                    lambda res: self.bg(task_status, self._got_status))
        else:
            self.register()

    def test(self):
        self.bg(task_run_now, lambda res: None)

    def record(self):
        exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        subprocess.Popen([exe if os.path.exists(exe) else sys.executable, SCRIPT, "--record"],
                         cwd=HERE, creationflags=NO_WINDOW)

    def report(self):
        try:
            names = sorted(n for n in os.listdir(REC_DIR)
                           if n.startswith("otchet_") and n.endswith(".txt"))
            if names:
                os.startfile(os.path.join(REC_DIR, names[-1]))        # noqa: S606
        except OSError:
            pass

    def add_night(self):
        DiaryForm(self.cfg, day=datetime.now().date(), parent=self.root).run()
        self.draw()

    def open_settings(self):
        s = Setup(parent=self.root)
        s.run()
        self.cfg = load_cfg()
        self.bg(task_status, self._got_status)
        self.draw()

    def quit(self):
        if self.pending:                 # время уже сохранено — донести его до Планировщика
            self.cancel_pending()
            register_all(load_cfg())
        self.alive = False
        close_window(self.root)

    def run(self):
        self.root.mainloop()


_FAULT_FILE = None


def main():
    global _FAULT_FILE
    tk_gc_guard()
    try:                                  # падения на уровне C (Tcl и т. п.) — тоже в журнал
        _FAULT_FILE = io.open(LOG, "a", encoding="utf-8")
        faulthandler.enable(_FAULT_FILE)
    except Exception:                                        # noqa: BLE001
        pass
    mode = next((a for a in sys.argv[1:] if a.startswith("--")), "панель")
    if mode not in ("--devices", "--check"):
        log("запуск: %s" % mode)
    try:
        _main()
    except Exception:
        log("ОШИБКА:\n" + traceback.format_exc())
        raise


def _main():
    if "--check" in sys.argv:
        level, text = speaker_check(load_cfg())
        print("%s: %s" % ({"ok": "ВСЁ ХОРОШО", "warn": "ВНИМАНИЕ", "bad": "ПРОБЛЕМА"}[level], text))
        return
    if "--devices" in sys.argv:
        for i, name in output_devices():
            print("%d: %s" % (i, name))
        return
    if "--bedtime" in sys.argv:
        BedtimeWindow().run()
        return
    if "--light" in sys.argv:
        arg = sys.argv[sys.argv.index("--light") + 1:]
        what = arg[0] if arg else "dawn"
        if what == "dawn":
            run_dawn()
        elif what in ("on", "off"):
            c = load_lights()
            ok, data = iot_on_off(c["token"], c["device_id"], what == "on")
            print(ok, data)
        else:
            print(light_setup())
        return
    if "--record" in sys.argv:
        RecorderWindow().run()
        return
    if "--up" in sys.argv:
        up_early()
        return
    if "--now" in sys.argv:
        cfg = load_cfg()
        test = take_test_flag()
        if test:                      # тест из панели — гасится одним ответом, без проверок
            cfg["need"] = 1
        wake_up(cfg, test=test)
        return
    if "--settings" in sys.argv:
        Setup().run()
        return
    Dashboard().run()


if __name__ == "__main__":
    main()
