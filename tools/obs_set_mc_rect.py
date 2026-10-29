# -*- coding: utf-8 -*-
"""调整 OBS 里 MC 画面和物品栏叠加层的大小/位置。

用法：
    python tools/obs_set_mc_rect.py                      # 默认 1280x720 居中
    python tools/obs_set_mc_rect.py --w 1600 --h 900
    python tools/obs_set_mc_rect.py --w 960 --h 540 --x 940 --y 520   # 右下角小窗
    python tools/obs_set_mc_rect.py --full               # 恢复铺满 1920x1080

同时调整「MC第一人称机位」和「大肥鱼物品栏」两个源，保证 HUD 跟着画面走。
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
SOURCES = ["MC第一人称机位", "大肥鱼物品栏"]


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
    ap.add_argument("--w", type=int, default=1280)
    ap.add_argument("--h", type=int, default=720)
    ap.add_argument("--x", type=int, default=-1, help="左上角 X（默认居中）")
    ap.add_argument("--y", type=int, default=-1, help="左上角 Y（默认居中）")
    ap.add_argument("--full", action="store_true", help="恢复铺满画布")
    args = ap.parse_args()

    ws = await connect(OBS_WS)
    r = await obs_call(ws, "GetCurrentProgramScene", {}, 1)
    scene = r["responseData"]["currentProgramSceneName"]
    r = await obs_call(ws, "GetVideoSettings", {}, 2)
    W = r["responseData"]["baseWidth"]
    H = r["responseData"]["baseHeight"]

    if args.full:
        w, h, x, y = W, H, 0, 0
    else:
        w, h = args.w, args.h
        x = args.x if args.x >= 0 else (W - w) // 2
        y = args.y if args.y >= 0 else (H - h) // 2

    lst = await obs_call(ws, "GetSceneItemList", {"sceneName": scene}, 3)
    ids = {it["sourceName"]: it["sceneItemId"] for it in lst["responseData"]["sceneItems"]}

    for name in SOURCES:
        if name not in ids:
            print("跳过（不存在）:", name)
            continue
        r = await obs_call(ws, "SetSceneItemTransform", {
            "sceneName": scene, "sceneItemId": ids[name],
            "sceneItemTransform": {
                "positionX": x, "positionY": y,
                "alignment": 5, "boundsAlignment": 0,
                "boundsType": "OBS_BOUNDS_STRETCH",
                "boundsWidth": w, "boundsHeight": h,
            },
        }, 10)
        print(f"{name}: {w}x{h} @ ({x},{y})", "OK" if ok(r) else r["requestStatus"])

    await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
