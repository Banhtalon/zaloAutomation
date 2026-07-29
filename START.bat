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
:: Tu dong mo trinh duyet sau 2 giay khi server dang khoi dong
start "" cmd /c "timeout /t 2 >nul && start chrome http://127.0.0.1:8000"

echo [*] Dang khoi dong FastAPI Server (uvicorn)...
.\venv\Scripts\python.exe main.py

pause
