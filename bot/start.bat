@echo off
rem TopBar feedback bot: double-click once. It runs hidden in the background and starts with Windows.
cd /d "%~dp0"
where pythonw >nul 2>nul || (echo Python isn't installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH". & pause & exit /b)
if not exist keys.txt (
  echo First-time setup. Paste each value and press Enter.
  set /p DT=Discord bot token: 
  set /p GT=GitHub token: 
  set /p CH=Feedback channel ID: 
  set /p OW=Your Discord user ID: 
  call :save
)
echo Installing...
python -m pip install -q -r requirements.txt
for /f "delims=" %%p in ('where pythonw') do (set PW=%%p& goto found)
:found
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v TopBarBot /t REG_SZ /d "\"%PW%\" \"%~dp0bot.py\"" /f >nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='pythonw.exe'\" | ? CommandLine -like '*bot.py*' | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>nul
start "" "%PW%" "%~dp0bot.py"
echo Done. The bot is running in the background and starts with Windows. You can close this.
timeout /t 5 >nul
exit /b
:save
(echo DISCORD_TOKEN=%DT%& echo GITHUB_TOKEN=%GT%& echo FEEDBACK_CHANNEL_ID=%CH%& echo OWNER_ID=%OW%) > keys.txt
exit /b
