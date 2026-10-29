# -*- coding: utf-8 -*-
"""验证气泡 + 礼物特效会不会真的显示在网页上。

为什么需要单独验证：
    OBS 浏览器源里如果是「看不见的效果」，开播就等于没做。
    这个脚本注入一条礼物和一条弹幕，然后用浏览器打开页面，
    直接问 DOM：气泡有没有上屏、飘心有没有生成、元素在不在可视区。

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools/verify_ui_effects.py
"""
import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8790"


def post(path, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(BASE + "/avatar", wait_until="networkidle")
        page.wait_for_timeout(1500)

        print("== 注入礼物（应触发飘心特效 + 礼物进弹幕流）")
        post("/api/test/gift", {"name": "提摩科亚", "gift": "大啤酒",
                                "num": 6, "diamond": 15})
        page.wait_for_timeout(600)
        hearts = page.locator("#fx .heart").count()
        gift_rows = page.locator("#feed .fi.gift").count()
        print(f"  飘心元素: {hearts} 个")
        print(f"  弹幕流礼物行: {gift_rows} 行")

        print("\n== 注入气泡（应上屏且完整可见）")
        post("/api/test/danmaku", {"name": "夜雨", "text": "主播唱得真好听呀"})
        page.wait_for_timeout(1800)

        info = page.evaluate("""() => {
          const b = document.getElementById('bubble');
          const r = b.getBoundingClientRect();
          const on = b.classList.contains('on');
          const cs = getComputedStyle(b);
          // 是否被裁掉：和视口比较
          const vis = r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth;
          return {
            on, opacity: cs.opacity,
            text: document.getElementById('btext').textContent,
            who: document.getElementById('bwho').textContent,
            rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
            fullyVisible: vis,
            viewport: [innerWidth, innerHeight]
          };
        }""")
        print("  气泡:", json.dumps(info, ensure_ascii=False, indent=2))

        # 再拍一张，气泡在屏
        page.screenshot(path=r"E:\qq-bot\stream\refs\ui_bubble_gift.png")

        ok = (info["on"] and info["fullyVisible"] and hearts > 0 and gift_rows > 0)
        print("\n" + ("✅ 气泡与礼物特效均正常上屏" if ok else "❌ 有元素没显示，见上"))
        browser.close()
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
