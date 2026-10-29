# -*- coding: utf-8 -*-
"""验证观众记忆库：身份令牌 / 记录 / 检索 / 摘要 / 提示词注入。

关键要验证的（用户需求）：
    · 身份以 user.id 为准 —— 改昵称仍是同一个人，同名不同 id 是两个人
    · A 认识人：来过几次、第一次/最近
    · C 偏好：常点歌、送礼金额
    · D 亲密度：互动次数
    · 检索 + 摘要组合成能给 LLM 的文本
    · 「小明又来直播间」这种回忆能说出来（提示词里含他之前的点歌/送礼）

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\test_memory.py
"""
import os
import sys

sys.path.insert(0, r"E:\qq-bot\stream")
sys.stdout.reconfigure(encoding="utf-8")

from memory import Memory

PASS, FAIL = [], []
DB = r"E:\qq-bot\data\_test_memory.db"


def check(desc, got, want):
    ok = (got == want)
    (PASS if ok else FAIL).append(desc)
    print(f"  {'✅' if ok else '❌'} {desc}: got={got!r} want={want!r}")


def main():
    print("=" * 74)
    print("  观众记忆库测试")
    print("=" * 74)

    for f in (DB, DB + "-wal", DB + "-shm"):
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

    mem = Memory(DB)

    print("\n【1】身份令牌：user.id 才是身份，昵称只是显示名")
    u1, n1 = mem.identity({"id": "1001", "nickname": "小明"})
    check("取到 id", u1, "1001")
    check("取到昵称", n1, "小明")

    # 同一个人改昵称
    mem.touch("1001", "小明")
    mem.touch("1001", "小明改名了")
    f = mem.get("1001")
    check("改昵称后 id 不变", f["id"], "1001")
    check("昵称已更新", f["nickname"], "小明改名了")
    check("还是同一条记录（没有多开）", mem.stats()["fans"], 1)

    # 同名不同 id
    u2, n2 = mem.identity({"id": "2002", "nickname": "小明改名了"})
    mem.touch(u2, n2)
    check("同名不同 id 是两个人", mem.stats()["fans"], 2)

    # 没有 id 的情况
    u3, n3 = mem.identity({"nickname": "无名氏"}, "")
    check("无 id 时用 name: 前缀兜底", u3, "name:无名氏")

    print("\n【2】A 认识人 + C 偏好 + D 亲密度")
    print("  （模拟小明：来 3 次、送 2 次礼共 6 元、点 1 首歌）")
    for _ in range(3):
        mem.bump_visit("1001", "小明")
    mem.remember("1001", "gift", "送了大啤酒×2")
    mem.add_gift_value("1001", 3.0)
    mem.remember("1001", "gift", "送了小心心×10")
    mem.add_gift_value("1001", 3.0)
    mem.remember("1001", "song", "晴天")

    f = mem.get("1001")
    check("来过 3 次", f["visits"], 3)
    check("送礼金额累积", f["gifts_value"], 6.0)
    check("礼物次数", f["gifts_count"], 2)
    check("点歌次数", f["songs_ordered"], 1)

    print("\n【3】检索：按关键词捞相关记忆")
    hits = mem.recall("1001", "晴天")
    check("搜「晴天」有命中", len(hits) > 0, True)
    titles = [h["content"] for h in hits]
    check("命中里有点歌记录", any("晴天" in t for t in titles), True)
    print(f"     命中: {titles}")

    # 送礼是权重最高的，即使不搜关键词也会被带出来
    hits2 = mem.recall("1001", "")
    kinds = [h["kind"] for h in hits2]
    check("空关键词也会带出送礼/点歌", ("gift" in kinds or "song" in kinds), True)

    print("\n【4】摘要：人物简介")
    mem.set_summary("1001", "爱点周杰伦的歌，常送小礼物，是老粉")
    check("能读回摘要", mem.summary("1001"), "爱点周杰伦的歌，常送小礼物，是老粉")

    print("\n【5】提示词注入（检索+摘要的组合产物）")
    prof = mem.profile_for_prompt("1001", "小明", keyword="晴天")
    print("  ---- 注入给 LLM 的文本 ----")
    for line in prof.splitlines():
        print("   ", line)
    print("  --------------------------")
    check("包含摘要", "老粉" in prof, True)
    check("包含点歌记忆", "晴天" in prof, True)
    check("包含送礼记忆", ("大啤酒" in prof or "小心心" in prof), True)
    check("提到名字", "小明" in prof, True)

    print("\n【6】新人没有记忆时，注入为空（不多花 token）")
    mem.touch("3003", "路人甲")
    prof_new = mem.profile_for_prompt("3003", "路人甲")
    check("新人注入很短或为空", len(prof_new) < 30, True)
    print(f"     新人注入: {prof_new!r}")

    print("\n【7】不该被太频繁欢迎（节流参数）")
    # needs_summary 只在事件够多时才 True
    check("新人不需要摘要", mem.needs_summary("3003"), False)

    print("\n【8】统计与清理")
    st = mem.stats()
    check("统计能返回观众数", st["fans"], 3)
    check("统计有 top 榜", len(st["top"]) > 0, True)
    print(f"     {st}")

    # 清理
    try:
        mem._conn().close()
    except Exception:
        pass
    for f2 in (DB, DB + "-wal", DB + "-shm"):
        if os.path.exists(f2):
            try:
                os.remove(f2)
            except Exception:
                pass

    print("\n" + "=" * 74)
    print(f"  结果：✅ {len(PASS)} 项通过   ❌ {len(FAIL)} 项失败")
    if FAIL:
        for x in FAIL:
            print("   失败:", x)
    print("=" * 74)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
