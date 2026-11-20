@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

echo ================================================================
echo  [헤어살롱 SNS 멀티 업로더] 최초 자동 설치
echo ================================================================
echo.

if not exist "%~dp0.installed_done" (
    echo [1/5] 설치 환경 초기화 중...
    del /f /q "%~dp0*_cookies.json" >nul 2>&1
    del /f /q "%~dp0youtube_token.json" >nul 2>&1
    del /f /q "%~dp0client_secret.json" >nul 2>&1
    del /f /q "%~dp0gemini_api_key.txt" >nul 2>&1
    del /f /q "%~dp0user_profile.json" >nul 2>&1
    del /f /q "%~dp0daily_usage.json" >nul 2>&1
    del /f /q "%~dp0login_credentials.json" >nul 2>&1
    del /f /q "%~dp0블랙박스_*.webm" >nul 2>&1
    del /f /q "%~dp0app.log" >nul 2>&1
    del /f /q "%~dp0export_code.py" >nul 2>&1
    del /f /q "%~dp0check_personal_leak.py" >nul 2>&1
    del /f /q "%~dp0total_code*.txt" >nul 2>&1
    del /f /q "%~dp0export_error.log" >nul 2>&1
    del /f /q "%~dp0*.command" >nul 2>&1
    for %%D in (auth_data blackbox_login blackbox_upload upload done debug_records temp_thumbs) do (
        if exist "%~dp0%%D" (
            rmdir /s /q "%~dp0%%D" >nul 2>&1
            mkdir "%~dp0%%D" >nul 2>&1
        )
    )
    if exist "%~dp0blackbox" (
        rmdir /s /q "%~dp0blackbox" >nul 2>&1
    )
    if exist "%~dp0_old_backup" (
        attrib -h -r -s /s /d "%~dp0_old_backup\*.*" >nul 2>&1
        rmdir /s /q "%~dp0_old_backup" >nul 2>&1
    )
    if exist "%~dp0.git" (
        attrib -h -r -s "%~dp0.git" >nul 2>&1
        attrib -h -r -s /s /d "%~dp0.git\*.*" >nul 2>&1
        rmdir /s /q "%~dp0.git" >nul 2>&1
    )
) else (
    echo [1/5] 기존 설치 환경 확인 완료.
)

set "PYTHON_EXE="
call :FIND_PYTHON

if defined PYTHON_EXE (
    echo [2/5] Python 3.10+ 확인 완료: %PYTHON_EXE%
    goto :INSTALL_PACKAGES
)

echo [2/5] 실행 환경(Python 3.10+) 자동 설치를 시작합니다...

set "LOCAL_PY_INST="
for %%F in ("%~dp0python-*.exe") do (
    if not defined LOCAL_PY_INST set "LOCAL_PY_INST=%%~fF"
)

if defined LOCAL_PY_INST (
    echo       동봉된 설치 파일 실행 중...
    "%LOCAL_PY_INST%" /passive InstallAllUsers=0 PrependPath=1 Include_test=0
    goto :AFTER_PY_INSTALL
)

where winget >nul 2>&1
if not errorlevel 1 (
    echo       Windows 공식 설치기(winget)로 Python 3.12 설치 중...
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    call :FIND_PYTHON_AFTER_INSTALL
    if defined PYTHON_EXE goto :INSTALL_PACKAGES
    echo       공식 설치기 직접 다운로드로 전환합니다...
)

echo       Python 공식 설치기 다운로드 중...
set "TMP_PY_EXE=%TEMP%\python-3.12.8-amd64.exe"
curl.exe -L -f -o "%TMP_PY_EXE%" "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
if not errorlevel 1 if exist "%TMP_PY_EXE%" (
    echo       Python 3.12.8 자동 설치 진행 중...
    "%TMP_PY_EXE%" /passive InstallAllUsers=0 PrependPath=1 Include_test=0
    del /f /q "%TMP_PY_EXE%" >nul 2>&1
)

:AFTER_PY_INSTALL
call :FIND_PYTHON_AFTER_INSTALL
if not defined PYTHON_EXE (
    echo.
    echo [오류] Python 실행 파일을 찾지 못했습니다.
    echo        PC를 재부팅하거나 Python 3.12 공식 설치기를 설치한 뒤 다시 실행해 주세요.
    echo        (설치 파일 및 안내서는 이동하지 않고 그대로 유지했습니다.)
    pause
    exit /b 1
)

