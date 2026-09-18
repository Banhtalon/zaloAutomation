@echo off
set PYTHONIOENCODING=utf-8
title Zalo Automation Controller
:: Di chuyen den thu muc chua file bat
cd /d "%~dp0"

:: Kiem tra neu thu muc venv ton tai
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Khong tim thay thu muc venv. Vui long tao virtual environment va cai dat thu vien truoc!
    pause
    exit /b
)

echo ======================================================
echo           Zalo Automation - He Thong Quet Bao Cao
echo ======================================================
echo [*] Dang kich hoat virtual environment (venv)...
call venv\Scripts\activate

echo [*] Dang mo trinh duyet den giao dien quan ly...
:: Khong khoi dong trung neu server da chay. Kiem tra health truoc khi mo Chrome.
set "APP_URL=http://127.0.0.1:8000"
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /R /C:"127.0.0.1:8000 .*LISTENING"') do set "PORT_PID=%%P"

if defined PORT_PID (
    echo [INFO] Cong 8000 dang duoc su dung boi PID %PORT_PID%.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-RestMethod -Uri '%APP_URL%/api/health/ready' -TimeoutSec 3; if ($r.ready -eq $true) { exit 0 } else { exit 1 } } catch { exit 1 }"
    if not errorlevel 1 (
        echo [OK] Server hien tai da san sang. Mo giao dien, khong khoi dong them.
        start "" "%APP_URL%"
        exit /b 0
    )
    echo [ERROR] Cong 8000 dang bi chiem nhung server khong phan hoi health.
    echo [INFO] Hay dong tien trinh PID %PORT_PID% roi chay lai START.bat.
    pause
    exit /b 1
)

echo [*] Dang khoi dong FastAPI Server (uvicorn)...
start "" cmd /c "timeout /t 2 >nul && start chrome %APP_URL%"
.\venv\Scripts\python.exe -m scripts.run_local

pause
