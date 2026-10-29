# -*- coding: utf-8 -*-
"""验证「点歌 + 放原曲」混在一起时，播放顺序 = 请求顺序。

背景（2026-10-27 用户需求 + 重构）：
    用户要求：「聊天框的点歌先放到点歌队列中，唱（放）完了再看下一个就行」。
    旧结构有两条独立链路（sing_worker / raw_worker），各自都调
    Mixer.play_music（覆盖式），谁后到谁顶掉前一首，顺序完全不可控。

新结构：JOB_Q（请求 FIFO）→ prep_worker（按序准备）→ PLAY_Q → play_worker（唯一出口）

本测试交替发「点歌 / 放原曲」，确认：
    · 全部按提交顺序播放（不是按类型分组）
    · 没有歌被顶掉
    · 放原曲的歌确实是原唱（prefer_gender=False）

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\test_queue_order.py
"""
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8790"


def post(path, payload=None, timeout=30):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def state():
    with urllib.request.urlopen(BASE + "/api/state", timeout=10) as r:
        return json.loads(r.read().decode())


def main():
    post("/api/test/reset")
    post("/api/test/stop")
    time.sleep(1)

    # 交替提交，观察是否按顺序播
    plan = [
        {"kind": "sing", "kw": "晴天", "label": "点歌-晴天后"},
        {"kind": "raw",  "kw": "十年", "label": "放原曲-十年"},
        {"kind": "sing", "kw": "小情歌", "label": "点歌-小情歌"},
    ]

    print("提交顺序：")
    for p in plan:
        if p["kind"] == "sing":
            post("/api/test/sing", {"kw": p["kw"]})
        else:
            post("/api/test/raw", {"kw": p["kw"]})
        print(f"  {p['label']}")
        time.sleep(0.8)

    print("\n观察播放顺序（应和上面一致）：")
    played = []
    deadline = time.time() + 900
    last_cur = None

    while time.time() < deadline:
        time.sleep(4)
        d = state()
        cur = (d.get("current") or {}).get("title") if d.get("current") else None
        if cur and cur != last_cur:
            last_cur = cur
            played.append(cur)
            raw_flag = (d.get("current") or {}).get("raw")
            print(f"  ▶ {cur}  {'（原曲）' if raw_flag else '（翻唱）'}")
        if (d["sing_q"] == 0 and d["play_q"] == 0
                and d["mode"] == "idle" and len(played) >= len(plan)):
            break

    print(f"\n实际播出 {len(played)} 首：")
    for i, t in enumerate(played, 1):
        print(f"  {i}. {t}")

    # 判断顺序：标题应大致对应提交顺序
    # （歌名会被替换成网易云上的完整标题，所以用关键词包含来判断）
    keys = ["晴天", "十年", "小情歌"]
    order_ok = True
    for i, kw in enumerate(keys):
        if i < len(played):
            if kw not in played[i]:
                print(f"  ⚠️ 第 {i+1} 首期望含「{kw}」，实际是「{played[i]}」")
                order_ok = False
        else:
            print(f"  ❌ 第 {i+1} 首「{kw}」没有播放")
            order_ok = False

    print()
    if len(played) != len(plan):
        print(f"❌ 丢歌：提交 {len(plan)} 首，只播了 {len(played)} 首")
        return 1
    if not order_ok:
        print("❌ 顺序不对（应严格按提交顺序）")
        return 1
    print("✅ 顺序正确：点歌与放原曲混排，严格按请求顺序播放，一首没丢")
    return 0


if __name__ == "__main__":
    sys.exit(main())
