# -*- coding: utf-8 -*-
"""生成可双击运行的 .bat 包装器。

编码要点（踩过的坑）：
  1. .bat 本身必须是 GBK —— cmd 默认代码页 936，UTF-8 的 bat 会让中文路径变乱码。
  2. 但为了 Python 输出中文不乱码，又要 chcp 65001。
     两者冲突：chcp 切 UTF-8 后，cmd 再读 bat 里 GBK 中文字面量就乱。
  3. 解法：bat 内部提示文字全用 ASCII，只让 Python 输出中文。

可移植性：
  不写死任何本机路径。查找顺序
    1) 环境变量 RVC_PY（用户自己设）
    2) 同目录/上级目录的 Runtime\\python.exe（有人把包解到 RVC 里）
    3) PATH 里的 python
"""
import os

TARGET_DIR = r"E:\qq-bot\tools"
BAT_NAME = "解密ncm-拖到这里.bat"

BAT_TEMPLATE = """@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

rem ---- find a python: RVC_PY env > local Runtime > PATH python ----
set "PY="

if defined RVC_PY if exist "%RVC_PY%" set "PY=%RVC_PY%"

if not defined PY (
    for %%D in ("%~dp0." "%~dp0.." "%~dp0..\\..") do (
        if exist "%%~fD\\Runtime\\python.exe" if not defined PY set "PY=%%~fD\\Runtime\\python.exe"
    )
)

if not defined PY (
    where python >nul 2>nul && set "PY=python"
)

if not defined PY (
    echo [ERROR] Python not found.
    echo         Set RVC_PY to your RVC Runtime\\python.exe, or install Python.
    pause
    exit /b 1
)

echo Using: %PY%
echo.

"%PY%" "%~dp0ncmdump_standalone.py" %*
set RC=%ERRORLEVEL%

echo.
if not "%RC%"=="0" (
    echo [FAILED] see the message above.
    pause
    exit /b %RC%
)

echo [DONE]
pause
"""


def main():
    content = BAT_TEMPLATE.format()
    path = os.path.join(TARGET_DIR, BAT_NAME)
    with open(path, "w", encoding="gbk", newline="\r\n") as f:
        f.write(content)
    size = os.path.getsize(path)
    print(f"写出: {path}  ({size} bytes, GBK/CRLF)")
    assert all(ord(c) < 128 for c in content), "bat 内容含非 ASCII 字符，会导致 cmd 乱码"
    assert "E:\\" not in content and "Administrator" not in content, "bat 仍含本机路径"
    print("检查: 全 ASCII -> OK   无本机路径 -> OK")


if __name__ == "__main__":
    main()
