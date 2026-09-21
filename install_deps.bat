@echo off
rem ============================================================
rem  Install Python dependencies for CONTCAR rotation GIF
rem  (ase / numpy / pillow / playwright / PySide6)
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "PY=D:\miniconda3\envs\chem_env\python.exe"
if not exist "%PY%" (
  for %%I in (python.exe) do set "PY=%%~$PATH:I"
)
if not exist "%PY%" (
  echo [ERROR] Python interpreter not found.
  echo         Expected: D:\miniconda3\envs\chem_env\python.exe
  echo         Install Miniconda and create the env, or edit PY in this file.
  pause
  exit /b 1
)

echo ============================================================
echo  Python : %PY%
echo  Installing:
echo    ase numpy pillow playwright PySide6
echo ============================================================
echo.

"%PY%" -m pip install --upgrade pip
if errorlevel 1 (
  echo [WARN] pip upgrade failed, continuing with existing pip.
)

"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [ERROR] pip install failed. See messages above.
  pause
  exit /b 1
)

rem Prefer the system Edge/Chrome; install Playwright's chromium as a fallback.
echo.
echo [INFO] Installing Playwright chromium fallback (optional) ...
"%PY%" -m playwright install chromium

echo.
echo ============================================================
echo  [Done] Dependencies installed.
echo  Start the GUI with: run_gui.bat
echo ============================================================
pause
