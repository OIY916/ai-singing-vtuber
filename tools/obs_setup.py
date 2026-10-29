# -*- coding: utf-8 -*-
"""配置 OBS 场景：为抖音直播准备浏览器源 + 音频输入。

OBS 32 用 WebSocket v5 API。需要先在 OBS 里开启 WebSocket 服务：
  工具 → WebSocket 服务器设置 → 启用
本脚本会用 obs-websocket-py 或直接调 WebSocket。
"""
import asyncio
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

OBS_WS = "ws://127.0.0.1:4455"


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


async def main():
    try:
        import websockets
    except ImportError:
        print("需要 websockets: pip install websockets")
        return

    try:
        ws = await websockets.connect(OBS_WS, open_timeout=5)
    except Exception as e:
        print(f"连不上 OBS WebSocket ({OBS_WS}): {e}")
        print("\n请在 OBS 里开启：工具 → WebSocket 服务器设置 → 启用")
        return

    # 握手
    hello = json.loads(await ws.recv())
    print("OBS 版本:", hello.get("d", {}).get("obsWebSocketVersion"))

    # 无需密码直接识别（或读取配置文件里的密码）
    auth_ok = False
    if "authentication" in hello.get("d", {}):
        # 读 OBS 配置文件里的密码
        cfgp = os.path.join(os.environ["APPDATA"], "obs-studio",
                            "plugin_config", "obs-websocket", "config.json")
        password = ""
        if os.path.exists(cfgp):
            try:
                c = json.load(open(cfgp, encoding="utf-8"))
                password = c.get("server_password", "")
            except Exception:
                pass
        if not password:
            print("OBS 开启了认证但读不到密码，请在 OBS 里关闭认证或手动填")
            await ws.close()
            return
        import base64
        import hashlib

        salt = hello["d"]["authentication"]["salt"]
        challenge = hello["d"]["authentication"]["challenge"]
        secret = base64.b64encode(
            hashlib.sha256((password + salt).encode()).digest()
        ).decode()
        auth = base64.b64encode(
            hashlib.sha256((secret + challenge).encode()).digest()
        ).decode()
        await ws.send(json.dumps({"op": 1, "d": {"rpcVersion": 1, "authentication": auth}}))
        resp = json.loads(await ws.recv())
        if resp.get("op") != 2:
            print("认证失败:", resp)
            await ws.close()
            return
        auth_ok = True
    else:
        await ws.send(json.dumps({"op": 1, "d": {"rpcVersion": 1}}))
        await ws.recv()
        auth_ok = True

    print("WebSocket 已连接" + ("（已认证）" if auth_ok else ""))

    # 1) 创建场景
    r = await obs_call(ws, "CreateScene", {"sceneName": "直播"}, 1)
    print("创建场景:", r["requestStatus"])

    # 2) 浏览器源（皮套）
    r = await obs_call(ws, "CreateInput", {
        "sceneName": "直播",
        "inputName": "皮套",
        "inputKind": "browser_source",
        "inputSettings": {
            "url": "http://127.0.0.1:8790/avatar",
            "width": 1080,
            "height": 1080,
            "fps_custom": True,
            "fps": 30,
            "shutdown": False,
            "restart_when_active": True,
            "css": "body{background:transparent!important}",
        },
        "sceneItemEnabled": True,
    }, 2)
    print("创建浏览器源(皮套):", r["requestStatus"])

    # 3) 音频输入采集（从 VB-Cable 拿声音）
    r = await obs_call(ws, "CreateInput", {
        "sceneName": "直播",
        "inputName": "AI语音",
        "inputKind": "wasapi_input_capture",
        "inputSettings": {"device_id": "default"},
        "sceneItemEnabled": True,
    }, 3)
    print("创建音频采集:", r["requestStatus"])

    # 4) 列出所有输入，方便确认设备
    r = await obs_call(ws, "GetInputList", {}, 4)
    if r["requestStatus"].get("result"):
        names = [i["inputName"] for i in r["responseData"]["inputs"]]
        print("当前输入:", names)

    await ws.close()
    print("\n完成。请在 OBS 里把「AI语音」的设备改成 CABLE Output")


if __name__ == "__main__":
    asyncio.run(main())
