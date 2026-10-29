# -*- coding: utf-8 -*-
"""验证 mixer 播放到「尾帧」时不再抛 broadcast 异常。

背景（2026-10-27 实测抓到的真 bug）：
    _callback 里 ramp 形状是 (frames,1)，而音乐缓冲最后一段切片是 (n,2)。
    当 n < frames（也就是每首歌的最后一帧）时 numpy 广播失败：
        ValueError: operands could not be broadcast together
                    with shapes (353,2) (480,1)
    这个异常发生在音频回调线程里 → 整条输出流静默停摆 →
    on_music_end 永不触发 → play_worker 永久挂住 →
    「连唱时第二首永远不播、待播队列不消化」。
    这就是存档里那条一直没验证的 bug 的根因。

本测试不碰声卡，直接手工调用 _callback，用不符合帧长整除的音频长度
（故意让最后一帧不满）来复现并确认已修好。

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools/test_mixer_tail.py
"""
import sys

import numpy as np

sys.path.insert(0, r"E:\qq-bot\stream")
sys.stdout.reconfigure(encoding="utf-8")

from mixer import Mixer

FRAMES = 480          # 回调每次要 480 帧
SR = 48000


def run_case(name, n_samples):
    """播一段 n_samples 长度的音乐，用固定 480 帧的回调把它放完。"""
    m = Mixer()
    # 不走真声卡：直接注入缓冲
    pcm = np.random.uniform(-0.3, 0.3, n_samples).astype(np.float32)
    ended = {"hit": 0}

    with m.lock:
        m.music = m._to_stereo_f32(pcm, SR)
        m.mpos = 0
        m.playing_music = True
        m.on_music_end = lambda: ended.__setitem__("hit", ended["hit"] + 1)
        m.music_dur = len(pcm) / SR

    guard = 0
    while m.playing_music and guard < 100000:
        out = np.zeros((FRAMES, 2), np.float32)
        m._callback(out, FRAMES, None, None)
        guard += 1

    boom = None
    try:
        # 再多跑几帧：缓冲已清空，确认空缓冲时也不炸
        out = np.zeros((FRAMES, 2), np.float32)
        m._callback(out, FRAMES, None, None)
    except Exception as e:
        boom = f"{type(e).__name__}: {e}"

    ok = (not m.playing_music) and ended["hit"] >= 1 and boom is None
    print(f"  {name:34s} 长度={n_samples:6d} 回调={guard:4d} 次 "
          f"on_end={ended['hit']} 尾部异常={boom or '无'} → {'✅' if ok else '❌'}")
    return ok


def main():
    print("mixer 尾帧回调测试（帧长 480，故意制造不满帧）")
    print("=" * 78)
    cases = [
        ("正好整除", FRAMES * 10),
        ("差 1 个采样（必现原 bug）", FRAMES * 10 - 1),
        ("差 127 个采样（复现实测 353/480）", FRAMES * 10 - 127),
        ("只有一个采样", 1),
        ("短于一次回调", 200),
        ("超长歌（2 分钟）", SR * 120 - 3),
    ]
    results = [run_case(n, s) for n, s in cases]
    print("=" * 78)
    if all(results):
        print("✅ 全部通过：尾帧不再抛异常，on_music_end 正常触发")
        return 0
    print("❌ 有失败用例")
    return 1


if __name__ == "__main__":
    sys.exit(main())
