@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    py -3 -m venv "%SCRIPT_DIR%.venv"
    if errorlevel 1 exit /b %errorlevel%
)

"%PYTHON%" -m pip install --quiet --disable-pip-version-check -r "%SCRIPT_DIR%requirements.txt" -r "%SCRIPT_DIR%requirements-build.txt"
if errorlevel 1 exit /b %errorlevel%
"%PYTHON%" "%SCRIPT_DIR%build.py" %*
