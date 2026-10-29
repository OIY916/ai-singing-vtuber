# -*- coding: utf-8 -*-
"""直播日志查看 / 复盘工具。

为什么要单独写：
    日志文件一多（main / events / rvc / douyin），直播中想快速回答
    「刚才那首歌为什么没播」很麻烦。这个工具把关键信息聚合出来。

用法：
    # 看最近发生的事（点歌/翻唱/播放/礼物）
    python tools/logview.py

    # 只看今天的
    ... tools/logview.py --today

    # 找某一首歌的所有记录
    ... tools/logview.py --grep 晴天

    # 找所有失败/异常
    ... tools/logview.py --errors

    # 看最近 N 行原始日志
    ... tools/logview.py --tail 50
"""
import argparse
import glob
import os
import re
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

# --- 项目根自定位（本文件在 tools/ 下）---
import os as _os
PROJ_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
# --------------------------------------
LOG_DIR = _os.path.join(PROJ_ROOT, "logs")

# 这些是关键事件类别，按直播复盘价值排序
IMPORTANT = ("点歌", "点歌拒", "识别", "翻唱开始", "翻唱完成", "RVC重试",
             "原曲就绪", "播放开始", "播放结束", "礼物", "错误")


def latest(pattern):
    files = glob.glob(os.path.join(LOG_DIR, pattern))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def read_lines(path, limit=None):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    return lines[-limit:] if limit else lines


def show_events(grep=None, errors_only=False, limit=80):
    path = latest("events-*.log")
    if not path:
        print("还没有事件日志。先启动直播：.\启动直播.ps1")
        print(f"（日志会写到 {LOG_DIR}）")
        return 1

    print(f"事件日志: {os.path.basename(path)}")
    print("=" * 78)
    lines = read_lines(path)

    out = []
    for ln in lines:
        if errors_only:
            if "[错误]" not in ln:
                continue
        else:
            # 默认只看关键事件，过滤掉噪声
            if not any(f"[{k}]" in ln for k in IMPORTANT):
                continue
        if grep and grep not in ln:
            continue
        out.append(ln)

    if not out:
        print("（没有匹配的记录）")
        return 0

    for ln in out[-limit:]:
        print(ln)

    print("=" * 78)
    print(f"共 {len(out)} 条匹配（显示最后 {min(limit, len(out))} 条）")
    return 0


def show_tail(n=50):
    """把 main / rvc / events 的最新内容各取一点，混着看"""
    print("=" * 78)
    for pattern, label in (("main-*.log", "主服务"),
                           ("rvc-*.log", "RVC"),
                           ("events-*.log", "事件"),
                           ("douyin-*.log", "弹幕采集")):
        p = latest(pattern)
        if not p:
            print(f"\n--- {label}: 无日志 ---")
            continue
        print(f"\n--- {label}: {os.path.basename(p)} ---")
        for ln in read_lines(p, n):
            print(ln)
    return 0


def show_stats():
    """粗略统计：点歌几次、翻唱成功几次、失败几次"""
    path = latest("events-*.log")
    if not path:
        print("还没有事件日志")
        return 1

    text = "\n".join(read_lines(path))
    def cnt(tag):
        return text.count(f"[{tag}]")

    print(f"事件日志: {os.path.basename(path)}")
    print("=" * 78)
    print(f"  点歌请求     {cnt('点歌')}")
    print(f"  点歌被拒     {cnt('点歌拒')}")
    print(f"  翻唱开始     {cnt('翻唱开始')}")
    print(f"  翻唱完成     {cnt('翻唱完成')}")
    print(f"  RVC 重试     {cnt('RVC重试')}")
    print(f"  原曲就绪     {cnt('原曲就绪')}")
    print(f"  播放开始     {cnt('播放开始')}")
    print(f"  播放结束     {cnt('播放结束')}")
    print(f"  礼物         {cnt('礼物')}")
    print(f"  错误         {cnt('错误')}")

    # 关键一致性检查：翻唱开始 vs 完成
    a, b = cnt('翻唱开始'), cnt('翻唱完成')
    if a != b:
        print(f"\n  ⚠️ 翻唱开始 {a} 次但只完成 {b} 次 —— "
              f"{a - b} 次失败或被丢，用 --errors 看原因")

    # 播放开始 vs 结束
    # ⚠️ 这两个数**正常情况也会差 1**：正在播的那首歌已经「开始」但还没「结束」。
    #    所以只有差 ≥2 时才是真问题（说明有歌被中断且又开始了下一首）。
    p, q = cnt('播放开始'), cnt('播放结束')
    diff = p - q
    if diff >= 2:
        print(f"  ❌ 播放开始 {p} 次但只结束 {q} 次 —— 有 {diff} 首被中断或异常结束")
    elif diff == 1:
        # 查一下是不是真的在播
        playing = False
        try:
            import json as _json
            import urllib.request as _ur
            with _ur.urlopen("http://127.0.0.1:8790/api/state", timeout=5) as _r:
                st = _json.loads(_r.read().decode())
            playing = bool((st.get("audio") or {}).get("music")) or st.get("mode") == "sing"
        except Exception:
            pass
        if playing:
            print(f"  ℹ️ 播放开始 {p} 次 / 结束 {q} 次 —— 差 1 是因为**当前正在播一首**（正常）")
        else:
            print(f"  ⚠️ 播放开始 {p} 次但只结束 {q} 次，而且当前没有在播 "
                  f"—— 可能有 1 首异常结束，用 --errors 看")
    elif diff < 0:
        print(f"  ⚠️ 播放结束({q}) 比 开始({p}) 还多 —— 日志可能跨端口混了")

    print("=" * 78)
    return 0


def main():
    ap = argparse.ArgumentParser(description="直播日志查看")
    ap.add_argument("--today", action="store_true", help="只看今天的文件")
    ap.add_argument("--grep", help="按关键字筛选")
    ap.add_argument("--errors", action="store_true", help="只看错误")
    ap.add_argument("--tail", type=int, help="看各服务最后 N 行原始日志")
    ap.add_argument("--stats", action="store_true", help="统计点歌/翻唱/播放次数")
    ap.add_argument("--limit", type=int, default=80, help="最多显示多少条")
    args = ap.parse_args()

    if not os.path.isdir(LOG_DIR):
        print(f"日志目录还不存在: {LOG_DIR}")
        print("先启动直播：.\\启动直播.ps1")
        return 1

    if args.stats:
        return show_stats()
    if args.tail:
        return show_tail(args.tail)
    return show_events(grep=args.grep, errors_only=args.errors,
                       limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
