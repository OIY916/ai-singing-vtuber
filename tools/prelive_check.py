# -*- coding: utf-8 -*-
"""开播前一键自检 —— 把所有「能不能开播」的条件逐条验一遍。

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\prelive_check.py

设计原则：
    每一条都给「通过 / 警告 / 失败」，警告不阻止开播但要知道，
    失败会直接告诉你哪里不通。绝不笼统说「可以开播了」。
"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"E:\qq-bot\stream")

BASE = "http://127.0.0.1:8790"
RESULTS = []


def add(level, item, detail):
    RESULTS.append((level, item, detail))
    icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}[level]
    print(f"  {icon} {item}")
    if detail:
        for line in str(detail).split("\n"):
            print(f"      {line}")


def get(path, timeout=10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(path, payload=None, timeout=30):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def check_services():
    print("\n【1】服务是否都在跑")
    ports = {1088: "弹幕采集 douyinLive", 8899: "RVC 翻唱", 8790: "直播主服务"}
    import socket
    for port, name in ports.items():
        s = socket.socket()
        s.settimeout(1.5)
        try:
            s.connect(("127.0.0.1", port))
            add("ok", f"{name} (:{port})", "")
        except Exception:
            add("fail", f"{name} (:{port})", "端口不通，先跑 启动直播.ps1")
        finally:
            s.close()


def check_rvc():
    print("\n【2】RVC 是否可用")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8899/health", timeout=10) as r:
            d = json.loads(r.read().decode())
        models = d.get("models") or []
        add("ok", "RVC /health", f"模型 {len(models)} 个")
        import os
        cfg = os.path.join(r"E:\qq-bot\stream", "config.json")
        c = json.load(open(cfg, encoding="utf-8"))
        for key, label in (("tts_model", "说话"), ("sing_model", "唱歌")):
            m = c.get(key)
            if m in models:
                add("ok", f"{label}音色 {m}", "")
            else:
                add("fail", f"{label}音色 {m}", "RVC 模型列表里没有这个文件！")
    except Exception as e:
        add("fail", "RVC /health", f"{type(e).__name__}: {e}")


def check_audio():
    print("\n【3】音频输出链路")
    try:
        import sounddevice as sd
        # ⚠️ 必须按混音器的真实选设备逻辑来报，不要随便取第一个命中的。
        # CABLE Input 在 MME/DirectSound/WASAPI 上各有一个同名设备
        # （[12]=MME 44100/Mono混音, [29]=DirectSound, [37]=WASAPI 48000/2ch），
        # 混音器优先级是 WASAPI > DirectSound > 其他，实际用的是 37。
        # 之前这里报 [12] MME，跟真实链路不符，容易误导排查。
        import sys as _sys
        _sys.path.insert(0, r"E:\qq-bot\stream")
        from mixer import Mixer
        found = Mixer.find_device()
        if found:
            _, idx, name, host = found
            d = sd.query_devices()[idx]
            add("ok", "混音器将使用的输出设备",
                f"[{idx}] {name} [{host}]  {d['max_output_channels']}ch "
                f"{int(d['default_samplerate'])}Hz")
            if "WASAPI" not in host:
                add("warn", "音频设备没走 WASAPI",
                    f"当前选到 {host}，正常应该是 Windows WASAPI")
        else:
            add("fail", "VB-Cable 输出设备",
                "找不到 CABLE Input，混音器没法出声。检查 VB-Cable 是否安装")
    except Exception as e:
        add("warn", "VB-Cable 检查", f"无法枚举设备: {e}")

    try:
        st = get("/api/state")
        au = st.get("audio") or {}
        add("ok", "混音器状态", f"music={au.get('music')} level={au.get('level')}")
    except Exception as e:
        add("fail", "读取 /api/state", str(e))


def check_douyin():
    print("\n【4】抖音连接（最关键）")
    import asyncio
    try:
        import websockets
    except ImportError:
        add("warn", "websockets 未安装", "跳过抖音连接检查")
        return

    async def probe():
        url = "ws://127.0.0.1:1088/ws/473385498459"
        async with websockets.connect(url, open_timeout=8) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            return json.loads(raw)

    try:
        d = asyncio.run(probe())
        code = d.get("code", "")
        status = d.get("status", "")
        if d.get("live"):
            add("ok", "直播间状态", "已开播，能收到真实弹幕")
        else:
            add("warn", "直播间当前未开播",
                f"code={code} status={status}\n"
                "这是正常的——你还没在抖音点开播。\n"
                "开播后 douyinLive 会自动切换成收弹幕模式（不用重启）。")
    except Exception as e:
        add("fail", "连 douyinLive", f"{type(e).__name__}: {e}")


def check_obs():
    print("\n【5】OBS 场景")
    try:
        import obsws_python as obsws
        cli = obsws.ReqClient(host="127.0.0.1", port=4455,
                              password="ezz664hcTWsXaAjH", timeout=8)
        scene = cli.get_current_program_scene().current_program_scene_name
        items = cli.get_scene_item_list(scene).scene_items
        names = {it["sourceName"]: it for it in items}
        add("ok", "OBS 已连接", f"场景「{scene}」")

        avatar = next((n for n in names if "皮套" in n), None)
        if not avatar:
            add("fail", "皮套浏览器源", "场景里没有「皮套」源")
        else:
            st = cli.get_input_settings(avatar).input_settings
            url, w, h = st.get("url"), st.get("width"), st.get("height")
            if url and url.endswith("/avatar") and w == 1920 and h == 1080:
                add("ok", "皮套源设置", f"{url}  {w}×{h}")
            else:
                add("warn", "皮套源设置不符",
                    f"当前 {url}  {w}×{h}\n应为 http://127.0.0.1:8790/avatar  1920×1080\n"
                    "可跑 tools/fix_obs_source.py 修正")
            if names[avatar]["sceneItemEnabled"]:
                add("ok", "皮套源可见", "")
            else:
                add("fail", "皮套源被隐藏", "场景里这个源是关闭状态，观众看不到画面")

        voice = next((n for n in names if "AI" in n and "语音" in n), None)
        if voice:
            st = cli.get_input_settings(voice).input_settings
            add("ok", f"音频源「{voice}」", f"device_id={str(st.get('device_id'))[:26]}…")
            add("warn", "「AI语音」建议静音",
                "声音已经走直播伴侣的麦克风(=CABLE Output)，\n"
                "OBS 这边再采集一次会让直播间听到两重声音。\n"
                "在 OBS 混音器里把这个源点静音即可（不影响直播伴侣）。")
        else:
            add("warn", "OBS 里没有 AI语音 源", "如果不用 OBS 出声，可以忽略")
    except Exception as e:
        add("warn", "OBS 检查失败", f"{type(e).__name__}: {e}\n（OBS 没开或 WebSocket 没启用）")


def check_panel_data():
    print("\n【6】面板数据是否干净（防止假数据上直播）")
    try:
        st = get("/api/state")
        live = st.get("live") or {}
        dirty = []
        if live.get("online") or live.get("total_viewers"):
            dirty.append(f"在线/累计观看 = {live.get('online')}/{live.get('total_viewers')}")
        if (live.get("gift_value") or 0) > 0:
            dirty.append(f"礼物面值 = ¥{live.get('gift_value')}")
        if live.get("gift_today"):
            dirty.append(f"今日点歌 = {live.get('gift_today')}")
        if (st.get("stats") or {}).get("songs"):
            dirty.append(f"已唱 = {st['stats']['songs']}")

        if dirty:
            add("warn", "面板里有残留数据（很可能是自测注入的假数据）",
                "\n".join(dirty) + "\n开播前请清空：POST /api/test/reset")
        else:
            add("ok", "面板数据干净", "在线/收益/点歌都是 0，不会把假数据播出去")
    except Exception as e:
        add("fail", "读取面板数据", str(e))


def check_state():
    print("\n【7】主服务运行状态")
    try:
        st = get("/api/state")
        add("ok", "当前模式", st.get("mode"))
        add("ok", "直播间号", st.get("room"))
        add("ok", "网页客户端连接数", f"{st.get('clients')} 个"
            + ("（OBS 已连上）" if (st.get("clients") or 0) >= 1 else "（OBS 可能没连上）"))
        if st.get("sing_q") or st.get("play_q"):
            add("warn", "还有歌在队列里",
                f"待翻唱 {st.get('sing_q')} / 待播 {st.get('play_q')}")
    except Exception as e:
        add("fail", "读取 /api/state", str(e))


def main():
    print("=" * 70)
    print("  大肥鱼直播 —— 开播前自检")
    print("=" * 70)

    check_services()
    check_rvc()
    check_audio()
    check_douyin()
    check_obs()
    check_panel_data()
    check_state()

    fails = [r for r in RESULTS if r[0] == "fail"]
    warns = [r for r in RESULTS if r[0] == "warn"]

    print("\n" + "=" * 70)
    print(f"  结果：✅ {len([r for r in RESULTS if r[0]=='ok'])} 项通过  "
          f"⚠️  {len(warns)} 项警告  ❌ {len(fails)} 项失败")
    print("=" * 70)

    if fails:
        print("\n❌ 有阻塞项，先修好再开播：")
        for _, item, detail in fails:
            print(f"  · {item}")
            if detail:
                print(f"      {detail.splitlines()[0]}")
        return 1

    if warns:
        print("\n⚠️ 警告项（不阻塞开播，但建议看一眼）：")
        for _, item, detail in warns:
            print(f"  · {item}")

    print("\n" + "-" * 70)
    print("剩下的必须你手动确认（脚本查不到）：")
    print("  1. 直播伴侣「主麦克风」= CABLE Output")
    print("  2. 直播伴侣「摄像头」= OBS Virtual Camera")
    print("  3. 抖音开播页面勾选「AI 生成」内容声明")
    print("  4. 先在直播间点「开始直播」，再观察本页面数据有没有动")
    print("-" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
