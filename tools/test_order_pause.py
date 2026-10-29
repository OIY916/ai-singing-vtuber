# -*- coding: utf-8 -*-
"""验证「暂停点歌 / 恢复点歌」逻辑（离线测试，不动正在直播的服务）。

关键要确认的语义：
    · 暂停点歌后，**新点歌请求被拒绝**
    · 但**已经在队列里的歌照常播完**（不清队列、不打断）
    · 「放原曲」同样被拦（都是往队列加歌）
    · 恢复后能正常接单

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\test_order_pause.py
"""
import asyncio
import sys

sys.path.insert(0, r"E:\qq-bot\stream")
sys.stdout.reconfigure(encoding="utf-8")

import main as M

PASS, FAIL = [], []


def check(desc, got, want):
    ok = (got == want)
    (PASS if ok else FAIL).append(desc)
    print(f"  {'✅' if ok else '❌'} {desc}: got={got!r} want={want!r}")


def clear():
    for q in (M.JOB_Q, M.PLAY_Q):
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break


class FakeSay:
    """记录 say() 被调用情况，替代真实发声"""

    def __init__(self):
        self.calls = []


async def run():
    M.load_cfg()
    M.S.skip = asyncio.Event()
    M.S.hot_q = asyncio.Queue(maxsize=4)

    # 用打桩替换真实的 say / llm_reply，避免真的调 LLM 和 RVC
    said = []

    async def fake_say(name, text, tag="回复", force_voice=None):
        said.append((name, text, tag))

    async def fake_llm(user_text, name=""):
        return None   # 让普通聊天不触发回复，只测点歌路径

    M.say = fake_say
    M.llm_reply = fake_llm

    print("\n【1】暂停点歌 → 新点歌被拒绝")
    clear()
    M.S.order_paused = True
    said.clear()

    await M.on_danmaku("小明", "点歌 晴天")
    check("队列没增加", M.JOB_Q.qsize(), 0)
    check("回了一句提示", len(said), 1)
    if said:
        check("提示是「暂停」相关", "点歌暂时关" in said[0][1], True)
        print(f"     实际回复: {said[0][1]!r}")

    print("\n【2】暂停期间已在队列的歌不受影响")
    clear()
    M.JOB_Q.put_nowait({"kind": "sing", "name": "小红", "kw": "十年"})
    check("队列里原有的歌还在", M.JOB_Q.qsize(), 1)
    await M.on_danmaku("小刚", "点歌 稻香")
    check("新请求没进来", M.JOB_Q.qsize(), 1)
    check("原有的歌没被清掉",
          M.JOB_Q._queue[0]["kw"], "十年")

    print("\n【3】暂停期间「放原曲」也被拦")
    clear()
    said.clear()
    await M.on_danmaku("小明", "放 晴天")
    check("放原曲没排进队列", M.JOB_Q.qsize(), 0)
    check("也回了提示", len(said), 1)
    if said:
        check("提示是「暂停」相关", "点歌暂时关" in said[0][1], True)

    print("\n【4】恢复点歌 → 正常接单")
    clear()
    M.S.order_paused = False
    said.clear()
    M.MAX_DUP_PER_SONG = 2
    await M.on_danmaku("小明", "点歌 晴天")
    check("恢复后点歌成功入队", M.JOB_Q.qsize(), 1)
    if M.JOB_Q.qsize():
        check("入队的是晴天", M.JOB_Q._queue[0]["kw"], "晴天")

    print("\n【5】暂停标志默认关闭")
    # 新建一个 State 看默认值（避免受上面污染）
    fresh = M.State()
    check("默认不暂停", fresh.order_paused, False)

    clear()
    M.S.order_paused = False


def main():
    print("=" * 72)
    print("  「暂停点歌 / 恢复点歌」逻辑测试")
    print("=" * 72)
    asyncio.run(run())
    print("\n" + "=" * 72)
    print(f"  结果：✅ {len(PASS)} 项通过   ❌ {len(FAIL)} 项失败")
    if FAIL:
        for f in FAIL:
            print("   失败:", f)
    print("=" * 72)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
