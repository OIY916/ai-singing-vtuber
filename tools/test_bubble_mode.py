# -*- coding: utf-8 -*-
"""测试：唱歌期间弹幕是否只上气泡、不出声、不阻塞。

不打真实 RVC（避免 GPU 争用），用假 mixer + 假 rvc_speak 验证调度逻辑。
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "stream")))
import main as M

LOG = []


class FakeMixer:
    """假混音器：playing_music 可手动控制"""

    def __init__(self):
        self.playing_music = False
        self.playing_voice = False
        self.voice_calls = []
        self.music_calls = []
        self.duck = 0.3

    def play_voice(self, pcm, sr, on_end=None):
        self.voice_calls.append(len(pcm))
        self.playing_voice = True
        if on_end:
            on_end()

    def play_music(self, pcm, sr, gain=None, on_end=None):
        self.music_calls.append(len(pcm))
        self.playing_music = True
        # 假装 2 秒后播完
        import threading

        def _end():
            time.sleep(2)
            self.playing_music = False
            if on_end:
                on_end()

        threading.Thread(target=_end, daemon=True).start()

    def stop_music(self):
        self.playing_music = False

    def set_duck(self, d):
        self.duck = d

    def ensure_stream(self):
        return True

    def close(self):
        pass


async def main():
    M.load_cfg()
    # 测试要确定性：概率拉满，冷却归零
    M.CFG["reply_prob"] = 1.0
    M.CFG["cooldown"] = 0
    fake = FakeMixer()
    M.S.mixer = fake

    # 假 RVC：记录调用
    calls = {"speak": 0, "sing": 0}

    async def fake_rvc_speak(text, model=None):
        calls["speak"] += 1
        LOG.append(("rvc_speak", text))
        return b"x" * 1000

    async def fake_rvc_sing(kw, model=None):
        calls["sing"] += 1
        return b"y" * 2000, f"《{kw}》测试版", ""

    def fake_decode(data):
        import numpy as np

        return np.zeros((16000, 2), dtype="float32"), 16000

    M.rvc_speak = fake_rvc_speak
    M.rvc_sing = fake_rvc_sing
    M.decode_mp3 = fake_decode

    # 接管 push，记录推送
    pushes = []

    async def fake_push(kind, data):
        pushes.append((kind, data))

    M.S.push = fake_push

    for fn in (M.voice_worker, M.sing_worker, M.play_worker):
        asyncio.create_task(fn())

    def replies():
        return [d for k, d in pushes if k == "reply"]

    print("=" * 62)
    print("场景 A：待机时发弹幕（应该出声）")
    print("=" * 62)
    pushes.clear()
    calls["speak"] = 0
    fake.playing_music = False
    await M.on_danmaku("观众甲", "主播你好可爱")
    await asyncio.sleep(1.5)   # 等 LLM + speak 走完
    r = replies()
    print(f"  RVC /speak 调用次数: {calls['speak']}  (期望 >=1)")
    print(f"  气泡推送: {len(r)} 条")
    for d in r:
        print(f"    bubble_only={d.get('bubble_only')} ms={d.get('ms')} text={d.get('text')[:20]}")
    ok_a = calls["speak"] > 0
    print(f"  结论: {'✅ 出声了' if ok_a else '❌ 没走 RVC'}")

    print()
    print("=" * 62)
    print("场景 B：唱歌中发弹幕（应该只上气泡、不出声）")
    print("=" * 62)
    pushes.clear()
    calls["speak"] = 0
    fake.playing_music = True  # 模拟正在唱歌

    t0 = time.time()
    await M.on_danmaku("观众乙", "这首歌唱得真好听")
    dt = time.time() - t0
    await asyncio.sleep(1.5)

    r = replies()
    print(f"  弹幕处理耗时: {dt*1000:.1f} ms  (期望 < 3000ms，不阻塞)")
    print(f"  RVC /speak 调用次数: {calls['speak']}  (期望 0)")
    print(f"  气泡推送: {len(r)} 条")
    for d in r:
        print(f"    bubble_only={d.get('bubble_only')} ms={d.get('ms')} text={d.get('text')[:24]}")
    ok_b = calls["speak"] == 0 and len(r) == 1 and r[0].get("bubble_only")
    print(f"  结论: {'✅ 只上气泡，未调用 RVC' if ok_b else '❌ 不符预期'}")

    print()
    print("=" * 62)
    print("场景 C：唱歌中点歌（应该进气队列 + 气泡确认）")
    print("=" * 62)
    pushes.clear()
    calls["speak"] = 0
    fake.playing_music = True
    # 先把 worker 停掉，避免 sing_worker 立刻把这首歌翻走影响断言
    qsize_before = M.SING_Q.qsize()
    await M.on_danmaku("观众丙", "点歌:晴天")
    # 只检查「入队」和「气泡」两个点，不等 worker 消费
    r = replies()
    qsize_after = M.SING_Q.qsize()
    print(f"  待翻唱队列: {qsize_before} -> {qsize_after}  (期望 +1)")
    print(f"  气泡: {[d.get('text')[:26] for d in r]}")
    ok_c = qsize_after == qsize_before + 1 and len(r) == 1 and r[0].get("bubble_only")
    print(f"  结论: {'✅ 入队且只上气泡' if ok_c else '❌ 不符预期'}")

    print()
    # ⚠️ 场景 C 的歌可能还在 sing_worker 里被处理（它会调 rvc_speak 说安抚语），
    #    会污染后面的计数。等它跑完再继续。
    for _ in range(40):
        if M.SING_Q.qsize() == 0 and M.PLAY_Q.qsize() == 0:
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(1.0)   # 再等 worker 停下来

    print()
    print("=" * 62)
    print("场景 D：唱歌中送礼物（应该只上气泡）")
    print("=" * 62)
    # ⚠️ 场景 C 的歌由 worker 播放，2 秒后 fake 会把 playing_music 复位，
    #    所以这里必须显式再置 True，否则测的是「待机送礼物」而不是「唱歌中送礼物」
    pushes.clear()
    calls["speak"] = 0
    fake.playing_music = True
    pre = M.is_singing()
    await M.on_gift("观众丁", "小花花", 3)
    await asyncio.sleep(0.3)
    r = replies()
    print(f"  [前置] is_singing() = {pre}  (期望 True)")
    print(f"  RVC /speak 调用: {calls['speak']}  (期望 0)")
    print(f"  气泡: {[d.get('text')[:26] for d in r]}")
    ok_d = pre and calls["speak"] == 0 and len(r) == 1
    print(f"  结论: {'✅ 只上气泡' if ok_d else '❌ 不符预期'}")

    print()
    print("=" * 62)
    all_ok = (calls is not None) and ok_b and ok_c and ok_d
    print(f"总计: {'✅ 全部通过' if all_ok else '❌ 有失败项'}")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
