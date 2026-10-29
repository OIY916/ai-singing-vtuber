"""QQ 电话常驻服务 —— 一个进程搞定「自动接听 + 对话」

    线程 A：UIA 轮询「语音通话」窗口，出现就点「接听」（鼠标坐标点击）
    主线程：WASAPI 环回录音 → VAD → ASR → LLM → TTS → CABLE Input

用法：
    python tools/call_service.py                # 常驻（自动接听 + 对话）
    python tools/call_service.py --no-answer    # 只对话，不自动接
    python tools/call_service.py --dry-run      # 只录+ASR，不调 LLM/TTS

⚠️⚠️ 本文件最重要的坑：**导入顺序决定生死**
    `soundcard`（WASAPI）会把 COM 初始化成 **MTA**（多线程套间），
    而 UIA（Windows 无障碍）**必须 STA**（单线程套间）。
    一旦先 import soundcard，后续再初始化 UIA 就报：

        [WinError -2147417850] 无法在设置线程模式后对其加以更改

    2026-10-26 实测踩到。解法：**先初始化 UIA（STA），再导入音频库**。
    所以下面把 UIA 初始化提到了所有音频 import 之前 ——
    不要为了「整洁」把它挪到后面。

⚠️ 前提（都实测过）：
    1. LuckyLillia 有头模式在跑，QQ 以机器人号 3862642627 登录且有窗口
    2. 手机不能同时登着 3862642627（否则来电推给手机，电脑不响）
    3. 来电接通后 QQ 麦克风是 CABLE Output（来电窗口自己会显示）

⚠️ 为什么录音可以一直跑：
    她自己的 TTS 是写进「CABLE Input」的，**不会**从物理扬声器出来，
    所以环回录音听不到她自己，不会自激。

已知限制：用户在这台电脑上放音乐/视频，会被误当成说话。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes

# ============================================================================
# ⚠️ 关键：UIA 必须在 soundcard 之前初始化（STA vs MTA 冲突）
# ============================================================================
_UIA_AUTO = None
_UIA_ERR = ""
try:
    import comtypes.client as _ct
    from comtypes.gen import UIAutomationClient as _UIA

    _UIA_AUTO = _ct.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=_UIA.IUIAutomation
    )
except Exception as _e:  # noqa: BLE001
    _UIA_ERR = str(_e)

# 现在才导入音频（它会把 COM 设成 MTA，必须在 UIA 之后）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from call_audio import log  # noqa: E402
from call_loop import build_stack, pick_speaker, run_once  # noqa: E402

# 常量在本文件内定义，**不 import** call_auto_answer 整个模块
# （它内部也初始化 UIA，会跟这里重复/冲突）
UIA_ControlTypePropertyId = 30003   # 别写 729
UIA_ButtonControlTypeId = 50000
TreeScope_Descendants = 4
CALL_WINDOW_TITLE = "语音通话"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_call_windows() -> list[int]:
    """标题精确等于「语音通话」的窗口。实测 0.4ms。"""
    out: list[int] = []

    def cb(hwnd, _lp):
        n = _user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value.strip() == CALL_WINDOW_TITLE:
            out.append(hwnd)
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return out


def answer_once() -> bool:
    """发现来电窗口就点「接听」。返回是否点了。"""
    if _UIA_AUTO is None:
        return False
    for hwnd in find_call_windows():
        try:
            el = _UIA_AUTO.ElementFromHandle(hwnd)
            cond = _UIA_AUTO.CreatePropertyCondition(
                UIA_ControlTypePropertyId, UIA_ButtonControlTypeId)
            found = el.FindAll(TreeScope_Descendants, cond)
            target = None
            name = ""
            for i in range(found.Length):
                e = found.GetElement(i)
                try:
                    nm = (e.CurrentName or "").strip()
                    br = e.CurrentBoundingRectangle
                except Exception:  # noqa: BLE001
                    continue
                # 只点「接听」，绝不碰「挂断」—— 点错就是把人家电话挂了
                if "接听" in nm and br.right > br.left:
                    target = br
                    name = nm
                    break
            if target is None:
                continue
            cx = (target.left + target.right) // 2
            cy = (target.top + target.bottom) // 2
            log(f"[接听] hwnd={hwnd:#x} 点击 {name!r} @ ({cx},{cy})")
            _user32.SetCursorPos(int(cx), int(cy))
            time.sleep(0.12)
            _user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
            time.sleep(0.06)
            _user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
            return True
        except Exception as e:  # noqa: BLE001
            log(f"[接听] 处理 hwnd={hwnd:#x} 失败: {e}")
    return False


class AnswerThread(threading.Thread):
    """常驻轮询来电窗口，出现就接。"""

    def __init__(self, interval: float = 0.35):
        super().__init__(daemon=True, name="auto-answer")
        self.interval = interval
        self.stop_event = threading.Event()
        self.answered = 0

    def run(self) -> None:
        log("[接听] 线程启动，轮询「语音通话」窗口")
        while not self.stop_event.is_set():
            try:
                if answer_once():
                    self.answered += 1
                    log(f"[接听] 累计接听 {self.answered} 次")
            except Exception as e:  # noqa: BLE001
                log(f"[接听] 轮询异常: {e}")
            self.stop_event.wait(self.interval)
        log("[接听] 线程退出")

    def stop(self) -> None:
        self.stop_event.set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("ASTRBOT_ROOT") or r"E:\qq-bot")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-answer", action="store_true")
    args = ap.parse_args()

    log("=" * 56)
    log("  QQ 电话常驻服务（自动接听 + 实时对话）")
    log("=" * 56)

    if not args.no_answer and _UIA_AUTO is None:
        log(f"[接听] UIA 初始化失败: {_UIA_ERR}")
        log("  可改用 --no-answer 只跑对话（需手动点接听）")
        return 2
    if _UIA_AUTO is not None:
        log("[接听] UIA 已就绪（STA）")

    speaker = pick_speaker(args.root)
    log(f"[端点] 环回监听: {speaker}")

    asr, tts, llm, player, rec = build_stack(args.root, speaker)
    rec.start()
    time.sleep(1.0)
    if rec.error:
        log(f"[录音] 启动失败: {rec.error}")
        return 1

    ans: AnswerThread | None = None
    if not args.no_answer:
        ans = AnswerThread()
        ans.start()
    else:
        log("[接听] 已禁用（--no-answer），需手动点接听")

    log("[就绪] 现在可以直接打电话给 3862642627")
    log("[就绪] Ctrl+C 停止")

    rounds = 0
    try:
        while True:
            try:
                if run_once(asr, tts, llm, player, rec, dry_run=args.dry_run):
                    rounds += 1
                    log(f"[统计] 累计完成 {rounds} 轮对话")
            except Exception as e:  # noqa: BLE001
                log(f"[主循环] 异常: {e}")
                traceback.print_exc()
                time.sleep(1.0)
    except KeyboardInterrupt:
        log("收到 Ctrl+C，正在停止……")
    finally:
        if ans:
            ans.stop()
            ans.join(timeout=2.0)
        rec.stop()
        log(f"[结束] 本次完成 {rounds} 轮对话，接听 {ans.answered if ans else 0} 次")
    return 0


if __name__ == "__main__":
    sys.exit(main())
