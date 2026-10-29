# -*- coding: utf-8 -*-
"""等队列播空后再重启主服务（直播不能野蛮中断）。

背景（2026-10-27）：
    直播中改了代码要重启，但队列里还有观众点的歌没播。
    直接重启会让那几首歌**彻底丢失** —— 观众点了、等了，结果没听到。

本脚本：
    1. 轮询 /api/state，等 play_q+sing_q 都空且不在播放
    2. 队列一空就立刻重启主服务（只重启 8790，不动 RVC/弹幕采集）
    3. 重启后自动跑一次自检

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools/restart_when_idle.py
    ... tools/restart_when_idle.py --max-wait 900   最多等 15 分钟
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request

# --- 路径自定位（自动生成，勿手改）：工具在 tools/ 下，项目根是上一层 ---
import os as _os
import sys as _sys
ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, ROOT)
import pathcfg
PYPATH = pathcfg.python()
# --- 路径自定位结束 ---

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8790"
LOG_DIR = _os.path.join(ROOT, "logs")


def state():
    try:
        with urllib.request.urlopen(BASE + "/api/state", timeout=8) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def fmt(t):
    return time.strftime("%H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-wait", type=int, default=900, help="最多等多久（秒）")
    ap.add_argument("--poll", type=float, default=3.0, help="轮询间隔（秒）")
    args = ap.parse_args()

    print("=" * 70)
    print("  等待队列播空后重启主服务（不会打断观众点的歌）")
    print("=" * 70)

    deadline = time.time() + args.max_wait
    last_line = ""
    while time.time() < deadline:
        d = state()
        if not d:
            print(f"{fmt(time.time())} 读不到 /api/state（服务可能已停），直接进入重启")
            break
        q = (d.get("sing_q") or 0) + (d.get("play_q") or 0)
        cur = (d.get("current") or {}).get("title", "")
        au = d.get("audio") or {}
        pos = au.get("music_pos") or 0
        dur = au.get("music_dur") or 0

        line = (f"队列={q} 模式={d.get('mode')} 正在播={cur[:22]!r} "
                f"{pos:.0f}/{dur:.0f}s")
        if line != last_line:
            print(f"{fmt(time.time())} {line}")
            last_line = line

        # 队列空 且 不在播放 且 没有在翻唱 → 可以安全重启
        if q == 0 and d.get("mode") in ("idle", "speak") and not au.get("music"):
            print(f"\n{fmt(time.time())} ✅ 队列已空，可以安全重启")
            break
        time.sleep(args.poll)
    else:
        print(f"\n⚠️ 等待超时（{args.max_wait}s），队列仍未空。")
        ans = input("仍要重启吗？会丢掉排队的歌 [y/N]: ").strip().lower()
        if ans != "y":
            print("已取消，服务保持不动。")
            return 1

    # ---- 只重启 8790，不动 RVC 和弹幕采集 ----
    print("\n重启主服务（8790）…")
    ps = (
        "Get-NetTCPConnection -LocalPort 8790 -State Listen "
        "-ErrorAction SilentlyContinue | "
        "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, text=True)
    time.sleep(3)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = open(rf"{LOG_DIR}\main-stdout-restart-{stamp}.log", "w",
               encoding="utf-8")
    err = open(rf"{LOG_DIR}\main-stderr-restart-{stamp}.log", "w",
               encoding="utf-8")
    p = subprocess.Popen(
        [PYPATH, "-u",
         _os.path.join(ROOT, "stream", "main.py")],
        cwd=ROOT, stdout=out, stderr=err,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    print(f"已启动，PID={p.pid}，日志: main-stdout-restart-{stamp}.log")

    # 等端口起来
    for i in range(30):
        d = state()
        if d:
            print(f"✅ 服务已就绪（{i+1}s）")
            break
        time.sleep(1)
    else:
        print("❌ 30 秒内没起来，看日志：")
        print(rf"  {LOG_DIR}\main-stderr-restart-{stamp}.log")
        return 1

    print("\n重启后状态：")
    d = state()
    print(f"  模式: {d.get('mode')}  队列: {d.get('sing_q')}/{d.get('play_q')}")
    print(f"  在线: {(d.get('live') or {}).get('online')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
