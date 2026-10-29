# -*- coding: utf-8 -*-
"""
本地 RVC 翻唱服务（HTTP API）
流程：输入歌曲 -> UVR5 人声分离 -> RVC 音色转换 -> 与原伴奏混音 -> 返回 mp3

启动（用 RVC 自带 runtime，路径见项目根 paths.json 的 rvc_home）：
  <rvc_home>\Runtime\python.exe cover_service.py

接口：
  GET  /health                     健康检查
  GET  /models                     列出可用音色
  POST /cover                      上传文件翻唱（multipart: file, model, f0up_key, index_rate）
  POST /cover_url                  给本地音频路径翻唱（queue 模式）
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# ⚠️ 不写死盘符：RVC 装在哪由项目根目录 paths.json 的 rvc_home
#    （或环境变量 DFY_RVC_HOME）决定。本服务跑在 RVC 自带的 Python 里，
#    所以要把项目根加进 sys.path 才能 import pathcfg。
sys.path.insert(0, os.path.dirname(BASE_DIR))
import pathcfg

RVC_ROOT = pathcfg.rvc_core()
if not RVC_ROOT or not os.path.isdir(RVC_ROOT):
    raise SystemExit(
        "找不到 RVC_Core 目录。\n"
        "请在项目根目录的 paths.json 里填 rvc_home，或设置环境变量 DFY_RVC_HOME，\n"
        "指向 RVCMAX 安装根目录（其下应有 RVC_Core/ 和 Runtime/）。\n"
        "当前解析结果: %r" % (RVC_ROOT,))
WORK_DIR = os.path.join(BASE_DIR, "work")
OUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(WORK_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

SERVICE_PORT = int(os.environ.get("COVER_PORT", "8899"))
# RVC 的 Config 会在模块导入时解析命令行参数，必须先清空 argv
sys.argv = [sys.argv[0]]

os.chdir(RVC_ROOT)
sys.path.insert(0, RVC_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(RVC_ROOT, ".env"))

FFMPEG = os.path.join(RVC_ROOT, "ffmpeg.exe")
if not os.path.exists(FFMPEG):
    FFMPEG = "ffmpeg"

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

app = FastAPI(title="RVC Cover Service")

_engine = {"config": None, "vc": None, "loaded_model": None}
_lock = None
_lock_holder = {"who": None, "since": 0.0}


def _acquire_busy(who: str):
    """加锁；失败返回 False。记录持锁者，便于排查锁泄漏。

    ⚠️ 「服务忙」是这套系统里最容易出现的错误（/sing 和 /speak 共用一把锁）。
       记日志时一定要带上**谁持锁、持了多久**，否则没法判断
       是正常的短暂占用，还是锁泄漏（持锁者异常退出但没 release）。
    """
    global _lock_holder
    lock = _get_lock()
    if not lock.acquire(blocking=False):
        held = _lock_holder
        age = (time.time() - held["since"]) if held.get("since") else 0.0
        print(f"[cover] 拒绝 {who}：服务忙，当前持锁 {held['who']} 已 {age:.1f}s")
        # 持锁超过 3 分钟大概率是出问题了（正常翻唱最长 ~90 秒）
        if age > 180:
            print(f"[cover] ⚠️ 锁已持有 {age:.0f}s，疑似泄漏！"
                  f"持锁者={held['who']}，请检查该请求是否异常退出")
        return False
    _lock_holder = {"who": who, "since": time.time()}
    print(f"[cover] 获得锁 {who}")
    return True


def _release(who: str):
    global _lock_holder
    lock = _get_lock()
    age = _lock_age_seconds()
    _lock_holder = {"who": None, "since": 0.0}
    try:
        lock.release()
        print(f"[cover] 释放锁 {who}（持锁 {age:.1f}s）")
    except RuntimeError:
        print(f"[cover] release 异常（{who}）：锁未被持有，已忽略")


def _lock_age_seconds() -> float:
    since = _lock_holder.get("since") or 0.0
    return (time.time() - since) if since else 0.0


def _get_lock():
    global _lock
    if _lock is None:
        import threading
        _lock = threading.Lock()
    return _lock


def get_vc():
    if _engine["vc"] is None:
        from configs.config import Config
        from infer.modules.vc.modules import VC
        _engine["config"] = Config()
        _engine["vc"] = VC(_engine["config"])
    return _engine["vc"]


def list_models():
    d = os.path.join(RVC_ROOT, "assets", "weights")
    return sorted([f for f in os.listdir(d) if f.endswith(".pth")])


def _ensure_playable(src):
    """把 NCM(网易云加密) 等格式转成可读音频，返回可用路径"""
    low = src.lower()
    if low.endswith(".ncm"):
        try:
            import ncmdump
            out = ncmdump.dump(src)
            if out and os.path.exists(out):
                print(f"[cover] NCM 已解密: {src} -> {out}")
                return out
        except Exception as e:
            print(f"[cover] NCM 解密失败: {e}")
            raise RuntimeError(f"NCM 解密失败: {e}")
    return src


def _ffmpeg_to_wav(src, dst, sr=44100):
    """统一转成 44.1k 立体声 wav，保证分离器稳定"""
    cmd = [FFMPEG, "-y", "-i", src, "-vn", "-acodec", "pcm_s16le", "-ac", "2", "-ar", str(sr), dst]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def _first_wav(d):
    files = sorted(glob.glob(os.path.join(d, "*.wav")))
    if not files:
        raise RuntimeError(f"分离未产出音频: {d}")
    return files[0]


def _measure_peak(wav_path):
    """测 wav 峰值（float 精度）"""
    import numpy as np
    from scipy.io import wavfile
    sr, data = wavfile.read(wav_path)
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float32) / 2147483648.0
    return float(np.max(np.abs(data))) if data.size else 0.0


# 削波检测阈值：超过这个值视为"已经削平"，波形顶部已丢失信息
CLIP_THRESHOLD = 0.995
# 归一化目标峰值（约 -1dBFS，留一点余量）
NORMALIZE_TARGET = 0.89
# 人声最小能量占比：低于此值视为"没有人声"（钢琴版/纯音乐/伴奏版）
#
# 阈值依据（2026-10-25 实测校准，17 个样本）：
#   正常有人声的歌曲：人声/伴奏 比值 0.44 ~ 0.90
#   无人声（钢琴版/伴奏）：        比值 0.16
# 取 0.30 作为分界，两边都留了足够余量。
MIN_VOCAL_RATIO = 0.30

# ---- 音高自动检测与升调（2026-10-26）----
#
# ⚠️ 默认关闭（2026-10-26 用户决定）。
#
# 历史：一开始想用「检测到男声就自动升调」解决男声歌失真问题。
# 实测后放弃，原因是**更好的方案已经取代了它**：
#
#   旧思路：男声歌 → 强行升 6 个半音 → 用户实测「还是很怪」
#   新方案：点歌时直接优先选**女声翻唱版**（见 FEMALE_HINT_PATTERNS），
#           根本不需要升调。实测《安和桥》选「治愈女版」(320Hz) 效果很好，
#           auto-pitch 压根没触发（320Hz ≥ 180Hz 阈值）。
#
# 而且留着它反而有害：万一 /pick 没找到女声版、落到男声原唱上，
# 它会升 6 个半音，产出的正是那个已验证「很怪」的效果。
# 实测白允y 的女声版（100Hz）几乎和宋冬野原唱（97Hz）一样，
# 说明女声唱低音本来就能听，不升调反而更自然。
#
# 另外自相关检测本身不可靠：实测会把谐波误判成基频（P75 全是 800Hz）。
#
# 保留代码的理由：以后若换**男声模型**，这套逻辑可能重新有用。
# 需要时把 AUTO_PITCH_ENABLE 改成 True 即可，其余代码不用动。
AUTO_PITCH_ENABLE = False

# 女声模型的舒适音域下限（Hz）。低于此值就要升调。
PITCH_MODEL_COMFORT_LOW = 190.0
# 升调目标（Hz）。240 是女声模型比较自然的中间位置。
PITCH_TARGET_HZ = 240.0
# 男声判定阈值（Hz）。人声基频中位数低于它就认为是男声歌。
PITCH_MALE_THRESHOLD = 180.0


def _estimate_f0(wav_path, fmin=70.0, fmax=800.0):
    """自相关估人声基频中位数（Hz）。失败返回 None。

    用中位数而非均值：歌声有大量静音/气声帧，均值会被拉偏。
    """
    import numpy as np
    rate = 16000
    try:
        r = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error",
             "-i", wav_path,
             "-f", "f32le", "-ac", "1", "-ar", str(rate), "pipe:1"],
            capture_output=True, timeout=180,
        )
        if r.returncode != 0 or not r.stdout:
            return None
        pcm = np.frombuffer(r.stdout, dtype=np.float32).copy()
    except Exception:
        return None

    if len(pcm) < rate // 4:
        return None

    frame = int(rate * 0.05)      # 50ms
    lo = int(rate / fmax)
    hi = int(rate / fmin)
    f0s = []
    i = 0
    n = len(pcm) - frame - hi
    while i < n:
        seg = pcm[i:i + frame + hi]
        i += frame
        if float(np.sqrt((seg[:frame] ** 2).mean())) < 0.012:
            continue
        seg = seg - seg.mean()
        size = frame + hi
        ac = np.correlate(seg, seg, mode="full")[size - 1:]
        if len(ac) <= hi or ac[0] <= 0:
            continue
        region = ac[lo:hi]
        if len(region) == 0:
            continue
        peak_idx = int(np.argmax(region)) + lo
        if ac[peak_idx] < 0.35 * ac[0]:
            continue
        f = rate / peak_idx
        if fmin <= f <= fmax:
            f0s.append(f)
    if len(f0s) < 3:
        return None
    return float(np.median(f0s))


def _auto_pitch_shift(vocal_wav, given_key):
    """决定实际使用 f0up_key。

    - 总开关 AUTO_PITCH_ENABLE=False 时：一律不升调（默认行为，2026-10-26 起）
    - given_key != 0  → 用户手动指定，直接尊重
    - 否则自动检测：男声歌升到女声模型舒适音域
    返回 (实际f0up_key, 说明文字)
    """
    if int(given_key) != 0:
        return int(given_key), f"手动指定 {given_key} 个半音"

    if not AUTO_PITCH_ENABLE:
        return 0, "自动升调已关闭（改用优先选女声版）"

    f0 = _estimate_f0(vocal_wav)
    if f0 is None:
        return 0, "音高检测失败，不升调"

    if f0 >= PITCH_MALE_THRESHOLD:
        return 0, f"人声 {f0:.0f}Hz（非男声音域），不升调"

    import math
    semitones = int(round(12 * math.log2(PITCH_TARGET_HZ / f0)))
    semitones = max(1, min(12, semitones))
    after = f0 * (2 ** (semitones / 12))
    return semitones, (
        f"人声 {f0:.0f}Hz（男声音域），自动升 {semitones} 个半音 → {after:.0f}Hz"
    )


def _vocal_energy_ratio(vocal_wav, ins_wav):
    """估算人声在整首里的能量占比，用于识别"没有人声"的音源。

    原理：人声分离后，如果源音频本来就没有人声（钢琴版、纯音乐、伴奏），
    分离出的 vocals 轨会极其安静，而 ins 轨接近原始能量。
    正常歌曲的人声能量通常占 15%~40%。

    用 RMS 之比衡量。分离器有时会把少量伴奏漏进 vocals，
    所以这个值只是粗判，不做精细分析。

    @returns 人声/伴奏 的 RMS 比值；无法测量时返回 1.0（放行，避免误杀）
    """
    import numpy as np
    from scipy.io import wavfile

    def _rms(path):
        sr, data = wavfile.read(path)
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            data = data.astype(np.float32) / 2147483648.0
        else:
            data = data.astype(np.float32)
        if data.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(data ** 2)))

    try:
        rv = _rms(vocal_wav)
        ri = _rms(ins_wav)
        if ri < 1e-9:
            # 伴奏几乎为 0（纯人声清唱），不判为无人声
            return 1.0
        return rv / ri
    except Exception as e:
        print(f"[vocal-check] 测量失败，跳过检查: {e}")
        return 1.0




def _audio_stats(wav_path):
    """统计削波情况，用于判断音源质量。

    ⚠️ 只支持 WAV（scipy.io.wavfile 读不了 FLAC/MP3）。
    主流程调用前已经是 wav，所以没问题；若要传入原始音源，
    先用 _ffmpeg_to_wav 转换，否则返回"未知"状态的兜底值。

    返回 dict:
        peak       —— 峰值（float）
        clip_ratio —— 处于削波阈值的采样占比（0~1）
        has_clip   —— 是否已削波
    """
    import numpy as np
    from scipy.io import wavfile

    try:
        sr, data = wavfile.read(wav_path)
    except Exception as e:
        print(f"[audio-stats] 读取失败（可能不是 WAV）: {type(e).__name__}")
        return {"peak": 0.0, "clip_ratio": 0.0, "has_clip": False, "unknown": True}

    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float32) / 2147483648.0
    else:
        data = data.astype(np.float32)

    if data.size == 0:
        return {"peak": 0.0, "clip_ratio": 0.0, "has_clip": False}

    peak = float(np.max(np.abs(data)))
    # 统计贴近满量程的采样（真正的削波：波形被压平在最大值）
    clip_ratio = float(np.mean(np.abs(data) >= CLIP_THRESHOLD))
    has_clip = clip_ratio > 0.0005 or peak >= 0.999
    return {"peak": peak, "clip_ratio": clip_ratio, "has_clip": has_clip}


def _normalize_source(src_audio, workdir):
    """对源音频做峰值归一化，缓解"破音"。

    背景（实测）：网易云搜不到原唱时会选中翻唱版，部分翻唱版 peak≈0.99
    甚至超过 1.0（已削波）。这种音频经过人声分离 + RVC 转换后，
    高频失真会被放大 2~3 倍，听起来就是破音、发闷、金属感。

    注意：**已经削平的波形无法真正修复**（信息已丢失），
    这里做的是把整体电平压到 -1dBFS，避免后续环节继续放大失真。

    @returns (音频路径, stats) —— 若无需处理则原样返回路径
    """
    import numpy as np
    from scipy.io import wavfile

    try:
        stats = _audio_stats(src_audio)
    except Exception as e:
        print(f"[normalize] 读取失败，跳过归一化: {e}")
        return src_audio, {"peak": 0.0, "clip_ratio": 0.0, "has_clip": False}

    peak = stats["peak"]
    # 峰值正常就不用动，避免无谓的重编码损失
    if peak <= 0.95:
        return src_audio, stats

    sr, data = wavfile.read(src_audio)
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float32) / 2147483648.0
    else:
        data = data.astype(np.float32)

    if peak <= 0:
        return src_audio, stats

    gain = NORMALIZE_TARGET / peak
    data = data * gain
    # 保险：限制在合法范围，避免 float 溢出
    data = np.clip(data, -1.0, 1.0)

    out = os.path.join(workdir, "source_normalized.wav")
    wavfile.write(out, sr, (data * 32767).astype(np.int16))
    print(
        f"[normalize] 源音频 peak={peak:.3f} 削波采样={stats['clip_ratio']*100:.3f}% "
        f"-> 归一化到 {NORMALIZE_TARGET}（增益 {gain:.3f}）"
    )
    return out, stats


# ============ 翻唱结果缓存 ============
# 同一首歌 + 同一音色 -> 直接复用之前的结果，省掉 30~60 秒的分离/转换/混音
CACHE_DIR = os.path.join(BASE_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_key(song_name: str, artist: str, model: str, f0up_key: int) -> str:
    """构造缓存键：歌曲名 + 歌手 + 音色 + 变调"""
    import hashlib
    raw_key = f"{song_name}|{artist}|{model}|{int(f0up_key)}"
    h = hashlib.md5(raw_key.encode("utf-8")).hexdigest()[:12]
    safe = "".join(c for c in f"{song_name}-{artist}" if c not in '\/:*?"<>|')[:40]
    return f"{safe}_{h}.mp3"


def _cache_get(song_name: str, artist: str, model: str, f0up_key: int):
    """命中返回路径，未命中返回 None"""
    path = os.path.join(CACHE_DIR, _cache_key(song_name, artist, model, f0up_key))
    if os.path.exists(path) and os.path.getsize(path) > 50000:
        return path
    return None


def _cache_put(out_mp3: str, song_name: str, artist: str, model: str, f0up_key: int):
    """把翻唱结果复制进缓存"""
    try:
        dst = os.path.join(CACHE_DIR, _cache_key(song_name, artist, model, f0up_key))
        if not os.path.exists(dst):
            shutil.copy2(out_mp3, dst)
            print(f"[cache] 已缓存: {os.path.basename(dst)}")
    except Exception as e:
        print(f"[cache] 写入失败: {e}")


def _cache_stats():
    """统计缓存大小和数量"""
    try:
        files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".mp3")]
        total = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in files)
        return len(files), total
    except Exception:
        return 0, 0


def _measure_rms(wav_path):
    """测 wav 的 RMS 响度（浮点）"""
    import numpy as np
    from scipy.io import wavfile
    sr, data = wavfile.read(wav_path)
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float32) / 2147483648.0
    if data.ndim > 1:
        data = data.mean(axis=1)
    if not data.size:
        return 0.0
    return float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))


def _mix(vocal_wav, ins_wav, out_audio, workdir):
    """高质量混音：自适应电平平衡 -> 峰值校准 -1dBFS -> 320k 输出"""
    mix_wav = os.path.join(workdir, "mix_f32.wav")

    # 1. 自适应电平平衡
    #    RVC 转换后的人声比原始人声衰减 20~35%，同时伴奏轨里残留原唱，
    #    所以以「人声 RMS ≈ 伴奏 x2 (+6dB)」为目标反推人声增益，
    #    让换声明显盖过残留原唱，而不是靠固定数字碰运气。
    rms_v = _measure_rms(vocal_wav)
    rms_i = _measure_rms(ins_wav)
    TARGET_RATIO = 2.0

    if rms_v > 1e-6 and rms_i > 1e-6:
        gain_v = min((rms_i * TARGET_RATIO) / rms_v, 6.0)
    else:
        gain_v = 2.0
    gain_i = 0.55

    print(f"[mix] rms_v={rms_v:.4f} rms_i={rms_i:.4f} -> gain_v={gain_v:.2f} gain_i={gain_i:.2f}")

    cmd = [
        FFMPEG, "-y",
        "-i", vocal_wav,
        "-i", ins_wav,
        "-filter_complex",
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={gain_v:.4f}[v];"
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={gain_i:.4f}[i];"
        "[v][i]amix=inputs=2:duration=longest[a]",
        "-map", "[a]", "-c:a", "pcm_f32le", mix_wav,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    # 2. 峰值校准到 -1dBFS（线性增益，不压缩动态）
    peak = _measure_peak(mix_wav)
    TARGET_PEAK = 0.89
    gain = (TARGET_PEAK / peak) if peak > 0 else 1.0
    gain = min(gain, 4.0)

    # 3. 高质量编码输出
    cmd2 = [
        FFMPEG, "-y", "-i", mix_wav,
        "-af", f"volume={gain:.6f}",
        "-ar", "48000", "-b:a", "320k", out_audio,
    ]
    subprocess.run(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    try:
        os.remove(mix_wav)
    except OSError:
        pass


def do_cover(src_audio, model="kikiV1.pth", f0up_key=0, index_rate=0.0, sep_model="HP2_all_vocals"):
    """核心翻唱流程，返回输出 mp3 路径"""
    job = str(uuid.uuid4())[:8]
    jobdir = os.path.join(WORK_DIR, job)
    indir = os.path.join(jobdir, "in")
    vdir = os.path.join(jobdir, "vocals")
    idir = os.path.join(jobdir, "ins")
    for d in (indir, vdir, idir):
        os.makedirs(d, exist_ok=True)

    # 0. 加密格式先解密
    src_audio = _ensure_playable(src_audio)

    # 1. 统一格式（只放一个文件，UVR5 按目录扫描）
    wav_in = os.path.join(indir, "song.wav")
    _ffmpeg_to_wav(src_audio, wav_in)

    # 1.5 源音频峰值归一化（缓解破音）
    #     实测：部分翻唱版音源 peak≈0.99~1.03，已达削波，
    #     经分离+RVC 后会放大成破音/金属噪声。
    wav_in_norm, src_stats = _normalize_source(wav_in, jobdir)
    if wav_in_norm != wav_in:
        # 用归一化后的替换原文件（UVR5 按目录扫描，必须同名覆盖）
        shutil.copy2(wav_in_norm, wav_in)

    # 2. 人声分离
    from infer.modules.uvr5.modules import uvr
    for _ in uvr(sep_model, indir, vdir, [], idir, 10, "wav"):
        pass
    vocal_src = _first_wav(vdir)
    ins_src = _first_wav(idir)

    # 2.5 人声有效性检查（防止拿钢琴版/伴奏版去转换，产出垃圾）
    #     实测：钢琴版、纯音乐版分离后"人声"几乎是静音，转换出来必然难听。
    vocal_ratio = _vocal_energy_ratio(vocal_src, ins_src)
    print(f"[vocal-check] 人声/伴奏 能量比 = {vocal_ratio:.4f}")
    if vocal_ratio < MIN_VOCAL_RATIO:
        raise RuntimeError(
            f"这首可能是纯音乐/钢琴版/伴奏版（人声能量仅占 {vocal_ratio*100:.1f}%），"
            f"没有人声可唱。换个版本吧～"
        )

    # 2.7 音高自动检测与升调（2026-10-26 新增）
    #     男声歌基频 ~120-165Hz，低于女声模型舒适音域（~190Hz+），
    #     直接转会导致音色崩坏/气声放大/跑调。这里自动升到 ~240Hz。
    #     用户手动指定的 f0up_key != 0 时以手动为准。
    actual_key, pitch_note = _auto_pitch_shift(vocal_src, f0up_key)
    print(f"[auto-pitch] {pitch_note}")

    # 3. RVC 音色转换
    vc = get_vc()
    if _engine["loaded_model"] != model:
        vc.get_vc(model)
        _engine["loaded_model"] = model
    _, wav_opt = vc.vc_single(
        0, vocal_src, int(actual_key), None, "rmvpe", None, None,
        float(index_rate), 3, 0, 1.0, 0.33,
    )
    import numpy as np
    from scipy.io import wavfile
    conv_wav = os.path.join(jobdir, "converted.wav")
    audio = wav_opt[1]
    if isinstance(audio, np.ndarray):
        if audio.dtype != np.int16:
            audio = audio.astype(np.float32)
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            # 峰值超限时整体归一化（避免硬截断造成的破音）
            if peak > 0.98 and peak > 0:
                audio = audio / peak * 0.95
            audio = (audio * 32767).astype(np.int16)
    wavfile.write(conv_wav, int(wav_opt[0]), audio)

    # 4. 混音输出
    out_name = f"cover_{job}.mp3"
    out_mp3 = os.path.join(OUT_DIR, out_name)
    _mix(conv_wav, ins_src, out_mp3, jobdir)

    # 清理临时目录（保留 output）
    shutil.rmtree(jobdir, ignore_errors=True)
    return out_mp3


@app.get("/health")
def health():
    return {"ok": True, "models": list_models(), "rvc_root": RVC_ROOT}


@app.get("/models")
def models():
    return {"models": list_models()}


@app.get("/cache/stats")
def cache_stats():
    """查看缓存占用"""
    cnt, total = _cache_stats()
    return {
        "ok": True,
        "count": cnt,
        "size_mb": round(total / 1024 / 1024, 2),
        "dir": CACHE_DIR,
    }


@app.get("/cache/list")
def cache_list(limit: int = 200):
    """列出已缓存的歌（本地曲库）。

    观众点同一首歌时直接命中缓存，不用再走一遍 RVC（27~80 秒 → 0 秒）。

    返回每首：歌名 / 歌手 / 大小 / 缓存时间 / 最近访问时间
    """
    import time as _t
    items = []
    try:
        for f in os.listdir(CACHE_DIR):
            if not f.endswith(".mp3"):
                continue
            p = os.path.join(CACHE_DIR, f)
            st = os.stat(p)
            # 文件名格式：歌名-歌手_<12位hash>.mp3
            stem = f[:-4]
            name_part = stem.rsplit("_", 1)[0]
            items.append({
                "file": f,
                "title": name_part,
                "size_mb": round(st.st_size / 1024 / 1024, 2),
                "cached_at": _t.strftime("%Y-%m-%d %H:%M", _t.localtime(st.st_ctime)),
                "last_used": _t.strftime("%Y-%m-%d %H:%M", _t.localtime(st.st_atime)),
            })
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

    # 最近用过的排前面（这就是「观众爱点哪些歌」的答案）
    items.sort(key=lambda x: x["last_used"], reverse=True)
    return {"ok": True, "count": len(items), "items": items[:limit]}


def _cache_evict_if_needed(max_mb: int = None):
    """缓存超容量时，按「最久没用过」淘汰。

    ⚠️ 为什么需要（2026-10-27）：
       曲库会一直变大。一首约 5MB，直播一个月点 1000 首就是 5GB，
       最终会把磁盘吃满，然后翻唱和下载全部失败。
       所以必须有上限，而且按 LRU 淘汰 —— 最近被点过的歌留着，
       三个月没人点的老歌清掉。

    @param max_mb  上限（MB），None 时读配置 CACHE_MAX_MB，默认 3000MB
    """
    if max_mb is None:
        try:
            max_mb = int(os.environ.get("CACHE_MAX_MB", "3000"))
        except Exception:
            max_mb = 3000
    try:
        files = []
        total = 0
        for f in os.listdir(CACHE_DIR):
            if not f.endswith(".mp3"):
                continue
            p = os.path.join(CACHE_DIR, f)
            st = os.stat(p)
            files.append((st.st_atime, st.st_size, p))
            total += st.st_size
        limit_bytes = max_mb * 1024 * 1024
        if total <= limit_bytes:
            return 0
        # 最久未使用优先删
        files.sort(key=lambda x: x[0])
        removed = 0
        for _atime, size, p in files:
            if total <= limit_bytes:
                break
            try:
                os.remove(p)
                total -= size
                removed += 1
            except Exception:
                pass
        if removed:
            print(f"[cache] 超出 {max_mb}MB，按最久未使用淘汰了 {removed} 首，"
                  f"现在 {total/1024/1024:.0f}MB")
        return removed
    except Exception as e:
        print(f"[cache] 淘汰失败: {e}")
        return 0


@app.get("/cache/evict")
def cache_evict(max_mb: int = None):
    """手动触发容量清理（也可由 cfg.cache_max_mb 自动触发）"""
    n = _cache_evict_if_needed(max_mb)
    cnt, total = _cache_stats()
    return {"ok": True, "removed": n, "count": cnt,
            "size_mb": round(total / 1024 / 1024, 2)}


@app.post("/cache/warm")
async def cache_warm(keywords: str = Form(...), model: str = Form(""),
                     half: bool = Form(True)):
    """预热：把常点的歌提前翻唱好放进曲库。

    直播前跑一遍常用的十几首，开播后观众点这些歌就是**秒播**，
    而且不占 GPU（不用现场等 27~80 秒）。

    @param keywords  逗号或换行分隔的歌名，例如 "晴天,十年,小情歌"
    """
    import asyncio
    # 模型由主服务传入（config.sing_model）；预热时没传就用说话音色兜底，
    # 保证不会因为空字符串挑到错误的缓存 key
    m = model or DEFAULT_SPEAK_MODEL
    kws = [k.strip() for k in re.split(r"[,，\n]+", keywords) if k.strip()]
    results = []
    for kw in kws[:30]:      # 最多 30 首，别一次跑太久
        try:
            songs = await asyncio.to_thread(netease_search, kw, 20)
            song = _pick_song(songs, kw, prefer_gender=True) if songs else None
            if not song:
                results.append({"kw": kw, "ok": False, "msg": "没搜到"})
                continue
            cache_name = song["name"] + ("|half" if half else "")
            hit = _cache_get(cache_name, song["artist"], m, 0)
            if hit:
                # 命中就更新访问时间，让 LRU 知道它「最近被用过」
                try:
                    os.utime(hit, None)
                except Exception:
                    pass
                results.append({"kw": kw, "ok": True, "cached": True,
                                "title": f"{song['name']} - {song['artist']}"})
                continue
            results.append({"kw": kw, "ok": True, "cached": False,
                            "title": f"{song['name']} - {song['artist']}",
                            "msg": "需要现场翻唱才会入缓存"})
        except Exception as e:
            results.append({"kw": kw, "ok": False, "msg": str(e)[:120]})
    return {"ok": True, "model": m, "results": results}


@app.post("/cache/clear")
def cache_clear():
    """清空缓存"""
    try:
        n = 0
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".mp3"):
                os.remove(os.path.join(CACHE_DIR, f))
                n += 1
        return {"ok": True, "removed": n}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.post("/cover")
async def cover(
    file: UploadFile = File(...),
    model: str = Form("kikiV1.pth"),
    f0up_key: int = Form(0),
    index_rate: float = Form(0.0),
):
    if not _acquire_busy("/cover"):
        return {"ok": False, "error": "服务忙，已有一个翻唱任务在进行"}
    try:
        ext = os.path.splitext(file.filename or "song.mp3")[1] or ".mp3"
        tmp_in = os.path.join(WORK_DIR, f"upload_{uuid.uuid4().hex[:8]}{ext}")
        with open(tmp_in, "wb") as f:
            f.write(await file.read())
        t0 = time.time()
        out = do_cover(tmp_in, model=model, f0up_key=f0up_key, index_rate=index_rate)
        try:
            os.remove(tmp_in)
        except OSError:
            pass
        return FileResponse(
            out, media_type="audio/mpeg", filename=os.path.basename(out),
            headers={"X-Cost-Seconds": f"{time.time()-t0:.1f}"},
        )
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}
    finally:
        _release("/cover")



# ============ 网易云音乐：扫码登录 / 搜索 / 下载 ============
import pickle

try:
    from curl_cffi import requests as requests
except ImportError:
    import requests

COOKIE_FILE = os.path.join(BASE_DIR, "netease_cookie.pkl")
COOKIE_JSON = os.path.join(BASE_DIR, "netease_cookie.json")
QR_FILE = os.path.join(BASE_DIR, "login_qr.png")
SONGS_DIR = os.path.join(BASE_DIR, "songs")
os.makedirs(SONGS_DIR, exist_ok=True)

_NET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://music.163.com",
    "Referer": "https://music.163.com/",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}


def _net_session():
    try:
        s = requests.Session(impersonate="chrome")
    except TypeError:
        s = requests.Session()
    s.headers.update(_NET_HEADERS)
    # 优先读登录器保存的 JSON cookie
    if os.path.exists(COOKIE_JSON):
        try:
            with open(COOKIE_JSON, "r", encoding="utf-8") as f:
                s.cookies.update(json.load(f))
            return s
        except Exception:
            pass
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "rb") as f:
                s.cookies.update(pickle.load(f))
        except Exception:
            pass
    return s


def _save_cookies(sess):
    try:
        with open(COOKIE_FILE, "wb") as f:
            pickle.dump(sess.cookies, f)
    except Exception as e:
        print("[netease] 保存cookie失败", e)


def netease_logged_in() -> bool:
    if not (os.path.exists(COOKIE_JSON) or os.path.exists(COOKIE_FILE)):
        return False
    s = _net_session()
    try:
        r = s.get("https://music.163.com/api/nuser/account/get", timeout=15)
        j = r.json()
        return bool(j.get("account"))
    except Exception:
        return False


def netease_search(keyword: str, limit: int = 8):
    s = _net_session()
    r = s.get(
        "https://music.163.com/api/search/get/web",
        params={"s": keyword, "type": 1, "limit": limit, "offset": 0},
        timeout=20,
    )
    j = r.json()
    songs = ((j.get("result") or {}).get("songs")) or []
    out = []
    for it in songs:
        out.append(
            {
                "id": it.get("id"),
                "name": it.get("name"),
                "artist": "/".join(a.get("name", "") for a in (it.get("artists") or [])),
                "album": (it.get("album") or {}).get("name", ""),
                "duration": it.get("duration", 0),
            }
        )
    return out


def netease_song_url(song_id: int, br: int = 999000):
    """取歌曲直链，br: 999000=无损, 320000=高品"""
    s = _net_session()
    r = s.get(
        "https://music.163.com/api/song/enhance/player/url",
        params={"ids": f"[{song_id}]", "br": br},
        timeout=20,
    )
    j = r.json()
    data = j.get("data") or []
    if not data:
        return None
    d = data[0]
    return {"url": d.get("url"), "br": d.get("br"), "type": d.get("type"), "size": d.get("size")}


def netease_download(song: dict, deadline_sec: float = 60.0) -> str:
    """下载歌曲到曲库，返回文件路径。

    ⚠️ 必须有「总时长」上限，不能只靠 requests 的 timeout（2026-10-28 线上事故）：
       `requests.get(timeout=180)` 是 **socket 空闲超时**，不是总时长。
       只要服务器每隔一会儿吐一点数据，这个连接就能无限拖下去。
       实测：一首 139 秒的歌「等我回家（粤语女声）」下载卡了 320 秒，
       期间没有任何日志输出。后果不只是慢 —— 整条队列被堵住，
       后面排队的观众一起等，直播间看着像"卡死"。

       所以这里按**墙钟时间**掐：每写一个 chunk 检查是否超时，
       超了就放弃、删半截文件、抛异常。
       主服务收到失败会去安抚观众，而不是让整个队列干等。
    """
    sid = song["id"]
    info = netease_song_url(sid, 999000) or {}
    if not info.get("url"):
        info = netease_song_url(sid, 320000) or {}
    if not info.get("url"):
        raise RuntimeError("拿不到播放地址（可能是会员/版权限制）")

    ext = info.get("type") or "mp3"
    safe = "".join(c for c in f"{song['name']}-{song['artist']}" if c not in '\/:*?"<>|').strip()
    path = os.path.join(SONGS_DIR, f"{safe}.{ext}")
    if os.path.exists(path) and os.path.getsize(path) > 50000:
        return path

    t_start = time.time()
    # connect/read 超时给短一点：真有响应就能覆盖住，没响应就快速失败
    r = requests.get(info["url"], headers=_NET_HEADERS,
                     timeout=(10, 20), stream=True)
    r.raise_for_status()
    try:
        with open(path, "wb") as f:
            for chunk in r.iter_content(65536):
                if not chunk:
                    continue
                f.write(chunk)
                elapsed = time.time() - t_start
                if elapsed > deadline_sec:
                    raise RuntimeError(
                        f"下载超时（{elapsed:.0f}s > {deadline_sec:.0f}s），放弃这首")
    except Exception:
        # 半截文件必须删掉，否则下次走到「已存在且>50000」会当成完整文件用
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        raise
    finally:
        try:
            r.close()
        except Exception:
            pass

    if os.path.getsize(path) < 30000:
        os.remove(path)
        raise RuntimeError("下载文件过小，可能失败")
    return path


def netease_login_qrcode():
    """生成扫码登录二维码，返回 unikey"""
    s = _net_session()
    # 预热：先访问首页获取基础 cookie（否则会被风控 8821）
    try:
        s.get("https://music.163.com/", timeout=20)
    except Exception:
        pass
    r = s.get(
        "https://music.163.com/api/login/qrcode/unikey",
        params={"type": 1},
        timeout=20,
    )
    j = r.json()
    if j.get("code") != 200:
        raise RuntimeError(f"获取二维码失败: {j}")
    key = j["unikey"]
    url = f"https://music.163.com/login?codekey={key}"
    import qrcode
    img = qrcode.make(url)
    img.save(QR_FILE)
    return {"unikey": key, "url": url, "qr_file": QR_FILE}


def netease_login_check(key: str):
    """轮询扫码状态：800过期 / 801等待 / 802已扫待确认 / 803成功"""
    s = _net_session()
    r = s.get(
        "https://music.163.com/api/login/qrcode/client/login",
        params={"key": key, "type": 1},
        timeout=20,
    )
    j = r.json()
    code = j.get("code")
    if code == 803:
        _save_cookies(s)
        return {"code": 803, "ok": True, "msg": "登录成功"}
    return {"code": code, "ok": False, "msg": {800: "二维码已过期", 801: "等待扫码", 802: "已扫码，请在手机上确认"}.get(code, str(j))}


# ============ RVC 说话（TTS -> RVC） ============
import base64
import json
import urllib.request


def _load_mimo_key() -> str:
    """读取 MiMo TTS 的 Key —— ⚠️ 绝不写进源码（仓库里只留这段读取逻辑）。

    优先级：
      1. 环境变量 MIMO_KEY
      2. cover_service/local_secrets.json 里的 mimo_key（本地文件，已被 .gitignore 忽略）
    两者都没有时返回空串，/speak 会给出明确报错而不是莫名其妙地出声失败。
    """
    k = (os.environ.get("MIMO_KEY") or "").strip()
    if k:
        return k
    for name in ("local_secrets.json", "secrets.json"):
        p = os.path.join(BASE_DIR, name)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                return str(json.load(f).get("mimo_key") or "").strip()
        except Exception as e:
            print(f"[cover] 读取 {name} 失败（忽略）: {e}")
    return ""


MIMO_KEY = _load_mimo_key()
MIMO_URL = "https://api.xiaomimimo.com/v1/chat/completions"
DEFAULT_SPEAK_MODEL = "youzhanv2-xi.pth"
DEFAULT_TTS_VOICE = "冰糖"


def _fail(msg: str, status: int = 502):
    """模块级错误响应（原先是 /sing 里的嵌套函数，/speak 访问不到）。"""
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": msg[:300]},
    )


def _mimo_tts(text: str, voice: str = DEFAULT_TTS_VOICE) -> bytes:
    """调用 MiMo TTS 生成 wav 字节

    注意：MiMo TTS 要求 messages 里同时有 user（语气指令）和 assistant（要读的文本），
    只传 assistant 会返回 400。
    """
    if not MIMO_KEY:
        raise RuntimeError(
            "没有配置 MiMo TTS 的 Key：请设置环境变量 MIMO_KEY，"
            "或把 {\"mimo_key\": \"...\"} 写进 cover_service/local_secrets.json"
            "（该文件已被 .gitignore 忽略，不会进仓库）")
    body = json.dumps(
        {
            "model": "mimo-v2.5-tts",
            "messages": [
                {
                    "role": "user",
                    "content": "请用标准普通话朗读，不要任何方言口音，语气自然活泼",
                },
                {"role": "assistant", "content": text},
            ],
            "audio": {"format": "wav", "voice": voice},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        MIMO_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {MIMO_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    if "choices" not in data:
        raise RuntimeError(f"TTS 返回异常: {str(data)[:200]}")
    b64 = data["choices"][0]["message"]["audio"]["data"]
    return base64.b64decode(b64)


def _rvc_convert(input_wav: str, model: str, f0up_key: int, output_wav: str):
    """纯 RVC 转换（不做人声分离）"""
    vc = get_vc()
    if _engine["loaded_model"] != model:
        vc.get_vc(model)
        _engine["loaded_model"] = model
    _, wav_opt = vc.vc_single(
        0, input_wav, int(f0up_key), None, "rmvpe", None, None, 0.0, 3, 0, 1.0, 0.33,
    )
    import numpy as np
    from scipy.io import wavfile
    audio = wav_opt[1]
    if isinstance(audio, np.ndarray) and audio.dtype != np.int16:
        audio = audio.astype(np.float32)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0.98 and peak > 0:
            audio = audio / peak * 0.95
        audio = (audio * 32767).astype(np.int16)
    wavfile.write(output_wav, int(wav_opt[0]), audio)


def _do_speak(text: str, model: str, f0up_key: int, voice: str) -> str:
    """文本 -> MiMo TTS -> RVC -> mp3，返回路径"""
    job = str(uuid.uuid4())[:8]
    jobdir = os.path.join(WORK_DIR, job)
    os.makedirs(jobdir, exist_ok=True)
    try:
        raw_wav = os.path.join(jobdir, "tts.wav")
        with open(raw_wav, "wb") as f:
            f.write(_mimo_tts(text, voice))
        conv_wav = os.path.join(jobdir, "converted.wav")
        _rvc_convert(raw_wav, model, f0up_key, conv_wav)
        out_mp3 = os.path.join(OUT_DIR, f"speak_{job}.mp3")
        cmd = [
            FFMPEG, "-y", "-i", conv_wav,
            "-af", "volume=1.0",
            "-ar", "48000", "-b:a", "192k", out_mp3,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return out_mp3
    finally:
        shutil.rmtree(jobdir, ignore_errors=True)


@app.post("/speak")
async def speak(
    text: str = Form(...),
    model: str = Form(DEFAULT_SPEAK_MODEL),
    f0up_key: int = Form(0),
    voice: str = Form(DEFAULT_TTS_VOICE),
):
    """文字 -> RVC 音色语音"""
    import asyncio
    if not _acquire_busy("/speak"):
        return {"ok": False, "error": "服务忙"}
    try:
        t0 = time.time()
        out = await asyncio.to_thread(_do_speak, text, model, f0up_key, voice)
        return FileResponse(
            out, media_type="audio/mpeg", filename=os.path.basename(out),
            headers={"X-Cost-Seconds": f"{time.time()-t0:.1f}"},
        )
    except Exception as e:
        traceback.print_exc()
        return _fail(str(e))
    finally:
        _release("/speak")


@app.post("/cover_path")
def cover_path(
    path: str = Form(...),
    model: str = Form("kikiV1.pth"),
    f0up_key: int = Form(0),
    index_rate: float = Form(0.0),
):
    """给服务器本地音频路径翻唱"""
    if not _acquire_busy("/cover_path"):
        return {"ok": False, "error": "服务忙"}
    try:
        if not os.path.exists(path):
            return {"ok": False, "error": f"文件不存在: {path}"}
        t0 = time.time()
        out = do_cover(path, model=model, f0up_key=f0up_key, index_rate=index_rate)
        return FileResponse(
            out, media_type="audio/mpeg", filename=os.path.basename(out),
            headers={"X-Cost-Seconds": f"{time.time()-t0:.1f}"},
        )
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}
    finally:
        _release("/cover_path")



# ============ 网易云 & 一条龙接口 ============

@app.get("/login/status")
def login_status():
    return {"logged_in": netease_logged_in()}


@app.post("/login/qrcode")
def login_qrcode():
    try:
        return {"ok": True, **netease_login_qrcode()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.get("/login/check")
def login_check(key: str):
    try:
        return netease_login_check(key)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.get("/login/qr_image")
def login_qr_image():
    if os.path.exists(QR_FILE):
        return FileResponse(QR_FILE, media_type="image/png")
    return {"ok": False, "error": "请先调用 /login/qrcode"}


@app.get("/search")
def search(kw: str, limit: int = 8):
    try:
        return {"ok": True, "songs": netease_search(kw, limit)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.get("/pick")
def pick_preview(kw: str):
    """只做「搜索 + 选歌」，不下载不翻唱，立刻返回。

    2026-10-26 新增，配合用户的体验需求：
        「在 bot 去搜索到歌时，优先选择女声唱的，
         且如果原曲是男声，就说一句我找到了，但我只能唱女声版的」

    为什么要单独开这个接口：
        /sing 是同步返回音频的，服务端要几十秒才回包，
        期间插件拿不到任何信息，没法及时说「我找到女声版了」。
        所以分成两步：先 /pick 探路（几百毫秒）→ 插件先说一句
        → 再 /sing 真正唱。
    """
    import asyncio
    try:
        t0 = time.time()

        async def _go():
            songs = await asyncio.to_thread(netease_search, kw, 20)
            if not songs:
                return None, songs
            return _pick_song(songs, kw), songs

        song, songs = asyncio.run(_go())
        if not song:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": f"没搜到「{kw}」",
                         "keyword": kw, "candidates": len(songs)},
            )

        nm = (song["name"] or "").replace(" ", "").lower()
        g_score, g_note = _gender_hint(nm, (song["artist"] or "").lower())
        version = "female" if g_score > 0 else ("male" if g_score < 0 else "unknown")

        return {
            "ok": True,
            "keyword": kw,
            "name": song["name"],
            "artist": song["artist"],
            "album": song.get("album", ""),
            "duration": song.get("duration", 0),
            "version": version,
            "version_note": g_note,
            "candidates": len(songs),
            "cost_seconds": round(time.time() - t0, 2),
        }
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)[:200]})
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ---------------------------------------------------------------- 选歌排序
# 版本修饰词：这些后缀意味着"不是原唱/不是正常演唱版本"。
# 只做**扣分**，绝不硬排除——因为像《晴天》《稻香》在网易云搜不到原唱，
# 结果全带修饰词，硬排除会导致一首也选不出来。
#
# 两类分开记账：
#   致命类（没人声或严重变调）：钢琴版/伴奏/纯音乐/演奏/降调/0.9x  → 重罚
#   降级类（仍有人声但非原唱）：DJ/Live/Remix/女声版/深情版/翻自 → 轻罚
DEAD_VERSION_PATTERNS = (
    "钢琴版", "伴奏", "纯音乐", "演奏版", "演奏", "instrumental",
    "karaoke", "卡拉ok", "无人声", "降调版", "升调版", "0.8x", "0.9x",
)
DEGRADED_VERSION_PATTERNS = (
    "live", "现场", "remix", "dj版", "男声版", "深情版", "烟嗓版",
    "翻自", "cover", "抖音", "热搜版", "史诗版", "史詩版", "治愈版", "童声",
    "r&b版", "粤语版", "升key", "0.89x",
)

# ---- 女声偏好（2026-10-26 用户需求）----
# 我们的 RVC 模型都是女声。用女声模型唱男声歌（尤其低音歌如《安和桥》97Hz）
# 会失真。实测发现：网易云上「女生版」往往不升调、靠音色本身（白允y 版 100Hz，
# 与原唱 97Hz 几乎一致），听着自然得多。
#
# 但网易云 API **不提供歌手性别**（artist 接口里没有该字段，2026-10-26 已确认），
# 只能靠歌名里的关键词判断。
FEMALE_HINT_PATTERNS = (
    "女生版", "女版", "女声", "女聲", "治愈女版", "温柔女声", "少女版",
    "女声翻唱", "女生翻唱", "女歌", "女声cover",
)
MALE_HINT_PATTERNS = (
    "男声版", "男版", "男声", "男聲", "烟嗓", "撕裂版", "低沉男声",
)
# 明确是女声的加分（覆盖掉 DEGRADED 里可能存在的扣分，并额外奖励）
FEMALE_BONUS = 45
# 明确是男声的扣分（我们的模型唱不了）
MALE_PENALTY = -60


def _gender_hint(name_lower: str, artist_lower: str = "") -> tuple[int, str]:
    """按歌名/歌手名里的性别线索给分。返回 (分数增减, 说明)。

    ⚠️ 全是启发式。网易云没有歌手性别字段，只能靠命名习惯猜。
    """
    text = f"{name_lower} {artist_lower}"
    for pat in FEMALE_HINT_PATTERNS:
        if pat in text:
            return FEMALE_BONUS, f"女声版本({pat})"
    for pat in MALE_HINT_PATTERNS:
        if pat in text:
            return MALE_PENALTY, f"男声版本({pat})"
    return 0, ""


def _version_penalty(name_lower: str) -> tuple[int, str]:
    """按版本修饰词算扣分，返回 (分数, 命中说明)。

    只有 name 里带修饰词才扣分。修饰词一律按子串匹配。
    """
    for pat in DEAD_VERSION_PATTERNS:
        if pat in name_lower:
            return -120, f"无人声/变调版本({pat})"
    for pat in DEGRADED_VERSION_PATTERNS:
        if pat in name_lower:
            return -25, f"非原唱版本({pat})"
    return 0, ""


def _pick_song(songs, keyword, prefer_gender=True):
    """挑选最匹配的一首（分档排序，宁可降级也不选不出来）。

    打分顺序（高到低）：
      区间A 名称精确匹配 + 无修饰词   ← 最理想（原唱）
      区间B 名称精确匹配 + 有修饰词   ← 次优（如"晴天|梦里啥都有"）
      区间C 名称包含关键词 + 无修饰词
      区间D 名称包含关键词 + 有修饰词
      区间E 反向包含（关键词包含歌名）

    修饰词只扣分不排除，保证《晴天》这类"全带后缀"的歌仍能选到最好的那个。

    @param prefer_gender 是否启用「女声优先」偏好。
        ⚠️ 这个开关是有原因的（2026-10-27）：
          · 翻唱（/sing）要走 RVC，音色模型是女声，男声歌会失真，
            所以必须偏向女声版 → prefer_gender=True
          · 放原曲（/raw）**不经过任何 RVC**，观众就是想听原唱，
            这时候偏向"女生版"反而是错的 → prefer_gender=False
        之前两条链路共用同一套打分，导致「放 十年」可能放出个
        女声翻唱版，不是观众要的原唱。
    """
    kw = keyword.replace(" ", "").lower()
    if not kw:
        return None

    scored = []
    for s_ in songs:
        raw_name = (s_["name"] or "").strip()
        name = raw_name.replace(" ", "").lower()
        artist = (s_["artist"] or "").lower()
        if not name:
            continue

        # ---- 基础分：name 与关键词的匹配程度 ----
        if name == kw:
            base = 100
            tier = "原唱级"
        elif kw in name:
            base = 60
            tier = "包含级"
        elif name in kw and len(name) >= 3:
            base = 40
            tier = "反向包含级"
        else:
            base = 0
            tier = ""

        # 逐词匹配（关键词是多词时）
        for part in kw.split():
            if len(part) >= 2:
                if part in name:
                    base += 10
                if part in artist:
                    base += 5

        # 不沾边的直接跳过（分数必须 > 0）
        if base <= 0:
            continue

        # ---- 版本扣分 ----
        penalty, reason = _version_penalty(name)

        # ---- 性别偏好（只在翻唱时启用）----
        # 放原曲时要的是原唱，绝不能因为"女声"加分就把原唱挤掉。
        if prefer_gender:
            gender_score, gender_note = _gender_hint(name, artist)
        else:
            gender_score, gender_note = 0, ""

        # ---- 时长：过短可能是试听/片段 ----
        dur = s_.get("duration") or 0
        if dur < 60000:
            base -= 30

        score = base + penalty + gender_score
        scored.append(
            {
                "song": s_,
                "score": score,
                "tier": tier,
                "penalty": penalty,
                "reason": reason,
                "gender_note": gender_note,
            }
        )

    if not scored:
        return None

    scored.sort(key=lambda r: r["score"], reverse=True)
    best = scored[0]
    s_ = best["song"]
    note = best.get("gender_note") or ""
    if best["penalty"]:
        print(
            f"[pick] 选中《{s_['name']}》- {s_['artist']} "
            f"（{best['tier']}，扣分 {best['penalty']}：{best['reason']}）"
            + (f" 性别分: {note}" if note else "")
        )
    else:
        print(
            f"[pick] 选中《{s_['name']}》- {s_['artist']}（{best['tier']}，无修饰词）"
            + (f" 性别分: {note}" if note else "")
        )
    return s_


@app.post("/sing")
async def sing_song(
    keyword: str = Form(...),
    model: str = Form(DEFAULT_SPEAK_MODEL),
    f0up_key: int = Form(0),
    half: bool = Form(False),
    fade: float = Form(1.5),
):
    """一条龙：搜索 -> 下载 -> RVC 翻唱 -> 返回 mp3

    注意：出错时返回 HTTP 502 + JSON（而不是 200 + JSON）。
    否则调用方（QQ 插件）会把错误 JSON 当成 mp3 交给 QQ，
    ffmpeg 探测失败后报 retcode=1200 "Failed to open input"。
    """
    import asyncio

    def _fail(msg: str, status: int = 502):
        return JSONResponse(
            status_code=status,
            content={"ok": False, "error": msg[:300], "keyword": keyword},
        )

    if not _acquire_busy("/sing"):
        return _fail("服务忙，正在处理另一首", status=503)
    try:
        t0 = time.time()
        # 1. 搜索
        #    2026-10-26：limit 从 8 提到 20 —— 我们的模型是女声，
        #    需要更多候选才能找到「女生版/女版」。实测搜「安和桥」时
        #    女声版（白允y）排在第 11 位，limit=8 根本拿不到。
        songs = await asyncio.to_thread(netease_search, keyword, 20)
        if not songs:
            return _fail(f"没搜到「{keyword}」", status=404)
        song = _pick_song(songs, keyword)
        if not song:
            return _fail(f"搜到相似歌曲但不是《{keyword}》，换个歌名试试", status=404)

        # 版本说明：告诉调用方选中的是不是女声版，好让机器人跟群友交代。
        # 2026-10-26 用户需求：「如果原曲是男声，就说一句我找到了，
        # 但我只能唱女声版的，委屈你一下」这类话。
        _nm = (song["name"] or "").replace(" ", "").lower()
        _gen_score, _gen_note = _gender_hint(_nm, (song["artist"] or "").lower())
        if _gen_score > 0:
            version_note = "female"      # 明确是女声版
        elif _gen_score < 0:
            version_note = "male"        # 明确是男声版（模型可能唱不好）
        else:
            version_note = "unknown"     # 没有性别线索

        def _hdr(extra: dict) -> dict:
            """统一构造响应头。

            ⚠️ 中文歌名的历史坑（2026-10-27 修正）：
               旧做法是 `encode('utf-8').decode('latin-1')`，指望客户端反向还原。
               但 aiohttp 发送 header 时会把这串再按 UTF-8 编一遍，
               客户端 `encode('latin-1','ignore')` 时中文就被全部丢弃，
               结果 X-Song 变成 " ()) - GYBeat" 这种残缺串。
               现改用 base64（纯 ASCII），X-Song-B64 / X-Song-Name-B64 是权威字段，
               旧的 X-Song 保留给英文歌/兼容老调用方。
            """
            import base64

            full = f"{song['name']} - {song['artist']}"
            h = {
                "X-Cost-Seconds": extra.pop("cost", "0"),
                "X-Cache": extra.pop("cache", "MISS"),
                "X-Song-B64": base64.b64encode(full.encode("utf-8")).decode("ascii"),
                "X-Song-Name-B64": base64.b64encode(song["name"].encode("utf-8")).decode("ascii"),
                "X-Song-Artist-B64": base64.b64encode(song["artist"].encode("utf-8")).decode("ascii"),
                "X-Version-Note": version_note,
            }
            # ⚠️ 不再输出旧的 X-Song / X-Song-Name / X-Song-Artist：
            #    h11 会校验 header 值，中文经过 ascii('ignore') 过滤后会变成
            #    " ()) - GYBeat" 这类以空格开头的非法值，直接 500 崩掉连接。
            h.update(extra)
            return h
        # 2. 先查缓存：同一首歌 + 同一音色 + 同一 half 设置直接复用
        cache_name = song["name"] + ("|half" if half else "")
        cached = _cache_get(cache_name, song["artist"], model, f0up_key)
        if cached:
            cost = time.time() - t0
            print(f"[cache] 命中: {song['name']} - {song['artist']} half={half} ({cost:.2f}s)")
            return FileResponse(
                cached,
                media_type="audio/mpeg",
                filename=os.path.basename(cached),
                headers=_hdr({"cost": f"{cost:.1f}", "cache": "HIT", "half": "1" if half else "0"}),
            )

        # 3. 下载
        path = await asyncio.to_thread(netease_download, song)

        # 3.5 「只唱前半段」：先裁源音频再翻唱
        #     好处：RVC 只需要处理一半的长度 → 翻唱时间也砍半。
        #     （先翻唱再裁是浪费算力，这一步要放对位置）
        if half:
            trimmed = os.path.join(WORK_DIR, f"half_{uuid.uuid4().hex[:8]}.mp3")
            try:
                keep = await asyncio.to_thread(_trim_audio, path, trimmed, True, float(fade))
                print(f"[half] 源音频裁到前 {keep:.0f} 秒（原全长的一半）")
                path = trimmed
            except Exception as e:
                print(f"[half] 裁切失败，退回整曲: {e}")
                trimmed = None
        else:
            trimmed = None

        # 4. 翻唱
        try:
            out = await asyncio.to_thread(do_cover, path, model, f0up_key, 0.0)
        finally:
            if trimmed:
                try:
                    os.remove(trimmed)
                except OSError:
                    pass

        # 5. 写入缓存（half 用独立的 key，避免和整曲互相覆盖）
        _cache_put(out, cache_name, song["artist"], model, f0up_key)
        # 新歌进了曲库，顺手检查容量上限（超出就按最久未使用淘汰）
        # 放在这里而不是定时任务：不额外起线程，且写入后才知道真实占用
        try:
            await asyncio.to_thread(_cache_evict_if_needed)
        except Exception:
            pass
        return FileResponse(
            out,
            media_type="audio/mpeg",
            filename=os.path.basename(out),
            headers=_hdr({"cost": f"{time.time()-t0:.1f}", "cache": "MISS",
                          "half": "1" if half else "0"}),
        )
    except Exception as e:
        traceback.print_exc()
        return _fail(str(e))
    finally:
        _release("/sing")


# ============ 原曲下载（不放翻唱，直接播原唱）============
#
# 场景：「放 晴天」= 直接播网易云原曲，不做任何 RVC 处理。
# 用途：直播暖场、切歌过渡、观众想听原版。
#
# ⚠️ 不佔用 _acquire_busy 那把锁 —— 下载和转码都不碰 GPU，
#    所以唱歌/说话进行中也能正常放原曲。

def _trim_audio(src: str, dst: str, half: bool = False, fade: float = 1.5) -> float:
    """把音频裁成「前半段」并加淡入淡出，返回时长（秒）。

    half=True 时只保留前一半（用于「整曲太长，只唱前半段」的需求）。
    用 ffmpeg 的 atrim + afade，避免二次编码损失。
    """
    # 先探测总时长
    ffprobe = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
    if not os.path.exists(ffprobe):
        ffprobe = "ffprobe"
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", src],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dur = float(out)
    except Exception:
        dur = 0.0

    args = [FFMPEG, "-y", "-i", src]
    if half and dur > 0:
        keep = dur / 2.0
        args += ["-t", f"{keep:.3f}"]
    else:
        keep = dur

    # 淡入 0.8 秒，结尾淡出 fade 秒（避免切歌爆音）
    fade_out_start = max(0.0, keep - fade)
    af = f"afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out_start:.3f}:d={fade:.3f}"
    args += ["-af", af, "-vn", "-ar", "48000", "-b:a", "192k"]
    # 输出格式跟随扩展名
    if dst.lower().endswith(".mp3"):
        args += ["-codec:a", "libmp3lame"]
    args.append(dst)

    subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return keep


@app.post("/raw")
async def raw_song(
    keyword: str = Form(...),
    half: bool = Form(True),
    fade: float = Form(1.5),
):
    """搜索 → 下载 → （可选）裁前半段 → 直接返回原曲 mp3

    @param half  只取前半段（默认 True，因为整曲往往 4~5 分钟太长）
    @param fade  淡出时长（秒），避免切歌爆音
    """
    import asyncio
    from starlette.responses import JSONResponse as _JR

    def _rfail(msg: str, status: int = 502):
        return _JR(status_code=status,
                   content={"ok": False, "error": msg[:300], "keyword": keyword})

    try:
        t0 = time.time()
        songs = await asyncio.to_thread(netease_search, keyword, 20)
        if not songs:
            return _rfail(f"没搜到「{keyword}」", status=404)
        # ⚠️ 放原曲不走 RVC，要的是**原唱**，关掉女声优先
        song = _pick_song(songs, keyword, prefer_gender=False)
        if not song:
            return _rfail(f"搜到相似歌曲但不是《{keyword}》，换个歌名试试", status=404)

        path = await asyncio.to_thread(netease_download, song)

        # 裁切输出到临时文件（原曲可能几十 MB，不缓存）
        import hashlib
        tag = hashlib.md5(f"{song['name']}|{song['artist']}|{half}|{fade}".encode()).hexdigest()[:12]
        safe = "".join(c for c in f"{song['name']}-{song['artist']}"
                       if c not in '\\/:*?"<>|').strip()
        out = os.path.join(OUT_DIR, f"raw_{safe}_{tag}.mp3")
        if not (os.path.exists(out) and os.path.getsize(out) > 10000):
            keep = await asyncio.to_thread(_trim_audio, path, out, bool(half), float(fade))
        else:
            keep = 0.0

        cost = time.time() - t0
        print(f"[raw] {song['name']} - {song['artist']}  half={half}  {cost:.1f}s -> {out}")

        # ⚠️ 中文歌名不能直接放 header。
        #    之前用 `encode('utf-8').decode('latin-1')` 转义，
        #    但 aiohttp 发 header 时又把这串按 utf-8 编了一遍，
        #    客户端 `encode('latin-1','ignore')` 时中文全被丢掉，
        #    结果 X-Song 变成 " ()) - GYBeat" 这种残缺串。
        #    改用 base64，纯 ASCII，任何 HTTP 栈都不会动它。
        import base64
        full_name = f"{song['name']} - {song['artist']}"
        song_b64 = base64.b64encode(full_name.encode("utf-8")).decode("ascii")
        name_b64 = base64.b64encode(song["name"].encode("utf-8")).decode("ascii")
        return FileResponse(
            out, media_type="audio/mpeg", filename=os.path.basename(out),
            headers={
                "X-Cost-Seconds": f"{cost:.1f}",
                "X-Song-B64": song_b64,
                "X-Song-Name-B64": name_b64,
                "X-Kept-Seconds": f"{keep:.1f}",
                "X-Half": "1" if half else "0",
            },
        )
    except Exception as e:
        traceback.print_exc()
        return _rfail(str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")
