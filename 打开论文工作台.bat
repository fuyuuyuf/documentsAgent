@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 文档解析智能体 - 论文工作台

if exist ".venv\Scripts\python.exe" (
    set "APP_PYTHON=.venv\Scripts\python.exe"
) else (
    set "APP_PYTHON=python"
)

"%APP_PYTHON%" src\bootstrap.py
if errorlevel 1 (
    echo.
    echo 请先安装依赖：python -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo 正在启动论文工作台，关闭此窗口会停止服务。
"%APP_PYTHON%" src\main.py --web
if errorlevel 1 pause
