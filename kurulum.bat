@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==============================
echo   AI Shorts Generator - Kurulum
echo ==============================
echo.

set PY=
python --version >nul 2>nul && set PY=python
if "%PY%"=="" py -3 --version >nul 2>nul && set PY=py -3
if "%PY%"=="" (
  echo [HATA] Python bulunamadi.
  echo 1. https://www.python.org/downloads/ adresinden indirip kur.
  echo 2. Kurulumda "Add python.exe to PATH" kutusunu ISARETLE.
  echo 3. Sonra bu dosyayi tekrar calistir.
  pause
  exit /b 1
)

echo [1/3] Python paketleri kuruluyor...
if not exist venv %PY% -m venv venv
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install edge-tts requests pillow
if errorlevel 1 (
  echo [HATA] Paket kurulumu basarisiz. Internet baglantini kontrol et.
  pause
  exit /b 1
)

echo.
echo [2/3] FFmpeg kontrol ediliyor...
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo FFmpeg yok, kuruluyor...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  echo.
  echo ONEMLI: FFmpeg kuruldu ama bu pencere onu goremez.
  echo Bu pencereyi KAPAT ve kurulum.bat dosyasini TEKRAR calistir.
  pause
  exit /b 0
) else (
  echo FFmpeg zaten kurulu.
)

echo.
echo [3/3] Pixabay API key
if exist pixabay_key.txt (
  echo Key zaten var, bu adim atlandi.
  goto bitti
)
if exist ..\shorts-fabrikasi\pixabay_key.txt (
  copy /y ..\shorts-fabrikasi\pixabay_key.txt pixabay_key.txt >nul
  echo Yandaki shorts-fabrikasi klasorunde key bulundu ve kopyalandi.
  goto bitti
)
echo Ucretsiz key: pixabay.com'a giris yap, pixabay.com/api/docs sayfasini ac.
echo Key'i yapistir ve Enter'a bas. Bos birakirsan yildizli gradyan arka plan kullanilir.
set KEY=
set /p KEY=Key: 
if not "%KEY%"=="" >pixabay_key.txt echo %KEY%

:bitti
echo.
echo Kurulum tamam! Simdi calistir.bat dosyasina cift tikla.
pause
