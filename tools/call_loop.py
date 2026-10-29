"""QQ 电话对话循环 —— 听 → 识别 → 思考 → 说话

链路（每个环节都已实测验证）：
    对方说 → QQ 播到「扬声器 (2- High Definition Audio Device)」(7 个音频会话)
           → WASAPI 环回录音 16k
           → VAD 判断说完（静音 0.9s）
           → MiMo ASR (chat/completions + input_audio)   实测 0.81s
           → LLM 生成回复
           → 火山 TTS (pcm 24000Hz)                      实测 1.22s / 1.98s 音频
           → 写入 CABLE Input → QQ 麦克风（CABLE Output）→ 对方听到

用法：
    python tools/call_loop.py --once           # 只处理一句话（最快验证链路）
    python tools/call_loop.py                  # 一直循环
    python tools/call_loop.py --dry-run        # 不调 LLM/TTS，只测录音+VAD+ASR

⚠️ 本机的三个必须满足的前提（都实测过）：
  1. QQ 必须有窗口（LuckyLillia 有头模式），否则来电接不起来
  2. 电话接通后 QQ 的麦克风必须是 CABLE Output（来电窗口会自己显示）
  3. 用户不能把手机上的同一个号也登着（来电会推给手机）

⚠️ VAD 参数来自实测调优（参考 ohwhale call_loop.py 816 行的经验值）：
  静音 0.90s 算说完（0.55 太急，一句话中间换气就被截断）
  预录 0.45s 防止吞掉第一个字
  打断阈值 3.0 倍能量（2.0 时环境噪音就误触发）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from call_audio import (  # noqa: E402
    FRAME_SAMPLES, RATE_ASR, CablePlayer, LoopbackRecorder,
    find_speaker, log, probe_qq_endpoint,
)
from call_asr_tts import AsrClient, load_providers, resample  # noqa: E402
import call_tts_engine  # noqa: E402  电话 TTS 引擎可在 volc / rvc 之间切换


# ---------------------------------------------------------------- VAD

class SimpleVad:
    """能量 + 自适应噪声底 的 VAD（不引第三方，语音够用）。"""

    def __init__(self, threshold_floor: float = 0.012, silence_end: float = 0.90,
                 min_speech: float = 0.25, preroll: float = 0.45,
                 max_utter: float = 18.0):
        self.floor = threshold_floor
        self.silence_end = silence_end
        self.min_speech = min_speech
        self.preroll = preroll
        self.max_utter = max_utter
        self.noise = 0.005
        self.reset()

    def reset(self) -> None:
        pre_len = max(1, int(self.preroll / 0.05))
        self.pre: list[np.ndarray] = []
        self.pre_len = pre_len
        self.speaking = False
        self.buf: list[np.ndarray] = []
        self.silence = 0.0
        self.speech = 0.0

    def push(self, frame: np.ndarray) -> np.ndarray | None:
        """喂一帧（50ms）。说完返回整句 PCM，否则 None。"""
        rms = float(np.sqrt(np.mean(frame ** 2))) if len(frame) else 0.0
        thr = max(self.floor, self.noise * 3.0)

        if not self.speaking:
            # 噪声底跟踪（只在非说话时更新）
            self.noise = 0.98 * self.noise + 0.02 * rms
            # 维护预录环
            self.pre.append(frame)
            if len(self.pre) > self.pre_len:
                self.pre.pop(0)
            if rms > thr:
                self.speaking = True
                self.speech = 0.0
                self.silence = 0.0
                self.buf = list(self.pre)   # 把预录拼进来，防止吞首字
                self.pre = []
            return None

        # 说话中
        self.buf.append(frame)
        dur = len(frame) / RATE_ASR
        if rms > thr:
            self.speech += dur
            self.silence = 0.0
        else:
            self.silence += dur

        total = sum(len(x) for x in self.buf) / RATE_ASR
        if self.silence >= self.silence_end or total >= self.max_utter:
            audio = np.concatenate(self.buf) if self.buf else np.zeros(0)
            was_speech = self.speech
            self.reset()
            if total >= self.min_speech and was_speech >= self.min_speech:
                return audio
        return None


# ---------------------------------------------------------------- LLM

class LlmClient:
    """直接调 DeepSeek（电话要低延迟，不走 AstrBot 的完整管线）。

    ⚠️ 电话模式的人设要单独写：短、口语、1~2 句。
    不要复用群聊人设（那个会输出多气泡长文本，电话里很怪）。
    """

    PHONE_SYSTEM = (
        "你在和主人通电话。这是语音通话，不是打字聊天。\n"
        "规则：\n"
        "1. 每次只说 1~2 句，总共不超过 40 个字。说完就停，等对方说话。\n"
        "2. 口语化，像真人打电话，可以短促、可以笑。\n"
        "3. 绝对不要输出任何括号、星号、旁白、动作描写、Markdown。\n"
        "4. 不要用书面语，不要写长句。不要用句号结尾，用停顿代替。\n"
        "5. 对方说的可能是语音识别结果，有错别字是正常的，按意思理解，不要去纠正。"
    )

    def __init__(self, api_base: str, api_key: str, model: str, timeout: float = 30.0):
        import httpx
        self._httpx = httpx
        self.base = api_base.rstrip("/")
        self.key = api_key
        self.model = model
        self.timeout = timeout
        self.history: list[dict] = []

    def chat(self, user_text: str) -> str:
        self.history.append({"role": "user", "content": user_text})
        # 只保留最近 12 轮，电话上下文不需要太长
        if len(self.history) > 24:
            self.history = self.history[-24:]

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": self.PHONE_SYSTEM}] + self.history,
            "max_tokens": 120,
            "temperature": 1.0,
        }
        headers = {"Authorization": f"Bearer {self.key}",
                   "Content-Type": "application/json"}
        try:
            with self._httpx.Client(timeout=self.timeout, trust_env=False) as c:
                r = c.post(f"{self.base}/chat/completions", json=payload, headers=headers)
            if r.status_code != 200:
                log(f"[llm] HTTP {r.status_code}: {r.text[:200]}")
                return ""
            j = r.json()
            reply = (j["choices"][0]["message"]["content"] or "").strip()
            if reply:
                self.history.append({"role": "assistant", "content": reply})
            return reply
        except Exception as e:  # noqa: BLE001
            log(f"[llm] 异常: {e}")
            return ""


def load_llm(astrbot_root: str) -> LlmClient:
    """从 cmd_config.json 取 deepseek 的 key 和 base。"""
    path = os.path.join(astrbot_root, "data", "cmd_config.json")
    with open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    src = next((s for s in cfg.get("provider_sources", [])
                if s.get("id") == "deepseek"), None)
    if not src:
        raise RuntimeError("找不到 deepseek provider")
    key = src.get("key")
    if isinstance(key, list):
        key = key[0] if key else ""
    return LlmClient(src.get("api_base") or "https://api.deepseek.com",
                     key, "deepseek-chat")


# ---------------------------------------------------------------- 主流程

def build_stack(astrbot_root: str, speaker_name: str):
    provs = load_providers(astrbot_root)
    if not provs.get("asr"):
        raise RuntimeError("没有 ASR 配置")
    # volc 引擎需要 TTS 配置；rvc 引擎不需要（走本地 8899）
    if call_tts_engine.read_engine() == "volc" and not provs.get("tts"):
        raise RuntimeError("当前引擎是 volc，但没有火山 TTS 配置")
    asr = AsrClient(provs["asr"])
    player = CablePlayer()
    llm = load_llm(astrbot_root)
    rec = LoopbackRecorder(speaker_name=speaker_name)
    # tts 传 None：实际引擎由 call_tts_engine 按文件/环境变量决定，
    # 好处是运行中切引擎不用重启服务
    return asr, None, llm, player, rec


def pick_speaker(astrbot_root: str) -> str:
    """优先用 pycaw 探测 QQ 实际播放端点，退化到默认扬声器。"""
    import soundcard as sc
    probe_py = os.path.join(astrbot_root, ".voicecall-venv", "Scripts", "python.exe")
    probe_sh = os.path.join(astrbot_root, "tools", "which_endpoint.py")

    if not os.path.exists(probe_py):
        probe_py = sys.executable
    if os.path.exists(probe_sh):
        info = probe_qq_endpoint(probe_py, probe_sh)
        ep = (info.get("qq") or {}).get("endpoint") or info.get("loudest")
        if ep:
            log(f"[端点] pycaw 探测到 QQ 播放端点: {ep}")
            return ep

    d = sc.default_speaker()
    log(f"[端点] 退化用默认扬声器: {d.name}")
    return d.name


def run_once(asr, tts, llm, player, rec, dry_run: bool = False) -> bool:
    """处理一句话：录 → 识别 → 回复 → 说。返回是否成功。"""
    vad = SimpleVad()
    log("[听] 等待说话……")
    t0 = time.time()
    audio = None
    while time.time() - t0 < 60:
        frame = rec.read_frame(timeout=2.0)
        if frame is None:
            if rec.error:
                log(f"[录音] 出错: {rec.error}")
                return False
            continue
        audio = vad.push(frame)
        if audio is not None:
            break
    if audio is None:
        log("[听] 60 秒内没听到完整一句话")
        return False

    dur = len(audio) / RATE_ASR
    log(f"[听] 录到 {dur:.2f}s 音频")

    text = asr.transcribe(audio, RATE_ASR)
    if not text:
        log("[识别] 空结果")
        return False
    log(f"[识别] {text!r}")

    if dry_run:
        log("[dry-run] 到此为止，不调 LLM/TTS")
        return True

    reply = llm.chat(text)
    if not reply:
        log("[LLM] 空回复")
        return False
    log(f"[回复] {reply!r}")

    # 电话 TTS 引擎（volc = 火山 / rvc = 用户自己的音色）
    # 每轮重读引擎配置，所以运行中改 data/call_tts_engine.txt 立刻生效
    eng_name = call_tts_engine.read_engine()
    t_tts = time.time()
    pcm, rate, used = call_tts_engine.synthesize(reply)
    if pcm is None:
        log(f"[TTS:{eng_name}] 合成失败")
        return False
    log(f"[TTS:{used}] {len(pcm)} 采样 @{rate}Hz = {len(pcm)/rate:.2f}s "
        f"(耗时 {time.time()-t_tts:.2f}s)")

    # CABLE Input 用 48000 播放（VB-CABLE 原生率），先重采样
    pcm48 = resample(pcm, rate, 48000)
    cost = player.play(pcm48, rate=48000)
    log(f"[说] 播放完成，耗时 {cost:.2f}s")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("ASTRBOT_ROOT") or r"E:\qq-bot")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seconds", type=int, default=0)
    args = ap.parse_args()

    log("=== QQ 电话对话循环 ===")
    speaker = pick_speaker(args.root)
    log(f"[端点] 环回监听: {speaker}")

    asr, tts, llm, player, rec = build_stack(args.root, speaker)
    rec.start()
    time.sleep(1.0)
    if rec.error:
        log(f"[录音] 启动失败: {rec.error}")
        return 1
    log("[就绪] 开始监听")

    ok = 0
    started = time.time()
    try:
        while True:
            if run_once(asr, tts, llm, player, rec, dry_run=args.dry_run):
                ok += 1
                if args.once:
                    break
            if args.seconds and (time.time() - started) >= args.seconds:
                break
            if args.once and ok == 0:
                log("[once] 没成功，退出")
                break
    except KeyboardInterrupt:
        log("Ctrl+C")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    finally:
        rec.stop()
    log(f"[结束] 成功处理 {ok} 轮")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
