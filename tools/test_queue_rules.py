# -*- coding: utf-8 -*-
"""验证点歌去重、黑名单、队列全量显示（不启动服务，纯逻辑测试）。

背景（2026-10-27）：
    · 观众把同一首歌点很多次，队列被同一首歌霸屏 → 方案 C（同曲最多 2 首）
    · 有些歌不适合唱/翻唱总失败 → 黑名单
    · 后端只返回前 6 个队列项，队列 19 首时界面看不全 → 改为全部

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\test_queue_rules.py
"""
import sys

sys.path.insert(0, r"E:\qq-bot\stream")
sys.stdout.reconfigure(encoding="utf-8")

import main as M

M.load_cfg()

PASS = []
FAIL = []


def check(desc, got, want):
    ok = (got == want)
    (PASS if ok else FAIL).append(desc)
    print(f"  {'✅' if ok else '❌'} {desc}: got={got!r} want={want!r}")


def clear_queue():
    for q in (M.JOB_Q, M.PLAY_Q):
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break
    M.S.current_song = None


def main():
    print("=" * 74)
    print("  点歌规则测试：去重（方案C）/ 黑名单 / 队列全量显示")
    print("=" * 74)

    # ---------- 1) 去重 ----------
    print("\n【1】去重：同一首歌最多排 2 次")
    clear_queue()
    M.JOB_Q.put_nowait({"kind": "sing", "name": "小明", "kw": "晴天"})
    M.JOB_Q.put_nowait({"kind": "sing", "name": "小红", "kw": "晴天"})
    M.JOB_Q.put_nowait({"kind": "sing", "name": "小刚", "kw": "稻香"})
    # PLAY_Q 里的是完整标题（网易云返回的），要能认出「晴天」
    M.PLAY_Q.put_nowait({"title": "十年 - 陈奕迅", "name": "小李", "raw": True})
    M.PLAY_Q.put_nowait({"title": "晴天 (女声版) - GYBeat", "name": "小美", "raw": False})
    M.S.current_song = {"title": "告白气球 - 周杰伦", "by": "x"}

    check("晴天 已在队列(2排队+1播放)=3", M.count_in_queue("晴天"), 3)
    check("稻香 只有 1", M.count_in_queue("稻香"), 1)
    check("十年(原曲) 只有 1", M.count_in_queue("十年"), 1)
    check("告白气球 正在播算 1", M.count_in_queue("告白气球"), 1)
    check("没点过的歌 = 0", M.count_in_queue("没点过的歌"), 0)
    check("晴天(Live) 是另一首 = 0", M.count_in_queue("晴天 (Live)"), 0)

    # 归一化：空格/大小写/标点不该影响计数
    check("「晴天 」带空格仍算 3", M.count_in_queue("晴天 "), 3)

    # ---------- 2) 黑名单 ----------
    print("\n【2】黑名单")
    M.CFG["song_blacklist"] = ["告白气球", "test"]
    check("告白气球 命中", M.is_blacklisted("告白气球"), True)
    check("告白气球 (Live版) 也命中（子串）", M.is_blacklisted("告白气球 (Live版)"), True)
    check("TEST 测试歌命中（大小写无关）", M.is_blacklisted("TEST - 邓典果DDG"), True)
    check("晴天 不命中", M.is_blacklisted("晴天"), False)
    check("稻香 不命中", M.is_blacklisted("稻香"), False)

    # 内置黑名单即使 config 为空也生效
    M.CFG["song_blacklist"] = []
    check("config 清空后内置 test 仍生效", M.is_blacklisted("test"), True)

    # ---------- 3) 队列全量显示 ----------
    print("\n【3】队列显示：不再截断成 6 条")
    clear_queue()
    # ⚠️ JOB_Q 有 maxsize=10，塞不了 12 个（会 QueueFull）。
    #    这里塞 9 个来验证「>6 也能全返回」，正好覆盖旧版截断问题。
    n = 9
    for i in range(n):
        M.JOB_Q.put_nowait({"kind": "sing", "name": f"观众{i}", "kw": f"歌{i}"})
    items = M._queue_preview()
    check(f"塞 {n} 首返回 {n} 首（旧版只返回 6）", len(items), n)
    check("第一首是歌0", items[0]["kw"], "歌0")
    # 显式限制仍然可用（兼容旧调用）
    check("传 n=3 只返 3 首", len(M._queue_preview(3)), 3)

    clear_queue()

    print("\n" + "=" * 74)
    print(f"  结果：✅ {len(PASS)} 项通过   ❌ {len(FAIL)} 项失败")
    if FAIL:
        for f in FAIL:
            print("   失败:", f)
    print("=" * 74)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
