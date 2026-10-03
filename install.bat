@echo off
title Az Cut Setup
echo ============================================
echo  Az Cut - Setup
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python nahi mila.
  echo https://www.python.org/downloads/ se Python 3.10 ya naya install karein.
  echo Install ke waqt "Add python.exe to PATH" wala tick zaroor lagayein.
  pause
  exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [ERROR] ffmpeg nahi mila.
  echo Ye command chalayein - internet zaroori hai:
  echo    winget install ffmpeg
  echo Phir ye setup dobara chalayein.
  pause
  exit /b 1
)

echo [0/4] Microsoft Visual C++ runtime - download aur install ho raha hai...
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile '%TEMP%\vc_redist.x64.exe'"
if not exist "%TEMP%\vc_redist.x64.exe" (
  echo [NOTE] Download nahi ho saka. Ye link browser mein khud kholein:
  echo        https://aka.ms/vs/17/release/vc_redist.x64.exe
  echo        File chala kar Install dabayein, phir setup dobara chalayein.
  pause
  exit /b 1
)
echo Install ho raha hai - admin permission mange to Yes dabayein...
"%TEMP%\vc_redist.x64.exe" /install /quiet /norestart
set VCRC=%errorlevel%
if "%VCRC%"=="0" goto vcrdone
if "%VCRC%"=="1638" goto vcrdone
if "%VCRC%"=="3010" goto vcrdone
echo [NOTE] Visual C++ runtime install nahi ho saka. Khud install karein:
echo        https://aka.ms/vs/17/release/vc_redist.x64.exe
pause
exit /b 1
:vcrdone
echo Visual C++ runtime OK.
echo.

echo [1/4] Virtual environment ban raha hai...
python -m venv venv
call venv\Scripts\activate.bat

echo [2/4] Libraries install ho rahi hain (2-4 minute lag sakte hain)...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Libraries install nahi huin. Internet check kar ke dobara chalayein.
  pause
  exit /b 1
)

echo [3/4] Voice model download ho raha hai (sirf pehli baar, ~150MB)...
python -c "from faster_whisper import WhisperModel; WhisperModel('base'); print('Model ready.')"

echo.
echo ============================================
echo  Setup mukammal!
echo  - Browser mein chalane ke liye: start.bat
echo  - Desktop app ki tarah chalane ke liye: AzCut.bat
echo ============================================
pause
