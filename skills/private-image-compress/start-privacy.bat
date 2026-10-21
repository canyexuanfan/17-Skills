@echo off
title Private Image Compressor (127.0.0.1:8765)
rem One-click launcher: auto-detect Python, install Pillow if missing, start local server.
rem The server runs in THIS window so errors stay visible. Close the window (or Ctrl+C) to stop.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Please install Python 3.10+ from python.org first.
  pause
  exit /b 1
)

python -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo Installing Pillow ...
  python -m pip install Pillow
)

cd /d "%~dp0scripts"
echo.
echo   Private Image Compressor : http://127.0.0.1:8765
echo   Browser opens automatically. Close this window (or Ctrl+C) to stop.
echo.
python privacy_server.py

echo.
echo Server stopped.
pause
