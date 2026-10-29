# -*- coding: utf-8 -*-
"""从 CABLE Output 录一小段，验证「声音真的进了这条线」。

为什么必须测这个：
    往 CABLE Input 写数据成功 ≠ 声音真的到了 CABLE Output。
    中间可能有：设备被独占、音量被静音、Windows 采样率不匹配、
    VB-Cable 内部路由没配对。
    直播伴侣读的是 **CABLE Output**，所以必须在这一端验证。

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\test_cable_loop.py
"""
import sys
import threading
import time

import numpy as np
import sounddevice as sd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"E:\qq-bot\stream")


def find_dev(kw, kind, prefer=None):
    """按关键字找设备，kind='in'/'out'，prefer 可指定必须含的 hostapi 片段"""
    apis = sd.query_hostapis()
    cands = []
    for i, d in enumerate(sd.query_devices()):
        ch = d["max_input_channels"] if kind == "in" else d["max_output_channels"]
        if ch < 1 or kw.lower() not in d["name"].lower():
            continue
        host = apis[d["hostapi"]]["name"]
        cands.append((i, d["name"], host))
    if not cands:
        return None
    if prefer:
        hit = [c for c in cands if prefer.lower() in c[2].lower()]
        if hit:
            return (0,) + hit[0]
    return (0,) + cands[0]


def list_devs(kw, kind):
    apis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        ch = d["max_input_channels"] if kind == "in" else d["max_output_channels"]
        if ch < 1 or kw.lower() not in d["name"].lower():
            continue
        out.append((i, d["name"], apis[d["hostapi"]]["name"]))
    return out


def main():
    print("== CABLE Output（录音端）可用的 Device API")
    devs = list_devs("CABLE Output", "in")
    for i, name, host in devs:
        print(f"  [{i:3d}] {host:22s} {name}")
    if not devs:
        sys.exit("❌ 找不到 CABLE Output，直播伴侣没东西可读")

    SR = 48000
    DUR = 4.0

    # ⚠️ WASAPI 的 CABLE Output 在直播伴侣/OBS 已经采集时会被独占，
    #    直接开流会报 WDM-KS -9999（GetNameFromCategory usbTerminalGUID）。
    #    所以验证「有没有声音」优先用 DirectSound/MME——它们允许多客户端共享。
    tried = []
    last_err = None
    for prefer in ("DirectSound", "MME", None):
        src = find_dev("CABLE Output", "in", prefer=prefer)
        if not src:
            continue
        _, rec_idx, rec_name, rec_host = src
        if any(t[0] == rec_idx for t in tried):
            continue
        tried.append((rec_idx, rec_host))
        print(f"\n== 尝试录音端: [{rec_idx}] {rec_host}")

        captured = {"data": None, "err": None, "peak": 0.0}

        def rec(idx=rec_idx):
            try:
                data = sd.rec(int(SR * DUR), samplerate=SR, channels=1,
                              dtype="float32", device=idx, blocking=True)
                captured["data"] = data
                captured["peak"] = float(np.max(np.abs(data))) if data.size else 0.0
            except Exception as e:
                captured["err"] = f"{type(e).__name__}: {e}"

        t = threading.Thread(target=rec, daemon=True)
        t.start()
        time.sleep(0.4)

        print("   往 CABLE Input 播 3 秒 440Hz…")
        from mixer import Mixer
        m = Mixer()
        if not m.ensure_stream():
            sys.exit("❌ 打开输出流失败")
        tone = (0.35 * np.sin(2 * np.pi * 440
                              * np.linspace(0, 3, int(SR * 3)))).astype(np.float32)
        m.play_music(tone, SR, gain=1.0)
        t.join(timeout=DUR + 4)
        m.close()

        if captured["err"]:
            print(f"   ✗ 这个 API 开流失败: {captured['err'][:90]}")
            last_err = captured["err"]
            continue

        data = captured["data"]
        peak = captured["peak"]
        rms = float(np.sqrt(np.mean(data ** 2))) if data.size else 0.0
        secs = 0.0
        if peak > 0:
            loud = np.abs(data[:, 0]) > peak * 0.2
            secs = float(np.sum(loud)) / SR

        print(f"   采样点数={data.size}  峰值={peak:.4f}  RMS={rms:.4f}  有声={secs:.2f}s")
        if peak > 0.01 and secs > 1.0:
            print("\n✅ 声音确实从 CABLE Input 流到了 CABLE Output")
            print(f"   （验证用的 Device API: {rec_host}；直播伴侣用哪个 API 都能读到）")
            print("   直播伴侣读这个设备就能听到 AI 的声音")
            return 0
        print("   ✗ 有流但没声音（峰值太低）")
        last_err = "peak too low"

    print("\n❌ 所有可用 Device API 都验证失败")
    if last_err:
        print("   最后错误:", str(last_err)[:160])
    print("   排查顺序：")
    print("   1. Windows 音量合成器里 CABLE Input 是否被静音")
    print("   2. 直播伴侣的「主麦克风」是否真的选成了 CABLE Output")
    print(f"   3. CABLE Output / CABLE Input 采样率是否冲突（期望 {SR}Hz）")
    print("   4. 是否被别的程序独占（WASAPI 独占模式下会抢不到）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
