@echo off
setlocal
cd /d "%~dp0"
where py.exe >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
    if not errorlevel 1 (
        py -3 app.py
        goto finished
    )
)
where python.exe >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
    if not errorlevel 1 (
        python app.py
        goto finished
    )
)
echo Install Python 3.10 or later from https://www.python.org/downloads/windows/
echo Then open this launcher again.
pause
exit /b 1

:finished
if errorlevel 1 pause
