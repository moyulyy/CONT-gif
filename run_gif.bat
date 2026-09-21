@echo off
rem Run: generate CONTCAR.gif from samples\CONTCAR
cd /d "%~dp0"

set PY=D:\miniconda3\envs\chem_env\python.exe
if not exist "%PY%" set PY=python

"%PY%" contcar_to_gif.py -f samples\CONTCAR -o CONTCAR.gif
echo.
echo [Done] CONTCAR.gif generated.
pause
