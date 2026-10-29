# -*- coding: utf-8 -*-
"""在 OBS 当前场景里创建「MC游戏画面」浏览器源（大肥鱼的第一人称视角）。

用法：
    python tools/obs_add_mc.py            # 创建，默认隐藏
    python tools/obs_add_mc.py --on       # 创建并直接显示
    python tools/obs_add_mc.py --remove   # 删掉这个源

画面来自 prismarine-viewer（http://127.0.0.1:3007/），
机器人 bot.js 不在跑时这个源会是黑屏/断线，先确认 mc 服务在跑。
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

OBS_WS = "ws://127.0.0.1:4455"
NAME = "MC游戏画面"
URL = "http://127.0.0.1:3007/"


async def obs_call(ws, method, params=None, req_id=1):
    msg = {"op": 6, "d": {"requestType": method, "requestId": str(req_id),
                          "requestData": params or {}}}
    await ws.send(json.dumps(msg))
    while True:
        r = json.loads(await ws.recv())
        if r.get("op") == 7 and r["d"].get("requestId") == str(req_id):
            return r["d"]
        if r.get("op") == 9:
            return {"requestStatus": {"result": False, "comment": "event"}}


async def connect(ws_url):
    import websockets
    ws = await websockets.connect(ws_url, open_timeout=5, max_size=2 ** 24)
    hello = json.loads(await ws.recv())
    auth = hello.get("d", {}).get("authentication")
    payload = {"rpcVersion": 1}
    if auth:
        cfgp = os.path.join(os.environ["APPDATA"], "obs-studio",
                            "plugin_config", "obs-websocket", "config.json")
        password = ""
        if os.path.exists(cfgp):
            try:
                password = json.load(open(cfgp, encoding="utf-8")).get("server_password", "")
            except Exception:
                pass
        if not password:
            raise RuntimeError("OBS 开了认证但读不到密码")
        secret = base64.b64encode(
            hashlib.sha256((password + auth["salt"]).encode()).digest()).decode()
        payload["authentication"] = base64.b64encode(
            hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
    await ws.send(json.dumps({"op": 1, "d": payload}))
    if json.loads(await ws.recv()).get("op") != 2:
        raise RuntimeError("OBS 认证失败")
    return ws


def ok(r):
    return bool(r.get("requestStatus", {}).get("result"))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--on", action="store_true", help="创建后立刻显示")
    ap.add_argument("--remove", action="store_true", help="删除该源")
    ap.add_argument("--scene", default="", help="目标场景（默认当前场景）")
    ap.add_argument("--url", default=URL, help="页面地址")
    args = ap.parse_args()

    ws = await connect(OBS_WS)

    scene = args.scene
    if not scene:
        r = await obs_call(ws, "GetCurrentProgramScene", {}, 1)
        scene = r["responseData"]["currentProgramSceneName"]
    print("场景:", scene)

    if args.remove:
        r = await obs_call(ws, "RemoveInput", {"inputName": NAME}, 2)
        print("删除:", "OK" if ok(r) else r["requestStatus"])
        await ws.close()
        return

    r = await obs_call(ws, "GetVideoSettings", {}, 3)
    W = r["responseData"]["baseWidth"]
    H = r["responseData"]["baseHeight"]
    print(f"画布: {W}x{H}")

    # 已存在就先删掉，保证参数是最新的
    lst = await obs_call(ws, "GetInputList", {}, 4)
    if ok(lst) and any(i["inputName"] == NAME for i in lst["responseData"]["inputs"]):
        await obs_call(ws, "RemoveInput", {"inputName": NAME}, 5)
        print("已删除旧的同名源")
        await asyncio.sleep(1.2)  # OBS 删除是异步的，立刻重建会报 601 已存在

    r = await obs_call(ws, "CreateInput", {
        "sceneName": scene,
        "inputName": NAME,
        "inputKind": "browser_source",
        "inputSettings": {
            "url": args.url,
            "width": W,
            "height": H,
            "fps_custom": True,
            "fps": 30,
            "shutdown": False,
            "restart_when_active": True,
        },
        "sceneItemEnabled": False,
    }, 6)
    if not ok(r):
        print("创建失败:", r["requestStatus"])
        await ws.close()
        return
    item_id = r["responseData"]["sceneItemId"]
    print("创建成功，sceneItemId =", item_id)

    await obs_call(ws, "SetSceneItemTransform", {
        "sceneName": scene, "sceneItemId": item_id,
        "sceneItemTransform": {
            "positionX": 0, "positionY": 0,
            "alignment": 5, "boundsAlignment": 0,
            "boundsType": "OBS_BOUNDS_STRETCH",
            "boundsWidth": W, "boundsHeight": H,
        },
    }, 7)

    # 挪到最底层：MC 画面是背景，皮套/字幕要压在它上面
    # ⚠️ v5 协议字段名是 sceneItemIndex（不是 itemIndex），错了会报 300 missing field
    await obs_call(ws, "SetSceneItemIndex", {
        "sceneName": scene, "sceneItemId": item_id, "sceneItemIndex": 0}, 71)
    print("已置于最底层（背景位）")

    if args.on:
        await obs_call(ws, "SetSceneItemEnabled", {
            "sceneName": scene, "sceneItemId": item_id, "sceneItemEnabled": True}, 8)
        print("已显示到画面")
    else:
        print("已创建但保持隐藏 —— 在 OBS 源列表里点「MC游戏画面」的眼睛图标即可打开")

    # 顺手报一下当前所有源，方便核对层级（MC 画面建议放最底层/皮套之下）
    r = await obs_call(ws, "GetSceneItemList", {"sceneName": scene}, 9)
    if ok(r):
        print("\n当前场景源（从下到上）:")
        for it in r["responseData"]["sceneItems"]:
            print(f"  [{ '显' if it['sceneItemEnabled'] else '隐' }] {it['sourceName']}")

    await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