:INSTALL_PACKAGES
echo.
echo [3/5] 필수 모듈 및 자동화 브라우저(Chromium) 엔진 설치 중...
"%PYTHON_EXE%" -m pip install --upgrade pip
"%PYTHON_EXE%" -m pip install --upgrade playwright opencv-python pillow pyperclip google-api-python-client google-auth-oauthlib
if errorlevel 1 goto :INSTALL_FAIL
"%PYTHON_EXE%" -m playwright install chromium
if errorlevel 1 goto :INSTALL_FAIL

echo.
echo [4/5] 설치 완료 2차 검증 중...
"%PYTHON_EXE%" -c "import playwright, cv2, PIL, pyperclip, googleapiclient"
if errorlevel 1 goto :INSTALL_FAIL

echo.
echo [5/5] 바탕화면 바로가기 생성 및 폴더 정리 중...
set "SNS_DIR=%~dp0"
powershell -NoProfile -Command "$d = $env:SNS_DIR.TrimEnd('\'); $desk = [Environment]::GetFolderPath('Desktop'); foreach($old in @('플랜에이치 업로더.lnk','플랜에이치 마곡나루역점 업로더.lnk')) { $p = Join-Path $desk $old; if (Test-Path $p) { Remove-Item -Force $p -ErrorAction SilentlyContinue } }; $ws = New-Object -ComObject WScript.Shell; $lnk = Join-Path $desk '헤어살롱 SNS 업로더.lnk'; $s = $ws.CreateShortcut($lnk); $s.TargetPath = Join-Path $d '2_프로그램_실행.bat'; $s.WorkingDirectory = $d; $s.Description = '헤어살롱 SNS 멀티 업로더'; $ico = Join-Path $d 'planh_uploader.ico'; if (Test-Path $ico) { $s.IconLocation = $ico + ',0' } else { $s.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ',220' }; $s.Save()" >nul 2>&1

echo installed > "%~dp0.installed_done"
if not exist "%~dp0_설치완료_보관함" mkdir "%~dp0_설치완료_보관함"
if exist "%~dp00_처음사용자_안내서.html" move /y "%~dp00_처음사용자_안내서.html" "%~dp0_설치완료_보관함\" >nul 2>&1
for %%F in ("%~dp0python-*.exe") do (
    if exist "%%~fF" move /y "%%~fF" "%~dp0_설치완료_보관함\" >nul 2>&1
)

echo.
echo ================================================================
echo  설치가 모두 완료되었습니다! 프로그램을 자동으로 실행합니다...
echo ================================================================
start "" "%~dp02_프로그램_실행.bat"
start /b "" cmd /c "timeout /t 2 /nobreak > nul & move /y "%~f0" "%~dp0_설치완료_보관함""
exit /b 0

:INSTALL_FAIL
echo.
echo ================================================================
echo  [오류] 필수 패키지 또는 브라우저 설치·검증에 실패했습니다.
echo  네트워크 상태나 백신(V3/Windows 보안) 예외 설정을 확인하신 뒤
echo  다시 실행하거나 [0_처음사용자_안내서.html]의 비상 명령어를 참고해 주세요.
echo  (설치 실패로 안내서 및 설치 파일은 이동하지 않았습니다.)
echo ================================================================
pause
exit /b 1

:FIND_PYTHON_AFTER_INSTALL
set "PYTHON_EXE="
for %%C in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python314\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python310\python.exe"
) do (
    if not defined PYTHON_EXE if exist "%%~C" (
        "%%~C" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if not errorlevel 1 set "PYTHON_EXE=%%~C"
    )
)
if defined PYTHON_EXE exit /b 0
goto :FIND_PYTHON

:FIND_PYTHON
set "PYTHON_EXE="
python --version >nul 2>&1
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)"`) do set "PYTHON_EXE=%%P"
        if defined PYTHON_EXE exit /b 0
    )
)
py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        for /f "usebackq delims=" %%P in (`py -3 -c "import sys; print(sys.executable)"`) do set "PYTHON_EXE=%%P"
        if defined PYTHON_EXE exit /b 0
    )
)
for %%C in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python310\python.exe"
) do (
    if not defined PYTHON_EXE if exist "%%~C" (
        "%%~C" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if not errorlevel 1 set "PYTHON_EXE=%%~C"
    )
)
exit /b 0
