"""RVC 音质 A/B 测试 —— 对比不同 vc_single 参数对音质的影响

## 背景（2026-10-26 实测数据）

用户反馈 RVC 电话音色「发闷」。频谱分析定位到确切原因：

    | 环节                    | 低频   | 中频    | 高频   | 超高频 |
    |------------------------|-------|--------|-------|-------|
    | MiMo TTS 原始（RVC输入）  | 40.0% | 35.0%  | 14.4% | 10.6% |
    | RVC 转换后（当前参数）     | 62.1% | 15.2% ⬇ | 16.6% | 5.5%  |

**RVC 把中频从 35% 压到 15%，低频从 40% 抬到 62%** —— 这就是发闷的元凶。
人声清晰度主要靠 1~4kHz 中频，被削掉 20 个百分点必然闷。

## 怀疑的参数（cover_service.py:773 `_rvc_convert`）

    vc.vc_single(0, input_wav, f0up_key, None, "rmvpe", None, None, 0.0, 3, 0, 1.0, 0.33)
    #            sid input      f0_up  f0file f0meth  idx  idx2 idxr  fil res  rms pro
    #                                                            ↑0.0  ↑3  ↑0  ↑1.0 ↑0.33

  1. `index_rate=0.0` —— 没用检索索引。但**本模型 youzhanv2-xi.pth 没有配套 .index**，
     所以这个值改了也没效果（assets/indices/ 里没有 youzhan）。
  2. `rms_mix_rate=1.0` —— 完全套用源音频响度包络，会让音色"被压平"。
  3. `protect=0.33` —— 保护清辅音的程度偏低，s/sh/t 容易被模型吃掉。
     ⚠️ STATUS.md 记过：**protect >= 0.5 会整段跳过保护逻辑**，所以上限 0.45。
  4. `filter_radius=3` —— f0 中值滤波窗口，越小细节越多（也可能更抖）。

## 本脚本做什么

**不启动服务、不改任何现有代码**，直接在 RVC Runtime 里跑多组参数，
每组生成 wav + 频谱分析，跑完你自己听、自己看数据决定用哪组。

用法（必须用 RVC 的 Runtime python）：
    E:\\RVC-bsq\\RVCMAX_Nvidia_xiaoyuan\\RVCMAX_Nvidia_xiaoyuan\\Runtime\\python.exe ^
        E:\\qq-bot\\tools\\rvc_ab_test.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import numpy as np

RVC_ROOT = r"E:\RVC-bsq\RVCMAX_Nvidia_xiaoyuan\RVCMAX_Nvidia_xiaoyuan\RVC_Core"
FFMPEG = os.path.join(RVC_ROOT, "ffmpeg.exe")
OUT_DIR = r"E:\qq-bot\logs\rvc_ab"
MODEL = "youzhanv2-xi.pth"

# 测试文本（用电话里最常出现的那种短句）
TEST_TEXTS = [
    "哎，我在呢，听得清吗",
]


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S', time.localtime())} {msg}", flush=True)


# ---------------------------------------------------------------- MiMo TTS

def mimo_tts(text: str) -> str:
    """生成 RVC 的输入 wav。复用电话链路的 TTS 客户端。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import wave
    from call_asr_tts import load_providers, TtsClient
    import numpy as _np

    provs = load_providers(r"E:\qq-bot")
    tts = TtsClient(provs["tts"])
    pcm, rate = tts.synthesize(text)
    if pcm is None:
        raise RuntimeError("MiMo TTS 失败")
    path = os.path.join(OUT_DIR, "input.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((_np.clip(pcm, -1, 1) * 32767).astype(_np.int16).tobytes())
    log(f"MiMo TTS -> {path} ({len(pcm)/rate:.2f}s @{rate}Hz)")
    return path


# ---------------------------------------------------------------- 频谱分析

def spectrum(path: str) -> dict:
    """读 wav，算四个频段占比。"""
    import wave

    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    d = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    m = min(len(d), rate * 2)
    seg = d[:m]
    spec = np.abs(np.fft.rfft(seg))
    freqs = np.fft.rfftfreq(m, 1 / rate)
    total = spec.sum() or 1.0

    out = {"rate": rate, "dur": len(d) / rate,
           "peak": float(np.abs(d).max()), "rms": float(np.sqrt((d ** 2).mean()))}
    for lo, hi, label in [(0, 1000, "low"), (1000, 4000, "mid"),
                          (4000, 8000, "high"), (8000, 16000, "air")]:
        mask = (freqs >= lo) & (freqs < hi)
        out[label] = float(spec[mask].sum() / total * 100)
    return out


# ---------------------------------------------------------------- RVC 转换

_engine = {"vc": None, "config": None, "loaded": None}


def get_vc():
    """复用 RVC 的 VC 单例（跟 cover_service.py:91 一样的做法）。

    ⚠️ 顺序很关键：必须**先** chdir + sys.path 插到最前，**再** import configs。
    否则 sys.path[0] 还是 E:\qq-bot	ools，找不到 RVC_ROOT 下的 configs 包。
    2026-10-26 实测踩到（ModuleNotFoundError: No module named 'configs'）。
    """
    if _engine["vc"] is not None:
        return _engine["vc"]

    # 1. 先切目录 + 插路径（必须在 import 之前）
    os.chdir(RVC_ROOT)
    if RVC_ROOT not in sys.path:
        sys.path.insert(0, RVC_ROOT)
    # 清掉可能已导入的同名模块缓存
    for mod in list(sys.modules):
        if mod == "configs" or mod.startswith("configs.") or mod.startswith("infer."):
            del sys.modules[mod]

    # 2. 设置 RVC 需要的环境变量（模块里用 os.getenv 直接读，不走 Config）
    #    infer/modules/vc/modules.py:100  ->  person = f'{os.getenv("weight_root")}/{sid}'
    #    不设的话会拼出 "None/xxx.pth"，报 FileNotFoundError。
    #    2026-10-26 实测踩到。
    os.environ.setdefault("weight_root",
                          os.path.join(RVC_ROOT, "assets", "weights"))
    os.environ.setdefault("rmvpe_root",
                          os.path.join(RVC_ROOT, "assets", "rmvpe"))
    os.environ.setdefault("index_root",
                          os.path.join(RVC_ROOT, "assets", "indices"))
    os.environ.setdefault("outside_index_root",
                          os.path.join(RVC_ROOT, "assets", "indices"))
    log(f"  weight_root = {os.environ['weight_root']}")

    # 3. 再导入
    from configs.config import Config
    from infer.modules.vc.modules import VC

    cfg = Config()
    _engine["config"] = cfg
    _engine["vc"] = VC(cfg)
    return _engine["vc"]


def convert(input_wav: str, out_wav: str, f0up_key: int = 0,
            f0_method: str = "rmvpe", index_rate: float = 0.0,
            filter_radius: int = 3, rms_mix_rate: float = 1.0,
            protect: float = 0.33) -> float:
    """跑一次 vc_single，返回耗时。"""
    vc = get_vc()
    if _engine["loaded"] != MODEL:
        vc.get_vc(MODEL)
        _engine["loaded"] = MODEL
    t0 = time.time()
    _, opt = vc.vc_single(
        0, input_wav, int(f0up_key), None, f0_method,
        None, None, float(index_rate), int(filter_radius),
        0, float(rms_mix_rate), float(protect),
    )
    cost = time.time() - t0
    audio = opt[1]
    if not isinstance(audio, np.ndarray):
        raise RuntimeError(f"vc_single 返回异常: {type(audio)}")
    if audio.dtype != np.int16:
        a = audio.astype(np.float32)
        peak = float(np.max(np.abs(a))) if a.size else 0.0
        if peak > 0.98 and peak > 0:
            a = a / peak * 0.95
        audio = (a * 32767).astype(np.int16)
    import wave
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(opt[0]))
        w.writeframes(audio.tobytes())
    return cost


