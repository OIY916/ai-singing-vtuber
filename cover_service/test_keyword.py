# -*- coding: utf-8 -*-
"""关键词识别测试：从消息文本中判断是否点歌，并提取歌名"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 触发关键词（长词优先）
SING_KEYWORDS = [
    "唱一首",
    "来一首",
    "唱首歌",
    "唱个歌",
    "唱首",
    "来首",
    "唱",
]

# 出现这些词就判定为闲聊，不触发
NEGATIVE_HINTS = [
    "唱歌好难", "唱歌很难", "唱歌太难", "不会唱", "唱不了", "别唱", "不唱",
    "唱的", "唱的什么", "唱得", "难听", "跑调",
]

# 歌名前面常带的连接字（要剥掉）
LEADING_NOISE = "：:，,、。. 的个首《》\"'“”‘’"


def extract_song_name(text: str):
    """从消息中提取歌名，返回 None 表示不是点歌"""
    if not text:
        return None
    t = text.strip()

    # 去掉 @ 提及（形如 [At:xxx] 或 CQ 码已由框架处理）
    t = re.sub(r"\[At:\d+[^\]]*\]", "", t)
    t = re.sub(r"@\S+", "", t)
    t = t.strip()

    if not t or len(t) > 40:
        return None

    # 闲聊过滤
    for neg in NEGATIVE_HINTS:
        if neg in t:
            return None

    # 长关键词优先，避免「唱一首 X」被「唱」截短
    for kw in sorted(SING_KEYWORDS, key=len, reverse=True):
        idx = t.find(kw)
        if idx < 0:
            continue
        rest = t[idx + len(kw):]
        rest = rest.strip().lstrip(LEADING_NOISE).strip()
        # 去掉尾巴的标点
        rest = re.sub(r"[，。！？!?、,.~～\s]+$", "", rest)

        if not rest:
            # 「唱首歌」这类没有歌名的情况：让 LLM 随机点，交给上层处理
            return None
        if len(rest) > 30:
            return None
        # 以「歌」开头的多半是闲聊（唱歌、歌曲…）
        if rest.startswith(("歌", "这个", "那个", "什么")):
            return None
        return rest
    return None


if __name__ == "__main__":
    cases = [
        "唱 我记得",
        "唱首 晴天",
        "来一首 晴天",
        "唱一首晴天",
        "来首 起风了",
        "唱个歌 稻香",
        "我记得",
        "唱歌这个很难搞",
        "唱歌好难啊",
        "唱 我 记得",
        "@大肥鱼 唱 南山南",
        "别唱了",
        "今天天气不错",
        "唱",
        "唱首歌",
    ]
    for c in cases:
        print(f"{c!r:28} -> {extract_song_name(c)!r}")
