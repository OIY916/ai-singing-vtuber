# -*- coding: utf-8 -*-
"""验证「今日」类数据跨天会自动归零。

为什么需要测：
    两个面板写着「今日礼物」「今日点歌」。如果服务连续跑过午夜还不归零，
    观众看到的就是「开机以来」的累计值，标签是骗人的。
    这个测试直接把 State 的日期改成昨天，再触发一次事件，确认数据被清零。

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools/test_day_rollover.py
"""
import sys

sys.path.insert(0, r"E:\qq-bot\stream")
sys.stdout.reconfigure(encoding="utf-8")

# 只测 State 的逻辑，不启动服务（不碰声卡/网络）
import importlib
import time


def main():
    # main.py 顶层会建 State 并开 Mixer（不启动流，安全）
    m = importlib.import_module("main")
    S = m.S

    print("== 造一些「今日」数据")
    S.live["gift_value"] = 12.34
    S.live["gift_count"] = 20
    S.live["gifpters"] = 0
    S.live["gifters"] = 3
    S.live["gift_names"] = ["玫瑰", "小心心"]
    S.live["gift_today"] = 5
    S.stats["songs"] = 4
    S.stats["replies"] = 9
    S._gift_users = {"甲", "乙", "丙"}
    # 累计类：不该被清零
    S.live["total_viewers"] = 1247
    S.live["likes"] = 8620
    S.live["online"] = 138

    print("  归零前:", {
        "gift_value": S.live["gift_value"], "gift_count": S.live["gift_count"],
        "gifters": S.live["gifters"], "gift_today": S.live["gift_today"],
        "songs": S.stats["songs"], "replies": S.stats["replies"],
    })

    print("\n== 把日期调成昨天，触发跨天检查")
    S.stat_date = "2000-01-01"
    rolled = S.roll_over_if_new_day()
    print("  是否触发归零:", rolled)

    print("\n== 归零后")
    after = {
        "gift_value": S.live["gift_value"], "gift_count": S.live["gift_count"],
        "gifters": S.live["gifters"], "gift_today": S.live["gift_today"],
        "songs": S.stats["songs"], "replies": S.stats["replies"],
        "总观看(应保留)": S.live["total_viewers"],
        "点赞(应保留)": S.live["likes"],
        "在线(应保留)": S.live["online"],
    }
    print(" ", after)
    print("  日期已更新为:", S.stat_date, "(今天:", time.strftime("%Y-%m-%d") + ")")

    print("\n== 同一天再调一次，不应重复归零")
    S.live["gift_value"] = 7.0
    rolled_again = S.roll_over_if_new_day()
    print("  是否再次触发:", rolled_again, "| gift_value 应仍为 7.0 →", S.live["gift_value"])

    ok = (
        rolled is True
        and S.live["total_viewers"] == 1247
        and S.live["likes"] == 8620
        and S.live["online"] == 138
        and rolled_again is False
        and S.live["gift_value"] == 7.0
        and after["gift_today"] == 0
        and after["songs"] == 0
        and after["gifters"] == 0
    )
    print("\n" + ("✅ 跨天归零正确：今日类清零，累计类保留，同日不重复归零"
                 if ok else "❌ 跨天归零逻辑有问题"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
