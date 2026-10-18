@echo off
rem screen-recap server runner (spawned by start.bat in its own window)
rem keep pure ASCII; runs in bat-file context so no cmd /c quote issues
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d %~dp0
.venv\Scripts\python.exe run.py
echo.
echo [screen-recap] exited with code %errorlevel%
pause
