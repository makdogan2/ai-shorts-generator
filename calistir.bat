@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist venv\Scripts\activate.bat (
  echo Once kurulum.bat dosyasini calistir.
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
python pipeline.py
echo.
echo Bitti. Videolar "output" klasorunde, aciliyor...
if exist output start "" output
pause
