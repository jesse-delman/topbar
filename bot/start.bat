@echo off
rem TopBar feedback bot: double-click to start. First time it asks for your keys and saves them in keys.bat (next to this file, never uploaded).
cd /d "%~dp0"
where python >nul 2>nul || (echo Python isn't installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH". & pause & exit /b)
if not exist keys.bat (
  echo First-time setup. Paste each value and press Enter.
  set /p DT=Discord bot token: 
  set /p GT=GitHub token: 
  set /p CH=Feedback channel ID: 
  set /p OW=Your Discord user ID: 
  call :save
)
call keys.bat
python -m pip install -q -r requirements.txt
python bot.py
pause
exit /b
:save
(echo set DISCORD_TOKEN=%DT%& echo set GITHUB_TOKEN=%GT%& echo set FEEDBACK_CHANNEL_ID=%CH%& echo set OWNER_ID=%OW%) > keys.bat
exit /b
