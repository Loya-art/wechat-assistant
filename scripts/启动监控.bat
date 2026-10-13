@echo off
chcp 65001 >nul
title 微信群聊实时监控
echo ============================================
echo   微信群聊实时监控
echo   每30秒扫描一次新消息，重要的推飞书
echo   Ctrl+C 停止
echo ============================================
echo.

cd /d "E:\<user>\知识库\skills\wechat-assistant\scripts"

:: 检查密钥
if not exist "..\..\wechat-chat-export\scripts\db_key.json" (
    echo [!] 密钥文件不存在，先抓取密钥：
    echo     cd ..\..\wechat-chat-export\scripts
    echo     python capture2.py --watch
    echo     然后重启微信，等它抓到 key
    echo.
    pause
    exit /b 1
)

python live_monitor.py --interval 30

pause
