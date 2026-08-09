@echo off
%SystemRoot%\System32\chcp.com 936 >nul 2>nul
title 晴屿 - 智能天气桌面应用
setlocal
cd /d "%~dp0"

echo ============================================
echo     晴屿  智能天气桌面应用
echo     （独立程序 · 自动定位 · 内置推送）
echo ============================================
echo.

rem ---- 探测 Python（PATH / py 启动器 / 常见安装目录） ----
set "PYTHON_CMD="
where python >nul 2>nul && set "PYTHON_CMD=python"
if defined PYTHON_CMD goto :found
py -3 -c "import sys" >nul 2>nul && set "PYTHON_CMD=py -3"
if defined PYTHON_CMD goto :found
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
if defined PYTHON_CMD goto :found
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
if defined PYTHON_CMD goto :found
:found
if not defined PYTHON_CMD (
    echo [错误] 未找到 Python，请先安装 Python 3.9+ 并勾选 "Add to PATH"。
    echo.
    pause
    exit /b 1
)

%PYTHON_CMD% -c "import httpx,pydantic,pydantic_settings,webview" >nul 2>nul
if errorlevel 1 (
    echo 首次运行，正在安装依赖（约 1-2 分钟）...
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [错误] 依赖安装失败，请检查网络后重试。
        echo.
        pause
        exit /b 1
    )
)

echo 正在启动，独立窗口将自动打开（约 3 秒）...
echo 关闭窗口即可退出；程序运行期间推送任务自动执行。
echo.
%PYTHON_CMD% weather_desktop.py

echo.
echo 程序已退出。
pause
