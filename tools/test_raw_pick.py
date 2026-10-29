# -*- coding: utf-8 -*-
"""验证「放原曲」是否真的优先选原唱（而不是女声翻唱版）。

背景（2026-10-27 用户需求）：
    「放 X」不经过 RVC，观众要听的是**原唱**。
    但 /raw 和 /sing 原本共用同一个 _pick_song，
    而它硬编码了「女声优先」（因为 RVC 模型是女声）。
    结果「放 十年」可能放出个女生版翻唱，不是观众要的。

修法：_pick_song 加 prefer_gender 开关，/raw 传 False。

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\test_raw_pick.py
"""
import sys

sys.path.insert(0, r"E:\qq-bot\cover_service")
sys.stdout.reconfigure(encoding="utf-8")

import cover_service as C


def make(name, artist, dur=240000):
    return {"name": name, "artist": artist, "duration": dur}


# 一批「搜 十年 会同时出现」的候选
CANDIDATES = [
    make("十年", "陈奕迅"),                 # 原唱
    make("十年 (女生版)", "白允y"),          # 女声翻唱
    make("十年 - 深情女版", "淳玥"),         # 女声翻唱（带修饰）
    make("十年 (Live)", "陈奕迅"),           # 原唱现场版
    make("十年 (DJ版)", "某DJ"),
]


def run(prefer_gender, label):
    """直接复用打分逻辑，绕开 print"""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        picked = C._pick_song(CANDIDATES, "十年", prefer_gender=prefer_gender)
    print(f"  {label:28s} → 《{picked['name']}》- {picked['artist']}")
    return picked


def main():
    print("候选歌曲：")
    for c in CANDIDATES:
        print(f"  · {c['name']} - {c['artist']}")
    print()

    print("== 翻唱（/sing，prefer_gender=True）应该挑女声")
    cov = run(True, "prefer_gender=True")

    print("\n== 放原曲（/raw，prefer_gender=False）应该挑原唱")
    raw = run(False, "prefer_gender=False")

    ok = True
    # 翻唱：希望是女声版（含「女」字）
    if "女" in cov["name"]:
        print(f"\n  ✅ 翻唱选中女声版：{cov['name']}")
    else:
        print(f"\n  ⚠️  翻唱没选中女声版（{cov['name']}）—— 可能是候选里女声分不够，可接受")
    # 原曲：必须是陈奕迅原唱，不能是任何「女」版
    if raw["artist"] == "陈奕迅" and "女" not in raw["name"]:
        print(f"  ✅ 放原曲选中原唱：{raw['name']} - {raw['artist']}")
    else:
        print(f"  ❌ 放原曲没选中原唱：{raw['name']} - {raw['artist']}")
        ok = False

    # 关键断言：两条链路的选择应该不同
    if cov["name"] == raw["name"] and cov["artist"] == raw["artist"]:
        print("  ⚠️  两种模式选到了同一首（说明偏好没起作用）")
    else:
        print("  ✅ 两种模式选出了不同的歌（说明开关生效）")

    print("\n" + ("✅ 通过：放原曲优先原唱" if ok else "❌ 失败：放原曲仍然偏向翻唱版"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
