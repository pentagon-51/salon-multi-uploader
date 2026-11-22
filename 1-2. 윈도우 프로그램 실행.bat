@echo off
chcp 65001 >nul
title 미용실 멀티업로더
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "PYTHONW_EXE="
for %%C in (
    "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"
    "%ProgramFiles%\Python312\pythonw.exe"
    "%ProgramFiles%\Python313\pythonw.exe"
    "%ProgramFiles%\Python314\pythonw.exe"
    "%ProgramFiles%\Python311\pythonw.exe"
    "%ProgramFiles%\Python310\pythonw.exe"
) do (
    if not defined PYTHONW_EXE if exist "%%~C" set "PYTHONW_EXE=%%~C"
)

if not defined PYTHONW_EXE (
    for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do (
        echo "%%P" | findstr /i "WindowsApps" >nul
        if errorlevel 1 if not defined PYTHONW_EXE set "PYTHONW_EXE=%%P"
    )
)

if not defined PYTHONW_EXE (
    for %%C in (
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    ) do (
        if not defined PYTHONW_EXE if exist "%%~C" set "PYTHONW_EXE=%%~C"
    )
)

if not defined PYTHONW_EXE (
    for /f "delims=" %%P in ('where python.exe 2^>nul') do (
        echo "%%P" | findstr /i "WindowsApps" >nul
        if errorlevel 1 if not defined PYTHONW_EXE set "PYTHONW_EXE=%%P"
    )
)

if not defined PYTHONW_EXE (
    echo [오류] Python을 찾을 수 없습니다. [1-1. 윈도우 최초설치.bat]을 먼저 실행해 주세요.
    pause
    exit /b 1
)

start /wait "" "%PYTHONW_EXE%" "%~dp0auto_updater.py"

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765 " ^| findstr "LISTENING"') do (
    if not "%%a"=="0" taskkill /F /PID %%a >nul 2>&1
)

start "" "%PYTHONW_EXE%" "%~dp0app_server.py" > "%~dp0app.log" 2>&1

powershell -NoProfile -Command "for($i=0;$i -lt 25;$i++){try{$c=New-Object Net.Sockets.TcpClient;$ar=$c.BeginConnect('127.0.0.1',8765,$null,$null);if($ar.AsyncWaitHandle.WaitOne(200,$false) -and $c.Connected){$c.Close();break};$c.Close()}catch{};Start-Sleep -Milliseconds 300}" >nul 2>&1
start "" "http://127.0.0.1:8765"
exit
