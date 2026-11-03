@echo off
rem Double-click to start Playlist Sorter.
cd /d "%~dp0"

set "PY="
set "PYW="
where py >nul 2>nul && (set "PY=py" & set "PYW=pyw")
if not defined PY where python >nul 2>nul && (set "PY=python" & set "PYW=pythonw")
if not defined PY (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

%PY% -c "import googleapiclient, google_auth_oauthlib" >nul 2>nul
if errorlevel 1 (
    echo Installing required packages - first run only...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Could not install the required packages.
        pause
        exit /b 1
    )
)

start "" %PYW% -m playlist_sorter
