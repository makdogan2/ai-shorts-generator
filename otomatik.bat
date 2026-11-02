@echo off
rem Haftalik otomatik is: 14 yeni senaryo uret (API), sonra videosu olmayan konulari render et.
rem Gorev Zamanlayici bunu Pazar 21:00 calistirir (zamanlayici-kur.bat). Cikti: output\otomatik.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist output mkdir output
call venv\Scripts\activate.bat
echo ==== %date% %time% yazar.py space 14 >> output\otomatik.log
python yazar.py space 14 --yes --budget 3 --aylik-tavan 10 >> output\otomatik.log 2>&1
echo ==== %date% %time% render >> output\otomatik.log
python pipeline.py space >> output\otomatik.log 2>&1
if exist youtube_token.json (
  echo ==== %date% %time% yukle >> output\otomatik.log
  python yukle.py space >> output\otomatik.log 2>&1 < nul
)
echo ==== %date% %time% bitti (cikis %errorlevel%) >> output\otomatik.log
