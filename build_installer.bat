@echo off
title Az Cut - Setup.exe Builder
cd /d "%~dp0"

echo ============================================
echo  Az Cut - Setup.exe banane wala tool
echo  Ye aap ke PC par ek baar chalega
echo  Time: 10-20 minute - internet zaroori hai
echo ============================================
echo.

echo [0/7] Windows Defender exclusion lag rahi hai...
net session >nul 2>&1
if errorlevel 1 (
  echo NOTE: Admin rights nahi hain - exclusion skip.
  echo Agar build beech mein ruk jaye to ye script
  echo "Run as administrator" se chalayein.
) else (
  powershell -NoProfile -Command "Add-MpPreference -ExclusionPath '%CD%'" >nul 2>&1
  echo Defender exclusion lag gayi.
)
echo.

if not exist venv\Scripts\python.exe (
  echo [ERROR] venv nahi mila. Pehle install.bat chalayein.
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
mkdir build\assets 2>nul

echo [1/7] PyInstaller install ho raha hai...
pip install pyinstaller >nul 2>&1
where pyinstaller >nul 2>nul
if errorlevel 1 (
  echo [ERROR] PyInstaller install nahi hua.
  pause
  exit /b 1
)

echo [2/7] ffmpeg tayyar ho raha hai...
if not exist "build\assets\ffmpeg.exe" call :getffmpeg
if not exist "build\assets\ffmpeg.exe" (
  echo [ERROR] ffmpeg nahi mil saka.
  echo winget install ffmpeg chalayein, phir dobara try karein.
  pause
  exit /b 1
)
echo ffmpeg OK.

echo [3/7] System files download ho rahe hain...
if not exist "build\assets\vc_redist.x64.exe" (
  powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile 'build\assets\vc_redist.x64.exe'"
)

echo [4/7] Voice model copy ho raha hai...
if not exist "build\assets\model\model.bin" call :copymodel
if not exist "build\assets\model\model.bin" (
  echo [ERROR] Voice model copy nahi ho saka.
  pause
  exit /b 1
)
echo Model OK.

echo.
echo [4b/7] Auto-update setup - GitHub
echo Doston ko one-click updates dene ke liye apna GitHub username likhein.
echo Repo ka naam "azcut" hona chahiye. Khali chhor kar Enter = skip.
set /p GHUSER=GitHub username:
set UPDATEDATA=
if not "%GHUSER%"=="" (
  echo %GHUSER%/azcut> update_repo.txt
  set UPDATEDATA=--add-data "update_repo.txt;."
  echo Update repo set: %GHUSER%/azcut
) else (
  del update_repo.txt 2>nul
  echo Update system skip kiya gaya.
)
echo.

echo [5/7] EXE build ho rahi hai - 5-10 minute lag sakte hain...
echo [5a/7] PyInstaller pipe workaround apply ho raha hai...
python patch_pyinstaller.py
findstr /C:"PYINSTALLER_NO_ISOLATION" venv\Lib\site-packages\PyInstaller\isolated\_parent.py >nul
if errorlevel 1 (
  echo [ERROR] Patch verify nahi hui - _parent.py mein patch nahi mili!
  pause
  exit /b 1
)
set PYINSTALLER_NO_ISOLATION=1
echo Patch OK.
echo [5b/7] Workaround test - hello app ban rahi hai...
echo print("hello") > hello_test.py
pyinstaller --noconfirm --clean --name HelloTest hello_test.py
if not exist "dist\HelloTest\HelloTest.exe" (
  echo.
  echo [ERROR] Hello test nahi bana. Upar wali error lines ka screenshot bhejein.
  echo NOTE: Agar Windows Defender ne exe ko foran delete kiya ho to
  echo Windows Security - Protection History mein check karein.
  pause
  exit /b 1
)
echo Workaround OK - ab asli build shuru...
echo Poora log build_pyinstaller.log mein save ho raha hai.
pyinstaller --noconfirm --clean --name AzCut --windowed --icon icon.ico ^
  --add-data "static;static" ^
  --add-data "icon.ico;." ^
  --add-data "build\assets\ffmpeg.exe;." ^
  --add-data "build\assets\model;model" ^
  %UPDATEDATA% ^
  --hidden-import webview ^
  --hidden-import webview.platforms.edgechromium ^
  --hidden-import webview.platforms.winforms ^
  --hidden-import webview.platforms.cef ^
  --hidden-import webview.platforms.mshtml ^
  --hidden-import clr --hidden-import clr_loader --hidden-import pythonnet ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all onnxruntime ^
  --collect-all cv2 ^
  --collect-data webview ^
  desktop.py 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath build_pyinstaller.log"
if not exist "dist\AzCut\AzCut.exe" (
  echo.
  echo [ERROR] PyInstaller build fail ho gayi. Aakhri 40 lines:
  echo ----------------------------------------
  powershell -NoProfile -Command "Get-Content build_pyinstaller.log -Tail 40"
  echo ----------------------------------------
  echo Poora log build_pyinstaller.log mein hai.
  echo NOTE: Agar Windows Defender ne roka ho to us mein exclusion add karein.
  pause
  exit /b 1
)
echo EXE ban gayi.

echo.
echo [6/7] Test - exe khul rahi hai, 15 second rukiye...
echo NOTE: Agar Az Cut pehle se chal raha hai to abhi band kar dein.
pause
start "" "dist\AzCut\AzCut.exe"
timeout /t 15 /nobreak >nul
curl -s http://127.0.0.1:5000/api/version > "%TEMP%\azcut_ver.txt"
type "%TEMP%\azcut_ver.txt"
echo.
findstr /C:"0.8" "%TEMP%\azcut_ver.txt" >nul
if errorlevel 1 (
  echo [WARNING] Version check fail - phir bhi aagay barh rahe hain.
) else (
  echo [OK] Test kamyab - exe theek chal rahi hai.
)
taskkill /F /IM AzCut.exe >nul 2>nul

echo.
echo [7/7] Installer ban raha hai...
where iscc >nul 2>nul
if errorlevel 1 (
  echo Inno Setup install ho raha hai...
  winget install -e --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements
  set "PATH=C:\Program Files (x86)\Inno Setup 6;%PATH%"
)
where iscc >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Inno Setup nahi mila. Khud install karein:
  echo         https://jrsoftware.org/isdl.php
  echo Phir ye file dobara chalayein.
  pause
  exit /b 1
)
iscc installer.iss
if errorlevel 1 (
  echo [ERROR] Installer compile nahi ho saka.
  pause
  exit /b 1
)

