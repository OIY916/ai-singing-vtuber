# -*- coding: utf-8 -*-
"""把音频播放到 VB-Cable（供直播伴侣/OBS 采集）。

设计要点：
  1. 常驻一条输出流，避免每次开流造成杂音
  2. 支持「边生成边播」：先放音乐，语音插进来时自动压低音乐
  3. 说话/唱歌时向 WebSocket 广播状态，让网页播放器切换视频段
"""
import asyncio
import json
import os
import sys
import threading
import time

import numpy as np
import sounddevice as sd

sys.stdout.reconfigure(encoding="utf-8")

# VB-Cable 的输入端（程序往里写，直播伴侣从 CABLE Output 读）
CABLE_OUTPUT = "CABLE Input"


class Mixer:
    """单路输出混音器：音乐 + 语音在一条流里混，互不打断。"""

    def __init__(self, sr=48000):
        self.sr = sr
        self.lock = threading.Lock()
        self.stream = None
        self.music = None          # np.ndarray float32 (n, 2)
        self.mpos = 0
        self.music_gain = 0.5
        self.voice = None
        self.vpos = 0
        self.voice_gain = 0.9
        self.duck = 0.3            # 说话时音乐压低到 30%
        self.cur_gain = 1.0
        self.on_music_end = None
        self.on_voice_end = None
        self.playing_music = False
        self.playing_voice = False
        # 实时电平（0~1）：音频回调里算 RMS 写进来，网页拿去做频谱条。
        # 音频回调是独立线程，这里是 float 赋值，CPython 里读到的要么是旧值要么是新值，够用。
        self.level = 0.0
        # 当前音乐总时长/已播位置（秒），给网页进度条用
        self.music_dur = 0.0
        # 暂停标志：暂停时不推进 mpos，恢复后接着播
        self.paused = False

    # ---------- 设备 ----------
    @staticmethod
    def find_device(name_kw="CABLE Input"):
        """找 VB-Cable 的输出设备索引"""
        apis = sd.query_hostapis()
        best = None
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] < 2:
                continue
            if name_kw.lower() in d["name"].lower():
                host = apis[d["hostapi"]]["name"]
                # WASAPI 最稳，优先
                pri = 0 if "WASAPI" in host else (1 if "DirectSound" in host else 2)
                if best is None or pri < best[0]:
                    best = (pri, i, d["name"], host)
        return best

    def ensure_stream(self, device_name="CABLE Input"):
        with self.lock:
            if self.stream is not None and self.stream.active:
                return True
        found = self.find_device(device_name)
        if not found:
            print(f"[mixer] 找不到设备 '{device_name}'")
            return False
        _, idx, name, host = found
        print(f"[mixer] 使用设备 {idx}: {name} [{host}]")
        self.stream = sd.OutputStream(
            samplerate=self.sr, channels=2, dtype="float32",
            device=idx, callback=self._callback, latency="high",
        )
        self.stream.start()
        return True

    # ---------- 混音回调 ----------
    def _callback(self, outdata, frames, _time, _status):
        """音频回调：绝对不能让异常逃出去。

        ⚠️ sounddevice 里回调抛异常会让整条输出流停摆（而且是静默的），
        之后所有播放都「看起来在播、其实没声音」，还会连带 worker 永久卡住。
        所以这里整体兜底：出异常就当这一次静音，流继续跑。
        """
        try:
            self._mix(outdata, frames)
        except Exception as e:
            # 不 print 太多噪声，只在出错时打一行，便于定位
            if not getattr(self, "_cb_warned", False):
                print(f"[mixer] 回调异常（已兜底）：{type(e).__name__}: {e}")
                self._cb_warned = True
            try:
                outdata.fill(0)
            except Exception:
                pass

    def _mix(self, outdata, frames):
        out = np.zeros((frames, 2), np.float32)
        ended_music = ended_voice = False

        with self.lock:
            # ⚠️ 暂停时直接输出静音，且**不动 mpos**（2026-10-27 新增）。
            #    这样恢复后从原位置继续播，而不是从头开始。
            #    注意：暂停期间也不推进 mpos，所以进度条应该冻住。
            if self.paused:
                self.cur_gain = 1.0
                outdata.fill(0)
                return

            # 音乐增益平滑过渡（250ms），避免爆音
            target = self.duck if self.playing_voice else 1.0
            step = min(1.0, frames / (self.sr * 0.25))
            g1 = self.cur_gain + (target - self.cur_gain) * step
            ramp = np.linspace(self.cur_gain, g1, frames, dtype=np.float32)[:, None]

            if self.music is not None:
                n = min(frames, len(self.music) - self.mpos)
                if n > 0:
                    # ⚠️ ramp 是 (frames,1)，缓冲区尾段是 (n,2)。
                    # 播到最后一帧时 n < frames，直接相乘会广播失败：
                    #     ValueError: operands could not be broadcast
                    #                    together with shapes (353,2) (480,1)
                    # 音频回调线程抛异常 → 整条输出流静默停摆 →
                    # on_music_end 永远不触发 → play_worker 永久挂住，
                    # 表现就是「连唱时第二首永远不播、待播队列不消化」。
                    # 这就是存档里那条一直没验证的 bug 的真正根因。
                    out[:n] += self.music[self.mpos:self.mpos + n] * (ramp[:n] * self.music_gain)
                    self.mpos += n
                if self.mpos >= len(self.music):
                    self.music = None
                    self.playing_music = False
                    ended_music = True

            self.cur_gain = g1

            if self.voice is not None:
                n = min(frames, len(self.voice) - self.vpos)
                if n > 0:
                    out[:n] += self.voice[self.vpos:self.vpos + n] * self.voice_gain
                    self.vpos += n
                if self.vpos >= len(self.voice):
                    self.voice = None
                    self.playing_voice = False
                    ended_voice = True

        np.clip(out, -1, 1, out=out)
        outdata[:] = out

        # 实时电平：给网页频谱条用（RMS，再按 3 倍放大让小声也有肉眼可见的跳动）
        try:
            if frames:
                self.level = min(1.0, float(np.sqrt(np.mean(out[:frames] ** 2))) * 3.0)
        except Exception:
            pass

        if ended_music and self.on_music_end:
            threading.Thread(target=self.on_music_end, daemon=True).start()
        if ended_voice and self.on_voice_end:
            threading.Thread(target=self.on_voice_end, daemon=True).start()

    # ---------- 播放接口 ----------
    def play_music(self, pcm, sr, gain=None, on_end=None):
        """播放音乐（背景/唱歌），可循环由上层控制"""
        data = self._to_stereo_f32(pcm, sr)
        with self.lock:
            self.music = data
            self.mpos = 0
            self.playing_music = True
            if gain is not None:
                self.music_gain = gain
            self.on_music_end = on_end
            self.music_dur = round(len(data) / self.sr, 2) if len(data) else 0.0

    def play_voice(self, pcm, sr, gain=None, on_end=None):
        """播放语音（说话/短句），会自动压低音乐"""
        data = self._to_stereo_f32(pcm, sr)
        with self.lock:
            self.voice = data
            self.vpos = 0
            self.playing_voice = True
            if gain is not None:
                self.voice_gain = gain
            self.on_voice_end = on_end

    def stop_music(self):
        with self.lock:
            self.music = None
            self.mpos = 0
            self.playing_music = False
            self.on_music_end = None

    def stop_voice(self):
        with self.lock:
            self.voice = None
            self.vpos = 0
            self.playing_voice = False
            self.on_voice_end = None

    def set_duck(self, duck: float):
        """设置说话时音乐压低到的比例（0~1）"""
        with self.lock:
            self.duck = max(0.0, min(1.0, duck))

    def pause(self):
        """暂停输出（音乐和语音都静音，但**保留播放位置**）。

        ⚠️ 为什么不直接用 stop_music：
           stop_music 会把 music 清空、mpos 归零、on_music_end 摘掉，
           结果 play_worker 靠 15 秒看门狗才醒，而且恢复时歌从头放。
           pause 只翻转一个标志，缓冲区原封不动，恢复后从原位置继续。
        """
        with self.lock:
            self.paused = True

    def resume(self):
        """从暂停位置继续播放"""
        with self.lock:
            self.paused = False

    def is_paused(self) -> bool:
        with self.lock:
            return bool(self.paused)

    def status(self):
        with self.lock:
            return {
                "music": self.playing_music,
                "voice": self.playing_voice,
                "paused": bool(getattr(self, "paused", False)),
                "level": 0.0 if getattr(self, "paused", False) else round(self.level, 3),
                "music_dur": round(getattr(self, "music_dur", 0.0), 2),
                "music_pos": (
                    round(self.mpos / self.sr, 2)
                    if self.music is not None else 0.0
                ),
                "music_remain": (
                    round((len(self.music) - self.mpos) / self.sr, 1)
                    if self.music is not None else 0
                ),
            }

    def _to_stereo_f32(self, pcm, sr):
        """统一成 float32 立体声，必要时重采样"""
        a = np.asarray(pcm, dtype=np.float32)
        if a.ndim == 1:
            a = np.stack([a, a], axis=1)
        elif a.shape[1] == 1:
            a = np.repeat(a, 2, axis=1)
        if a.dtype != np.float32:
            a = a.astype(np.float32)
        # 归一化到 [-1, 1]（如果传进来是 int16 范围）
        mx = float(np.max(np.abs(a))) if a.size else 0.0
        if mx > 2.0:
            a = a / 32768.0
        if sr != self.sr:
            a = self._resample(a, sr, self.sr)
        return a.astype(np.float32)

    @staticmethod
    def _resample(a, src_sr, dst_sr):
        if src_sr == dst_sr:
            return a
        n_dst = int(len(a) * dst_sr / src_sr)
        if n_dst <= 0:
            return a
        idx = np.linspace(0, len(a) - 1, n_dst)
        out = np.empty((n_dst, a.shape[1]), np.float32)
        for ch in range(a.shape[1]):
            out[:, ch] = np.interp(idx, np.arange(len(a)), a[:, ch])
        return out

    # ---------- 音量包络（给网页做口型/动效） ----------
    @staticmethod
    def envelope(pcm, sr, fps=30, bars=48):
        """生成频谱条/音量包络，给前端动画用"""
        a = np.asarray(pcm, dtype=np.float32)
        if a.ndim > 1:
            a = a.mean(axis=1)
        hop = max(1, sr // fps)
        n = len(a) // hop
        if n == 0:
            return []
        frame = a[:n * hop].reshape(n, hop)
        rms = np.sqrt(np.mean(frame ** 2, axis=1))
        ref = float(np.percentile(rms, 95)) or 1.0
        return [round(float(v), 3) for v in np.clip(rms / ref, 0, 1)]

    def close(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


if __name__ == "__main__":
    # 自检：往 CABLE Input 播一段测试音
    m = Mixer()
    found = m.find_device()
    print("找到设备:", found)
    if not found:
        print("❌ 没有 VB-Cable，请确认已安装 VB-Cable")
        sys.exit(1)
    if not m.ensure_stream():
        sys.exit(1)
    sr = 48000
    t = np.linspace(0, 3, sr * 3, dtype=np.float32)
    tone = (0.25 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    print("播放 440Hz 测试音 3 秒到 CABLE Input …")
    m.play_music(tone, sr)
    time.sleep(3.5)
    m.close()
    print("完成")
