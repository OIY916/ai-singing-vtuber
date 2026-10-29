# -*- coding: utf-8 -*-
"""验证切歌 / 插队 / 删除 / 暂停 的核心逻辑（不启动服务、不碰正在直播的进程）。

为什么要在重启前先测：
    直播中重启成本很高（丢队列、观众感知），所以逻辑必须先离线验证过。
    这些测试只构造内存对象，不碰声卡、不连网络。

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\test_playback_control.py
"""
import asyncio
import sys

sys.path.insert(0, r"E:\qq-bot\stream")
sys.stdout.reconfigure(encoding="utf-8")

import main as M
from mixer import Mixer

PASS, FAIL = [], []


def check(desc, got, want):
    ok = (got == want)
    (PASS if ok else FAIL).append(desc)
    print(f"  {'✅' if ok else '❌'} {desc}: got={got!r} want={want!r}")


def clear_queues():
    for q in (M.JOB_Q, M.PLAY_Q, M.S.hot_q):
        if q is None:
            continue
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break


async def test_mixer_pause():
    print("\n【1】暂停/继续：保留播放位置，不从头开始")
    m = Mixer()
    import numpy as np
    sr = 48000
    # 5 秒音频
    pcm = (0.3 * np.sin(2 * np.pi * 440 * np.linspace(0, 5, sr * 5))).astype(np.float32)

    with m.lock:
        m.music = m._to_stereo_f32(pcm, sr)
        m.mpos = 0
        m.playing_music = True
        m.music_dur = 5.0

    # 播 1 秒（48000 帧，每帧 480 → 100 次回调）
    FRAMES = 480
    out = np.zeros((FRAMES, 2), np.float32)
    for _ in range(100):
        m._callback(out, FRAMES, None, None)
    pos_before = m.status()["music_pos"]
    print(f"    播放 1 秒后位置: {pos_before}s")

    m.pause()
    check("暂停后 is_paused", m.is_paused(), True)
    check("暂停后 status.paused", m.status()["paused"], True)

    # 暂停期间再跑 100 次回调，位置不应变化
    for _ in range(100):
        m._callback(out, FRAMES, None, None)
    pos_paused = m.status()["music_pos"]
    check("暂停期间位置不动", pos_paused, pos_before)
    check("暂停时电平为 0", m.status()["level"], 0.0)

    m.resume()
    check("继续后未暂停", m.is_paused(), False)
    for _ in range(50):
        m._callback(out, FRAMES, None, None)
    pos_resume = m.status()["music_pos"]
    check("继续后从原位置往后走", pos_resume > pos_paused, True)
    print(f"    继续后位置: {pos_resume}s（没回到 0 就对了）")


async def test_skip_event():
    print("\n【2】切歌：skip 事件能立刻唤醒，不等 15 秒看门狗")
    M.S.skip = asyncio.Event()

    done = asyncio.Event()
    # 模拟：正在播一首 100 秒的歌，但我们 0.2 秒后就切
    async def do_skip():
        await asyncio.sleep(0.2)
        M.S.skip.set()

    async def wait():
        return await M.wait_music_end(100.0, done, skip=M.S.skip)

    t0 = asyncio.get_event_loop().time()
    sk = asyncio.create_task(do_skip())
    result = await wait()
    elapsed = asyncio.get_event_loop().time() - t0
    await sk

    check("切歌返回 False", result, False)
    check("切歌耗时 < 1 秒（不是 15 秒）", elapsed < 1.0, True)
    print(f"    实际耗时: {elapsed:.2f}s")


async def test_normal_end():
    print("\n【3】正常播完：done 事件返回 True")
    M.S.skip = asyncio.Event()
    done = asyncio.Event()

    async def finish():
        await asyncio.sleep(0.2)
        done.set()

    async def wait():
        return await M.wait_music_end(1.0, done, skip=M.S.skip)

    t = asyncio.create_task(finish())
    result = await wait()
    await t
    check("正常播完返回 True", result, True)
    check("skip 未被误触发", M.S.skip.is_set(), False)


async def test_hot_queue_priority():
    print("\n【4】插队：下一个取的是插队队列（方案A，不打断当前）")
    M.S.hot_q = asyncio.Queue(maxsize=4)
    clear_queues()

    M.PLAY_Q.put_nowait({"title": "正常队第一", "mp3": b"", "name": "a"})
    M.PLAY_Q.put_nowait({"title": "正常队第二", "mp3": b"", "name": "b"})
    M.S.hot_q.put_nowait({"title": "插队歌", "mp3": b"", "name": "主播", "raw": False})

    job, from_hot = await M.next_play_job()
    check("优先取到插队歌", job["title"], "插队歌")
    check("标记为插队", from_hot, True)

    job2, from_hot2 = await M.next_play_job()
    check("然后才是正常队列", job2["title"], "正常队第一")
    check("标记为正常", from_hot2, False)

    clear_queues()


async def main_async():
    await test_mixer_pause()
    await test_skip_event()
    await test_normal_end()
    await test_hot_queue_priority()


def main():
    print("=" * 74)
    print("  播放控制测试：暂停 / 切歌 / 插队")
    print("=" * 74)

    M.load_cfg()
    # 这些 asyncio 对象正常是在 main() 里创建的
    M.S.skip = asyncio.Event()
    M.S.hot_q = asyncio.Queue(maxsize=4)

    asyncio.run(main_async())

    print("\n" + "=" * 74)
    print(f"  结果：✅ {len(PASS)} 项通过   ❌ {len(FAIL)} 项失败")
    if FAIL:
        for f in FAIL:
            print("   失败:", f)
    print("=" * 74)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
