@echo off
chcp 65001 >nul
title 大肥鱼直播间 · 控制台
cd /d E:\qq-bot
start "" "E:\qq-bot\astrbot-venv\Scripts\pythonw.exe" "E:\qq-bot\tools\control_panel.py"
exit
