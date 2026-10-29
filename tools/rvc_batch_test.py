"""批量测试 56 个 RVC 模型 —— 用同一句话全跑一遍，出频谱报告

用途：用户下载了 56 个模型（E:\\game\\rvc模型\\106nvsheng），要挑一个
      **不闷**的用于 QQ 电话。本脚本一次性跑完，输出频谱数据 + wav 文件。

## 为什么看频谱能判断"闷不闷"

人声清晰度主要靠 **1~4kHz 中频**。闷 = 中频弱、低频重。
2026-10-26 实测参照（同一句「哎，我在呢，听得清吗」）：

    MiMo TTS 输入:  低 51.4%  中 24.4%  高 15.8%  空 8.3%
    当前 youzhanv2: 低 57.1%  中 18.8%  ← 闷（中频掉 5.6 个点）

目标是找「中频尽量接近或高于 24.4%」的模型。

## 用法（必须用 RVC 的 Runtime python）

    E:\\RVC-bsq\\RVCMAX_Nvidia_xiaoyuan\\RVCMAX_Nvidia_xiaoyuan\\Runtime\\python.exe ^
        E:\\qq-bot\\tools\\rvc_batch_test.py

    # 只测部分（按名字过滤）
    ... rvc_batch_test.py --filter 甜

    # 只测带 index 的
    ... rvc_batch_test.py --with-index

## 输出

    E:\\qq-bot\\logs\\rvc_batch\\
      report.txt          按"中频从高到低"排序的完整报告
      audio\\<模型名>.wav  每个模型的音频，方便试听对比
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
import traceback

import numpy as np

RVC_ROOT = r"E:\RVC-bsq\RVCMAX_Nvidia_xiaoyuan\RVCMAX_Nvidia_xiaoyuan\RVC_Core"
WEIGHTS_DIR = os.path.join(RVC_ROOT, "assets", "weights")
INDICES_DIR = os.path.join(RVC_ROOT, "assets", "indices")

SRC_MODELS = r"E:\game\rvc模型\106nvsheng"
SRC_INDICES = os.path.join(SRC_MODELS, "15个index")

OUT_DIR = r"E:\qq-bot\logs\rvc_batch"
AUDIO_DIR = os.path.join(OUT_DIR, "audio")
INPUT_WAV = os.path.join(OUT_DIR, "input.wav")

TEST_TEXT = "哎，我在呢，听得清吗"


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S', time.localtime())} {msg}", flush=True)


# ---------------------------------------------------------------- index 对应

def build_index_map() -> dict[str, str]:
    """把 index 文件名对应到模型名。

    命名乱，实测映射（2026-10-26）：
        shaoluosusui_v2   -> shaoluosusu.pth
        feifei            -> feifei48K.pth
        nikou48K          -> nikou48K1999.pth
        suannai           -> suannai40k.pth
        xiaoju            -> xiaojuDLC.pth
        yujie             -> quan_yujie.pth
        duoduo            -> 御姐多多.pth
        七妹              -> 七妹1000模型.pth
        月亮              -> 月亮.pth
        熙然              -> 熙然6666666.pth
        lili              -> 少御莉莉.pth
        雾雾05            -> 雾雾の少御音05(1).pth
        雾雾06            -> 雾雾の少萝音06(1).pth
        雾雾40K           -> 雾雾40k.pth
        青橘              -> 青橘40k.pth
    """
    return {
        "shaoluosusui": "shaoluosusu.pth",
        "feifei": "feifei48K.pth",
        "nikou48K": "nikou48K1999.pth",
        "suannai": "suannai40k.pth",
        "xiaoju": "xiaojuDLC.pth",
        "yujie": "quan_yujie.pth",
        "duoduo": "御姐多多.pth",
        "七妹": "七妹1000模型.pth",
        "月亮": "月亮.pth",
        "熙然": "熙然6666666.pth",
        "lili": "少御莉莉.pth",
        "雾雾05": "雾雾の少御音05(1).pth",
        "雾雾06": "雾雾の少萝音06(1).pth",
        "雾雾40K": "雾雾40k.pth",
        "青橘": "青橘40k.pth",
    }


def prepare_files() -> tuple[list[str], dict[str, str]]:
    """把 56 个模型和 15 个 index 复制进 RVC 的 assets 目录。

    返回 (可用模型名列表, {模型名: index路径})。
    """
    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    os.makedirs(INDICES_DIR, exist_ok=True)

    # 1. 复制 .pth
    pths = [f for f in os.listdir(SRC_MODELS) if f.lower().endswith(".pth")]
    log(f"找到 {len(pths)} 个 .pth")
    copied = 0
    for f in pths:
        src = os.path.join(SRC_MODELS, f)
        dst = os.path.join(WEIGHTS_DIR, f)
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copy2(src, dst)
            copied += 1
    log(f"  复制/更新 {copied} 个模型到 {WEIGHTS_DIR}")

    # 2. 复制 .index（保留原名，便于 file_index 传路径）
    idx_map = build_index_map()
    model_to_index: dict[str, str] = {}
    copied_i = 0
    if os.path.exists(SRC_INDICES):
        for f in os.listdir(SRC_INDICES):
            if not f.lower().endswith(".index"):
                continue
            src = os.path.join(SRC_INDICES, f)
            dst = os.path.join(INDICES_DIR, f)
            if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
                shutil.copy2(src, dst)
                copied_i += 1
            # 匹配模型：直接用映射表的 key 做包含匹配
            # ⚠️ 之前用 stem 规范化会漏掉「雾雾06.index」「青橘.index」这类纯中文名，
            #    2026-10-26 实测踩到（报 faiss 找不到文件，其实文件存在、只是没匹配上）
            for key, model in idx_map.items():
                if key.lower() in f.lower():
                    model_to_index[model] = dst
                    break
    log(f"  复制/更新 {copied_i} 个 index 到 {INDICES_DIR}")
    log(f"  已匹配 {len(model_to_index)} 个模型有 index")

    return pths, model_to_index


# ---------------------------------------------------------------- 音频分析

def spectrum(path: str) -> dict:
    import wave
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    d = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    m = min(len(d), rate * 2)
    if m < 100:
        return {}
    spec = np.abs(np.fft.rfft(d[:m]))
    freqs = np.fft.rfftfreq(m, 1 / rate)
    total = spec.sum() or 1.0
    out = {"rate": rate, "dur": len(d) / rate,
           "peak": float(np.abs(d).max()),
           "rms": float(np.sqrt((d ** 2).mean()))}
    for lo, hi, label in [(0, 1000, "low"), (1000, 4000, "mid"),
                          (4000, 8000, "high"), (8000, 16000, "air")]:
        mask = (freqs >= lo) & (freqs < hi)
        out[label] = float(spec[mask].sum() / total * 100)
    return out


# ---------------------------------------------------------------- RVC

_engine = {"vc": None, "loaded": None}


def get_vc():
    if _engine["vc"] is not None:
        return _engine["vc"]
    os.chdir(RVC_ROOT)
    if RVC_ROOT not in sys.path:
        sys.path.insert(0, RVC_ROOT)
    for mod in list(sys.modules):
        if mod == "configs" or mod.startswith(("configs.", "infer.")):
            del sys.modules[mod]
    # ⚠️ RVC 内部用 os.getenv 拼路径，不设会拼出 "None/xxx.pth"
    os.environ.setdefault("weight_root", WEIGHTS_DIR)
    os.environ.setdefault("rmvpe_root", os.path.join(RVC_ROOT, "assets", "rmvpe"))
    os.environ.setdefault("index_root", INDICES_DIR)
    os.environ.setdefault("outside_index_root", INDICES_DIR)

    # ⚠️ RVC 的 Config() 内部会跑 argparse.parse_args()，它看不到我们的参数，
    #    会把 --limit / --filter 当成非法参数报错退出。
    #    2026-10-26 实测踩到。解法：调用期间临时清空 sys.argv。
    _saved_argv = sys.argv[:]
    sys.argv = [sys.argv[0]]

    from configs.config import Config
    from infer.modules.vc.modules import VC
    try:
        cfg = Config()
        _engine["vc"] = VC(cfg)
    finally:
        sys.argv = _saved_argv   # 恢复我们的参数
    return _engine["vc"]


def convert(input_wav: str, out_wav: str, model: str,
            file_index: str = "", index_rate: float = 0.0,
            rms_mix_rate: float = 0.25, protect: float = 0.45,
            filter_radius: int = 3) -> float:
    """跑一次转换。参数用 A/B 测试选出的较优值。"""
    vc = get_vc()
    if _engine["loaded"] != model:
        vc.get_vc(model)
        _engine["loaded"] = model
    t0 = time.time()
    _, opt = vc.vc_single(
        0, input_wav, 0, None, "rmvpe",
        file_index, "", float(index_rate), int(filter_radius),
        0, float(rms_mix_rate), float(protect),
    )
    cost = time.time() - t0
    audio = opt[1]
    if not isinstance(audio, np.ndarray):
        raise RuntimeError("vc_single 返回异常")
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


# ---------------------------------------------------------------- 主流程

def ensure_input() -> str:
    if os.path.exists(INPUT_WAV) and os.path.getsize(INPUT_WAV) > 1000:
        log(f"复用已有输入: {INPUT_WAV}")
        return INPUT_WAV
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_gen_tts_wav.py")
    main_py = "E:/qq-bot/astrbot-venv/Scripts/python.exe"
    log("用主 venv 生成测试音频……")
    r = __import__("subprocess").run([main_py, helper, INPUT_WAV],
                                     capture_output=True, timeout=180)
    if r.returncode != 0 or not os.path.exists(INPUT_WAV):
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[:300])
    log("  输入音频已生成")
    return INPUT_WAV


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter", default="", help="只要名字含这些字的模型")
    ap.add_argument("--with-index", action="store_true", help="只测带 index 的")
    ap.add_argument("--limit", type=int, default=0, help="最多测几个")
    args = ap.parse_args()

    os.makedirs(AUDIO_DIR, exist_ok=True)
    log("=" * 66)
    log("  RVC 模型批量对比（找不闷的音色）")
    log("=" * 66)

    models, idx_map = prepare_files()
    if args.filter:
        models = [m for m in models if args.filter in m]
    if args.with_index:
        models = [m for m in models if m in idx_map]
    models.sort()
    if args.limit:
        models = models[:args.limit]
    log(f"待测模型: {len(models)} 个")

    src = ensure_input()
    base = spectrum(src)
    log(f"输入(MiMo TTS): 低{base['low']:.1f}% 中{base['mid']:.1f}% "
        f"高{base['high']:.1f}% 空{base['air']:.1f}%")
    log(f"目标: 中频 >= {base['mid']:.1f}%（越高越不闷）")
    log("")

    results = []
    t_all = time.time()

    for i, model in enumerate(models, 1):
        name = os.path.splitext(model)[0]
        out = os.path.join(AUDIO_DIR, f"{name}.wav")
        idx = idx_map.get(model, "")
        has_idx = bool(idx)
        ir = 0.4 if has_idx else 0.0   # 有 index 才启用检索

        try:
            cost = convert(src, out, model, file_index=idx, index_rate=ir)
            sp = spectrum(out)
            if not sp:
                log(f"[{i:2d}/{len(models)}] {name:28s} 分析失败")
                continue
            score = sp["mid"] - base["mid"]   # 中频相对输入的增减
            results.append({
                "model": model, "name": name, "cost": cost,
                "has_index": has_idx, "score": score, **sp,
            })
            flag = "✅" if score >= 0 else ("~" if score > -3 else "❌")
            log(f"[{i:2d}/{len(models)}] {name:28s} "
                f"低{sp['low']:5.1f}% 中{sp['mid']:5.1f}% 高{sp['high']:4.1f}% "
                f"空{sp['air']:4.1f}% 峰{sp['peak']:.2f} "
                f"中频{score:+5.1f} {flag} {cost:.2f}s {'有idx' if has_idx else ''}")
        except Exception as e:  # noqa: BLE001
            log(f"[{i:2d}/{len(models)}] {name:28s} ❌ {e}")
            continue

    total = time.time() - t_all

    # 排序输出
    results.sort(key=lambda r: r["mid"], reverse=True)
    log("")
    log("=" * 78)
    log("  排名（中频从高到低 = 从最不闷到最闷）")
    log("=" * 78)
    log(f"  {'排名':>4s} {'模型':30s} {'低频':>6s} {'中频':>6s} {'空感':>5s} {'峰值':>5s} {'idx':>3s}")
    for i, r in enumerate(results, 1):
        log(f"  {i:3d}. {r['name']:30s} {r['low']:5.1f}% {r['mid']:5.1f}% "
            f"{r['air']:5.1f}% {r['peak']:5.2f} {('是' if r['has_index'] else ''):>3s}")

    if results:
        log("")
        log(f"  🏆 最不闷的三个: {', '.join(r['name'] for r in results[:3])}")
        log(f"  总耗时 {total:.1f}s（{len(results)} 个模型）")

    # 写报告文件
    rp = os.path.join(OUT_DIR, "report.txt")
    with open(rp, "w", encoding="utf-8") as f:
        f.write(f"输入(MiMo TTS): 低{base['low']:.1f}% 中{base['mid']:.1f}% "
                f"高{base['high']:.1f}% 空{base['air']:.1f}%\n")
        f.write(f"测试文本: {TEST_TEXT}\n")
        f.write(f"参数: rms_mix_rate=0.25 protect=0.45 filter_radius=3\n\n")
        f.write(f"{'排名':>4s} {'模型':30s} {'低频':>7s} {'中频':>7s} {'高频':>7s} {'空感':>6s} {'峰值':>6s}\n")
        for i, r in enumerate(results, 1):
            f.write(f"{i:4d} {r['name']:30s} {r['low']:6.1f}% {r['mid']:6.1f}% "
                    f"{r['high']:6.1f}% {r['air']:5.1f}% {r['peak']:6.2f}\n")

    log(f"\n报告: {rp}")
    log(f"音频: {AUDIO_DIR}")
    log("  听一下前三个，选一个最满意的")
    return 0


if __name__ == "__main__":
    sys.exit(main())
