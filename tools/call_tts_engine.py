"""电话 TTS 引擎 —— 支持在「火山 TTS」和「RVC 音色」之间切换

用户需求（2026-10-26）：
    「我想将接语音电话的声音能切换成我的这套 tts-rvc 处理输出的形式，且还能切换回去」

两种引擎：

  ┌─────────────┬──────────────┬──────────┬───────────────────────┐
  │ 引擎         │ 实现          │ 实测延迟  │ 音色                   │
  ├─────────────┼──────────────┼──────────┼───────────────────────┤
  │ volc        │ 火山 TTS API  │ ~1.2s    │ 火山内置（zh_female_vv）│
  │ rvc         │ 本地 8899     │ 2.8~4.4s │ ✅ 用户自己调的音色      │
  └─────────────┴──────────────┴──────────┴───────────────────────┘

RVC 的实现见 `astrbot_plugin_rvc_tts/main.py:20` 的 `_synthesize_voice`：
    POST http://127.0.0.1:8899/speak
      data = {text, model, f0up_key}
      → 返回 mp3 字节

⚠️ RVC 返回的是 **mp3**，不是 pcm。电话链路要 pcm 才能直接喂给 CABLE。
   所以这里用 ffmpeg 解码成 48000 单声道 float32。
   本机 ffmpeg 在 `runtime/ffmpeg/bin/ffmpeg.exe`（ohwhale 装的）
   或系统 PATH 里，两个都试。

切换方式（三选一，都会生效）：
    1. 配置文件：E:\\qq-bot\\data\\call_tts_engine.txt（内容就一个词 volc 或 rvc）
    2. 环境变量：QQ_CALL_TTS_ENGINE=volc|rvc
    3. 运行时改文件 —— 服务每轮念之前都会重读，**不用重启**
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S', time.localtime())} {msg}", flush=True)


# ---------------------------------------------------------------- 配置

ROOT = os.environ.get("ASTRBOT_ROOT") or r"E:\qq-bot"
MODEL_FILE = os.path.join(ROOT, "data", "rvc_model.txt")


def read_model(fallback: str = "nikou48K1999.pth") -> str:
    """读统一的音色配置（2026-10-26）。

    说话/唱歌/电话共用同一个文件，用 tools/rvc_model_manager.py 切换，
    或直接改 data/rvc_model.txt。即时生效，不用重启。
    """
    try:
        if os.path.exists(MODEL_FILE):
            with open(MODEL_FILE, encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
    except Exception:
        pass
    return fallback
ENGINE_FILE = os.path.join(ROOT, "data", "call_tts_engine.txt")
RVC_API = os.environ.get("COVER_API", "http://127.0.0.1:8899")

VALID_ENGINES = ("volc", "rvc")


def read_engine() -> str:
    """读当前引擎。文件 > 环境变量 > 默认 volc。

    ⚠️ 每轮都重读，这样运行中改文件就能立刻生效，不用重启服务。
    """
    # 1. 文件
    try:
        if os.path.exists(ENGINE_FILE):
            with open(ENGINE_FILE, encoding="utf-8") as f:
                v = f.read().strip().lower()
            if v in VALID_ENGINES:
                return v
    except Exception:  # noqa: BLE001
        pass
    # 2. 环境变量
    v = (os.environ.get("QQ_CALL_TTS_ENGINE") or "").strip().lower()
    if v in VALID_ENGINES:
        return v
    # 3. 默认
    return "volc"


def write_engine(name: str) -> bool:
    """切换引擎。返回是否成功。"""
    name = name.strip().lower()
    if name not in VALID_ENGINES:
        return False
    try:
        os.makedirs(os.path.dirname(ENGINE_FILE), exist_ok=True)
        with open(ENGINE_FILE, "w", encoding="utf-8") as f:
            f.write(name)
        return True
    except Exception as e:  # noqa: BLE001
        log(f"[tts] 写引擎文件失败: {e}")
        return False


# ---------------------------------------------------------------- ffmpeg 解码

def find_ffmpeg() -> str | None:
    """找 ffmpeg。优先项目内置，再找 PATH。"""
    candidates = [
        os.path.join(ROOT, "runtime", "ffmpeg", "bin", "ffmpeg.exe"),
        r"E:\qq-bot\runtime\ffmpeg\bin\ffmpeg.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return shutil.which("ffmpeg")


def decode_to_pcm(data: bytes, rate: int = 48000) -> np.ndarray | None:
    """任意音频字节（mp3/wav/ogg）→ float32 单声道 PCM。"""
    ff = find_ffmpeg()
    if not ff:
        log("[tts] 找不到 ffmpeg，无法解码 mp3")
        return None
    try:
        proc = subprocess.run(
            [ff, "-hide_banner", "-loglevel", "error",
             "-i", "pipe:0",
             "-f", "f32le", "-acodec", "pcm_f32le",
             "-ac", "1", "-ar", str(rate),
             "pipe:1"],
            input=data, capture_output=True, timeout=60,
        )
        if proc.returncode != 0:
            log(f"[tts] ffmpeg 解码失败: {proc.stderr.decode('utf-8', 'replace')[:200]}")
            return None
        return np.frombuffer(proc.stdout, dtype=np.float32).copy()
    except Exception as e:  # noqa: BLE001
        log(f"[tts] ffmpeg 异常: {e}")
        return None


# ---------------------------------------------------------------- 引擎实现

class RvcEngine:
    """本地 RVC（MiMo TTS + RVC 变声），走 8899 的 /speak。

    这跟群里 rvc_tts 插件用的是**同一个服务、同一个模型**，
    所以电话里的音色和群里完全一致。
    """

    name = "rvc"

    def __init__(self, model: str = "", f0up_key: int = 0,
                 api: str = "", timeout: float = 180.0):
        self.api = (api or RVC_API).rstrip("/")
        # 空字符串 = 用共享配置里的当前音色
        self.model = model or read_model()
        self.f0up_key = f0up_key
        self.timeout = timeout
        self.fails = 0

    def synthesize(self, text: str) -> tuple[np.ndarray | None, int]:
        """返回 (pcm float32 mono, rate)。失败 (None, 0)。"""
        text = (text or "").strip()
        if not text:
            return None, 0
        try:
            import httpx
            with httpx.Client(timeout=self.timeout, trust_env=False) as c:
                r = c.post(f"{self.api}/speak",
                           data={"text": text,
                                 "model": self.model,
                                 "f0up_key": str(self.f0up_key)})
            if r.status_code != 200:
                log(f"[rvc] HTTP {r.status_code}: {r.text[:150]}")
                self.fails += 1
                return None, 0
            raw = r.content
            if not raw:
                log("[rvc] 返回空音频")
                self.fails += 1
                return None, 0
        except Exception as e:  # noqa: BLE001
            log(f"[rvc] 请求异常: {e}")
            self.fails += 1
            return None, 0

        rate = 48000
        pcm = decode_to_pcm(raw, rate=rate)
        if pcm is None or len(pcm) == 0:
            self.fails += 1
            return None, 0
        return pcm, rate

    def healthy(self) -> bool:
        try:
            import httpx
            with httpx.Client(timeout=5, trust_env=False) as c:
                r = c.get(f"{self.api}/health")
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False


class VolcEngine:
    """火山 TTS（电话原来的方案，延迟低）。

    实现直接复用 tools/call_asr_tts.py 里的 TtsClient。
    """

    name = "volc"

    def __init__(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from call_asr_tts import TtsClient, load_providers
        provs = load_providers(ROOT)
        if not provs.get("tts"):
            raise RuntimeError("没有火山 TTS 配置")
        self._c = TtsClient(provs["tts"])
        self.fails = 0

    def synthesize(self, text: str) -> tuple[np.ndarray | None, int]:
        pcm, rate = self._c.synthesize(text)
        if pcm is None:
            self.fails += 1
            return None, 0
        return pcm, rate

    def healthy(self) -> bool:
        return True   # 云端服务，靠实际合成验活


# ---------------------------------------------------------------- 工厂

_cache: dict[str, object] = {}


def get_engine(name: str | None = None):
    """按名字拿引擎实例（带缓存，避免重复初始化）。"""
    name = (name or read_engine()).lower()
    if name not in VALID_ENGINES:
        name = "volc"

    if name not in _cache:
        if name == "rvc":
            _cache[name] = RvcEngine()
        else:
            _cache[name] = VolcEngine()
        log(f"[tts] 已加载引擎: {name}")
    return _cache[name]


def synthesize(text: str, name: str | None = None) -> tuple[np.ndarray | None, int, str]:
    """统一入口。返回 (pcm, rate, engine_name)。"""
    eng = get_engine(name)
    pcm, rate = eng.synthesize(text)
    return pcm, rate, eng.name


# ---------------------------------------------------------------- 自检

def selftest() -> int:
    log("=== 电话 TTS 引擎自检 ===")
    log(f"当前引擎: {read_engine()}")
    log(f"引擎文件: {ENGINE_FILE}")

    ok = True
    text = "你好，我是测试语音"

    for name in VALID_ENGINES:
        log(f"\n--- 测试 {name} ---")
        t0 = time.time()
        pcm, rate, used = synthesize(text, name)
        cost = time.time() - t0
        if pcm is not None and len(pcm) > 0:
            dur = len(pcm) / rate
            log(f"✅ {name}: {len(pcm)} 采样 @{rate}Hz = {dur:.2f}s 音频, 耗时 {cost:.2f}s")
        else:
            log(f"❌ {name}: 合成失败")
            ok = False

    log("\n=== 自检结束: " + ("通过" if ok else "有失败") + " ===")
    if ok:
        log(f"切换方式：写 {ENGINE_FILE}，内容 volc 或 rvc")
        log(f"  当前 = {read_engine()}")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in VALID_ENGINES:
            if write_engine(arg):
                log(f"已切换到: {arg}")
            else:
                log(f"切换失败")
            sys.exit(0)
        else:
            log(f"用法: {sys.argv[0]} [volc|rvc]  （不带参数 = 自检）")
            sys.exit(2)
    sys.exit(selftest())
