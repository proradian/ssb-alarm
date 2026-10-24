# -*- coding: utf-8 -*-
"""Скриншоты для README на демо-данных (Windows). Запуск: python tools/make_screenshots.py

Демо-дневник: человек засыпал в ~06:00 и сдвигал подъём лестницей на 30–60 минут —
на графике видно, как оценка ритма уезжает раньше. Настоящий diary.csv не трогается.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import tkinter as tk
from datetime import date, datetime, timedelta

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "docs", "screenshots")
spec = importlib.util.spec_from_file_location("a", os.path.join(ROOT, "ssb_alarm.py"))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

DEMO = [  # (засыпание, подъём, будильник, самочувствие)
    ("06:40", "14:40", "", 0), ("05:50", "13:45", "13:45", 2), ("05:10", "13:00", "13:00", 3),
    ("06:00", "12:30", "12:30", 2), ("04:30", "12:30", "12:30", 3), ("04:10", "11:30", "11:30", 3),
    ("03:40", "11:30", "11:30", 3), ("03:20", "11:00", "11:00", 4), ("02:50", "11:00", "11:00", 4),
    ("03:10", "11:00", "11:00", 3), ("02:40", "10:30", "10:30", 4), ("02:20", "10:30", "10:30", 4),
    ("02:30", "10:00", "10:00", 4), ("02:00", "10:00", "10:00", 5),
]


def demo_diary():
    path = os.path.join(tempfile.gettempdir(), "ssb_alarm_demo_diary.csv")
    rows, d0 = [], date.today() - timedelta(days=len(DEMO))
    for i, (on, off, al, feel) in enumerate(DEMO):
        rows.append({"date": (d0 + timedelta(days=i + 1)).isoformat(), "lay": "",
                     "asleep": on, "woke": off, "alarm": al, "solve_sec": "",
                     "attempts": "", "awakenings": "", "feel": "", "note": ""})
    a.diary_save(rows, path)
    return path


def grab(widget, name):
    widget.update()
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    w, h = widget.winfo_width(), widget.winfo_height()
    path = os.path.join(OUT, name)
    ps = ("Add-Type -AssemblyName System.Drawing;"
          "$b=New-Object Drawing.Bitmap %d,%d;$g=[Drawing.Graphics]::FromImage($b);"
          "$g.CopyFromScreen(%d,%d,0,0,$b.Size);$b.Save('%s');" % (w, h, x, y, path))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print("снято:", path, "%dx%d" % (w, h))


def main():
    os.makedirs(OUT, exist_ok=True)
    a.DIARY = demo_diary()
    cfg = a.load_cfg()
    cfg.update(hh=10, mm=0, plan={})

    root = tk.Tk()
    root.withdraw()
    rw = a.RhythmWindow(root, cfg)
    rw.top.attributes("-topmost", True)
    rw.top.geometry("+0+0")
    root.after(1200, lambda: (grab(rw.top, "rhythm.png"), root.quit()))
    root.mainloop()
    rw.top.destroy()

    a.diary_upsert(date.today(), woke="10:00", alarm="10:00")   # как после звонка
    form = a.DiaryForm(cfg, day=date.today(), parent=root)
    form.root.geometry("+0+0")
    root.after(900, lambda: (grab(form.root, "diary.png"), form.close()))
    form.run()
    root.destroy()


if __name__ == "__main__" and len(sys.argv) == 1:
    if sys.platform != "win32":
        sys.exit("нужен Windows")
    main()


def panel():
    """Окно настроек — отдельным процессом: у него свой корневой Tk.
    Статус задачи подставной, чтобы на картинку не попало настоящее расписание."""
    a.CONF = os.path.join(tempfile.gettempdir(), "ssb_alarm_demo_settings_panel.json")
    if os.path.exists(a.CONF):
        os.remove(a.CONF)
    a.task_status = lambda: {"exists": True, "state": "Ready", "wake": True,
                             "next": (date.today() + timedelta(days=1)).strftime("%d.%m") + "  06:30",
                             "last": date.today().strftime("%d.%m") + "  06:30", "hh": 6, "mm": 30}
    s = a.Setup()
    s.root.geometry("+0+0")
    s.root.after(7000, lambda: (grab(s.root, "panel.png"), s.root.destroy()))
    s.root.mainloop()


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "panel":
    panel()


def dashboard():
    """Единая панель на демо-данных: свой settings.json, статус и колонка — подставные."""
    import json
    a.DIARY = demo_diary()
    a.CONF = os.path.join(tempfile.gettempdir(), "ssb_alarm_demo_settings.json")
    today = date.today()
    plan = {(today + timedelta(days=2)).isoformat(): "09:30"}
    with open(a.CONF, "w", encoding="utf-8") as f:
        json.dump(dict(a.DEFAULTS, hh=10, mm=0, plan=plan, speaker="станция"), f)
    a.task_status = lambda: {"exists": True, "state": "Ready", "wake": True,
                             "next": (today + timedelta(days=1)).strftime("%d.%m") + "  10:00"}
    a.speaker_check = lambda cfg: ("ok", "колонка подключена, громкость 80%")
    d = a.Dashboard()
    d.root.geometry("+0+0")
    d.root.attributes("-topmost", True)

    def shoot(pages):
        if not pages:
            d.quit()
            return
        page, name = pages[0]
        d.go(page)
        d.root.after(700, lambda: (grab(d.root, name), shoot(pages[1:])))
    d.root.after(2500, lambda: shoot([("rhythm", "dashboard.png"), ("today", "today.png"),
                                      ("nights", "nights.png")]))
    d.run()


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "dashboard":
    dashboard()
