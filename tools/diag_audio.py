# -*- coding: utf-8 -*-
"""诊断 CABLE Input 输出流是否真的在跑。

排查现象：/api/state 里 music=true、music_pos 卡在结尾不动、level≈0，
说明音频回调线程可能已经停了（设备掉线/被独占/流 inactive），
直播时表现为「歌放不出来、频谱不动」。

用法：
    E:\qq-bot\astrbot-venv\Scripts\python.exe tools/diag_audio.py
"""
import sys
import time

sys.path.insert(0, r"E:\qq-bot\stream")
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

from mixer import Mixer

m = Mixer()
found = m.find_device()
print("设备:", found)
if not found:
    sys.exit("找不到 CABLE Input")

if not m.ensure_stream():
    sys.exit("开流失败")

print("stream.active =", m.stream.active)
print("stream.latency =", getattr(m.stream, "latency", None))

# 播一段 3 秒 440Hz，边播边采样 level，看回调有没有在推进
sr = 48000
t = np.linspace(0, 3, sr * 3, dtype=np.float32)
tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
m.play_music(tone, sr, gain=1.0)

print("\n时间   level   music_pos  playing")
for i in range(12):
    time.sleep(0.3)
    st = m.status()
    print(f"{i*0.3:4.1f}s  {st['level']:.4f}  {st['music_pos']:8.2f}  {st['music']}")

print("\n最终 status:", m.status())
print("stream.active =", m.stream.active)
m.close()
