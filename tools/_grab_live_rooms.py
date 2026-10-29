# -*- coding: utf-8 -*-
"""用已登录的 profile 打开抖音直播首页，抓出正在开播的房间号"""
import json
import os
import sys
import re

sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright

PROFILE_DIR = r"E:\qq-bot\douyin_profile"
CHROME = r"C:\Users\Administrator\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe"


def main():
    with sync_playwright() as p:
        kwargs = dict(user_data_dir=PROFILE_DIR, headless=True,
                      args=["--disable-blink-features=AutomationControlled"])
        if os.path.exists(CHROME):
            kwargs["executable_path"] = CHROME
        ctx = p.chromium.launch_persistent_context(**kwargs)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://live.douyin.com/", timeout=90000)
        page.wait_for_timeout(8000)
        html = page.content()
        page.screenshot(path=r"E:\qq-bot\tools_raw\douyin_home.png")

        ids = {}
        # 方式1：a 标签 /数字
        for m in re.finditer(r'href="(?:https://live\.douyin\.com)?/(\d{8,16})"', html):
            ids[m.group(1)] = 1
        # 方式2：web_rid 字段
        for m in re.finditer(r'web_rid["\s:=]+["\']?(\d{8,16})', html):
            ids[m.group(1)] = 1
        # 方式3：webRid json
        for m in re.finditer(r'"webRid":\s*"(\d{8,16})"', html):
            ids[m.group(1)] = 1

        # 判断登录态
        body = page.evaluate("document.body.innerText.slice(0,200)") or ""
        logged = "登录" not in body

        print("length(html):", len(html))
        print("logged_in_guess:", logged)
        print("rooms_found:", len(ids))
        for r in sorted(ids)[:30]:
            print("  ", r)
        ctx.close()


if __name__ == "__main__":
    main()
