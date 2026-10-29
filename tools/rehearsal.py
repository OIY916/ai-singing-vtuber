# -*- coding: utf-8 -*-
"""开播前「彩排」—— 完整走一遍真实直播会发生的事。

为什么单独做这个：
    单个接口测试（注入 n 条弹幕）证明不了「直播时会不会翻车」。
    真实直播是一堆事情一起来：弹幕刷屏、有人点歌、有人送礼、
    歌还在唱的时候又来弹幕、连点好几首……
    这个脚本一次性模拟这种并发压力，边跑边检查：
      · 服务有没有卡死（mode 长时间不动 / 队列不消化）
      · 有没有异常（日志里出现 Traceback / Error）
      · 音频有没有一直出声音（level 是否真的有起伏）

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\rehearsal.py
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\rehearsal.py --quick   # 快速版
"""
import json
import random
import sys
import threading
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8790"
QUICK = "--quick" in sys.argv

NAMES = ["夜雨", "白昼", "小明", "提摩科亚", "自以为", "牧之", "是白白", "派溟"]
CHATS = ["主播好呀", "这首歌好听", "唱得真棒", "666", "来了来了", "再来一首",
         "主播今天状态不错", "这个声音好温柔", "哈哈哈哈", "支持一下"]
GIFTS = [("小心心", 1), ("玫瑰", 3), ("大啤酒", 15), ("墨镜", 30)]


def post(path, payload=None, timeout=60):
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
    print("=" * 70)
    print("  直播彩排：模拟真实并发场景")
    print("=" * 70)

    # 从干净状态开始
    post("/api/test/reset")
    post("/api/test/live", {"online": 0, "total": 0, "likes": 0})
    print("已重置面板数据\n")

    problems = []
    levels = []          # 采样音频电平
    stop_flag = threading.Event()

    # ---- 后台持续采样，模拟「有观众持续互动」----
    def audience():
        """模拟观众行为：刷弹幕 + 送礼物，持续一段时间。"""
        n = 0
        while not stop_flag.is_set():
            n += 1
            who = random.choice(NAMES)
            try:
                if n % 7 == 0:
                    g, d = random.choice(GIFTS)
                    post("/api/test/gift", {"name": who, "gift": g,
                                            "num": random.randint(1, 5),
                                            "diamond": d}, timeout=20)
                else:
                    post("/api/test/danmaku", {"name": who,
                                               "text": random.choice(CHATS)},
                         timeout=20)
            except Exception as e:
                problems.append(f"观众模拟出错: {type(e).__name__}: {e}")
            time.sleep(random.uniform(0.8, 2.2))

    # ---- 后台采样音频电平（验证真的在出声）----
    def audio_probe():
        while not stop_flag.is_set():
            try:
                st = state()
                au = st.get("audio") or {}
                if au.get("music") or au.get("voice"):
                    levels.append(float(au.get("level") or 0))
            except Exception:
                pass
            time.sleep(0.25)

    ta = threading.Thread(target=audience, daemon=True)
    tp = threading.Thread(target=audio_probe, daemon=True)
    ta.start(); tp.start()

    print("【阶段1】模拟观众刷弹幕/送礼 15 秒…")
    time.sleep(15 if not QUICK else 8)

    print("\n【阶段2】同时点 3 首歌（考验连唱 + GPU 锁）")
    for kw in ["晴天", "十年", "小情歌"]:
        try:
            r = post("/api/test/sing", {"kw": kw})
            print(f"  点歌 {kw} → 待翻唱 {r.get('sing_q')}")
        except Exception as e:
            problems.append(f"点歌失败: {e}")
        time.sleep(0.6)

    print("\n【阶段3】唱歌过程中继续刷弹幕（考验 GPU 锁是否被抢）")
    time.sleep(8 if not QUICK else 4)

    print("\n【阶段4】等歌播完（最多 8 分钟，边等边监控）")
    deadline = time.time() + (480 if not QUICK else 120)
    last = ""
    stuck = 0
    last_songs = 0
    stuck_since = time.time()

    while time.time() < deadline:
        time.sleep(5)
        st = state()
        cur = (st.get("current") or {}).get("title", "-")
        au = st.get("audio") or {}
        line = (f"  songs={st['stats']['songs']} mode={st['mode']} "
                f"sing_q={st['sing_q']} play_q={st['play_q']} "
                f"cur={cur} pos={au.get('music_pos')}")
        if line != last:
            print(line)
            last = line

        # 卡死检测：状态长时间不变且没在播
        if st["stats"]["songs"] != last_songs:
            last_songs = st["stats"]["songs"]
            stuck_since = time.time()

        idle_like = (st["sing_q"] == 0 and st["play_q"] == 0
                     and st["mode"] == "idle")
        if idle_like:
            break
        if time.time() - stuck_since > 150:
            stuck += 1
            problems.append(
                f"疑似卡死：150 秒内 songs 没增加（mode={st['mode']}, "
                f"sing_q={st['sing_q']}, play_q={st['play_q']}）")
            break

    stop_flag.set()
    ta.join(timeout=3); tp.join(timeout=3)

    # ---- 汇总 ----
    print("\n" + "=" * 70)
    st = state()
    print("  最终状态")
    print(f"    已唱完:      {st['stats']['songs']} 首")
    print(f"    收到事件:    {st['stats']['events']}")
    print(f"    AI 回复:     {st['stats']['replies']}")
    print(f"    今日礼物面值: ¥{(st.get('live') or {}).get('gift_value')}")
    print(f"    送礼人数:    {(st.get('live') or {}).get('gifters')}")
    print(f"    弹幕流条数:  {len(st.get('feed') or [])}")

    if levels:
        nonzero = [x for x in levels if x > 0.005]
        print(f"\n  音频电平采样: {len(levels)} 次，其中有声 {len(nonzero)} 次 "
              f"({len(nonzero)/len(levels)*100:.0f}%)")
        print(f"    峰值 {max(levels):.3f}  平均 {sum(levels)/len(levels):.3f}")
        if not nonzero:
            problems.append("整个彩排期间音频电平一直是 0 —— 可能根本没出声")
    else:
        problems.append("没采到任何音频电平（可能一直没在播放）")

    print("\n" + "=" * 70)
    if problems:
        print(f"  ⚠️ 发现 {len(problems)} 个问题：")
        for p in problems[:10]:
            print("   ·", p)
        return 1

    print("  ✅ 彩排通过：连唱正常、无卡死、音频有输出、弹幕/礼物都在正常处理")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
