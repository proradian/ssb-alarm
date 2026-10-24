# Собирает архив релиза dist/ssb-alarm.zip и печатает SHA-256 для README (раздел «Безопасность»).
# Запуск из корня репозитория: powershell -File tools/build_release.ps1
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force dist | Out-Null
Compress-Archive -Path 'ssb_alarm.py', 'LICENSE', 'Запустить будильник.bat', 'Я встал.bat' -DestinationPath 'dist/ssb-alarm.zip' -Force
$h = (Get-FileHash 'dist/ssb-alarm.zip').Hash.ToLower()
"dist/ssb-alarm.zip  $((Get-Item 'dist/ssb-alarm.zip').Length) байт"
"SHA-256: $h"
if (-not (Select-String -Path README.md -SimpleMatch $h -Quiet)) { 'ВНИМАНИЕ: в README.md другая контрольная сумма — обновите раздел «Безопасность» (и README.en.md).' }
