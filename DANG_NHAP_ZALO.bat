@echo off
title Dang nhap Zalo Automation
cd /d "%~dp0"
echo ======================================================
echo    DANG MO TRINH DUYET DE QUET MA QR ZALO...
echo ======================================================
echo Sau khi dang nhap xong va thay danh sach tin nhan:
echo HAY DONG (TAT) CUA SO TRINH DUYET DO DE HOAN TAT!
echo ======================================================
echo.
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --new-window --user-data-dir="%~dp0zalo_session" "https://chat.zalo.me"
