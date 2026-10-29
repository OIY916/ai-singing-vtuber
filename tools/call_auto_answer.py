"""QQ 语音通话自动接听 v2 —— 用鼠标坐标点击（InvokePattern 在 Electron 上无效）

2026-10-26 实测结论（决定性的）：
  - LuckyLillia 有头模式下拉起的 QQ 会弹出独立窗口，标题正好是「语音通话」
  - 窗口内有可读的「接听」「挂断」按钮（UIA ControlType=50000）
  - 【坑】这些按钮虽然报告 InvokePattern 可用，但调用 Invoke() 一律失败 ——
    QQ 是 Electron 应用，按钮是自绘的，不支持 UIA 的 Invoke 协议。
  - 【正解】读按钮的 CurrentBoundingRectangle，取中心坐标，用 SetCursorPos +
    mouse_event 真实点击。实测坐标准确：接听按钮 (635,689)-(675,729)。

用法：
    python tools/call_auto_answer.py               # 常驻自动接听
    python tools/call_auto_answer.py --seconds 120
    python tools/call_auto_answer.py --once        # 接一通就退出
    python tools/call_auto_answer.py --dry-run     # 只打印坐标，不点

为什么不用 pywinauto 的 Desktop().windows()：
    本机单次要 8.7 秒（被桌面 Progman 窗口拖死），来电只响 20~30 秒，必然漏接。
    改用 Win32 EnumWindows（0.4ms）先按标题筛出 hwnd，再走 UIA 读按钮。
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
import traceback
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

CALL_WINDOW_TITLE = "语音通话"
UIA_ControlTypePropertyId = 30003   # 别写 729
UIA_ButtonControlTypeId = 50000
TreeScope_Descendants = 4


def log(msg: str) -> None:
    now = time.time()
    stamp = time.strftime("%H:%M:%S", time.localtime(now))
    print(f"{stamp}.{int(now * 1000) % 1000:03d} {msg}", flush=True)


# ------------------------------------------------------------ Win32

def find_call_windows() -> list[int]:
    """所有标题精确等于「语音通话」的窗口。实测 0.4ms。"""
    out: list[int] = []

    def cb(hwnd, _lp):
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value.strip() == CALL_WINDOW_TITLE:
            out.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def click_at(x: int, y: int) -> None:
    """真实鼠标点击。会把鼠标挪过去，用完不还原（还原会干扰后续点击）。"""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP


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
    auto = _UIA["auto"] = None if auto is None else auto
    return auto


def buttons_with_rect(hwnd: int) -> list[tuple[str, tuple[int, int, int, int]]]:
    """返回 [(按钮文字, (left, top, right, bottom))]。"""
    auto = automation()
    out: list[tuple[str, tuple[int, int, int, int]]] = []
    if auto is None:
        return out
    try:
        el = auto.ElementFromHandle(hwnd)
        cond = auto.CreatePropertyCondition(UIA_ControlTypePropertyId, UIA_ButtonControlTypeId)
        found = el.FindAll(TreeScope_Descendants, cond)
        for i in range(found.Length):
            e = found.GetElement(i)
            try:
                nm = (e.CurrentName or "").strip()
                br = e.CurrentBoundingRectangle
                if nm and br.right > br.left and br.bottom > br.top:
                    out.append((nm, (br.left, br.top, br.right, br.bottom)))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return out


def window_texts(hwnd: int) -> list[str]:
    auto = automation()
    if auto is None:
        return []
    try:
        el = auto.ElementFromHandle(hwnd)
        found = el.FindAll(TreeScope_Descendants, auto.CreateTrueCondition())
        out = []
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


# ------------------------------------------------------------ 主逻辑

def try_answer(dry_run: bool = False) -> bool:
    hwnds = find_call_windows()
    if not hwnds:
        return False

    for hwnd in hwnds:
        btns = buttons_with_rect(hwnd)
        if not btns:
            continue
        names = [n for n, _ in btns]

        # 只点「接听」，坚决不碰「挂断」——点错就是把人家电话挂了
        answer = next(((n, r) for n, r in btns if "接听" in n), None)
        if not answer:
            continue

        name, (l, t, r, b) = answer
        cx, cy = (l + r) // 2, (t + b) // 2
        log(f"[来电] hwnd={hwnd:#x} 按钮={names} -> 点 {name!r} @ ({cx},{cy})")

        if dry_run:
            log("  (--dry-run 不点击)")
            return True

        click_at(cx, cy)
        time.sleep(1.2)

        # 看状态有没有变
        texts = window_texts(hwnd)
        state = next((x for x in texts if any(k in x for k in
                      ("等待", "通话中", "连接", "未接听", "已接通"))), None)
        still_open = hwnd in find_call_windows()
        log(f"  点击后: 状态={state!r} 窗口仍在={still_open}")
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=0)
    ap.add_argument("--interval", type=float, default=0.35)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if automation() is None:
        log("UIA 不可用，退出")
        return 2

    log("=== QQ 语音通话自动接听 v2（鼠标坐标点击）===")
    log(f"监听窗口: {CALL_WINDOW_TITLE!r}    dry-run={args.dry_run}")
    log("等待来电…… Ctrl+C 停止")

    started = time.time()
    handled: set[int] = set()
    count = 0

    try:
        while True:
            time.sleep(args.interval)
            live = set(find_call_windows())

            for h in live - handled:
                log(f"[新来电窗口] hwnd={h:#x}")
                handled.add(h)

            if live and try_answer(dry_run=args.dry_run):
                if args.dry_run:
                    log("dry-run 完成，退出")
                    break
                count += 1
                log(f"累计接听 {count} 次")
                if args.once:
                    break

            handled &= live  # 窗口关了就从记录里移除

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
