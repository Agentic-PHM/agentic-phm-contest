@echo off
chcp 65001 >nul
title Agentic-PHM 演示启动
cd /d "%~dp0"
echo 正在启动 AGH 运行时与平台后端（首次约 1~2 分钟），完成后将自动打开浏览器...
python scripts\start_demo.py
echo.
echo 如浏览器未自动打开，请访问 http://127.0.0.1:8181 （登录 admin / 123456）
pause
