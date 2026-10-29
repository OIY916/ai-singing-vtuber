# -*- coding: utf-8 -*-
"""RVC 单步测试：把人声 wav 转成指定音色（不做分离）"""
import os
import sys

_args = sys.argv[1:]
INPUT = _args[0] if len(_args) > 0 else ""
MODEL = _args[1] if len(_args) > 1 else ""   # 留空 = 用 weights 里第一个
OUTPUT = _args[2] if len(_args) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_out.wav")
F0UP = int(_args[3]) if len(_args) > 3 else 0
F0METHOD = _args[4] if len(_args) > 4 else "rmvpe"
sys.argv = [sys.argv[0]]

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
import pathcfg
RVC_ROOT = pathcfg.rvc_core()
if not RVC_ROOT or not os.path.isdir(RVC_ROOT):
    raise SystemExit("找不到 RVC_Core：请在项目根 paths.json 里填 rvc_home")
os.chdir(RVC_ROOT)
sys.path.insert(0, RVC_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(RVC_ROOT, ".env"))

from scipy.io import wavfile
from configs.config import Config
from infer.modules.vc.modules import VC

if not INPUT or not os.path.exists(INPUT):
    raise SystemExit("用法: python test_vc.py <输入音频> [音色.pth] [输出.wav] [f0up] [method]")
print("[test] in=%s model=%s f0up=%s method=%s" % (INPUT, MODEL, F0UP, F0METHOD))

config = Config()
vc = VC(config)
vc.get_vc(MODEL)

_, wav_opt = vc.vc_single(
    0, INPUT, F0UP, None, F0METHOD, None, None, 0.0, 3, 0, 1.0, 0.33,
)

import numpy as np
audio = wav_opt[1]
if isinstance(audio, np.ndarray) and audio.dtype != np.int16:
    audio = audio.astype(np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.98 and peak > 0:
        audio = audio / peak * 0.95
    audio = (audio * 32767).astype(np.int16)

wavfile.write(OUTPUT, int(wav_opt[0]), audio)
print("[test] DONE sr=%s -> %s" % (wav_opt[0], OUTPUT))