# ---------------------------------------------------------------- 测试矩阵

# (名字, rms_mix_rate, protect, filter_radius)
CASES = [
    ("baseline_当前",      1.00, 0.33, 3),   # 就是现在 cover_service 用的
    ("rms025_prot033",     0.25, 0.33, 3),   # 只降 rms
    ("rms025_prot045",     0.25, 0.45, 3),   # 降 rms + 提高保护（<0.5 才生效）
    ("rms000_prot045",     0.00, 0.45, 3),   # 完全不用源响度
    ("rms025_prot045_f2",  0.25, 0.45, 2),   # 再降 filter_radius
]


def ensure_input_wav() -> str:
    """确保有 input.wav。如果 RVC Runtime 里没有 httpx，就调主 venv 生成。"""
    path = os.path.join(OUT_DIR, "input.wav")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        log(f"复用已有输入: {path}")
        return path

    # 用主 venv（有 httpx）跑 TTS，导出 wav
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_gen_tts_wav.py")
    main_py = "E:/qq-bot/astrbot-venv/Scripts/python.exe"
    log("用主 venv 生成 TTS 输入……")
    r = subprocess.run([main_py, helper, path], capture_output=True, timeout=180)
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    if out.strip():
        for line in out.strip().splitlines():
            log("    " + line)
    if r.returncode != 0 or not os.path.exists(path):
        log(f"生成失败 rc={r.returncode}")
        if err.strip():
            log("    stderr: " + err.strip()[:400])
        raise RuntimeError("TTS 输入生成失败")
    return path


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    log("=" * 60)
    log("  RVC 音质 A/B 测试（不改任何现有服务）")
    log("=" * 60)
    log(f"输出目录: {OUT_DIR}")
    log(f"模型: {MODEL}")

    if not os.path.exists(RVC_ROOT):
        log(f"找不到 RVC_ROOT: {RVC_ROOT}")
        return 1

    # 各频段汇总表
    results = []

    for text in TEST_TEXTS:
        log(f"\n测试文本: {text!r}")
        try:
            src = ensure_input_wav()
        except Exception as e:  # noqa: BLE001
            log(f"输入音频准备失败，退出: {e}")
            return 1

        base = spectrum(src)
        log(f"  输入频谱: 低{base['low']:.1f}% 中{base['mid']:.1f}% "
            f"高{base['high']:.1f}% 空{base['air']:.1f}%")
        results.append(("输入(MiMo)", 0.0, base))

        for name, rms, prot, filt in CASES:
            out = os.path.join(OUT_DIR, f"{name}.wav")
            log(f"\n  --- {name}  (rms={rms} protect={prot} filter={filt}) ---")
            try:
                cost = convert(src, out, rms_mix_rate=rms,
                               protect=prot, filter_radius=filt)
                sp = spectrum(out)
                log(f"      耗时 {cost:.2f}s  时长 {sp['dur']:.2f}s  峰值 {sp['peak']:.3f}")
                log(f"      频谱: 低{sp['low']:.1f}% 中{sp['mid']:.1f}% "
                    f"高{sp['high']:.1f}% 空{sp['air']:.1f}%")
                # 与输入的中频对比，衡量"闷化"程度
                mid_loss = base["mid"] - sp["mid"]
                log(f"      中频变化: {mid_loss:+.1f} 个百分点 "
                    f"({'改善' if mid_loss < 0 else '仍被削弱'})")
                results.append((name, cost, sp))
            except Exception as e:  # noqa: BLE001
                log(f"      失败: {e}")
                import traceback
                traceback.print_exc()

    # 汇总
    log("\n" + "=" * 74)
    log("  汇总（低频越低、中频越高 = 越不闷）")
    log("=" * 74)
    log(f"  {'方案':22s} {'低频':>7s} {'中频':>7s} {'高频':>7s} {'空':>6s} {'耗时':>7s}")
    for name, cost, sp in results:
        log(f"  {name:22s} {sp['low']:6.1f}% {sp['mid']:6.1f}% "
            f"{sp['high']:6.1f}% {sp['air']:5.1f}% {cost:6.2f}s")

    log("")
    log(f"生成的 wav 都在: {OUT_DIR}")
    log("  你自己听，选一个最满意的，再决定是否改 cover_service.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
