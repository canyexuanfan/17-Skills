@echo off
rem Private Image Compressor launcher (auto-detect Python, install Pillow if missing)
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Please install Python 3.10+ from python.org first.
  pause
  exit /b 1
)
python -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo Installing Pillow ...
  python -m pip install --quiet Pillow
)
echo Starting Private Image Compressor at http://127.0.0.1:8765 ...
start "" python "%~dp0privacy_server.py"
