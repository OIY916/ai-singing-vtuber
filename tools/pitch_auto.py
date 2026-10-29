"""音高检测与自动升调（解决「男声歌用女声模型唱出来很奇怪」）

## 问题（2026-10-26 用户反馈）

「唱男男的声唱出来的歌很奇怪」

实测翻唱成品的基频：

    我记得-赵雷（男声原曲）    翻唱后 ≈ 162 Hz   ← 仍停在男声音域
    长大别害怕-奚缘（女声）    翻唱后 ≈ 219 Hz   ← 正常

女声模型（nikou48K1999 等）的舒适音域约 200~350 Hz。
男声歌 120~165 Hz **低于模型音域下限**，模型被迫在自己能力边界外工作：
  - 音色崩坏（模型没见过低声区，输出"不像人"）
  - 气声/哑音被放大
  - rmvpe 在低音区检测精度下降 → 可能跑调

## 解法：转换前把人声升到模型舒适音域

`f0up_key` 单位是**半音**（每 +12 = 一个八度）：

    赵雷 162 Hz → 目标 240 Hz  =  12 * log2(240/162) ≈ 6.8  → 取 +7
    宋冬野（更低）              →  +8 或 +9
    女声歌（≥200Hz）            →  0（不动）

## 实现说明

本模块**不依赖 RVC 内部模块**，只用 numpy 做自相关基频估计，
避免和 RVC 的 import 顺序问题打架（见 STATUS.md 坑 K/L）。

用中位数而不是均值：歌声有大量静音/气声帧，均值会被拉偏。
"""

from __future__ import annotations

import subprocess

import numpy as np

FFMPEG = r"C:\tools\ffmpeg"

# 女声模型的舒适音域下限（Hz）。低于这个值就该升调。
MODEL_COMFORT_LOW = 190.0
# 升调目标（Hz）。240 是女声模型比较自然的中间位置。
TARGET_HZ = 240.0
# 男声判定阈值（Hz）。中位数低于它就认为是男声歌。
MALE_THRESHOLD = 180.0


def load_mono(path: str, rate: int = 16000, max_sec: float = 60.0) -> tuple[np.ndarray | None, int]:
    """用 ffmpeg 解码成 16k 单声道 float32。"""
    try:
        r = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error",
             "-i", path, "-t", str(max_sec),
             "-f", "f32le", "-ac", "1", "-ar", str(rate), "pipe:1"],
            capture_output=True, timeout=180,
        )
        if r.returncode != 0 or not r.stdout:
            return None, rate
        return np.frombuffer(r.stdout, dtype=np.float32).copy(), rate
    except Exception:  # noqa: BLE001
        return None, rate


def estimate_f0(pcm: np.ndarray, rate: int,
                fmin: float = 70.0, fmax: float = 800.0) -> float | None:
    """自相关估基频，返回中位数（Hz）。失败返回 None。

    只用能量足够、且自相关峰明显的帧，避免气声/噪声污染。
    """
    if pcm is None or len(pcm) < rate // 4:
        return None

    frame = int(rate * 0.05)      # 50ms
    lo = int(rate / fmax)         # 最快周期
    hi = int(rate / fmin)         # 最慢周期

    f0s: list[float] = []
    for i in range(0, len(pcm) - frame - hi, frame):
        seg = pcm[i:i + frame + hi]
        if len(seg) < frame + hi:
            break
        # 只取能量足够的一段
        if float(np.sqrt((seg[:frame] ** 2).mean())) < 0.012:
            continue
        seg = seg - seg.mean()
        ac = np.correlate(seg[:frame + hi], seg[:frame + hi], mode="full")[frame + hi - 1:]
        if len(ac) <= hi or ac[0] <= 0:
            continue
        region = ac[lo:hi]
        if len(region) == 0:
            continue
        peak_idx = int(np.argmax(region)) + lo
        # 峰要够明显（避免选中噪声/谐波）
        if ac[peak_idx] < 0.35 * ac[0]:
            continue
        f = rate / peak_idx
        if fmin <= f <= fmax:
            f0s.append(f)

    if len(f0s) < 3:
        return None
    return float(np.median(f0s))


def suggest_f0up_key(f0_median: float | None,
                     target: float = TARGET_HZ,
                     threshold: float = MALE_THRESHOLD) -> tuple[int, str]:
    """按检测到的基频算建议的 f0up_key（半音）。

    返回 (半音数, 人类可读说明)。
    """
    if f0_median is None:
        return 0, "音高检测失败，不升调"

    if f0_median >= threshold:
        return 0, f"检测到 {f0_median:.0f}Hz（非男声音域），不升调"

    semitones = int(round(12 * np.log2(target / f0_median)))
    # 限制在合理范围，避免极端值把声音搞坏
    semitones = max(1, min(12, semitones))
    after = f0_median * (2 ** (semitones / 12))
    return semitones, (
        f"检测到 {f0_median:.0f}Hz（男声音域），"
        f"升 {semitones} 个半音 → {after:.0f}Hz"
    )


def auto_pitch_for(path: str, rate: int = 16000) -> tuple[int, str]:
    """一步到位：读音频 → 估基频 → 给升调建议。"""
    pcm, r = load_mono(path, rate=rate)
    if pcm is None:
        return 0, "音频读取失败，不升调"
    f0 = estimate_f0(pcm, r)
    return suggest_f0up_key(f0)


# ---------------------------------------------------------------- 自检

if __name__ == "__main__":
    import os
    import sys
    import time

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    def log(m):
        print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)

    log("=== 音高检测自检 ===")

    test_files = [
        (r"E:\qq-bot\cover_service\cache\我记得-赵雷_17951090c71d.mp3", "赵雷（男声原曲翻唱）"),
        (r"E:\qq-bot\cover_service\cache\长大别害怕-奚缘_9c949f3ce66f.mp3", "奚缘（女声原曲翻唱）"),
    ]

    for path, label in test_files:
        if not os.path.exists(path):
            log(f"  跳过（不存在）: {path}")
            continue
        f0 = estimate_f0(*load_mono(path))
        s, msg = suggest_f0up_key(f0)
        log(f"  {label}: {msg}")

    log("")
    log("说明：以上测的是**已翻唱成品**，用于验证检测逻辑。")
    log("     实际应用时测的是**分离后的人声**（转换前），更有意义。")
