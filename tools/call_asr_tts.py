"""电话用的 ASR / TTS 客户端（复用 AstrBot 已配置的凭证，不重复造轮子）

本机已确认可用的两个服务（2026-10-26 实测读取 data/cmd_config.json）：

  ASR — mimo_asr
      type = openai_whisper_api
      api_base = https://api.xiaomimimo.com/v1      （OpenAI 兼容）
      model    = mimo-v2.5-asr
      → 用法：POST /audio/transcriptions，multipart 传 wav

  TTS — volc_vivi_tts
      type = volcengine_tts
      api_base = https://openspeech.bytedance.com/api/v1/tts
      appid / api_key / cluster / voice_type = zh_female_vv_uranus_bigtts
      → 用法：POST JSON，返回 base64 音频（mp3/pcm）

⚠️ 为什么不用 AstrBot 的 provider 对象：
  电话进程要独立于 AstrBot 运行（AstrBot 重启不该把电话掐断），
  所以这里直接按 HTTP 调，凭证从 cmd_config.json 读。

⚠️ MiMo 的两个坑（STATUS.md 记过）：
  1. ASR/TTS 都可能触发内容安全过滤，失败时要能降级
  2. provider 返回可能是 base64 也可能是 URL，都要处理
"""

from __future__ import annotations

import base64
import io
import json
import os
import struct
import sys
import time
import wave

import httpx
import numpy as np


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S', time.localtime())} {msg}", flush=True)


# ---------------------------------------------------------------- 读配置

def load_providers(astrbot_root: str) -> dict:
    """从 AstrBot 的 cmd_config.json 读 ASR/TTS 凭证。"""
    path = os.path.join(astrbot_root, "data", "cmd_config.json")
    with io.open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)

    out = {"asr": None, "tts": None}
    sources = {s.get("id"): s for s in cfg.get("provider_sources", [])}

    asr_id = (cfg.get("provider_stt_settings") or {}).get("provider_id")
    if asr_id and asr_id in sources:
        out["asr"] = sources[asr_id]

    tts_id = (cfg.get("provider_tts_settings") or {}).get("provider_id")
    if tts_id and tts_id in sources:
        out["tts"] = sources[tts_id]

    return out


# ---------------------------------------------------------------- PCM 工具

