@echo off
REM Windows: double-click to run. First run installs packages and downloads models (~3GB).
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo 找不到 Python，請到 https://www.python.org/downloads/ 安裝 Python 3（勾選 Add python.exe to PATH）
  pause
  exit /b 1
)
%PY% install.py
if errorlevel 1 (
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" translator.py
