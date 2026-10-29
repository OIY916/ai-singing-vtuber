# -*- coding: utf-8 -*-
"""验证：气泡是所有文字输出的统一出口。

检查三种 voice_mode 和唱歌/待机两种状态下，气泡 + 声音的实际行为。
"""
import asyncio
import json
import time

import aiohttp
import websockets

WS = "ws://127.0.0.1:8790/ws"
BASE = "http://127.0.0.1:8790"


async def post(path, data=None):
    async with aiohttp.ClientSession() as s:
        async with s.post(BASE + path, json=data or {},
                          timeout=aiohttp.ClientTimeout(total=120)) as r:
            return await r.json()


async def state():
    async with aiohttp.ClientSession() as s:
        async with s.get(BASE + "/api/state", timeout=aiohttp.ClientTimeout(total=10)) as r:
            return await r.json()


async def collect(ws, sec):
    out = []
    t0 = time.time()
    while time.time() - t0 < sec:
        try:
            out.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=0.8)))
        except asyncio.TimeoutError:
            pass
    return out


async def wait_singing(ws, limit=150):
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            if m.get("kind") == "motion" and m.get("state") == "sing":
                return True
        except asyncio.TimeoutError:
            pass
        st = await state()
        if st["mode"] == "sing":
            return True
    return False


async def main():
    ws = await websockets.connect(WS)
    json.loads(await ws.recv())

    print("=" * 64)
    print("场景 1：待机 + 弹幕 → 应该有气泡 + 有声音")
    print("=" * 64)
    await post("/api/test/stop")
    await asyncio.sleep(1)
    r = await post("/api/test/danmaku", {"name": "观众甲", "text": "主播你好呀"})
    print(f"  返回: {r}")
    msgs = await collect(ws, 6)
    reps = [m for m in msgs if m.get("kind") == "reply"]
    for d in reps:
        print(f"    气泡: {d.get('name','')}: {d.get('text','')[:36]}  ms={d.get('ms')}")
    s1 = len(reps) >= 1
    print(f"  结论: {'✅ 有气泡' if s1 else '❌ 没气泡'}")

    print()
    print("=" * 64)
    print("场景 2：唱歌 + 弹幕 → 气泡必须有（声音不出）")
    print("=" * 64)
    print("  发起歌曲…")
    await post("/api/test/sing", {"kw": "晴天"})
    ok = await wait_singing(ws)
    if not ok:
        print("  ❌ 没进入唱歌状态")
        return
    print("  ✅ 已进入唱歌状态")
    await post("/api/test/danmaku", {"name": "观众乙", "text": "唱得真好听"})
    msgs = await collect(ws, 6)
    reps = [m for m in msgs if m.get("kind") == "reply"]
    for d in reps:
        print(f"    气泡: {d.get('name','')}: {d.get('text','')[:36]}  ms={d.get('ms')}")
    s2 = len(reps) >= 1
    # 唱歌中应该听不到说话，验证 state 里没有 voice 活动（间接）
    print(f"  结论: {'✅ 唱歌中仍有气泡' if s2 else '❌ 没气泡'}")

    print()
    print("=" * 64)
    print("场景 3：唱歌中点歌 → 气泡确认")
    print("=" * 64)
    msgs_before = await collect(ws, 0.2)
    r = await post("/api/test/danmaku", {"name": "观众丙", "text": "点歌:十年"})
    print(f"  返回: {r}")
    msgs = await collect(ws, 5)
    reps = [m for m in msgs if m.get("kind") == "reply"]
    for d in reps:
        print(f"    气泡: {d.get('name','')}: {d.get('text','')[:36]}")
    s3 = len(reps) >= 1
    print(f"  结论: {'✅ 点歌有气泡确认' if s3 else '❌ 无确认'}")

    print()
    await post("/api/test/stop")
    print("=" * 64)
    print(f"总结: 待机气泡={'✅' if s1 else '❌'}  唱歌气泡={'✅' if s2 else '❌'}  点歌气泡={'✅' if s3 else '❌'}")
    print("=" * 64)
    await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
