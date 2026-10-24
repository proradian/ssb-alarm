# -*- coding: utf-8 -*-
"""Падение 0x80000003 (tcl86t.dll) 20.10.2026: окно проверки после звонка закрывалось
посреди решения. Причина: сборщик мусора Python сработал в потоке сирены и удалил
Tcl-интерпретатор первого окна не из того потока → Tcl_AsyncDelete → аварийный выход.

Сценарий как в будильнике: окно → закрыли → в чужом потоке много аллокаций
(автоматический GC) → второе окно. С защитой alarm.tk_gc_guard() процесс выживает."""
import os
import subprocess
import sys

ALARM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssb_alarm.py")

SCENARIO = r'''
import gc, importlib.util, sys, threading, tkinter as tk
sp = importlib.util.spec_from_file_location("a", %r)
a = importlib.util.module_from_spec(sp); sp.loader.exec_module(a)
if sys.argv[1] == "guard":
    a.tk_gc_guard()
gc.set_threshold(100, 1, 1)           # чтобы автоматический GC точно сработал в потоке

class W:
    def __init__(self):
        self.root = tk.Tk()
        self.cb = lambda: self        # цикл ссылок — как у окон будильника
        self.root.after(200, lambda: a.close_window(self.root))

w = W(); w.root.mainloop(); del w     # первое окно стало мусором

def churn():                          # как поток сирены: просто живёт и аллоцирует
    junk = []
    for i in range(200000):
        x = []; x.append(x); junk.append(x)
t = threading.Thread(target=churn); t.start(); t.join()

w2 = W(); w2.root.mainloop()
print("ВЫЖИЛ")
''' % ALARM


def run(mode):
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", SCENARIO, mode],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    return p.returncode, (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]


if __name__ == "__main__":
    code_bad, out_bad = run("noguard")
    code_ok, out_ok = run("guard")
    print("без защиты: код %d, %s" % (code_bad, out_bad[0]))
    print("с защитой:  код %d, %s" % (code_ok, out_ok[0]))
    ok = code_ok == 0 and out_ok[0] == "ВЫЖИЛ"
    print("ИТОГ:", "всё прошло" if ok else "ПРОВАЛ")
    sys.exit(0 if ok else 1)
