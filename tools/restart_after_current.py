# -*- coding: utf-8 -*-
"""等**当前这一首**播完就立刻重启主服务（不等整个队列清空）。

和 restart_when_idle.py 的区别：
    · restart_when_idle：等队列全部播空（适合收工）
    · 本脚本：只等**当前这首**唱完 → 立刻重启
      队列里剩下的歌会丢，但主播要求的就是这个时机
      （一首歌刚结束、下一首还没开始的空档，中断最自然）

用法：
    python tools\\restart_after_current.py
    ... --dry-run     只看时机，不真重启
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
        with urllib.request.urlopen(BASE + "/api/state", timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def fmt():
    return time.strftime("%H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只观察，不重启")
    ap.add_argument("--max-wait", type=int, default=600)
    args = ap.parse_args()

    print("=" * 70)
    print("  等当前这首播完 → 立刻重启主服务（队列里剩下的会丢）")
    print("=" * 70)

    d = state()
    if not d:
        print("❌ 读不到 /api/state")
        return 1

    cur0 = (d.get("current") or {}).get("title", "")
    if not cur0:
        print("⚠️ 当前没在播歌，可以直接重启")
    else:
        print(f"当前这首: {cur0}")
        print(f"队列剩余: {d.get('sing_q', 0)}/{d.get('play_q', 0)} 首（重启会丢）")
        print(f"在线人数: {(d.get('live') or {}).get('online')}")

    deadline = time.time() + args.max_wait
    left_current = False
    last = ""

    while time.time() < deadline:
        d = state()
        if not d:
            time.sleep(2)
            continue

        au = d.get("audio") or {}
        cur = (d.get("current") or {}).get("title", "")
        playing = bool(au.get("music"))

        # 只要有歌在播，就继续等
        if playing and cur:
            pos = au.get("music_pos") or 0
            dur = au.get("music_dur") or 0
            line = f"{fmt()} 正在播 {cur[:26]!r} {pos:.0f}/{dur:.0f}s"
            if line != last:
                print(line)
                last = line
            left_current = True
            time.sleep(1.0)
            continue

        # 没在播了：如果刚从「有歌」变「没歌」，说明这首结束了
        if left_current or not cur0:
            print(f"\n{fmt()} ✅ 当前这首已结束，立刻重启")
            break

        # 一开始就没在播的情况
        print(f"{fmt()} 空闲中…")
        time.sleep(1.0)
    else:
        print(f"\n⚠️ 等待超时（{args.max_wait}s）")
        return 1

    if args.dry_run:
        print("（--dry-run，不重启）")
        return 0

    # ---------- 重启 ----------
    print("\n停止主服务…")
    ps = ("Get-NetTCPConnection -LocalPort 8790 -State Listen "
          "-ErrorAction SilentlyContinue | "
          "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, text=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = open(rf"{LOG_DIR}\main-stdout-restart-{stamp}.log", "w", encoding="utf-8")
    err = open(rf"{LOG_DIR}\main-stderr-restart-{stamp}.log", "w", encoding="utf-8")

    t0 = time.time()
    p = subprocess.Popen(
        [PYPATH, "-u",
         _os.path.join(ROOT, "stream", "main.py")],
        cwd=ROOT, stdout=out, stderr=err,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    print(f"已启动 PID={p.pid}")

    for i in range(40):
        d = state()
        if d:
            cost = time.time() - t0
            print(f"✅ 服务就绪，耗时 {cost:.1f} 秒")
            break
        time.sleep(1)
    else:
        print("❌ 40 秒未就绪，看日志：")
        print(rf"  {LOG_DIR}\main-stderr-restart-{stamp}.log")
        return 1

    time.sleep(2)
    d = state() or {}
    print("\n重启后：")
    print(f"  模式: {d.get('mode')}  队列: {d.get('sing_q')}/{d.get('play_q')}")
    print(f"  在线: {(d.get('live') or {}).get('online')}")
    print(f"  账号生日志: events-{(time.strftime('%Y-%m-%d'))}.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
