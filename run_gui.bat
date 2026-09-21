@echo off
rem CONTCAR rotation GIF - GUI launcher (no console window)
setlocal EnableExtensions
cd /d "%~dp0"

set "SCRIPT=%~dp0contcar_gif_gui.py"
set "PY=D:\miniconda3\envs\chem_env\python.exe"

if not exist "%SCRIPT%" (
  echo [ERROR] contcar_gif_gui.py not found in %~dp0
  pause
  exit /b 1
)
if not exist "%PY%" (
  for %%I in (python.exe) do set "PY=%%~$PATH:I"
)
if not exist "%PY%" (
  echo [ERROR] Python interpreter not found.
  echo         Expected: D:\miniconda3\envs\chem_env\python.exe
  pause
  exit /b 1
)

rem prefer pythonw.exe (no console); fall back to python.exe
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"

start "" "%PYW%" "%SCRIPT%"
exit /b 0
