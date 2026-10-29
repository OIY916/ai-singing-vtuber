# -*- coding: utf-8 -*-
"""真实链路验证：唱歌中弹幕只上气泡。

用 /api/test/* 注入，不需要真实开播。
"""
import asyncio
import json
import time

import aiohttp
import websockets

WS = "ws://127.0.0.1:8790/ws"
BASE = "http://127.0.0.1:8790"


async def post(path, data):
    async with aiohttp.ClientSession() as s:
        async with s.post(BASE + path, json=data,
                          timeout=aiohttp.ClientTimeout(total=60)) as r:
            return await r.json()


async def main():
    ws = await websockets.connect(WS)
    json.loads(await ws.recv())
    msgs = []

    async def pump(sec):
        t0 = time.time()
        while time.time() - t0 < sec:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                msgs.append(m)
            except asyncio.TimeoutError:
                pass

    print("=" * 64)
    print("步骤 1：发起一首歌（晴天，已缓存应很快）")
    print("=" * 64)
    r = await post("/api/test/sing", {"kw": "晴天"})
    print("  ->", r)

    # 等进入 sing 状态
    singing = False
    t0 = time.time()
    while time.time() - t0 < 120:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            msgs.append(m)
            if m.get("kind") == "motion":
                print(f"  [motion] {m.get('state')}  {m.get('song','')}")
                if m.get("state") == "sing":
                    singing = True
                    break
            elif m.get("kind") == "log":
                print(f"  [log] {m.get('msg')}")
        except asyncio.TimeoutError:
            pass
    if not singing:
        print("  ❌ 没进入唱歌状态")
        return

    print()
    print("=" * 64)
    print("步骤 2：唱歌中发弹幕（应只上气泡）")
    print("=" * 64)
    msgs.clear()
    for name, text in [
        ("观众甲", "这首唱得真好听"),
    ]:
        r = await post("/api/test/danmaku", {"name": name, "text": text})
        print(f"  POST 返回: {r}")
    await pump(6)

    reps = [m for m in msgs if m.get("kind") == "reply"]
    print(f"  收到 reply {len(reps)} 条:")
    for d in reps:
        print(f"    bubble_only={d.get('bubble_only')} ms={d.get('ms')} "
              f"{d.get('name','')}: {d.get('text','')[:40]}")
    ok = reps and all(d.get("bubble_only") for d in reps)
    print(f"  结论: {'✅ 全部气泡，未出声' if ok else '❌ 不符预期'}")

    print()
    print("=" * 64)
    print("步骤 3：唱歌中送礼物（应只上气泡）")
    print("=" * 64)
    msgs.clear()
    r = await post("/api/test/gift", {"name": "观众乙", "gift": "小花花", "num": 3})
    print(f"  POST 返回: {r}")
    await pump(4)
    reps = [m for m in msgs if m.get("kind") == "reply"]
    print(f"  收到 reply {len(reps)} 条:")
    for d in reps:
        print(f"    bubble_only={d.get('bubble_only')} {d.get('text','')[:40]}")
    ok2 = reps and all(d.get("bubble_only") for d in reps)
    print(f"  结论: {'✅ 全部气泡' if ok2 else '❌ 不符预期'}")

    print()
    print("=" * 64)
    print("步骤 4：停止播放")
    print("=" * 64)
    r = await post("/api/test/stop", {})
    print("  ->", r)
    await asyncio.sleep(1)

    print()
    print("=" * 64)
    print(f"总结: 唱歌中弹幕={'✅' if ok else '❌'}  唱歌中礼物={'✅' if ok2 else '❌'}")
    print("=" * 64)
    await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
