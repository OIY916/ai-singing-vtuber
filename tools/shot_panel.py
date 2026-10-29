# -*- coding: utf-8 -*-
"""给直播间场景页拍 1920×1080 截图（开发期核对视觉用）。

用法：
    E:\qq-bot\astrbot-venv\Scripts\python.exe tools/shot_panel.py
    E:\qq-bot\astrbot-venv\Scripts\python.exe tools/shot_panel.py 输出.png 等待秒数

为什么单独写脚本：
    dsh 的 browser_screenshot 只能按当前视口出图（1280×800），
    而 OBS 浏览器源是 1920×1080，必须按这个尺寸截图才看得出真实版面。
"""
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

URL = "http://127.0.0.1:8790/avatar"
OUT = sys.argv[1] if len(sys.argv) > 1 else r"E:\qq-bot\stream\refs\panel_1920.png"
WAIT_MS = int(sys.argv[2]) * 1000 if len(sys.argv) > 2 else 3500


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": 1920, "height": 1080},
                                device_scale_factor=1)
        page.goto(URL, wait_until="networkidle")
        # 等 WS 推几帧面板数据，气泡/进度条/频谱都动起来再拍
        page.wait_for_timeout(WAIT_MS)
        page.screenshot(path=OUT)
        browser.close()
    print("已保存:", OUT)


if __name__ == "__main__":
    main()
