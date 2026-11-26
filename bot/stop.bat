@echo off
rem Stops the bot and keeps it from starting with Windows. Run start.bat to turn it back on.
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v TopBarBot /f >nul 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='pythonw.exe'\" | ? { $_.CommandLine -like '*bot.py*' -or $_.CommandLine -like '*runner.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Bot stopped.
timeout /t 3 >nul