echo.
echo ============================================
echo  MUBARAK! Setup.exe tayyar hai:
echo  Output\AzCut-Setup-0.8.exe
echo.
echo  Ye file dost ko bhej dein - Google Drive
echo  ya USB se. Koi aur setup nahi chahiye!
echo ============================================
pause
exit /b 0

:copymodel
for /f "delims=" %%P in ('python -c "from huggingface_hub import snapshot_download; print(snapshot_download(repo_id='guillaumekln/faster-whisper-base'))"') do set MODELPATH=%%P
xcopy "%MODELPATH%" "build\assets\model\" /E /I /Y /Q
goto :eof

:getffmpeg
for /f "delims=" %%F in ('where ffmpeg 2^>nul') do (
  echo Installed ffmpeg mil gaya, copy ho raha hai...
  copy "%%F" "build\assets\ffmpeg.exe" >nul
  goto :eof
)
echo Download ho raha hai...
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '%TEMP%\ffmpeg.zip'"
if not exist "%TEMP%\ffmpeg.zip" (
  echo Pehla link nahi khula, doosra try ho raha hai...
  powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile '%TEMP%\ffmpeg.zip'"
)
if exist "%TEMP%\ffmpeg.zip" (
  powershell -NoProfile -Command "Expand-Archive -Path '%TEMP%\ffmpeg.zip' -DestinationPath '%TEMP%\ffx' -Force"
  powershell -NoProfile -Command "$e = Get-ChildItem '%TEMP%\ffx' -Recurse -Filter ffmpeg.exe | Select-Object -First 1; Copy-Item $e.FullName 'build\assets\ffmpeg.exe' -Force"
)
goto :eof
