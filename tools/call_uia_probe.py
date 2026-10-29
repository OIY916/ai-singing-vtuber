"""QQ 来电窗口探针 v2（只观察，绝不点击）

只监听 QQ 进程自己的窗口，不看其他程序，所以不会被浏览器/输入法刷屏。
扫描两件事：
  1. 有没有「接听/挂断/来电」类文字的元素出现
  2. QQ 窗口拓扑有没有变化（新窗口/窗口关闭/按钮变化）

用法：
    python tools/call_uia_probe.py              # 一直跑
    python tools/call_uia_probe.py --seconds 300
    python tools/call_uia_probe.py --tree       # 命中来电时把整个元素树打出来

⚠️ 关键前提：来电必须真的到达这台电脑上的 QQ。
   如果同一个号在手机上登录，QQ 会把来电推给手机，电脑端不弹窗也不响铃，
   这时候探针什么都抓不到 —— 不是探针坏了，是压根没有来电。
   测试前先把该号在手机上退出登录（或改用另一个号来拨）。

2026-10-26 已实测的两个坑（都记下来，别再踩）：
  1. time.strftime 不认 %f，要自己拼毫秒
  2. UIA_ControlTypePropertyId 真实值是 30003，不是 729；写错的话
     FindAll 永远返回 0 个按钮且不报错（静默失败，最难查的一种）
  3. pywinauto 的 Desktop().windows() 单次要 8.7 秒（被桌面 Progman 拖死），
     来电只响 20~30 秒，用它必然漏接 —— 所以先用 Win32 EnumWindows（0.4ms）
"""

from __future__ import annotations

import argparse
import ctypes
import re
import sys
import time
import traceback
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# 来电相关关键字
CALL_KW = re.compile(r"接听|接受|接起|来电|邀请你|通话中|挂断|拒绝")
# 只用来判断「是不是通话窗口」，不当作命中
SOFT_KW = re.compile(r"通话|语音|视频")

UIA_ControlTypePropertyId = 30003   # 别写 729，会静默失败
UIA_ButtonControlTypeId = 50000
TreeScope_Descendants = 4
UIA_NamePropertyId = 30005


def log(msg: str) -> None:
    now = time.time()
    stamp = time.strftime("%H:%M:%S", time.localtime(now))
    print(f"{stamp}.{int(now * 1000) % 1000:03d} {msg}", flush=True)


# ------------------------------------------------------------ Win32

def qq_pids() -> set[int]:
    out: set[int] = set()
    try:
        import psutil
        for p in psutil.process_iter(["name", "pid"]):
            try:
                if (p.info["name"] or "").lower() == "qq.exe":
                    out.add(p.info["pid"])
            except Exception:  # noqa: BLE001
                continue
    except Exception as e:  # noqa: BLE001
        log(f"[warn] psutil 不可用，退回全窗口扫描: {e}")
    return out


