@echo off
rem Debug run: keep the console open to see any error
setlocal EnableExtensions
cd /d "%~dp0"

set "PY=D:\miniconda3\envs\chem_env\python.exe"
if not exist "%PY%" (
  for %%I in (python.exe) do set "PY=%%~$PATH:I"
)
if not exist "%PY%" (
  echo [ERROR] Python interpreter not found.
  echo         Expected: D:\miniconda3\envs\chem_env\python.exe
  pause
  exit /b 1
)

echo ============================================================
echo  CONTCAR rotation GIF - GUI (debug mode)
echo  Python : %PY%
echo  Script : %~dp0contcar_gif_gui.py
echo ============================================================
echo.

"%PY%" "%~dp0contcar_gif_gui.py"
echo.
echo [exit code] %errorlevel%
pause
