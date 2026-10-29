# -*- coding: utf-8 -*-
"""通过 OBS WebSocket 把「AI语音」输入设备改成 VB-Cable 的输出端。"""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

OBS_WS = "ws://127.0.0.1:4455"
import websockets


class Obs:
    def __init__(self):
        self.ws = None
        self.n = 0

    async def connect(self):
        self.ws = await websockets.connect(OBS_WS, open_timeout=5)
        hello = json.loads(await self.ws.recv())
        await self.ws.send(json.dumps({"op": 1, "d": {"rpcVersion": 1}}))
        await self.ws.recv()
        return hello

    async def call(self, method, params=None):
        self.n += 1
        rid = str(self.n)
        await self.ws.send(json.dumps({
            "op": 6,
            "d": {"requestType": method, "requestId": rid, "requestData": params or {}},
        }))
        while True:
            r = json.loads(await self.ws.recv())
            if r.get("op") == 7 and r["d"].get("requestId") == rid:
                return r["d"]
            if r.get("op") in (8, 9):
                continue

    async def close(self):
        if self.ws:
            await self.ws.close()


async def main():
    o = Obs()
    await o.connect()

    # 1) 列出所有音频设备
    r = await o.call("GetInputPropertiesListPropertyItems",
                     {"inputName": "AI语音", "propertyName": "device_id"})
    devs = r.get("responseData", {}).get("propertyItems", [])
    print("AI语音 可用设备:")
    target = None
    for d in devs:
        mark = ""
        if "CABLE Output" in d.get("itemName", ""):
            mark = "  ← 目标"
            if target is None:
                target = d["itemValue"]
        print(f"  [{d.get('itemValue')}] {d.get('itemName')}{mark}")

    if target is None:
        print("\n❌ 没找到 CABLE Output 设备")
        await o.close()
        return

    # 2) 设置设备
    r = await o.call("SetInputSettings",
                     {"inputName": "AI语音", "inputSettings": {"device_id": target}})
    print("\n设置设备:", r["requestStatus"])

    # 3) 确认
    r = await o.call("GetInputSettings", {"inputName": "AI语音"})
    print("当前设置:", r["responseData"].get("inputSettings"))

    # 4) 看音频是否静音
    r = await o.call("GetInputMute", {"inputName": "AI语音"})
    print("是否静音:", r["responseData"].get("inputMuted"))

    await o.close()


if __name__ == "__main__":
    asyncio.run(main())
