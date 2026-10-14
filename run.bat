@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "BUNDLED_APP=%SCRIPT_DIR%dist\VikhmanSSH\VikhmanSSH.exe"
set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"

if exist "%BUNDLED_APP%" (
    start "" "%BUNDLED_APP%" %*
    exit /b 0
)

if not exist "%PYTHON%" (
    python -m venv "%SCRIPT_DIR%.venv"
)

"%PYTHON%" -m pip install --quiet --disable-pip-version-check -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 exit /b %errorlevel%
"%PYTHON%" "%SCRIPT_DIR%main.py" %*
