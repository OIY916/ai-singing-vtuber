# -*- coding: utf-8 -*-
"""往运行中的直播服务注入测试数据，验证面板 UI（开播前自测用）。

用法：
    python tools/test_panel_data.py

为什么用脚本而不是 curl：
    Windows 的 shell 会把中文参数按 GBK 转码，发给服务端后 JSON 解析失败，
    礼物名/昵称全变成默认值，测不出真实效果。
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8790"
sys.stdout.reconfigure(encoding="utf-8")


def post(path, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    print("== 注入在线人数")
    print(post("/api/test/live", {"online": 138, "total": 1247, "likes": 8620}))

    print("\n== 注入礼物")
    for name, gift, num, diamond in [
        ("提摩科亚", "大啤酒", 2, 15),
        ("派溟", "小心心", 12, 1),
        ("自以为", "玫瑰", 3, 10),
    ]:
        r = post("/api/test/gift", {"name": name, "gift": gift,
                                    "num": num, "diamond": diamond})
        print(f"  {name} 送 {gift}×{num} → 累计 ¥{r.get('gift_value')}")

    print("\n== 注入弹幕（会走完整 AI 回复链路，没 key 时只有气泡）")
    for who, text in [
        ("提摩科亚", "唱得真好听"),
        ("夜雨", "主播声音好温柔"),
        ("白昼", "点歌 晴天"),
        ("小明", "点歌 十年"),
    ]:
        try:
            r = post("/api/test/danmaku", {"name": who, "text": text})
            print(f"  {who}: {text} → {r}")
        except Exception as e:
            print(f"  {who}: 失败 {type(e).__name__}: {e}")

    print("\n== 当前面板快照")
    d = get("/api/state")
    print("  live :", json.dumps(d["live"], ensure_ascii=False))
    print("  mode :", d["mode"], "| sing_q:", d["sing_q"], "| play_q:", d["play_q"])
    print("  queue:", json.dumps(d["queue_items"], ensure_ascii=False))
    print("  feed :", len(d["feed"]), "条")
    for it in d["feed"]:
        print("    -", it.get("type"), it.get("name"), it.get("text"))


if __name__ == "__main__":
    main()
