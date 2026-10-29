# -*- coding: utf-8 -*-
"""录一段「大肥鱼爬屏」的 GIF，用来确认手感。

思路：把页面暂停，然后手动每帧推进固定 dt 再截图。
好处是帧间隔绝对均匀，不受截图耗时影响，GIF 不会忽快忽慢。

    python tools/record_crawl_demo.py
输出：
    avatar/crawler/crawl_demo.gif
"""
import io
import os
import sys

from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")

URL = "http://127.0.0.1:8790/asset/crawler/crawl.html?bg=1&speed=200&fall=0"
OUT = "avatar/crawler/crawl_demo.gif"
VIEW = {"width": 880, "height": 495}
FPS = 15
DT = 1.0 / FPS
MAX_FRAMES = 150          # 上限 10 秒
TAIL_FRAMES = 26          # 落地后多录一点，让循环收得干净


def main():
    from playwright.sync_api import sync_playwright

    frames = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-device-scale-factor=1"])
        page = browser.new_page(viewport=VIEW, device_scale_factor=1)
        page.goto(URL, wait_until="load")
        page.wait_for_function("window.__crawl && window.__crawl.state", timeout=15000)
        page.wait_for_timeout(400)

        page.evaluate("__crawl.pause(true); __crawl.at('c-bl', 0.0)")

        fell = False
        landed_at = -1
        for i in range(MAX_FRAMES):
            st = page.evaluate("__crawl.step(%f), __crawl.state()" % DT)
            png = page.screenshot()
            frames.append(png)

            # 爬到天花板中段就手动松手，保证这段 GIF 一定包含掉落
            if not fell and st["seg"] == "top" and st["u"] >= 0.50:
                page.evaluate("__crawl.fall()")
                fell = True
                print(f"  第 {i} 帧：从天花板掉落")

            if fell and landed_at < 0 and st["mode"] == "crawl" and st["seg"] == "bottom":
                landed_at = i
            if landed_at > 0 and i - landed_at >= TAIL_FRAMES:
                print(f"  第 {i} 帧：落地后已录 {i - landed_at} 帧，收工")
                break

        browser.close()

    print(f"共 {len(frames)} 帧，开始合成 GIF …")
    imgs = []
    for b in frames:
        im = Image.open(io.BytesIO(b)).convert("RGB")
        imgs.append(im.resize((440, int(440 * VIEW["height"] / VIEW["width"])),
                              Image.LANCZOS).convert("P", palette=Image.ADAPTIVE, colors=128))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    imgs[0].save(OUT, save_all=True, append_images=imgs[1:],
                 duration=int(1000 / FPS), loop=0, optimize=True, disposal=2)
    kb = os.path.getsize(OUT) / 1024
    print(f"完成：{OUT}  {len(imgs)} 帧  {kb:.0f} KB")


if __name__ == "__main__":
    main()
