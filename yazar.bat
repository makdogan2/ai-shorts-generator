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
python -c "import anthropic" 2>nul || python -m pip install -q anthropic
if not exist anthropic_key.txt (
  echo anthropic_key.txt yok. platform.claude.com ^> API keys ^> Create key; anahtari yapistir ve Enter:
  set KEY=
  set /p KEY=Key: 
  if not "%KEY%"=="" >anthropic_key.txt echo %KEY%
)
rem Ornekler:  yazar.bat space 10   /   yazar.bat space 5 --dry-run   /   yazar.bat --harcama
python yazar.py %*
pause