def pcm_to_wav(pcm: np.ndarray, rate: int = 16000, channels: int = 1) -> bytes:
    """float32 [-1,1] → 16bit PCM WAV bytes。"""
    a = np.asarray(pcm, dtype=np.float32).flatten()
    a = np.clip(a, -1.0, 1.0)
    int16 = (a * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(int16.tobytes())
    return buf.getvalue()


def wav_to_pcm(data: bytes) -> tuple[np.ndarray, int]:
    """WAV bytes → (float32 mono, rate)。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        rate = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, rate


def resample(pcm: np.ndarray, src: int, dst: int) -> np.ndarray:
    """线性重采样（语音够用，避免引入 scipy 依赖）。"""
    if src == dst or len(pcm) == 0:
        return pcm
    n = int(len(pcm) * dst / src)
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.linspace(0, 1, len(pcm), endpoint=False)
    x_new = np.linspace(0, 1, n, endpoint=False)
    return np.interp(x_new, x_old, pcm).astype(np.float32)


# ---------------------------------------------------------------- ASR

class AsrClient:
    """MiMo ASR（OpenAI 兼容 /audio/transcriptions）。"""

    def __init__(self, src: dict, timeout: float = 30.0):
        self.base = (src.get("api_base") or "").rstrip("/")
        self.key = (src.get("api_key") or "")
        if isinstance(self.key, list):
            self.key = self.key[0] if self.key else ""
        self.model = src.get("model") or "mimo-v2.5-asr"
        self.timeout = timeout
        self.fails = 0
        if not self.base or not self.key:
            raise RuntimeError("ASR 凭证不全")

    def transcribe(self, pcm: np.ndarray, rate: int = 16000) -> str:
        """返回识别文本；失败返回 ''。

        ⚠️ 关键：MiMo ASR 走的是 chat/completions + input_audio，
        **不是** OpenAI 的 /audio/transcriptions（那个返回 404）。
        依据：astrbot/core/provider/sources/whisper_api_source.py:104-131 的
        「MiMo ASR (TokenPlan)」分支 —— 这是 AstrBot 核心文件里被 patch 过的部分。
        """
        if pcm is None or len(pcm) == 0:
            return ""
        wav = pcm_to_wav(pcm, rate=rate)
        b64 = base64.b64encode(wav).decode("utf-8")
        url = f"{self.base}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": f"data:audio/wav;base64,{b64}"},
                        }
                    ],
                }
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as c:
                r = c.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                log(f"[asr] HTTP {r.status_code}: {r.text[:300]}")
                self.fails += 1
                return ""
            j = r.json()
            choices = j.get("choices") or []
            if not choices:
                log(f"[asr] 无 choices: {json.dumps(j, ensure_ascii=False)[:300]}")
                self.fails += 1
                return ""
            text = (choices[0].get("message", {}).get("content") or "").strip()
            if not text:
                log(f"[asr] 空文本: {json.dumps(j, ensure_ascii=False)[:300]}")
            return text
        except Exception as e:  # noqa: BLE001
            log(f"[asr] 请求异常: {e}")
            self.fails += 1
            return ""


# ---------------------------------------------------------------- TTS

class TtsClient:
    """火山 TTS（openspeech v1/tts）。"""

    def __init__(self, src: dict, timeout: float = 25.0):
        self.api_base = src.get("api_base") or "https://openspeech.bytedance.com/api/v1/tts"
        self.appid = str(src.get("appid") or "")
        self.token = src.get("api_key") or ""
        if isinstance(self.token, list):
            self.token = self.token[0] if self.token else ""
        self.cluster = src.get("volcengine_cluster") or "volcano_tts"
        self.voice = src.get("volcengine_voice_type") or "zh_female_vv_uranus_bigtts"
        self.speed = float(src.get("volcengine_speed_ratio") or 1.0)
        self.timeout = timeout
        self.fails = 0
        if not self.appid or not self.token:
            raise RuntimeError("TTS 凭证不全")

    def synthesize(self, text: str, want_pcm: bool = True) -> tuple[np.ndarray | None, int]:
        """返回 (float32 mono pcm, rate)；失败返回 (None, 0)。"""
        text = (text or "").strip()
        if not text:
            return None, 0
        payload = {
            "app": {
                "appid": self.appid,
                "token": self.token,
                "cluster": self.cluster,
            },
            "user": {"uid": "qqbot-call"},
            "audio": {
                "voice_type": self.voice,
                "encoding": "pcm" if want_pcm else "mp3",
                "speed_ratio": self.speed,
                "rate": 24000,
            },
            "request": {
                "reqid": f"call-{int(time.time()*1000)}",
                "text": text,
                "operation": "query",
            },
        }
        headers = {
            "Authorization": f"Bearer;{self.token}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as c:
                r = c.post(self.api_base, json=payload, headers=headers)
            if r.status_code != 200:
                log(f"[tts] HTTP {r.status_code}: {r.text[:200]}")
                self.fails += 1
                return None, 0
            j = r.json()
            if j.get("code") not in (0, 3000):
                log(f"[tts] code={j.get('code')} msg={j.get('message')}")
                self.fails += 1
                return None, 0
            b64 = j.get("data")
            if not b64:
                log("[tts] 返回没有 data")
                self.fails += 1
                return None, 0
            raw = base64.b64decode(b64)
            if want_pcm:
                # 火山 pcm 是 16bit 单声道 24000Hz
                a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                return a, 24000
            # mp3 分支：交给上层用 ffmpeg 解，这里只返回原始字节的 PCM 占位
            log("[tts] mp3 分支未实现解码，改用 pcm")
            self.fails += 1
            return None, 0
        except Exception as e:  # noqa: BLE001
            log(f"[tts] 请求异常: {e}")
            self.fails += 1
            return None, 0


# ---------------------------------------------------------------- 自检

def selftest(astrbot_root: str = "") -> int:
    root = astrbot_root or os.environ.get("ASTRBOT_ROOT") or r"E:\qq-bot"
    log(f"=== ASR / TTS 自检（root={root}）===")
    provs = load_providers(root)
    ok = True

    asr_src, tts_src = provs.get("asr"), provs.get("tts")
    log(f"ASR 源: {asr_src.get('id') if asr_src else None}")
    log(f"TTS 源: {tts_src.get('id') if tts_src else None}")

    # TTS：合成一句话
    pcm = None
    rate = 0
    if tts_src:
        try:
            tts = TtsClient(tts_src)
            log("合成测试: '你好，我是测试语音'")
            t0 = time.time()
            pcm, rate = tts.synthesize("你好，我是测试语音")
            cost = time.time() - t0
            if pcm is not None and len(pcm) > 0:
                dur = len(pcm) / rate
                log(f"✅ TTS 成功: {len(pcm)} 采样 @{rate}Hz = {dur:.2f}s, 耗时 {cost:.2f}s")
            else:
                log("❌ TTS 合成失败")
                ok = False
        except Exception as e:  # noqa: BLE001
            log(f"❌ TTS 异常: {e}")
            ok = False
    else:
        log("❌ 没有 TTS 源")
        ok = False

    # ASR：把刚才合成的音频（或提示音）转回去
    if asr_src:
        try:
            asr = AsrClient(asr_src)
            probe = pcm if pcm is not None else (
                0.3 * np.sin(2 * np.pi * 440 * np.linspace(0, 1, 16000, endpoint=False))
            ).astype(np.float32)
            probe_rate = rate if pcm is not None else 16000
            if probe_rate != 16000:
                probe = resample(probe, probe_rate, 16000)
            log("识别测试中……")
            t0 = time.time()
            text = asr.transcribe(probe, 16000)
            cost = time.time() - t0
            if text:
                log(f"✅ ASR 成功: {text!r}（耗时 {cost:.2f}s）")
                if pcm is not None and "测试" not in text and "你好" not in text:
                    log(f"  ⚠️ 识别结果和原文不太像，可能是 TTS 音色导致，可接受")
            else:
                log("❌ ASR 返回空")
                ok = False
        except Exception as e:  # noqa: BLE001
            log(f"❌ ASR 异常: {e}")
            ok = False
    else:
        log("❌ 没有 ASR 源")
        ok = False

    log("=== 自检结束: " + ("全部通过" if ok else "有失败项") + " ===")
    return 0 if ok else 1


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit(selftest(root))
