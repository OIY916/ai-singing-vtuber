"""用主 venv（有 httpx）生成一段 TTS wav，供 RVC A/B 测试用。

被 tools/rvc_ab_test.py 以子进程方式调用：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe _gen_tts_wav.py <输出路径>

之所以要拆出来：RVC 的 Runtime python（torch 2.0+cu118）里没有 httpx，
不能直接调云端 TTS。所以用主 venv 生成、RVC Runtime 只负责转换。
"""

from __future__ import annotations

import os
import sys
import wave

import numpy as np


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: _gen_tts_wav.py <输出wav路径>", file=sys.stderr)
        return 2
    out_path = sys.argv[1]

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from call_asr_tts import TtsClient, load_providers

    provs = load_providers(r"E:\qq-bot")
    if not provs.get("tts"):
        print("没有 TTS 配置", file=sys.stderr)
        return 1

    tts = TtsClient(provs["tts"])
    text = "哎，我在呢，听得清吗"
    pcm, rate = tts.synthesize(text)
    if pcm is None or len(pcm) == 0:
        print("TTS 合成失败", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(pcm, -1.0, 1.0) * 32767).astype(np.int16).tobytes())

    print(f"已生成 {out_path}: {len(pcm)} 采样 @{rate}Hz = {len(pcm)/rate:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
