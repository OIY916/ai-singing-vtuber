# -*- coding: utf-8 -*-
"""等指定的那首歌播完，立刻重启主服务。

⚠️ 为什么不用「等没歌在播」来判断（2026-10-27 踩过的坑）：
   歌是一首接一首连续播放的，`play_worker` 播完立刻取下一首，
   中间几乎没有「空闲」窗口（实测一直在播，等了 5 分钟都没等到空档）。
   所以必须**按歌名判断**：记录当前歌名，等 current_song 换成别的名字（或空）
   就说明这首结束了，立刻重启。

用法：
    python tools\\restart_after_song.py
    ... --song "潮汐 - 苏也莉"   指定歌名（默认取当前正在播的那首）
    ... --dry-run               只看不重启
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


def now():
    return time.strftime("%H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--song", default=None, help="等这首歌播完（默认取当前这首）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-wait", type=int, default=600)
    args = ap.parse_args()

    d = state()
    if not d:
        print("❌ 读不到 /api/state")
        return 1

    target = args.song or ((d.get("current") or {}).get("title") or "")
    if not target:
        print("当前没在播歌 —— 直接重启")
    else:
        print("=" * 70)
        print(f"  等这首播完就重启：{target}")
        print(f"  队列剩余 {d.get('sing_q', 0)}/{d.get('play_q', 0)} 首（重启会丢）")
        print(f"  在线 {(d.get('live') or {}).get('online')}")
        print("=" * 70)

    deadline = time.time() + args.max_wait
    last = ""

    while time.time() < deadline:
        d = state()
        if not d:
            time.sleep(1.5)
            continue
        cur = (d.get("current") or {}).get("title") or ""
        au = d.get("audio") or {}
        pos = au.get("music_pos") or 0
        dur = au.get("music_dur") or 0

        if target:
            # ⚠️ 判断依据：歌名变了（或变成空）＝目标歌已结束
            if cur != target:
                print(f"\n{now()} ✅ 「{target}」已结束（现在是「{cur or '空闲'}」）")
                print("   立刻重启")
                break
            line = f"{now()} 正在播 {cur[:28]!r} {pos:.0f}/{dur:.0f}s"
            if line != last:
                print(line)
                last = line
        else:
            # 没指定目标：等出现空闲
            if not cur and not au.get("music"):
                print(f"{now()} ✅ 空闲，重启")
                break
            line = f"{now()} {cur[:28]!r} {pos:.0f}/{dur:.0f}s"
            if line != last:
                print(line)
                last = line

        time.sleep(0.8)
    else:
        print(f"\n⚠️ 超时（{args.max_wait}s），目标歌还没结束")
        return 1

    if args.dry_run:
        print("（--dry-run，不重启）")
        return 0

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
    print(f"已启动 PID={p.pid}，等端口…")

    for _ in range(40):
        if state():
            print(f"✅ 服务就绪，耗时 {time.time() - t0:.1f} 秒")
            break
        time.sleep(1)
    else:
        print("❌ 40 秒未就绪：")
        print(rf"  {LOG_DIR}\main-stderr-restart-{stamp}.log")
        return 1

    time.sleep(2)
    d = state() or {}
    print("\n重启后：")
    print(f"  mode={d.get('mode')}  队列={d.get('sing_q')}/{d.get('play_q')}  "
          f"在线={(d.get('live') or {}).get('online')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
