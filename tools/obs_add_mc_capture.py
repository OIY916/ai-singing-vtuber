# -*- coding: utf-8 -*-
"""在 OBS 当前场景里创建「MC游戏画面」= 真客户端窗口采集（game_capture）。

用法：
    python tools/obs_add_mc_capture.py            # 创建，隐藏
    python tools/obs_add_mc_capture.py --on       # 创建并显示
    python tools/obs_add_mc_capture.py --shot E:/qq-bot/tmp/mc_cap.png   # 顺便截图验证
    python tools/obs_add_mc_capture.py --remove   # 删除

采集的是 Minecraft 客户端窗口（不是网页），走 OBS 游戏采集，流畅度=客户端原生。
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
    global NAME
    ap = argparse.ArgumentParser()
    ap.add_argument("--on", action="store_true", help="创建后立刻显示")
    ap.add_argument("--remove", action="store_true", help="删除该源")
    ap.add_argument("--shot", default="", help="截图保存路径（验证采集）")
    ap.add_argument("--scene", default="", help="目标场景（默认当前场景）")
    ap.add_argument("--name", default=NAME, help="源名称")
    ap.add_argument("--contains", default="minecraft", help="窗口标题包含的关键字（区分 Forge/原版）")
    ap.add_argument("--shot-name", default="", help="截图使用的源名（默认同 --name）")
    args = ap.parse_args()
    NAME = args.name

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

    # 删旧的同名源
    lst = await obs_call(ws, "GetInputList", {}, 4)
    if ok(lst) and any(i["inputName"] == NAME for i in lst["responseData"]["inputs"]):
        await obs_call(ws, "RemoveInput", {"inputName": NAME}, 5)
        print("已删除旧的同名源")
        await asyncio.sleep(1.2)  # OBS 删除是异步的，立刻重建会报 601

    # 先建空源，才能枚举可采集窗口
    r = await obs_call(ws, "CreateInput", {
        "sceneName": scene, "inputName": NAME, "inputKind": "game_capture",
        "inputSettings": {}, "sceneItemEnabled": False,
    }, 6)
    if not ok(r):
        print("创建失败:", r["requestStatus"])
        await ws.close()
        return
    item_id = r["responseData"]["sceneItemId"]
    print("创建成功，sceneItemId =", item_id)

    # 枚举窗口，找 Minecraft
    r = await obs_call(ws, "GetInputPropertiesListPropertyItems", {
        "inputName": NAME, "propertyName": "window"}, 7)
    if not ok(r):
        print("枚举窗口失败:", r["requestStatus"])
        await ws.close()
        return
    cand = None
    for it in r["responseData"]["propertyItems"]:
        name = f"{it.get('itemName', '')} {it.get('itemValue', '')}"
        if args.contains.lower() in name.lower():
            cand = it
            break
    if not cand:
        print("⚠️ 没找到 Minecraft 窗口，客户端是不是没启动？")
        await ws.close()
        return
    print("找到窗口:", cand.get("itemName"))
    win_val = cand["itemValue"]

    r = await obs_call(ws, "SetInputSettings", {
        "inputName": NAME,
        "inputSettings": {"window": win_val, "capture_mode": "window", "method": 0},
        "overlay": True,
    }, 8)
    print("设置采集窗口:", "OK" if ok(r) else r["requestStatus"])

    # 铺满画布
    r = await obs_call(ws, "GetVideoSettings", {}, 9)
    W = r["responseData"]["baseWidth"]
    H = r["responseData"]["baseHeight"]
    await obs_call(ws, "SetSceneItemTransform", {
        "sceneName": scene, "sceneItemId": item_id,
        "sceneItemTransform": {"positionX": 0, "positionY": 0,
                               "alignment": 5, "boundsAlignment": 0,
                               "boundsType": "OBS_BOUNDS_STRETCH",
                               "boundsWidth": W, "boundsHeight": H},
    }, 10)

    # 置底当背景（字段名是 sceneItemIndex，别写 itemIndex）
    await obs_call(ws, "SetSceneItemIndex", {
        "sceneName": scene, "sceneItemId": item_id, "sceneItemIndex": 0}, 11)

    if args.on:
        await obs_call(ws, "SetSceneItemEnabled", {
            "sceneName": scene, "sceneItemId": item_id, "sceneItemEnabled": True}, 12)
        print("已显示到画面")

    if args.shot:
        os.makedirs(os.path.dirname(args.shot), exist_ok=True)
        r = await obs_call(ws, "SaveSourceScreenshot", {
            "sourceName": NAME, "imageFormat": "png", "imageFilePath": args.shot}, 13)
        print("截图:", "OK" if ok(r) else r["requestStatus"], "→", args.shot)

    r = await obs_call(ws, "GetSceneItemList", {"sceneName": scene}, 14)
    if ok(r):
        print("\n当前场景源（从下到上）:")
        for it in r["responseData"]["sceneItems"]:
            print(f"  [{ '显' if it['sceneItemEnabled'] else '隐' }] {it['sourceName']}")

    await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