def enum_qq_windows(pids: set[int]) -> list[tuple[int, str, str, bool]]:
    """QQ 进程的所有顶层窗口（含不可见）。实测 0.4ms。"""
    out: list[tuple[int, str, str, bool]] = []

    def cb(hwnd, _lp):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pids or pid.value in pids:
            n = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, title, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            out.append((hwnd, title.value, cls.value, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


# ------------------------------------------------------------ UIA

_UIA: dict = {}


def automation():
    if "auto" in _UIA:
        return _UIA["auto"]
    auto = None
    try:
        import comtypes.client
        from comtypes.gen import UIAutomationClient as UIA
        auto = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=UIA.IUIAutomation
        )
        _UIA["UIA"] = UIA
    except Exception as e:  # noqa: BLE001
        log(f"[warn] UIA 初始化失败: {e}")
    _UIA["auto"] = auto
    return auto


def elements_of(hwnd: int, button_only: bool = False) -> list[str]:
    """窗口内元素的 Name 列表。button_only=True 时只要按钮。"""
    auto = automation()
    if auto is None:
        return []
    try:
        el = auto.ElementFromHandle(hwnd)
        if el is None:
            return []
        if button_only:
            cond = auto.CreatePropertyCondition(UIA_ControlTypePropertyId, UIA_ButtonControlTypeId)
        else:
            cond = auto.CreateTrueCondition()
        found = el.FindAll(TreeScope_Descendants, cond)
        out: list[str] = []
        for i in range(found.Length):
            try:
                nm = (found.GetElement(i).CurrentName or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if nm:
                out.append(nm)
        return out
    except Exception:  # noqa: BLE001
        return []


def dump_tree(hwnd: int, limit: int = 400) -> None:
    auto = automation()
    if auto is None:
        return
    try:
        UIA = _UIA["UIA"]
        el = auto.ElementFromHandle(hwnd)
        found = el.FindAll(TreeScope_Descendants, auto.CreateTrueCondition())
        log(f"    --- 元素树 (共 {found.Length} 个) ---")
        for i in range(min(found.Length, limit)):
            try:
                e = found.GetElement(i)
                nm = (e.CurrentName or "").strip()
                ct = e.CurrentControlType
                if nm:
                    log(f"    [{ct}] {nm[:80]}")
            except Exception:  # noqa: BLE001
                continue
    except Exception as e:  # noqa: BLE001
        log(f"    元素树转储失败: {e}")


# ------------------------------------------------------------ 主逻辑

def snapshot(pids: set[int]) -> dict[int, tuple[str, str, bool, list[str]]]:
    snap: dict[int, tuple[str, str, bool, list[str]]] = {}
    for hwnd, title, cls, vis in enum_qq_windows(pids):
        if not title and not cls:
            continue
        # 非可见窗口也读：来电浮层可能先隐藏再显示
        snap[hwnd] = (title, cls, vis, elements_of(hwnd))
    return snap


def report(prev: dict, cur: dict, show_tree: bool) -> None:
    for hwnd, (title, cls, vis, els) in cur.items():
        old = prev.get(hwnd)
        if old is None:
            log(f"[新窗口] {title!r} class={cls!r} hwnd={hwnd:#x} vis={vis} 元素={len(els)}")
            hits = [e for e in els if CALL_KW.search(e)]
            if hits:
                log(f"    ★★★ 来电关键字命中: {list(dict.fromkeys(hits))}")
                if show_tree:
                    dump_tree(hwnd)
            continue

        o_title, o_cls, o_vis, o_els = old
        if o_vis != vis:
            log(f"[可见性] {title!r} {o_vis} -> {vis}")
        added = [e for e in els if e not in o_els]
        if added:
            hits = [e for e in added if CALL_KW.search(e)]
            if hits:
                log(f"[★★★ 来电关键字新增] {title!r} -> {list(dict.fromkeys(hits))}")
                if show_tree:
                    dump_tree(hwnd)
            else:
                soft = [e for e in added if SOFT_KW.search(e)]
                if soft:
                    log(f"[通话相关新增] {title!r} -> {soft[:8]}")
        removed = [e for e in o_els if e not in els]
        if removed:
            hard = [e for e in removed if CALL_KW.search(e)]
            if hard:
                log(f"[来电关键字消失] {title!r} -> {hard[:8]}")

    for hwnd, (title, cls, vis, els) in prev.items():
        if hwnd not in cur:
            log(f"[窗口关闭] {title!r} class={cls!r} hwnd={hwnd:#x}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=0)
    ap.add_argument("--interval", type=float, default=0.3)
    ap.add_argument("--tree", action="store_true", help="命中来电时转储整个元素树")
    args = ap.parse_args()

    if automation() is None:
        log("UIA 不可用，退出")
        return 2

    pids = qq_pids()
    log("=== QQ 来电探针 v2 · 只观察不点击 ===")
    log(f"监听 QQ 进程: {sorted(pids) if pids else '(未找到，退回全部)'}")

    t0 = time.time()
    prev = snapshot(pids)
    log(f"起始 QQ 窗口 {len(prev)} 个，首扫 {time.time() - t0:.3f}s")
    log("【前提】来电必须到达这台电脑：手机先退出该号登录，再用另一个号拨过来")

    started = time.time()
    n = 0
    try:
        while True:
            time.sleep(args.interval)
            n += 1
            cur = snapshot(pids)
            report(prev, cur, args.tree)
            prev = cur
            if n % 100 == 0:
                log(f"(轮询 {n} 次 · 存活 QQ 窗口 {len(cur)} 个)")
            if args.seconds and (time.time() - started) >= args.seconds:
                log(f"到达 {args.seconds} 秒上限，退出")
                break
    except KeyboardInterrupt:
        log("Ctrl+C，退出")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
