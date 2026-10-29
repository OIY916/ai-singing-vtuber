"""QQ 电话音频链路（不依赖 AstrBot，可独立运行/测试）

这条链路的四个方向（2026-10-26 实测确认）：

    对方说 → QQ 播到「扬声器 (2- High Definition Audio Device)」
           → WASAPI 环回录音（include_loopback=True）
           → ASR → LLM
           → TTS 音频
           → 写入「CABLE Input (VB-Audio Virtual Cable)」
           → QQ 麦克风选的是「CABLE Output」→ 对方听到

关键事实（都实测过，别记错）：

1. `CABLE Input` 是**播放端点**（往里播放），`CABLE Output` 是**录音端点**（从它录音）。
   VB-CABLE 的链路已实测验证：往 CABLE Input 播 440Hz 正弦，从 CABLE Output
   录回来 peak=0.2500，与送出的幅度完全一致（无损）。

2. QQ 的来电窗口自己会显示「正使用"系统默认 (CABLE Output (VB-Audio Virtual Cable))"
   作为您的麦克风」—— 说明 QQ 侧已经选好虚拟麦克风，**不需要再配**。

3. 环回录音**不需要虚拟声卡**，`soundcard` 的 `include_loopback=True` 就能直接录
   扬声器的输出。但只录默认扬声器不够 —— 用户改过音频设备，必须先用 pycaw
   探测「QQ 到底往哪个端点播」（见 scripts/which_endpoint.py 的同款逻辑）。

4. 采样率：ASR/环回用 16000（够语音识别），TTS 输出常用 32000/48000。
   录音时统一重采样到 16000，播放时按端点原生率写。
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from dataclasses import dataclass, field

import numpy as np
import soundcard as sc


# ---------------------------------------------------------------- 常量

RATE_ASR = 16000        # 送 ASR 的采样率
RATE_TTS = 32000        # TTS 常见输出率
FRAME_MS = 50           # 建议帧长
FRAME_SAMPLES = int(RATE_ASR * FRAME_MS / 1000)

CABLE_IN_HINT = "CABLE Input"      # 播放端点（写给它）
CABLE_OUT_HINT = "CABLE Output"    # 录音端点（QQ 麦克风读它）


def log(msg: str) -> None:
    now = time.time()
    print(f"{time.strftime('%H:%M:%S', time.localtime(now))} {msg}", flush=True)


# ---------------------------------------------------------------- 端点查找

def find_speaker(hint: str) -> sc._Speaker | None:
    for s in sc.all_speakers():
        if hint.lower() in s.name.lower():
            return s
    return None


def find_microphone(hint: str) -> sc._Microphone | None:
    """hint='CABLE Output' 找虚拟线；hint='loopback:扬声器 (xxx)' 找环回。"""
    if hint.startswith("loopback:"):
        name = hint[len("loopback:"):]
        for m in sc.all_microphones(include_loopback=True):
            if m.name == name:
                return m
        return None
    for m in sc.all_microphones(include_loopback=True):
        if hint.lower() in m.name.lower():
            return m
    return None


def probe_qq_endpoint(probe_python: str = None, probe_script: str = None) -> dict:
    """跑 which_endpoint.py 问 QQ 往哪个端点播。失败返回 {}。"""
    import json
    import os
    import subprocess

    if not probe_python or not probe_script:
        return {}
    if not (os.path.exists(probe_python) and os.path.exists(probe_script)):
        return {}
    try:
        r = subprocess.run([probe_python, probe_script, "qq"],
                           capture_output=True, timeout=25)
        return json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------- 录音（环回）

@dataclass
class LoopbackRecorder:
    """从某个扬声器端点做 WASAPI 环回录音，产出 16k 单声道 float32 帧。

    用法：
        r = LoopbackRecorder(speaker_name="扬声器 (2- High Definition Audio Device)")
        r.start()
        frame = r.read_frame()      # 320 samples @16k = 20ms，或 None
        r.stop()
    """

    speaker_name: str
    rate: int = RATE_ASR
    _q: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=200))
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _error: str | None = None
    dropped: int = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            mic = sc.get_microphone(id=self.speaker_name, include_loopback=True)
            chunk = FRAME_SAMPLES
            with mic.recorder(samplerate=self.rate) as rec:
                while not self._stop.is_set():
                    try:
                        data = rec.record(numframes=chunk)
                    except Exception as e:  # noqa: BLE001
                        self._error = f"record 失败: {e}"
                        break
                    # 转单声道
                    mono = data.mean(axis=1) if data.ndim > 1 else data
                    mono = mono.astype(np.float32)
                    try:
                        self._q.put_nowait(mono)
                    except queue.Full:
                        self.dropped += 1
                        try:
                            self._q.get_nowait()
                        except queue.Empty:
                            pass
        except Exception as e:  # noqa: BLE001
            self._error = f"环回录音启动失败: {e}"

    def read_frame(self, timeout: float = 2.0) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    @property
    def error(self) -> str | None:
        return self._error


# ---------------------------------------------------------------- 播放（进虚拟线）

class CablePlayer:
    """把 PCM 写进 CABLE Input，QQ 麦克风（CABLE Output）就会拾音。"""

    def __init__(self, rate: int = RATE_TTS, hint: str = CABLE_IN_HINT):
        self.target = find_speaker(hint)
        self.rate = rate
        if self.target is None:
            raise RuntimeError(f"找不到播放端点（{hint}）")

    def play(self, pcm: np.ndarray, rate: int | None = None) -> float:
        """播放单声道/立体声 float32 PCM，返回耗时秒数。"""
        r = rate or self.rate
        if pcm.ndim == 1:
            pcm2 = np.column_stack([pcm, pcm])
        else:
            pcm2 = pcm
        t0 = time.time()
        with self.target.player(samplerate=r) as p:
            p.play(pcm2.astype(np.float32))
        return time.time() - t0

    def play_stream(self, chunks, rate: int | None = None) -> float:
        """流式播放：边收边播（TTS 分句流式用）。

        chunks: 可迭代对象，每次 yield 一段 float32 PCM。
        """
        r = rate or self.rate
        t0 = time.time()
        with self.target.player(samplerate=r) as p:
            for ch in chunks:
                if ch is None or len(ch) == 0:
                    continue
                a = ch if ch.ndim > 1 else np.column_stack([ch, ch])
                p.play(a.astype(np.float32))
        return time.time() - t0


# ---------------------------------------------------------------- 自检

def selftest() -> int:
    """不依赖任何外部服务，验证本机音频硬件与虚拟线。"""
    ok = True
    log("=== 音频自检 ===")

    # 1. VB-CABLE 两段
    spk = find_speaker(CABLE_IN_HINT)
    mic = find_microphone(CABLE_OUT_HINT)
    if spk and mic:
        log(f"✅ CABLE Input : {spk.name}")
        log(f"✅ CABLE Output: {mic.name}")
    else:
        log(f"❌ 虚拟线端点缺失  spk={spk}  mic={mic}")
        ok = False

    # 2. 环回能力
    try:
        default_spk = sc.default_speaker()
        log(f"✅ 默认扬声器: {default_spk.name}")
        probe = sc.get_microphone(id=default_spk.name, include_loopback=True)
        with probe.recorder(samplerate=RATE_ASR) as rec:
            d = rec.record(numframes=1600)
        log(f"✅ 环回录音可用: shape={d.shape}")
    except Exception as e:  # noqa: BLE001
        log(f"❌ 环回录音失败: {e}")
        ok = False

    # 3. 虚拟线实通路（播→录）
    if spk and mic:
        try:
            tone = (0.25 * np.sin(2 * np.pi * 440 * np.linspace(
                0, 1.0, 48000, endpoint=False))).astype(np.float32)
            got: list = []

            def _rec():
                with mic.recorder(samplerate=48000) as r_:
                    got.append(r_.record(numframes=48000))

            th = threading.Thread(target=_rec)
            th.start()
            time.sleep(0.3)
            with spk.player(samplerate=48000) as p:
                p.play(np.column_stack([tone, tone]))
            th.join(timeout=5)
            if got and float(np.abs(got[0]).max()) > 0.01:
                peak = float(np.abs(got[0]).max())
                log(f"✅ 虚拟线实通路验证通过 peak={peak:.3f}")
            else:
                log("❌ 虚拟线播→录 没有信号")
                ok = False
        except Exception as e:  # noqa: BLE001
            log(f"❌ 虚拟线实通路测试异常: {e}")
            ok = False

    log("=== 自检结束: " + ("全部通过" if ok else "有失败项") + " ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest())
