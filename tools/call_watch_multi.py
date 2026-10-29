"""多路监视：手机上拨给机器人号 3862642627 时，看有没有任何反应。

同时盯：
  1. AstrBot 日志新增内容（含「语音」「通话」「call」等关键字）
  2. 所有 QQ 进程窗口的新增/变化
  3. 新增的网络连接（可能来电走独立通道）

用法：python tools/call_watch_multi.py --seconds 180
"""
from __future__ import annotations

import argparse
import ctypes
import io
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

LOG = "astrbot_run.log"
KW = re.compile(r"语音|通话|call|voice|voip|音视频|邀请", re.I)


def log(msg: str) -> None:
    now = time.time()
    print(f"{time.strftime('%H:%M:%S', time.localtime(now))}.{int(now*1000)%1000:03d} {msg}", flush=True)


def qq_windows() -> dict[int, tuple[str, str, bool]]:
    import psutil
    pids = set()
    for p in psutil.process_iter(["name", "pid"]):
        try:
            if (p.info["name"] or "").lower() == "qq.exe":
                pids.add(p.info["pid"])
        except Exception:
            pass
    out: dict[int, tuple[str, str, bool]] = {}

    def cb(hwnd, _lp):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            n = user32.GetWindowTextLengthW(hwnd)
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, b, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            out[hwnd] = (b.value, cls.value, bool(user32.IsWindowVisible(hwnd)))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def net_conns() -> set[str]:
    """当前 QQ 相关进程的 TCP 连接。"""
    try:
        import psutil
        pids = set()
        for p in psutil.process_iter(["name", "pid"]):
            try:
                if (p.info["name"] or "").lower() == "qq.exe":
                    pids.add(p.info["pid"])
            except Exception:
                pass
        out = set()
        for c in psutil.net_connections(kind="tcp"):
            if c.pid in pids and c.raddr:
                out.add(f"{c.raddr.ip}:{c.raddr.port}")
        return out
    except Exception:
        return set()


def read_tail(path: str, n: int = 0) -> tuple[str, int]:
    try:
        size = os.path.getsize(path)
        with io.open(path, "rb") as f:
            f.seek(max(0, size - 400_000))
            data = f.read().decode("utf-8", "replace")
        return data, size
    except Exception:
        return "", 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=180)
    args = ap.parse_args()

    log("=== 多路监视启动 ===")
    base_text, base_size = read_tail(LOG)
    log(f"AstrBot 日志基线: {base_size} 字节")

    prev_wins = qq_windows()
    prev_nets = net_conns()
    log(f"QQ 窗口基线: {len(prev_wins)} 个")
    log(f"QQ 网络连接基线: {len(prev_nets)} 条")

    log("★ 现在可以拨了：手机上拨给机器人号 3862642627")
    log("   （注意：是机器人号，不是 OlY 的号）")

    started = time.time()
    n = 0
    while time.time() - started < args.seconds:
        time.sleep(1.0)
        n += 1

        # 1) 日志
        text, size = read_tail(LOG)
        if size != base_size:
            # 只取新增部分
            new_text = text[len(base_text):] if size > base_size and len(text) > len(base_text) else text[-4000:]
            hits = [ln for ln in new_text.splitlines() if KW.search(ln)]
            if hits:
                log(f"[日志命中] {len(hits)} 行:")
                for h in hits[-8:]:
                    log(f"    {h[:150]}")
            base_text, base_size = text, size

        # 2) 窗口
        cur = qq_windows()
        for hwnd, (title, cls, vis) in cur.items():
            if hwnd not in prev_wins:
                log(f"[新窗口] {title!r} class={cls!r} vis={vis}")
        for hwnd, (title, cls, vis) in prev_wins.items():
            if hwnd not in cur:
                log(f"[窗口关闭] {title!r}")
            elif cur[hwnd][2] != vis:
                log(f"[可见性变化] {title!r} {vis} -> {cur[hwnd][2]}")
        prev_wins = cur

        # 3) 网络
        cur_nets = net_conns()
        added = cur_nets - prev_nets
        if added:
            log(f"[新网络连接] {sorted(added)[:10]}")
        prev_nets = cur_nets

        if n % 30 == 0:
            log(f"(已监视 {n}s · QQ窗口 {len(cur)})")

    log("=== 监视结束 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
