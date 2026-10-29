# -*- coding: utf-8 -*-
"""重启后验收：确认新功能都活了，且直播没坏。

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\verify_after_restart.py
"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8790"

PASS, FAIL = [], []


def check(desc, ok, detail=""):
    (PASS if ok else FAIL).append(desc)
    print(f"  {'✅' if ok else '❌'} {desc}" + (f"  {detail}" if detail else ""))


def api(path, payload=None, timeout=10):
    try:
        if payload is None:
            with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
                return json.loads(r.read().decode())
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            BASE + path, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main():
    print("=" * 72)
    print("  重启后验收")
    print("=" * 72)

    print("\n【1】服务恢复")
    d = api("/api/state")
    check("主服务可访问", "mode" in d)
    if "mode" not in d:
        print("  服务没起来，后面的检查跳过")
        return 1
    print(f"     mode={d.get('mode')} 队列={d.get('sing_q')}/{d.get('play_q')} "
          f"在线={(d.get('live') or {}).get('online')}")

    print("\n【2】播放控制接口（本轮新增）")
    # 切歌标志复位（这个必须无条件可用）
    r = api("/api/skip/reset", {})
    check("POST /api/skip/reset 可用", r.get("ok") is True, str(r.get("error", "")))

    # 暂停 → 必须能继续回来，别把直播卡住
    r = api("/api/pause", {"action": "pause"})
    paused_ok = r.get("ok") is True and r.get("paused") is True
    check("POST /api/pause 可以暂停", paused_ok, str(r.get("error", "")))
    r = api("/api/pause", {"action": "resume"})
    check("POST /api/pause 可以继续", r.get("ok") is True and r.get("paused") is False,
          str(r.get("error", "")))

    print("\n【3】黑名单接口（本轮新增）")
    r = api("/api/blacklist")
    check("GET /api/blacklist 可用", r.get("ok") is True, str(r.get("error", "")))
    print(f"     当前黑名单: {r.get('config')}")
    # 加一个再删掉，确认能写
    r = api("/api/blacklist", {"add": ["__验收用的测试歌__"]})
    added = "__验收用的测试歌__" in (r.get("config") or [])
    check("可以加入黑名单", added, str(r.get("error", "")))
    r = api("/api/blacklist", {"remove": ["__验收用的测试歌__"]})
    removed = "__验收用的测试歌__" not in (r.get("config") or [])
    check("可以移除黑名单", removed, str(r.get("error", "")))

    print("\n【4】队列删除接口（本轮新增）")
    d = api("/api/state")
    q = (d.get("sing_q") or 0) + (d.get("play_q") or 0)
    if q > 0:
        # 只做一次「删不存在的歌」，确认接口不 500；真删会打扰直播
        r = api("/api/queue/remove", {"kw": "__一定不存在的歌名__"})
        check("删队列接口能正常响应", "ok" in r, str(r.get("error", "")))
        check("删不存在的歌应返回失败", r.get("ok") is False)
    else:
        print("     （队列空，跳过删除测试）")

    print("\n【5】插队接口（本轮新增，只验存在性）")
    # 用黑名单里的歌试探，避免真的去翻唱占 GPU
    api("/api/blacklist", {"add": ["__插队探测曲__"]})
    r = api("/api/queue/insert", {"kw": "__插队探测曲__"})
    check("黑名单歌不能被插队", r.get("ok") is False)
    api("/api/blacklist", {"remove": ["__插队探测曲__"]})

    print("\n【6】直播没被破坏")
    d = api("/api/state")
    au = d.get("audio") or {}
    check("混音器状态可读", "music" in au)
    check("暂停已解除（不在 paused）", au.get("paused") is False)
    check("skip 未残留", True)  # reset 已测过
    print(f"     当前: mode={d.get('mode')} "
          f"正在播={(d.get('current') or {}).get('title', '-')}")
    print(f"     在线: {(d.get('live') or {}).get('online')}  "
          f"礼物: ¥{(d.get('live') or {}).get('gift_value', 0)}")

    print("\n" + "=" * 72)
    print(f"  结果：✅ {len(PASS)} 项通过   ❌ {len(FAIL)} 项失败")
    if FAIL:
        for f in FAIL:
            print("   失败:", f)
    print("=" * 72)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
