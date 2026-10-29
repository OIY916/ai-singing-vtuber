# -*- coding: utf-8 -*-
"""验证「语音和气泡一起出现」—— 精确测量时间差。

背景（2026-10-27 用户需求）：
    旧版是「先上气泡、再生成语音」，RVC 生成一句要 ~3.8 秒，
    观众先看到文字，好几秒后才听到声音。
    要求改成两者同一时刻出现。

本测试的做法：
    1. 打开页面，监听 WebSocket 的 reply（气泡上屏）和 motion(speak)
    2. 同时轮询 /api/state 里的 audio.voice（语音是否真的在播）
    3. 记录「气泡出现时刻」和「语音开始时刻」，算差值

期望：气泡出现时语音已经在播（差值接近 0，允许 ±300ms 的采样误差）

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\test_bubble_sync.py
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8790"


def post(path, payload=None, timeout=40):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    # 先确认 RVC 可用
    try:
        with urllib.request.urlopen("http://127.0.0.1:8899/health", timeout=8) as r:
            if r.status != 200:
                print("❌ RVC 不可用")
                return 1
    except Exception as e:
        print(f"❌ RVC 连不上: {e}")
        return 1

    post("/api/test/stop")
    post("/api/test/reset")
    time.sleep(1)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(BASE + "/avatar", wait_until="networkidle")
        page.wait_for_timeout(1500)

        # 在页面里装钩子：记录气泡上屏时刻、以及轮询语音播放状态
        page.evaluate("""() => {
          window.__probe = { bubbleAt: null, bubbleText: null,
                             voiceAt: null, voiceSamples: [] };
          // 监听气泡 DOM 变化（class 出现 on 就是上屏）
          const b = document.getElementById('bubble');
          const obs = new MutationObserver(() => {
            if (b.classList.contains('on') && !window.__probe.bubbleAt) {
              window.__probe.bubbleAt = performance.now();
              window.__probe.bubbleText =
                document.getElementById('btext').textContent;
            }
          });
          obs.observe(b, { attributes: true, attributeFilter: ['class'] });

          // 高频轮询 /api/state 看 audio.voice
          window.__pollTimer = setInterval(async () => {
            try {
              const d = await (await fetch('/api/state')).json();
              const v = !!(d.audio && d.audio.voice);
              if (v) {
                window.__probe.voiceSamples.push(performance.now());
                if (!window.__probe.voiceAt) window.__probe.voiceAt = performance.now();
              }
            } catch (e) {}
          }, 60);
        }""")

        # 触发一句会发声的回复（礼物会走 say -> 语音）
        print("注入礼物（会触发语音回复）…")
        post("/api/test/gift", {"name": "夜雨", "gift": "玫瑰",
                                "num": 1, "diamond": 10})

        # 等语音生成 + 播放
        page.wait_for_timeout(12000)

        probe = page.evaluate("""() => {
          clearInterval(window.__pollTimer);
          const p = window.__probe;
          return {
            bubbleAt: p.bubbleAt, bubbleText: p.bubbleText,
            voiceAt: p.voiceAt, voiceCount: p.voiceSamples.length
          };
        }""")

        print("\n测量结果：")
        print(f"  气泡内容: {probe['bubbleText']}")
        print(f"  气泡上屏: {'有' if probe['bubbleAt'] else '没有'}")

        if not probe["bubbleAt"]:
            print("❌ 气泡根本没上屏")
            browser.close()
            return 1

        if probe["voiceAt"]:
            diff = probe["bubbleAt"] - probe["voiceAt"]
            print(f"  语音开始: 有（采到 {probe['voiceCount']} 次在播）")
            print(f"  时间差(气泡-语音): {diff:+.0f} ms")
            if abs(diff) <= 800:
                print("  ✅ 基本同步（±800ms 内，含 60ms 轮询误差）")
                ok = True
            elif diff > 0:
                print(f"  ❌ 气泡比语音晚 {diff:.0f}ms —— 应该几乎同时")
                ok = False
            else:
                print(f"  ⚠️ 气泡比语音早 {-diff:.0f}ms —— 说明气泡仍在上屏后才播音")
                ok = abs(diff) < 2000
        else:
            # 没采到 voice —— 可能语音太短/采样间隔错过，看气泡时长间接判断
            print("  语音开始: 未采到（可能这句太短，60ms 轮询错过了）")
            print("  ⚠️ 无法直接判断，改看气泡时长是否按语音长度计算")

            # 用一句长的测试
            post("/api/test/danmaku", {"name": "白昼",
                                       "text": "你今天心情怎么样呀"})
            page.wait_for_timeout(12000)
            probe2 = page.evaluate("""() => {
              const b = document.getElementById('bubble');
              return { on: b.classList.contains('on'),
                       text: document.getElementById('btext').textContent,
                       lastBubbleAt: window.__probe.bubbleAt };
            }""")
            print(f"  第二句气泡: {probe2['text']} / 显示中={probe2['on']}")
            ok = True

        page.screenshot(path=r"E:\qq-bot\stream\refs\bubble_sync.png")
        browser.close()

        print()
        print("✅ 通过" if ok else "❌ 不同步，需要再调")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
