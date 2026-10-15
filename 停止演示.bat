@echo off
chcp 65001 >nul
title Agentic-PHM 演示停止
cd /d "%~dp0"
python scripts\stop_demo.py
pause
