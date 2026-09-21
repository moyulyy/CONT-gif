@echo off
rem Debug run: keep the console open to see any error
cd /d "%~dp0"

set PY=D:\miniconda3\envs\chem_env\python.exe
if not exist "%PY%" set PY=python

"%PY%" contcar_to_gif.py -f samples\CONTCAR -o CONTCAR.gif
echo.
echo [exit code] %ERRORLEVEL%
pause
