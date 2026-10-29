# -*- coding: utf-8 -*-
"""端到端验证「连唱三首」是否真的会播完 3 首。

背景：存档里挂着一条未验证的疑点——
    历史记录 [进度] 已唱 1 首 | mode=sing | 待播=1 ，最终 stats.songs 只到 1，
    第二首没播出来。

这次改动加了 wait_music_end 看门狗（播放流断了也不会永久挂住），
需要实测确认 songs 能到 3。

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools/test_chain_sing.py
    （先确保 8790 服务在跑，且 RVC 8899 可用）

注意：会真的调用 RVC 翻唱，单首 27~80 秒，三首可能要 2~4 分钟。
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8790"
sys.stdout.reconfigure(encoding="utf-8")


def post(path, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def state():
    with urllib.request.urlopen(BASE + "/api/state", timeout=10) as r:
        return json.loads(r.read().decode())


def main():
    songs = ["晴天", "十年", "小情歌"]
    s0 = state()
    base_songs = s0["stats"]["songs"]
    print(f"起始已唱: {base_songs}")

    for kw in songs:
        print(f"\n→ 注入点歌: {kw}")
        try:
            print("  ", post("/api/test/sing", {"kw": kw}))
        except Exception as e:
            print("   失败:", type(e).__name__, e)
        time.sleep(1)

    print("\n等待播放…（每 10 秒打印一次状态）")
    deadline = time.time() + 900          # 最多等 15 分钟
    last = None
    while time.time() < deadline:
        time.sleep(10)
        st = state()
        cur = (st.get("current") or {}).get("title", "-")
        au = st.get("audio") or {}
        line = (f"  songs={st['stats']['songs']} (目标 {base_songs + 3}) "
                f"mode={st['mode']} sing_q={st['sing_q']} play_q={st['play_q']} "
                f"cur={cur} pos={au.get('music_pos')}/{au.get('music_dur')}")
        if line != last:
            print(line)
            last = line
        done = st["stats"]["songs"] - base_songs
        if done >= 3 and st["sing_q"] == 0 and st["play_q"] == 0 and st["mode"] != "sing":
            print(f"\n✅ 连唱验证通过：共播出 {done} 首")
            return 0
        # 卡死检测：队列空、没在播、也没在翻唱，但还没唱够
        if (st["sing_q"] == 0 and st["play_q"] == 0
                and st["mode"] == "idle" and done < 3):
            print(f"\n⚠️ 队列已空但只唱了 {done} 首，疑似丢歌")
            return 1

    print("\n❌ 超时未完成")
    return 1


if __name__ == "__main__":
    sys.exit(main())
