# SSB Alarm — the hardest alarm clock

[Русский](README.md) | **English**

A Windows alarm clock built on sleep research. SSB stands for «Самый Сложный Будильник» — Russian for “the hardest alarm clock”.

> **The interface is in Russian.** This page is a short English overview; the full description with every study linked is in the [Russian README](README.md).

<p align="center">
  <a href="https://github.com/proradian/ssb-alarm/releases/latest/download/ssb-alarm.zip"><img src="https://img.shields.io/badge/Download-for%20Windows-ff3b5c?style=for-the-badge&logo=windows&logoColor=white" alt="Download for Windows"></a>
  <br>
  <sub>Windows 10 and 11 · requires <a href="https://www.python.org/downloads/">Python 3</a> · 65 KB archive · <a href="https://github.com/proradian/ssb-alarm/releases">all releases</a> · <a href="#safety">safety</a></sub>
</p>

<p align="center">
  <img src="docs/screenshots/alarm.png" width="640" alt="The alarm: full screen, on top of every window, problems in a row">
</p>

<p align="center">
  <img src="docs/screenshots/dashboard.png" width="420" alt="Sleep rhythm: sleep onset and wake time by day, the target and advice">
  &nbsp;
  <img src="docs/screenshots/today.png" width="420" alt="Today: tonight's plan by the clock">
</p>

## Features

- **Hard to dismiss.** A full-screen alarm on top of every window that stops only after several correct answers in a row (math and logic, three difficulty levels). A low ~520 Hz melodic tone ramps up over 4 minutes and turns harsh after 8.
- **No falling back asleep.** 10 minutes after you solve it, another problem appears with a soft sound; no answer in 2 minutes means the full alarm again. Woke up early? The "I'm up" shortcut turns the alarm into a quiet check after 10 problems.
- **Same wake time, "bed not before".** A silent bedtime reminder shows the "bed not before" time (wake − 8 h) and checks that the speaker is connected while you're still awake.
- **Gradual schedule shifts.** Per-date wake times and a "step earlier −30 min" button; the whole schedule is registered in Windows Task Scheduler.
- **One-question sleep log.** Wake time is when you solved the problems; afterwards it asks only when you fell asleep.
- **Rhythm chart and advice.** Sleep onset and wake lines with the target, an estimated melatonin onset (≈ usual sleep onset − 2 h) and core-temperature minimum (≈ melatonin + 7 h), and advice on morning light, a dim evening and whether to step earlier or hold.
- **Bluetooth speaker routing** with a volume guard, plus an optional night recording (not a diagnosis) and Yandex smart-home sunrise.

## What it is built on

Regularity predicts health more strongly than sleep duration ([Windred 2024](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10782501/)). In experiments the body clock kept up with a shift of about an hour a day, and much better with bright morning light ([Burgess 2003](https://pubmed.ncbi.nlm.nih.gov/12932084/); [Eastman 2005](https://pmc.ncbi.nlm.nih.gov/articles/PMC1249488/)). Restricting time in bed and getting up when you can't sleep are the most effective parts of CBT for insomnia ([network meta-analysis 2024](https://pubmed.ncbi.nlm.nih.gov/39504928/)). Light before the core-temperature minimum delays the clock, light after it advances it ([Khalsa 2003](https://pubmed.ncbi.nlm.nih.gov/12717008/)). The full list with verification marks is in [docs/research/SOURCES.md](docs/research/SOURCES.md) (Russian).

## Safety

- **Open source.** The whole app is one file, [`ssb_alarm.py`](ssb_alarm.py), using only the Python standard library.
- **Untampered archive.** SHA-256 of `ssb-alarm.zip` v1.0.0:
  `a47cc2330ffab00db034a504cbbe96134551693aaf8f6865255ce302c9f685bf`
  Check in PowerShell: `Get-FileHash .\ssb-alarm.zip`.
- **What it does on your PC.** Creates Task Scheduler tasks («Будильник», «Будильник-отбой»), raises the chosen speaker's volume before ringing and restores it afterwards, uses the microphone only in "night recording" mode, and goes online only to the Yandex smart-home API if you set up the sunrise light.

## Install

1. Install [Python 3](https://www.python.org/downloads/) with **Add python.exe to PATH** checked.
2. Download and unpack the [archive](https://github.com/proradian/ssb-alarm/releases/latest/download/ssb-alarm.zip).
3. Run **«Запустить будильник.bat»**, set the time with − and +, and switch the alarm on.

## Disclaimer

This is **not a medical device**. The rhythm estimate is a behavioral approximation and the night recording is not a diagnosis. Very long sleep, loud snoring, breathing pauses or daytime sleepiness are a reason to see a sleep specialist.

## License

[MIT](LICENSE)
