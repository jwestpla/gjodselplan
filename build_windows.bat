@echo off
rem Bygger Gjodselplan for Windows:
rem   dist\Gjodselplan\Gjodselplan.exe   (mappe med programmet)
rem   dist\Gjodselplan-<versjon>-windows.zip   (det du legger ut for nedlasting)
rem Krever Python 3.11 eller nyere fra python.org (tcl/tk er med som standard).
setlocal
cd /d "%~dp0"

set "PY=py -3.11"
%PY% --version >nul 2>&1 || set "PY=python"
%PY% --version >nul 2>&1 || (
  echo Fant ikke Python. Installer Python 3.11 fra https://www.python.org/downloads/windows/
  exit /b 1
)

if not exist ".venv-build\Scripts\python.exe" (
  %PY% -m venv .venv-build || exit /b 1
)
set "VPY=.venv-build\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip >nul || exit /b 1
"%VPY%" -m pip install -r requirements-build.txt || exit /b 1

echo === Tester ===
"%VPY%" -m pytest -q || exit /b 1

echo === Demodata (Testgard, oppdiktet) ===
"%VPY%" tools\make_demo_db.py || exit /b 1

echo === PyInstaller ===
"%VPY%" -m PyInstaller --noconfirm --clean gjodselplan.spec || exit /b 1
"%VPY%" tools\check_bundle.py dist\Gjodselplan || exit /b 1

echo === Selvtest av exe i tom datamappe ===
set "GJODSEL_DATA=%TEMP%\gjodselplan_selvtest"
if exist "%GJODSEL_DATA%" rmdir /s /q "%GJODSEL_DATA%"
start "" /wait "dist\Gjodselplan\Gjodselplan.exe" --selftest
set "RC=%ERRORLEVEL%"
type "%GJODSEL_DATA%\selftest.log"
if not "%RC%"=="0" (
  echo Selvtesten feilet.
  exit /b 1
)
set "GJODSEL_DATA="

"%VPY%" -c "import gjodsel; print(gjodsel.__version__)" > build\version.txt || exit /b 1
set /p VERSION=<build\version.txt
set "ZIP=dist\Gjodselplan-%VERSION%-windows.zip"
if exist "%ZIP%" del "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\Gjodselplan' -DestinationPath '%ZIP%'" || exit /b 1
echo.
echo Ferdig: %ZIP%
endlocal
