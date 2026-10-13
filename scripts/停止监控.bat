@echo off
chcp 65001 >nul
echo 正在停止微信监控...
taskkill /f /im python.exe /fi "WINDOWTITLE eq 微信群聊实时监控*" 2>nul
if %errorlevel%==0 (
    echo 已停止
) else (
    echo 没找到监控进程，可能已经停了
)
timeout /t 2 /nobreak >nul
