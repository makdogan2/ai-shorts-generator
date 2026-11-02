@echo off
rem Tek sefer cift tikla: haftalik otomatik gorevi kurar (Pazar 21:00 otomatik.bat).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0zamanlayici-kur.ps1"
echo.
pause
