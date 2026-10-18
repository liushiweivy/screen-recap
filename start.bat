@echo off
rem Screen Recap: one-click start (backend :8870)
rem NOTE: keep this file pure ASCII. UTF-8 Chinese + chcp 65001 corrupts cmd's
rem batch parser (byte-offset drift) and the script dies mid-file.
cd /d %~dp0

echo ============================================
echo  Screen Recap - one-click start
echo ============================================

where py >nul 2>nul
if %errorlevel%==0 (
  set PYBOOT=py -3
) else (
  set PYBOOT=python
)

if not exist ".venv" (
  echo [1/2] first run: creating venv and installing deps (large, be patient)...
  %PYBOOT% -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
) else (
  echo [1/2] venv ready
)

echo [2/2] starting server :8870 ...
rem NOTE: do NOT inline "chcp 65001 >nul && ..." into a cmd /k string - the
rem quoted-compound form intermittently fails with "path not found" on this
rem machine. Delegate to the runner bat instead.
start "screen-recap" cmd /k "%~dp0run-server.bat"

echo.
echo open http://localhost:8870
echo (close the spawned console window to stop)
