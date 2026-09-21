@echo off
rem ============================================================
rem  Build the portable folder (PyInstaller onedir)
rem  Output: dist\CONTCAR_GIF\  (exe + _internal\)
rem  Copy that folder anywhere - no Python needed.
rem
rem  Set CONT_ONEFILE=1 before running to build a single exe instead.
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
  pause
  exit /b 1
)

echo ============================================================
echo  Building with: %PY%
if "%CONT_ONEFILE%"=="1" (
  echo  Mode: ONEFILE ^(single exe^)
) else (
  echo  Mode: PORTABLE FOLDER ^(recommended^)
)
echo ============================================================

rem Keep this env's DLL dirs ahead of base conda to avoid mixing DLLs
for %%D in ("%PY%") do set "ENVDIR=%%~dpD"
if exist "%ENVDIR%Library\bin" set "PATH=%ENVDIR%Library\bin;%ENVDIR%DLLs;%ENVDIR%Scripts;%PATH%"

"%PY%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo [INFO] Installing PyInstaller ...
  "%PY%" -m pip install pyinstaller || (echo [ERROR] pip install failed & pause & exit /b 1)
)

"%PY%" -m PyInstaller --noconfirm --clean CONTCAR_GIF.spec
if errorlevel 1 (
  echo.
  echo [ERROR] Build failed. See messages above.
  pause
  exit /b 1
)

echo.
echo ============================================================
if "%CONT_ONEFILE%"=="1" (
  echo  Done!  Single file: %~dp0dist\CONTCAR_GIF.exe
) else (
  echo  Done!  Portable folder: %~dp0dist\CONTCAR_GIF\
  echo         Run  CONTCAR_GIF.exe  inside that folder.
)
echo ============================================================
pause
