# Changelog

## 1.0.0 — first public release

- One main window in a pixel style: alarm in the sidebar (−/+, on/off, "bed not before"), pages Today, Sleep rhythm (7/14/30 days) and Nights; alarm settings in a separate window.
- Per-date ladder is registered in Task Scheduler automatically (one-time triggers + daily), and "step earlier" shifts the whole schedule by 30 minutes.
- Full-screen alarm that stops only after N correct answers in a row (math and logic, three difficulty levels).
- Low ~520 Hz melodic tone with a 4-minute volume ramp and an "angry" stage after 8 minutes.
- Anti-snooze re-check 10 minutes after solving (soft rising sound, full alarm again if there's no answer).
- "I'm up" mode: woke up early, solve 10 problems and the alarm becomes a quiet check.
- Sleep log: wake time recorded automatically when the problems are solved, then one question ("when did you fall asleep?"); bedtime from the reminder.
- Circadian rhythm estimate (DLMO ≈ onset − 2 h, Tmin ≈ DLMO + 7 h) with a 14-day chart and evidence-based recommendations.
- Per-date wake-time ladder (`plan`) for gradual schedule shifts.
- Direct playback to a chosen (Bluetooth) speaker with volume guard and re-scan.
- Silent bedtime reminder with a speaker check.
- Optional night recording with a breathing-pause hint (not a diagnosis) and Yandex smart-home sunrise.
- Fixes after an independent review: no missed alarms between ladder dates, default output device volume and mute guarded when no speaker is set, the morning question closes itself so the 10-minute re-check always runs, −/+ no longer re-enables a switched-off alarm, sleep across 18:00 kept, no handler leak on redraw.
- Fix: Tcl crash (0x80000003) when Python's garbage collector ran in a non-Tk thread. Automatic GC is now disabled and collection runs on the Tk thread.
