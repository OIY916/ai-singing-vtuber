@echo off
chcp 65001 >nul
title 大肥鱼直播间 · 控制台
rem Locate project root by walking up (marker: stream\main.py).
rem Works whether this bat sits in the project root or in scripts\live\.
set "ROOT=%~dp0"
for %%i in ("%ROOT%..\.." "%ROOT%.." "%ROOT%.") do (
    if exist "%%~fi\stream\main.py" set "ROOT=%%~fi\"
)
if not exist "%ROOT%stream\main.py" set "ROOT=%~dp0"
cd /d "%ROOT%"

rem Prefer the project venv (pythonw = no console window), else system pythonw.
if exist "%ROOT%astrbot-venv\Scripts\pythonw.exe" (
    start "" "%ROOT%astrbot-venv\Scripts\pythonw.exe" "%ROOT%tools\control_panel.py"
) else (
    start "" pythonw "%ROOT%tools\control_panel.py"
)
exit
