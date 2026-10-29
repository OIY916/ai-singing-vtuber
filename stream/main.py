# -*- coding: utf-8 -*-
"""抖音 AI 唱歌主播 —— 主服务

链路：
  抖音弹幕(1088) ──► 事件队列 ──► LLM(deepseek/mimo) ──► RVC /speak ──► 混音器 ──► VB-Cable
                        │                                                  │
                        └──► 点歌(cover_service /sing) ───────────────────┘
                                                                           │
  浏览器(live2d.html) ◄── WebSocket 状态推送 ◄──────────────────────────────┘
                                                                           │
                                                              OBS 浏览器源 ◄┘

启动：
  python stream/main.py
接口：
  GET  /            控制台
  GET  /avatar      皮套页（OBS 浏览器源）
  GET  /overlay     纯字幕（OBS 浏览器源）
  GET  /ws          WebSocket 状态流
"""
import asyncio
import json
import os
import sys
import time
import traceback

import aiohttp
import websockets
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from applog import get_logger, log_error, log_event, setup_logging
import memory
from mixer import Mixer

# ⚠️ 尽早初始化日志：必须在任何 print/traceback 之前，
#    否则早期输出会漏掉（实测踩过：服务启动失败但日志里什么都没有）。
setup_logging("main")

BASE = os.path.dirname(os.path.abspath(__file__))
AVATAR_DIR = os.path.join(os.path.dirname(BASE), "avatar")
WEB_DIR = os.path.join(BASE, "web")

# ---------- 配置 ----------
CFG = {
    "web_port": 8790,
    "douyin_ws": "ws://127.0.0.1:1088/ws",   # douyinLive 服务
    "room_id": "",                            # 抖音直播间号（web_rid）
    "cover_api": "http://127.0.0.1:8899",     # RVC 翻唱服务
    "llm_base": "https://api.deepseek.com",
    "llm_key": "",
    "llm_model": "deepseek-chat",
    "tts_model": "guanguanV1.pth",            # 说话用的音色
    "sing_model": "nikou48K1999.pth",         # 唱歌用的音色
    "persona": "你是一位活泼可爱的虚拟主播，正在直播唱歌。说话口语化、简短，不超过40字。",
    "max_chars": 40,
    "reply_prob": 0.35,                       # 弹幕回复概率
    "cooldown": 8,                            # 同人冷却秒数
}

CFG_PATH = os.path.join(BASE, "config.json")


def load_cfg():
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                CFG.update(json.load(f))
        except Exception as e:
            print("配置读取失败:", e)
    # LLM key 从 AstrBot 配置里借
    if not CFG.get("llm_key"):
        try:
            p = os.path.join(os.path.dirname(BASE), "data", "cmd_config.json")
            raw = open(p, "rb").read()
            cfg = json.loads(raw.decode("utf-8-sig"))
            for s in cfg.get("provider_sources", []):
                if s.get("id") == "deepseek":
                    CFG["llm_key"] = s["key"][0]
                    CFG["llm_base"] = s.get("api_base", "https://api.deepseek.com")
                    break
        except Exception as e:
            print("读取 LLM key 失败:", e)


# ---------- 全局状态 ----------
class State:
    def __init__(self):
        self.clients = set()       # WebSocket 客户端
        self.mixer = Mixer()
        self.queue = []            # 待播放
        self.current = None
        self.song_queue = []       # 点歌队列
        self.mode = "idle"         # idle / speak / sing
        self.sing_busy = False     # True = 翻唱请求正在进行（RVC /sing 持锁中）
        # ---- 直播永不静音（2026-10-29）----
        # 上一首播完的时间戳：用来判断「队列饿了多久」。
        # RVC 一次翻唱 60~200 秒，GPU 被抢时能挂十几分钟不返回，
        # 这段时间直播间一个音都没有（2026-10-29 实测静默 11 分钟）。
        # 现在饿超过 sing_starve_fallback 秒就直接放原唱顶上。
        self.last_play_end = 0.0
        self.last_play_start = 0.0
        self.last_seen = {}
        self.stats = {"events": 0, "replies": 0, "songs": 0}
        self.llm_history = []

        # ---- 直播间面板数据（UI 用）----
        # 抖音推的 WebcastRoomUserSeqMessage 才有真实在线人数；
        # 拿不到时 here=0，界面显示「—」而不是编一个数字。
        self.live = {
            "online": 0,          # 当前在线人数（来自 total）
            "online_str": "",     # 抖音原始文案
            "online_known": False,  # 是否**真的收到过**在线人数（区分「0人」和「没收到」）
            "total_viewers": 0,   # 累计观看（totalUserStr）
            "total_viewers_str": "",
            "likes": 0,           # 累计点赞
            "gift_value": 0.0,    # 礼物收益（元，按 diamond 折算）
            "gift_count": 0,      # 礼物件数
            "gifters": 0,         # 送礼人数
            "gift_names": [],     # 最近礼物名（做特效）
            "gift_today": 0,      # 今日点歌次数（收到点歌请求的次数）
        }
        self._gift_users = set()
        # 礼物明细（给 /api/gifts 用，面板上只显示总额）
        self._gift_by_user = {}   # 昵称 → 累计金额
        self._gift_by_kind = {}   # 礼物名 → 累计件数
        # 「今日」数据要能跨天自动归零，所以要记日期。
        # ⚠️ 没有这个的话，服务连续跑两天，「今日收益/今日点歌」会一直累加不重置，
        #    观众看到的是「今日」，实际是「开机以来」，属于骗人。
        self.stat_date = time.strftime("%Y-%m-%d")
        # 直播状态跟踪：只在状态**变化**时记日志，避免 douyinLive 的
        # 30 秒轮询把日志刷满「未开播」
        self.live_state = {"online": None, "last_offline": 0.0}

        # ---- 切歌 / 插队（2026-10-27 新增）----
        # skip：切歌信号。play_worker 在等歌播完时会同时等它，
        #       谁先到听谁的（避免 stop_music 导致 15 秒看门狗空转）。
        # hot_q：插队队列（方案A：插到下一个，当前这首播完就播它，不打断）。
        self.skip = None          # asyncio.Event，在 main() 里创建
        self.hot_q = None         # asyncio.Queue，在 main() 里创建
        self.skipped_once = False # 刚才那首是不是被切走的（影响统计）
        # 重启后从磁盘恢复回来的歌：未开播前不播，暂存在这里，开播后由
        # restored_flush_loop 放回 PLAY_Q。用列表而不用「阻塞 play_worker」，
        # 是因为阻塞会卡死整条播放链路，而且被卡住的那首不在队列快照里，
        # 再重启一次就真丢了。
        self.restored_hold = []

        # ---- 点歌开关（2026-10-27 新增）----
        # order_paused=True 时：
        #   · 新的「点歌」「放原曲」请求一律拒绝并回一句提示
        #   · **已经在队列里的歌照常播**（不清队列、不打断）
        # 用途：主播想休息一下不接单、或者队列太长想先消化完。
        self.order_paused = False

        # ---- 唱歌时是否允许语音回复（2026-10-29 用户需求）----
        # 默认 False：唱歌流程里只说气泡，绝不调 /speak 去抢 GPU 锁。
        #
        # 打开后分两种情况（关键区别）：
        #   · 音乐正在放（GPU 其实空闲）→ 直接说，mixer 的 duck 会把歌压到 30%
        #   · 翻唱正在转（/sing 持锁）或还在 JOB_Q 里等着转
        #     → 这时调 /speak 会被**秒拒**（服务忙），万一抢到还会把翻唱
        #       挤成 503（实测「连点三首只唱两首」），所以先攒起来，
        #       等锁放开由 deferred_voice_worker 补说。
        # 控制台可一键开关，会写回 config.json 长期生效。
        self.sing_voice_reply = False

        # 老粉欢迎节流：uid → 上次打招呼时间（防多次进出刷屏）
        self.last_welcome = {}
        self.current_song = None   # 当前播放的歌曲信息
        self.song_started = 0.0
        self.latest_bubble = {"name": "", "text": ""}
        # 弹幕流（右侧滚动区），只留最近 N 条
        self.feed = []

    def roll_over_if_new_day(self):
        """跨天就把「今日」类数据清零。只在事件循环里调用。"""
        today = time.strftime("%Y-%m-%d")
        if today == self.stat_date:
            return False
        self.stat_date = today
        # 今日类
        self.live["gift_value"] = 0.0
        self.live["gift_count"] = 0
        self.live["gifters"] = 0
        self.live["gift_names"] = []
        self.live["gift_today"] = 0
        self._gift_users.clear()
        self.stats["songs"] = 0
        self.stats["replies"] = 0
        # 累计观看/点赞/在线 是直播间维度，不归零
        return True

    async def push(self, kind, data):
        msg = {"kind": kind, "t": time.strftime("%H:%M:%S"), **data}
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_str(json.dumps(msg, ensure_ascii=False))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def snapshot(self):
        """给网页的完整面板快照（WebSocket 定时推 + /api/state 兜底）"""
        st = self.mixer.status()
        return {
            "mode": S.mode,
            "stats": dict(S.stats),
            "live": dict(S.live),
            "sing_q": JOB_Q.qsize(),     # 待准备（翻唱/下载中）
            "play_q": PLAY_Q.qsize(),    # 已就绪、待播
            "raw_q": 0,                  # 已合并进 JOB_Q，保留字段兼容旧面板
            "queue": JOB_Q.qsize() + PLAY_Q.qsize(),
            "queue_items": _queue_preview(),
            "current": S.current_song,
            "audio": st,
            "clients": len(S.clients),
            "room": CFG.get("room_id"),
            # 点歌开关状态（控制台要显示、网页也能提示）
            "order_paused": bool(S.order_paused),
            # 唱歌时是否也用语音回复（控制台开关）
            "sing_voice_reply": bool(S.sing_voice_reply),
            "bubble": S.latest_bubble,
            "feed": S.feed[-12:],
        }


def _queue_preview(n=None):
    """点歌/放曲队列预览（给界面右侧的队列卡片用）。

    顺序 = **真实播放顺序**（这才是观众关心的）：
       正在播 → PLAY_Q（已就绪待播）→ JOB_Q（还在准备）

    只在事件循环里调用，asyncio.Queue 的 _queue 是 deque，安全。

    @param n  None = 全部返回（2026-10-27 改：原来写死 6 个，
              队列有 19 首时界面只显示 6 个，观众和主播都看不全，
              也没法判断哪些重了）。
    """
    out = []
    play_items = []
    job_items = []
    try:
        play_items = list(PLAY_Q._queue)
    except Exception:
        pass
    try:
        job_items = list(JOB_Q._queue)
    except Exception:
        pass

    for it in play_items:
        out.append({"kw": it.get("title", ""), "name": it.get("name", ""),
                    "ready": True, "raw": bool(it.get("raw"))})
    for it in job_items:
        out.append({"kw": it.get("kw", ""), "name": it.get("name", ""),
                    "ready": False, "raw": it.get("kind") == "raw"})

    if n is not None:
        out = out[:n]
    return out


S = State()

# 观众记忆库（用 user.id 做身份令牌）
# ⚠️ 放在 S 后面：memory 是独立模块，不依赖 State
MEM = memory.get_memory()


# ---------- LLM ----------
def _live_context() -> str:
    """拼一段「现在直播间正在发生什么」塞进提示词，让回复能接住现实。

    ⚠️ 为什么要这个（2026-10-29 用户需求：「想让她更像人一样回弹幕」）：
       真人主播回弹幕会自然地提到「我正在唱你点的那首」「刚唱完一首，歇会儿」。
       之前 LLM 完全看不到直播间状态，只能凭空瞎聊，于是每条回复都像客服话术
       ——「谢谢你的支持」「这首歌很好听哦」，观众一眼就看出是机器人。
       把「正在播什么 / 谁点的 / 有没有在唱歌 / 队列多长 / 大概多少人」喂给它，
       回复才能落到当下，而不是永远漂在抽象的寒暄里。
    """
    bits = []
    cur = S.current_song
    if cur:
        tag = "（原曲）" if cur.get("raw") else "（翻唱）"
        who = f"，{cur['by']}点的" if cur.get("by") else ""
        bits.append(f"正在播《{cur.get('title','')}》{tag}{who}，观众在听歌")
    else:
        bits.append("现在没在播歌，可以正常聊天")
    if S.mode == "sing" and cur:
        bits.append("你刚唱完/正在唱歌")
    nq = JOB_Q.qsize() + PLAY_Q.qsize() + (1 if cur else 0)
    if nq:
        bits.append(f"歌单里还有{nq}首在排队")
    if S.live.get("online_known") and (S.live.get("online") or 0):
        bits.append(f"大概{S.live['online']}人在看")
    return "，".join(bits)


async def llm_reply(user_text, name="", uid="", use_history=True, mem_keyword=None):
    """调 LLM 生成回复。

    @param uid          观众的身份令牌（user.id）。有它会带上记忆。
    @param use_history  是否带全局短期对话历史。
                        ⚠️ 记忆库相关的调用应该传 False：
                           全局历史是所有观众混在一起的，
                           把别人的对话塞进"欢迎老粉"的提示里会串味。
    @param mem_keyword  检索记忆用的关键词。默认取 user_text 里的内容。
    """
    if not CFG.get("llm_key"):
        return None
    # ⚠️ 人设 + 说话风格（2026-10-29 重做：「想让她更像人一样回弹幕」）
    #    旧版只有一句「你是活泼可爱的虚拟主播」，模型收到后一律回客服腔的空话。
    #    现在把「怎么说话」写死在提示词里，并且把直播间当下的状态也喂进去。
    sys_prompt = CFG["persona"]
    sys_prompt += (
        "\n\n【说话方式】"
        "\n· 你是活人主播，不是客服。永远别用「感谢您的支持」「很高兴为您服务」这类话。"
        "\n· 接着观众的话茬往下说，别每次都另起话题，也别把人家的话复读一遍。"
        "\n· 可以很短（一句反应、一个语气词都行），不必每条都长篇大论。"
        "\n· 带点情绪和性格：可以调侃、接梗、撒娇、得意、无奈、被夸了就臭美。"
        "\n· 拿不准对方什么意思就反问一句，这比硬编一个回答更像真人。"
        "\n· 不用表情符号，不要写「我说：」这种前缀，只输出你要讲的那句话。"
    )

    # 直播间当下的状态（正在播什么 / 谁点的 / 有没有在唱 / 队列 / 人数）
    ctx = _live_context()
    if ctx:
        sys_prompt += f"\n\n【现在直播间】{ctx}\n（可以自然地提到正在播的歌、点歌的人、队列或人气，但别每条都报一遍）"

    # ⚠️ 记忆注入（2026-10-28）：把「摘要 + 检索」的结果拼进 system prompt。
    #    只对知道的观众注入，新人不会多花 token。
    mem_text = ""
    if uid:
        try:
            kw = mem_keyword if mem_keyword is not None else _mem_keyword(user_text)
            mem_text = MEM.profile_for_prompt(uid, name, keyword=kw)
        except Exception as e:
            print("记忆读取失败:", e)

    if mem_text:
        sys_prompt += (
            "\n\n【你对这位观众的印象】\n" + mem_text +
            "\n（可以在回复里自然地体现你记得他，但别生硬报数据、别每句都提）"
        )

    sys_prompt += f"\n\n只输出主播要说的一段话，不超过{CFG['max_chars']}字。"

    msgs = [{"role": "system", "content": sys_prompt}]
    if use_history:
        msgs.extend(S.llm_history[-6:])
    msgs.append({"role": "user", "content": f"观众{name}说：{user_text}"})
    body = {
        "model": CFG["llm_model"],
        "messages": msgs,
        "temperature": 0.9,
        "max_tokens": 120,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                CFG["llm_base"].rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + CFG["llm_key"]},
                json=body,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                d = await r.json(content_type=None)
                if r.status != 200:
                    print("LLM 错误:", str(d)[:200])
                    return None
                txt = (d["choices"][0]["message"].get("content") or "").strip()
                if txt and use_history:
                    # ⚠️ 历史里也带上「观众X说：」的前缀（2026-10-29）：
                    #    旧版存的是裸弹幕文本，多观众混在一起时模型分不清谁说了什么，
                    #    没法接话茬。带名字后它才能「顺着刚才那个人的话」往下聊。
                    S.llm_history.append(
                        {"role": "user", "content": f"观众{name}说：{user_text}"})
                    S.llm_history.append({"role": "assistant", "content": txt})
                    S.llm_history = S.llm_history[-12:]
                return txt or None
    except Exception as e:
        print("LLM 异常:", e)
        return None


def _mem_keyword(text: str, max_len: int = 8) -> str:
    """从弹幕里挑一个"检索关键词"。

    ⚠️ 为什么不做正经分词：
      中文分词要额外依赖，而且这里只是给记忆检索一个提示，
      用最长的一段中文就够用（比如「点歌晴天」→「晴天」）。
      纯英文/数字也算，取最长的连续片段。
    """
    import re as _re
    t = (text or "").strip()
    if not t:
        return ""
    # 去掉明显的指令词
    for w in ("点歌", "点首", "唱一首", "唱首", "来首", "放原曲", "放首", "播放"):
        t = t.replace(w, " ")
    chunks = _re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", t)
    if not chunks:
        return ""
    return max(chunks, key=len)[:max_len]


# ---------- RVC ----------
def _hdr_text(headers, b64_key, plain_key=""):
    """从响应头取回 UTF-8 文本。

    优先读 base64 字段（服务端已经把中文 base64 化了，纯 ASCII 不会被
    aiohttp/starlette 的 header 编码破坏）；没有再退回旧字段。
    """
    import base64 as _b64

    v = headers.get(b64_key)
    if v:
        try:
            return _b64.b64decode(v).decode("utf-8", "ignore")
        except Exception:
            pass
    if plain_key:
        return headers.get(plain_key, "") or ""
    return ""


async def rvc_speak(text, model=None):
    """文字 → RVC 语音 mp3 字节"""
    model = model or CFG["tts_model"]
    try:
        async with aiohttp.ClientSession() as s:
            form = aiohttp.FormData()
            form.add_field("text", text)
            form.add_field("model", model)
            form.add_field("f0up_key", "0")
            async with s.post(CFG["cover_api"] + "/speak", data=form,
                              timeout=aiohttp.ClientTimeout(total=180)) as r:
                body = await r.read()
                ct = r.headers.get("Content-Type", "")
                if r.status != 200 or "json" in ct:
                    print("RVC /speak 失败:", body[:150])
                    return None
                return body
    except Exception as e:
        print("RVC 异常:", e)
        return None


async def rvc_sing(keyword, model=None, half=None, retries=3):
    """点歌 → RVC 翻唱 mp3 字节

    @param half  None = 跟配置 sing_half（默认 True，只唱前半段）
                 True/False = 强制指定
    @param retries  遇到 503「服务忙」时重试几次
    @return (bytes, song_name, err) 三元组，err 是给人看的失败原因。

    ⚠️ 为什么 503 要重试（2026-10-27 实测）：
       RVC 的 /sing 和 /speak 共用一把 GPU 锁，撞上了就直接 503。
       旧版把 503 当永久失败返回，导致**观众点的这首歌彻底消失**
       （实测连点三首只唱了两首，第一首被 503 吃掉）。
       锁通常几百毫秒~几秒钟就放（一句语音 3.8 秒），
       所以退避重试几次基本都能拿到，不该丢歌。
    """
    model = model or CFG["sing_model"]
    if half is None:
        half = bool(CFG.get("sing_half", True))

    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            async with aiohttp.ClientSession() as s:
                # ⚠️ 中文歌名在这里曾经坏过：aiohttp 默认把 str 当 latin-1 编码，
                # 服务端收到的是乱码（「晴天」→「ÇçÌì」），报「搜到相似歌曲但不是…」。
                # 正确做法是给 FormData 传 charset，让 aiohttp 用 utf-8 序列化 str。
                form = aiohttp.FormData(charset="utf-8")
                form.add_field("keyword", keyword)
                form.add_field("model", model)
                form.add_field("f0up_key", "0")
                form.add_field("half", "true" if half else "false")
                async with s.post(CFG["cover_api"] + "/sing", data=form,
                                  # ⚠️ 900 → 300（2026-10-29）：
                                  #    正常一次翻唱 60~200 秒；900 秒意味着
                                  #    RVC 挂住时直播要干等 15 分钟才认输，
                                  #    这期间一个音都没有。300 秒已经很宽松，
                                  #    超了就走原唱兜底（见 prep_worker）。
                                  timeout=aiohttp.ClientTimeout(total=300)) as r:
                    body = await r.read()
                    ct = r.headers.get("Content-Type", "")
                    song = _hdr_text(r.headers, "X-Song-B64", "X-Song")
                    if r.status != 200 or "json" in ct:
                        detail = body.decode("utf-8", "ignore")[:200]
                        print("RVC /sing 失败:", r.status, detail)
                        # 从服务端 JSON 里挖出人话
                        msg = ""
                        try:
                            js = json.loads(detail)
                            msg = js.get("error") or js.get("msg") or ""
                        except Exception:
                            msg = detail
                        last_err = msg

                        # 503 = 锁被别人拿着（通常是 /speak）。等一会儿再来。
                        if r.status == 503 and attempt < retries:
                            wait = 2.0 * attempt
                            log_event("RVC重试", 歌名=keyword, 状态=r.status,
                                      第几次=f"{attempt}/{retries}",
                                      等待=f"{wait:.0f}s", 原因=msg)
                            print(f"[rvc_sing] 服务忙，{wait:.0f}s 后重试 "
                                  f"({attempt}/{retries})：{keyword}")
                            await S.push("log", {
                                "level": "warn",
                                "msg": f"《{keyword}》等服务忙，{wait:.0f}s 后重试"
                                       f"（{attempt}/{retries}）"})
                            await asyncio.sleep(wait)
                            continue
                        log_error("RVC翻唱", f"HTTP {r.status} {msg}",
                                  歌名=keyword, 第几次=f"{attempt}/{retries}")
                        return None, "", msg
                    return body, song, ""
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print("RVC 异常:", e)
            if attempt < retries:
                await asyncio.sleep(2.0 * attempt)
                continue
            return None, "", last_err

    return None, "", last_err or "重试后仍失败"


def decode_mp3(data):
    """mp3 字节 → float32 立体声 + 采样率（用 ffmpeg）

    ⚠️ data 也允许是文件路径：重启后从队列快照恢复回来的歌存的是路径
       （见 _snap_play_item），这里统一读成 bytes 再走后面的流程。
    """
    import subprocess
    import tempfile

    import numpy as np

    if isinstance(data, str):
        with open(data, "rb") as f:
            data = f.read()
    ffmpeg = r"E:\RVC-bsq\RVCMAX_Nvidia_xiaoyuan\RVCMAX_Nvidia_xiaoyuan\RVC_Core\ffmpeg.exe"
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        raw = subprocess.run(
            [ffmpeg, "-v", "error", "-i", tmp, "-f", "f32le", "-ac", "2", "-ar", "48000", "-"],
            capture_output=True, check=True,
        ).stdout
        a = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)
        return a, 48000
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ---------- 播放 ----------
# ⚠️ 设计要点（2026-10-27 重构）
#
# 旧版把「播完」当成阻塞点：play_voice_bytes / play_song_bytes 都 await 到播完，
# 而 handle_douyin 又是串行 await 调用，于是出现两个致命问题：
#   1. 唱歌期间（几分钟）弹幕回复被卡住 → 「不能边唱边回」
#   2. song_worker 一次只播一首 → 「不能连唱」
#
# 新版做法：语音队列化 + 不阻塞调用方 + 音乐允许被替换。
#   · 语音进 voice_q，由独立 worker 依次播出（保证不糊在一起）
#   · speak() 立刻返回，调用方（弹幕处理）不会被拖住
#   · 音乐是单一缓冲，新歌直接顶掉旧歌（Mixer.play_music 覆盖）
#   · Mixer 的 duck 让说话时音乐自动压低到 30%

VOICE_Q: asyncio.Queue = asyncio.Queue(maxsize=32)

# ---- 唱歌期间被挡下的语音（只有 sing_voice_reply 打开才会用到）----
# 为什么需要「攒」而不是直接丢：用户开这个开关就是为了唱歌时也能听到我说话。
# 翻唱那 40~200 秒里 GPU 锁被 /sing 占着，/speak 一去就是「服务忙」，
# 所以先把句子存下来，等锁放开立刻补说。
DEFER_VOICE = []            # [{"text","model","ts"}]
DEFER_VOICE_MAX = 6         # 最多攒 6 句，多了丢最旧的
DEFER_VOICE_TTL = 45.0      # 超过 45 秒的就不补了（观众早忘了这句）


def _defer_voice(text, model=None):
    """把这一句攒起来，等 GPU 锁放开再补说。"""
    DEFER_VOICE.append({"text": text, "model": model, "ts": time.time()})
    while len(DEFER_VOICE) > DEFER_VOICE_MAX:
        DEFER_VOICE.pop(0)


def song_locking_gpu() -> bool:
    """GPU 锁是不是正被 / 马上就被翻唱占用。

    ⚠️ 和 is_singing() 的区别（这是本开关不误伤翻唱的关键）：
      is_singing() 把「音乐正在放」也算进去，但那时候翻唱早就转完了、
      GPU 其实是空闲的 —— 说话只会和歌重叠，不会让任何任务失败。
      真正危险的是：翻唱正在转（S.sing_busy，/sing 持锁），
      或者翻唱还排在 JOB_Q 里马上要开始 —— 这时候 /speak 抢到锁，
      prep_worker 的 /sing 就会拿到 503「服务忙」，这首歌就唱不成了。
    """
    try:
        return bool(S.sing_busy) or JOB_Q.qsize() > 0
    except Exception:
        return False


async def _flush_deferred_voice(max_wait: float = 8.0) -> int:
    """把攒下的语音赶紧说掉几条，最多占用 max_wait 秒。返回说掉几条。

    ⚠️ 为什么还需要它（2026-10-29）：
      「队列连着的时候」GPU 锁几乎一直被 /sing 占着 —— prep_worker 是
      边播边转下一首的，所以 sing_busy 长期为 True，光靠「等锁空闲」
      可能一整场都等不到空档，攒下的回复就白攒了。
      唯一能腾出锁的时刻是：上一首翻完了、下一首还没开始翻的那几秒。
      所以 prep_worker 在开始下一首之前会调这里，让麦一次。
    """
    loop = asyncio.get_running_loop()
    t0 = time.time()
    said = 0
    # ⚠️ 补说期间把 sing_busy 立起来，等于告诉其他链路「GPU 锁归我」：
    #    否则这 3~8 秒里新来一条弹幕回复也会去调 /speak，两边抢锁 →
    #    后到的那个拿到「服务忙」，正在排队的翻唱也会被挤成 503。
    prev_busy = S.sing_busy
    S.sing_busy = True
    try:
        while DEFER_VOICE and (time.time() - t0) < max_wait:
            item = DEFER_VOICE.pop(0)
            if time.time() - item["ts"] > DEFER_VOICE_TTL:
                continue                    # 太旧了，不补了
            try:
                mp3 = await rvc_speak(item["text"], item["model"])
            except Exception as e:
                print("[flush_voice] 跳过:", type(e).__name__, str(e)[:50])
                continue
            if not mp3:
                continue
            pcm, sr = await loop.run_in_executor(None, decode_mp3, mp3)
            try:
                VOICE_Q.put_nowait({"pcm": pcm, "sr": sr, "text": item["text"]})
                said += 1
                await S.push("log", {"level": "info",
                                     "msg": f"补说一句（翻唱期间攒的）: {item['text'][:24]}"})
            except asyncio.QueueFull:
                break
    finally:
        S.sing_busy = prev_busy
    return said


async def deferred_voice_worker():
    """等翻唱把 GPU 锁放开后，把攒下的语音补说出来。

    和 prep_worker 里的「让麦窗口」分工：
      · 队列空着/正在放歌但没在翻唱 → 这里随时能补
      · 队列连着翻唱不停 → 那里在每首歌之间开个窗口（见 prep_worker）
    两边都调 _flush_deferred_voice，逻辑只有一份。
    """
    while True:
        await asyncio.sleep(1.0)
        if not DEFER_VOICE:
            continue
        if not S.sing_voice_reply:
            DEFER_VOICE.clear()        # 开关被关掉 → 攒的直接作废
            continue
        if song_locking_gpu():
            continue                   # 翻唱还占着锁，等 prep_worker 让麦
        try:
            await _flush_deferred_voice(6.0)
        except Exception as e:
            print("[defer_voice] 跳过:", type(e).__name__, str(e)[:60])


async def speak(text, model=None, wait=False, bubble=None):
    """生成语音并入队播放。

    @param wait    True = 等这句话播完才返回（需要顺序的场合）
                   False = 入队后立刻返回
    @param bubble  气泡信息 {"name","text","tag"}。
                   ⚠️ 传了这个参数，气泡就**不在生成时上屏**，而是等语音真正
                      开始播放的那一刻由 voice_worker 推出去 —— 这样
                      「听到声音」和「看到文字」是同一时刻。
                      否则 RVC 生成要 3.8 秒，观众会先看到文字、几秒后才听到声音。
    """
    # ⚠️ 唱歌流程中一律不调 /speak。（2026-10-27 二次修正）
    #
    # RVC 只有一块 GPU，/sing 和 /speak 共用同一把锁。谁先抢到锁，另一个就被拒：
    #     [cover] 拒绝 /sing：服务忙，当前持锁 /speak
    #
    # 旧版这里只判 S.sing_busy，存在一个**窗口期**：
    #     sing_worker 取到队列项 → await reply_to_bubble(...) 期间
    #     此时 S.sing_busy 还是 False，弹幕回复的 /speak 就冲进去抢到了锁，
    #     等 reply_to_bubble 回来再调 /sing 时 → 503「服务忙，正在处理另一首」，
    #     这首歌就**永久丢失**了（实测：连点三首只唱了两首）。
    #
    # 修复：改判 is_singing()，它同时覆盖三个阶段：
    #     ① JOB_Q/PLAY_Q 非空（刚排队/刚翻好，马上就占 GPU）
    #     ② S.sing_busy（正在翻唱）
    #     ③ mixer 正在放音乐
    # 也就是说：只要「有关歌的事在流程里」，就不说话，全部走气泡。
    if is_singing():
        if not S.sing_voice_reply:
            return None
        # 开关打开：还要再分一层，否则会把翻唱挤掉
        if song_locking_gpu():
            # 翻唱正占着（或马上要占）GPU 锁。这时候硬调 /speak 一定被秒拒，
            # 万一抢到了还会让 prep_worker 的 /sing 拿到 503 —— 歌就没了。
            # 所以攒起来，等锁放开由 deferred_voice_worker 补说。
            _defer_voice(text, model)
            return None          # 返回 None → 调用方立刻上气泡，文字不迟到
        # 只剩「音乐在放、GPU 空闲」这种情况 → 正常发声，
        # mixer 的 duck 会自动把歌压到 30%，不会盖住人声。

    mp3 = await rvc_speak(text, model)
    if not mp3:
        return None
    pcm, sr = await asyncio.get_running_loop().run_in_executor(None, decode_mp3, mp3)
    item = {"pcm": pcm, "sr": sr, "text": text}
    if bubble:
        item["bubble"] = bubble
    ev = None
    if wait:
        import asyncio as _a
        ev = _a.Event()
        item["done"] = ev
    try:
        # 队列满时不阻塞整个链路，直接丢弃这条（宁可少说一句也不要卡死）
        VOICE_Q.put_nowait(item)
    except asyncio.QueueFull:
        print("[speak] 语音队列已满，丢弃:", text[:30])
        if ev is not None:
            ev.set()
        return None
    if ev is not None:
        # 双重保险：即便 worker 出异常也不会永久挂起
        try:
            await asyncio.wait_for(ev.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("[speak] 等待播完超时（60s），继续:", text[:30])
    return item


async def voice_worker():
    """串行播出语音队列（语音之间不重叠，但音乐照常垫底）"""
    loop = asyncio.get_running_loop()
    while True:
        item = await VOICE_Q.get()
        done = asyncio.Event()
        prev_mode = S.mode
        S.mode = "speak"

        # ⚠️ 气泡和语音**同一时刻**上屏（2026-10-27 用户需求）。
        #    气泡停留时长 = 这句话的真实播放时长（+300ms 收尾余量），
        #    所以「文字消失」和「声音说完」基本同步，不会出现
        #    「字早没了声音还在念」或「声音停了字还挂着」。
        bm = item.get("bubble")
        if bm:
            try:
                voice_ms = int(len(item["pcm"]) / float(item["sr"]) * 1000) + 300
            except Exception:
                voice_ms = int(CFG.get("bubble_ms", 6000))
            await reply_to_bubble(bm.get("name", ""), bm.get("text", ""),
                                  tag=bm.get("tag", "回复"), ms=voice_ms)

        await S.push("motion", {"state": "speak", "text": item["text"]})
        try:
            S.mixer.play_voice(item["pcm"], item["sr"],
                               on_end=lambda: loop.call_soon_threadsafe(done.set))
            await done.wait()
        finally:
            # ⚠️ 必须通知等待者（speak(wait=True) 在等这个 Event）
            #    漏了这句 → play_worker 会永远卡在 await speak(..., wait=True)，
            #    表现就是「连唱时第二首永远不播、待播队列不消化」。
            ev = item.get("done")
            if ev is not None and not ev.is_set():
                ev.set()
        # 回到之前的状态（唱歌时说完话继续显示 sing）
        S.mode = "sing" if S.mixer.playing_music else prev_mode
        if S.mode != "speak":
            await S.push("motion", {"state": S.mode})


async def wait_music_end(dur_sec, done: asyncio.Event, poll=1.0, skip=None):
    """等一首歌播完，带看门狗 + 切歌支持。

    ⚠️ 为什么要看门狗（实测踩过）：
       正常播放时 Mixer 会在缓冲放空时回调 on_music_end → done.set()。
       但只要出现下面任一情况，回调就永远不会来：
         · 音频流被别的程序抢占 / 设备掉线（回调线程直接停了）
         · 中途被 /api/test/stop 或新的 play_music 覆盖（on_music_end 被换掉）
       旧代码 `await done.wait()` 会**永久挂住** play_worker，
       表现就是「唱完一首后，待播队列再也不消化」——这正是存档里那条未验证的 bug。

    做法：按「歌曲时长 + 15 秒余量」超时；超时后如果音乐确实已经不在播，就返回 False。

    @param skip  切歌信号（asyncio.Event）。**两个事件谁先到就算谁**。
                 ⚠️ 为什么不能直接 stop_music 来实现切歌：
                    stop_music 会把 on_music_end 摘掉，done 永远不触发，
                    只能等 15 秒看门狗超时 → 观众听到「切歌后静音 15 秒」。
                    用 skip 事件唤醒，立刻就能进入下一首。
    @return True=正常播完；False=被切走或超时
    """
    timeout = max(5.0, float(dur_sec or 0) + 15.0)
    waits = [asyncio.ensure_future(done.wait())]
    if skip is not None:
        waits.append(asyncio.ensure_future(skip.wait()))
    try:
        finished, pending = await asyncio.wait(
            waits, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        # 清掉没被用完的 waiter，避免协程泄漏
        for w in pending:
            w.cancel()
        if not finished:
            # 超时：看门狗分支
            if S.mixer.playing_music:
                return await wait_music_end(
                    max(0.0, timeout - 15.0), done, poll, skip)
            await S.push("log", {"level": "warn",
                                 "msg": f"播放超时兜底（{timeout:.0f}s），强行收尾"})
            return False
        if skip is not None and skip.is_set():
            # 被切歌 → 立刻停掉当前音乐，让下一首马上开始
            S.mixer.stop_music()
            return False
        return True
    except asyncio.CancelledError:
        for w in waits:
            w.cancel()
        raise


async def play_song_bytes(mp3, title="", wait=True, by="", raw=False, gain=None):
    """播放音乐。wait=False 时不阻塞（用于连唱）。"""
    loop = asyncio.get_running_loop()
    pcm, sr = await loop.run_in_executor(None, decode_mp3, mp3)
    dur = round(len(pcm) / sr, 2)
    S.mode = "sing"
    S.current_song = {"title": title, "by": by, "raw": raw, "dur": dur}
    S.song_started = time.time()
    await S.push("motion", {"state": "sing", "song": title, "raw": raw})
    await S.push("now_playing", dict(S.current_song))
    await S.push("log", {"level": "info", "msg": f"开始播放《{title}》"})

    if gain is None:
        gain = 0.6 if raw else 0.55

    if not wait:
        # 连唱模式：直接覆盖当前音乐，不等待
        S.mixer.play_music(pcm, sr, gain=gain)
        return

    done = asyncio.Event()
    S.mixer.play_music(pcm, sr, gain=gain,
                       on_end=lambda: loop.call_soon_threadsafe(done.set))
    # ⚠️ 传入 skip：切歌时立刻唤醒，不用等 15 秒看门狗。
    #    同时把「暂停状态」清掉，否则切歌后新歌会因为 paused 还是 True 而不出声。
    if S.mixer.is_paused():
        S.mixer.resume()
    finished = await wait_music_end(dur, done, skip=S.skip)
    if not finished and S.skip is not None and S.skip.is_set():
        S.skipped_once = True
        log_event("切歌", 歌名=title,
                  已播=f"{time.time() - S.song_started:.0f}s",
                  总长=f"{dur:.0f}s")
    S.mode = "idle"
    S.current_song = None
    await S.push("motion", {"state": "idle"})
    await S.push("now_playing", {})


# 兼容旧调用名
async def play_voice_bytes(mp3, text=""):
    """把已生成的 mp3 字节交给语音队列（不阻塞）"""
    pcm, sr = await asyncio.get_running_loop().run_in_executor(None, decode_mp3, mp3)
    await VOICE_Q.put({"pcm": pcm, "sr": sr, "text": text})


# ---------- 抖音事件 ----------
async def douyin_loop():
    """连 douyinLive，收弹幕"""
    room = CFG.get("room_id")
    if not room:
        await S.push("log", {"level": "warn", "msg": "还没配置 room_id（抖音直播间号），弹幕不会接入"})
        return
    url = f"{CFG['douyin_ws']}/{room}"
    while True:
        try:
            log_event("抖音连接", 直播间=room, url=url)
            await S.push("log", {"level": "info", "msg": f"连接抖音直播间 {room} …"})
            async with websockets.connect(url, open_timeout=20, ping_interval=None) as ws:
                log_event("抖音已连接", 直播间=room)
                await S.push("log", {"level": "info", "msg": "抖音 WS 已连接"})
                async for raw in ws:
                    try:
                        d = json.loads(raw)
                    except Exception:
                        continue
                    try:
                        await handle_douyin(d)
                    except Exception as e:
                        # ⚠️ 单条消息处理失败，绝不能拖垮整个抖音连接。
                        #    以前这里会冒泡到外层 except → 断开 WS 重连 →
                        #    正在处理的那条消息（可能就是点歌）直接丢失，
                        #    而且外层只会记一条「抖音 WS 断开」，
                        #    真正的 TypeError 隐藏在堆栈里（实测踩过）。
                        log_error("处理弹幕", e,
                                  method=str(d.get("method", ""))[:40],
                                  内容=str(d.get("content", ""))[:60],
                                  观众=str((d.get("user") or {}).get("nickname", ""))[:30])
                        traceback.print_exc()
        except Exception as e:
            log_error("抖音WS", e, 直播间=room)
            await S.push("log", {"level": "error", "msg": f"抖音 WS 断开: {type(e).__name__}"})
            await asyncio.sleep(10)


def _num(v, default=0):
    """把抖音的 "138" / "1.2万" / 138 变成数字。

    抖音把在线人数放在 userCountStr 这类字符串里，可能是 "1.2万"。
    解析失败返回 default，绝不抛异常（UI 数据不能拖垮主链路）。
    """
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return default
    mult = 1
    if s.endswith("万") or s.endswith("萬"):
        mult, s = 10000, s[:-1]
    elif s.endswith("亿"):
        mult, s = 100000000, s[:-1]
    try:
        return int(float(s) * mult)
    except Exception:
        return default


def feed_add(item):
    """往右侧弹幕流塞一条（只留最近 60 条，防止内存涨）"""
    try:
        S.feed.append(item)
        if len(S.feed) > 60:
            del S.feed[:-60]
    except Exception:
        pass


# 礼物钻石 → 人民币折算（⚠️ 只是估算，不是真实到手金额）
#
# 抖音的经济关系：
#   · 观众充值 1 元 → 约 10 音浪（douyin 平台币）
#   · 礼物上的 diamond_count 就是这个礼物的音浪价格
#   · 主播**实际到手**约为观众消费的 50%（平台抽成），还有公会/税等差异
#
# 这里按「观众消费面值」折算：1 音浪 = 0.1 元。
# 所以界面显示的金额 ≈ 观众花了多少钱，**不是**主播到手多少钱（到手约为一半）。
# 界面上明确写「估算」，真实结算一律以抖音后台为准。
DIAMOND_TO_YUAN = 0.1

# ---------- 点歌黑名单 & 去重 ----------

# 同一首歌最多在队列里排几次（方案 C）
# 为什么不设 1：别人也想听同一首时彻底没机会；
# 为什么设 2：一个人刷 10 遍会霸屏，观众全在等同一首歌。
MAX_DUP_PER_SONG = 2

# 黑名单：主播不想唱的歌。config.json 的 "song_blacklist" 也能加，
# 两处合并生效（配置里改不用重启不了的话，用 /api/songblack 接口动态增删）。
BLACKLIST_BUILTIN = {"test", "测试"}   # 内置：测试用的假歌名


def _norm_song(s: str) -> str:
    """歌名归一化：去空格、全半角、大小写，用于比较和黑名单匹配。

    「晴天」「晴天 」「晴天」要认为是同一首；
    黑名单里写「晴天」应该能挡住「晴天 (女声版)」这类带后缀的。
    """
    if not s:
        return ""
    s = str(s).lower()
    # 去掉常见分隔符和空白
    out = []
    for ch in s:
        if ch.isspace() or ch in "-_·、,，.:：/\\()（）[]【】":
            continue
        out.append(ch)
    return "".join(out)


def blacklist():
    """当前生效的黑名单（内置 + config.song_blacklist），返回归一化后的集合"""
    items = set(BLACKLIST_BUILTIN)
    for x in (CFG.get("song_blacklist") or []):
        if x:
            items.add(str(x))
    return {_norm_song(x) for x in items if _norm_song(x)}


def _drop_from_queues(should_drop):
    """从待播/待准备队列里移除符合条件的歌，返回被移除的歌名列表。

    ⚠️ 为什么需要（2026-10-28 用户反馈「不能实时添加黑名单」）：
       原来加黑名单**只改配置**，只影响"以后"的点歌。
       已经排进队列的那首照样会唱出去 —— 主播加了黑名单却发现歌还在放，
       看着就像功能没生效。

       现在改成：加黑名单的同时，把队列里已经排着的匹配歌**立刻清掉**。
       正在播的那首**不强行中断**（声音硬切很难听），要立刻停请用 /api/skip。

    ⚠️ asyncio.Queue 不支持随机删除，只能「倒出来 → 过滤 → 原序放回」。
       这里用 list 承载，顺序保持原样，不会打乱观众的排队次序。
    """
    dropped = []
    for q in (PLAY_Q, JOB_Q):
        try:
            items = list(q._queue)
        except Exception:
            continue
        keep = []
        for it in items:
            title = it.get("title") or it.get("kw") or ""
            if title and should_drop(title):
                dropped.append(title)
            else:
                keep.append(it)
        if len(keep) == len(items):
            continue                      # 这首队列没命中，不用重建
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break
        for it in keep:
            try:
                q.put_nowait(it)
            except Exception:
                break
    return dropped


# ---------- 队列持久化（2026-10-28）----------
# 点歌队列原本只在内存里，主服务一重启就全没了。
# 实际事故：直播中队列排了 18~20 首，重启后丢了 3 首（谁/夏天的风/小宇），
# 观众点的歌凭空消失。现在把两个队列定期落盘，启动时灌回去。
QUEUE_FILE = os.path.join(BASE, "queue_state.json")


def _snap_play_item(it):
    """把一个待播条目变成 JSON 能存的形状（bytes → 临时文件路径）。

    ⚠️ 2026-10-29 修的真 bug（代价：丢掉 14 首观众点的歌）：
       PLAY_Q 里的 mp3 是**原始字节**，json.dumps 遇到 bytes 直接抛
       TypeError；而 queue_save_loop 里 `except Exception: continue`
       把这个异常悄悄吞了 —— 所以队列**从来没真正落盘过**，
       queue_state.json 一直停在服务刚启动时那个空快照。
       22:59 重启一次，14 首排队中的歌就全没了。
       _load_queue 本来就是按「路径」读的（会 os.path.exists 检查），
       所以这里落盘前把 bytes 写成临时文件、存路径，两边就对上了。
    """
    mp3 = it.get("mp3")
    if isinstance(mp3, (bytes, bytearray)):
        path = it.get("snap_path")
        if not (path and os.path.exists(path)):
            import tempfile
            fd, path = tempfile.mkstemp(prefix="qsnap_", suffix=".mp3")
            with os.fdopen(fd, "wb") as f:
                f.write(mp3)
            # 存在条目自己身上：写一次就够，别每 2 秒重写 5MB
            it["snap_path"] = path
        item = {k: v for k, v in it.items() if k != "mp3"}
        item["mp3"] = path
        return item
    return dict(it)


def _queue_snapshot():
    """把两个队列的当前内容拍成可序列化快照（顺序 = 播放顺序）。"""
    try:
        job = list(JOB_Q._queue)
    except Exception:
        job = []
    play = []
    try:
        for it in list(PLAY_Q._queue):
            try:
                play.append(_snap_play_item(it))
            except Exception as e:
                log_error("队列快照单条", e)     # 单条坏了别拖累整份快照
    except Exception:
        play = []
    try:
        hold = list(S.restored_hold)
    except Exception:
        hold = []
    return {"job": job, "play": play, "hold": hold}


def _save_queue():
    """原子写队列快照。写失败只记日志，不影响运行。"""
    try:
        snap = _queue_snapshot()
        tmp = QUEUE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        os.replace(tmp, QUEUE_FILE)
        return True
    except Exception as e:
        log_error("保存队列", e)
        return False


def _load_queue():
    """启动时把上次保存的队列灌回内存，返回恢复条数。

    ⚠️ PLAY_Q 的条目里 mp3 是 tempfile 路径（delete=False，不会自动删），
       但可能被系统清理过，所以恢复前要确认文件还在，否则丢弃。
    ⚠️ 恢复的歌打上 restored=True：未开播时不自动播（见 play_worker），
       避免重启后对着空房间把歌全唱一遍。
    """
    if not os.path.exists(QUEUE_FILE):
        return 0
    try:
        with open(QUEUE_FILE, encoding="utf-8") as f:
            snap = json.load(f)
    except Exception as e:
        log_error("读取队列", e)
        return 0

    restored = 0
    for it in snap.get("play", []):
        mp3 = it.get("mp3")
        if not (mp3 and os.path.exists(mp3)) or PLAY_Q.full():
            continue
        it["restored"] = True
        PLAY_Q.put_nowait(it)
        restored += 1
    for it in snap.get("job", []):
        if not it.get("kw") or JOB_Q.full():
            continue
        it["restored"] = True
        JOB_Q.put_nowait(it)
        restored += 1
    # hold：上次已经「暂存等开播」的歌，原样放回暂存区
    for it in snap.get("hold", []):
        if it.get("mp3") and os.path.exists(it["mp3"]):
            S.restored_hold.append(it)
            restored += 1
    return restored


async def queue_save_loop():
    """定期把队列落盘（内容变了才写），防止重启丢歌。"""
    last = None
    while True:
        await asyncio.sleep(2)
        try:
            snap = json.dumps(_queue_snapshot(), ensure_ascii=False)
        except Exception as e:
            # ⚠️ 这里以前是静默 continue —— 正因为不报错，队列二十年没落盘
            #    也没人发现（2026-10-29 丢 14 首歌才查出来）。现在必须留痕。
            log_error("队列快照序列化", e)
            continue
        if snap != last:
            last = snap
            _save_queue()


async def restored_flush_loop():
    """开播后把暂存的「恢复歌」放回播放队列。"""
    while True:
        await asyncio.sleep(2)
        if not S.restored_hold or S.live_state.get("online") is not True:
            continue
        moved = 0
        while S.restored_hold and not PLAY_Q.full():
            it = S.restored_hold.pop(0)
            it.pop("restored", None)          # 已放回，别再被拦
            try:
                PLAY_Q.put_nowait(it)
                moved += 1
            except Exception:
                S.restored_hold.insert(0, it)
                break
        if moved:
            log_event("恢复队列放回", 条数=moved)
            await S.push("log", {"level": "info",
                                 "msg": f"已开播，恢复排队中的 {moved} 首歌"})


def is_blacklisted(kw: str) -> bool:
    """点歌关键词是否命中黑名单。

    匹配规则：黑名单项是关键词的**子串**就算命中（双向），
    这样配「告白气球」能挡住「告白气球 (Live版)」，
    配「test」能挡住「TEST - 邓典果DDG」这种讨厌的测试歌。
    """
    n = _norm_song(kw)
    if not n:
        return False
    for b in blacklist():
        if b and (b in n or n in b):
            return True
    return False


def count_in_queue(kw: str) -> int:
    """同一首歌目前在队列里排了几次（JOB_Q 待准备 + PLAY_Q 待播）。

    用**归一化后的歌名**比较，避免「晴天」和「晴天 」被当成两首。
    注意 PLAY_Q 里的 title 是网易云返回的完整名（带歌手），
    所以用「包含」判断而不是严格相等。
    """
    n = _norm_song(kw)
    if not n:
        return 0
    cnt = 0
    try:
        for it in list(JOB_Q._queue):
            if n and n in _norm_song(it.get("kw", "")):
                cnt += 1
    except Exception:
        pass
    try:
        for it in list(PLAY_Q._queue):
            if n and n in _norm_song(it.get("title", "")):
                cnt += 1
    except Exception:
        pass
    # 正在播的那首也算占用了一个位置
    if S.current_song:
        if n and n in _norm_song(S.current_song.get("title", "")):
            cnt += 1
    return cnt


async def handle_douyin(d):
    """处理单条抖音消息"""
    if d.get("type") == "system":
        code = d.get("code")
        ln = d.get("live_name", "")
        if code == "ROOM_ONLINE":
            log_event("开播", 直播间=ln,
                      未开播时长=f"{(time.time() - S.live_state.get('last_offline', time.time())):.0f}s"
                                 if S.live_state.get("last_offline") else "未知")
            S.live_state["online"] = True
            await S.push("log", {"level": "info", "msg": f"直播间已开播: {ln}"})
            await S.push("live_status", {"online": True, "live_name": ln})
        elif code in ("ROOM_OFFLINE", "ACCOUNT_OFFLINE_NO_ROOM"):
            # ⚠️ 未开播时 douyinLive 每 30 秒推一条，直接全记会把日志淹掉。
            #    只在「状态发生变化」时记一次，方便复盘「几点开的播」。
            if S.live_state.get("online") is not False:
                log_event("未开播", 直播间=ln, code=code)
                S.live_state["online"] = False
                S.live_state["last_offline"] = time.time()
            else:
                S.live_state["last_offline"] = time.time()
            await S.push("log", {"level": "warn", "msg": f"未开播: {ln}"})
            await S.push("live_status", {"online": False, "live_name": ln})
        return

    method = d.get("method", "")

    # ---- 在线人数 / 累计观看（不依赖 user 字段，先处理）----
    if method == "WebcastRoomUserSeqMessage":
        # ⚠️ 字段名是**实测确认**的（2026-10-27 真实开播时靠日志抓到）：
        #    douyinLive 推 RoomUserSeqMessage 时，实际存在的 key：
        #        common / ranks / total / totalUser / totalUserStr / totalStr
        #        onlineUserForAnchor / totalPvForAnchor / livename / title / avatarThumb
        #    而 **userCountStr 根本不存在**（之前以为是它，所以一直显示「—」）。
        #
        # 实测语义（14→22→38 随时间只涨不跌）：
        #    totalUserStr   = 累计观看人数
        #    total          = 当前在线人数
        #    onlineUserForAnchor 是 protobuf bytes（内嵌结构），不能直接当数字
        oc = d.get("total") or d.get("userCountStr") or ""
        tc = d.get("totalPvForAnchor") or d.get("totalUserStr") or ""
        log_event("在线人数", total=d.get("total", ""),
                  totalUserStr=d.get("totalUserStr", ""),
                  totalPvForAnchor=d.get("totalPvForAnchor", ""),
                  采用在线=oc, 采用累计=tc)
        lk = d.get("likeCount") or d.get("totalLike") or 0
        if oc not in ("", None):
            S.live["online"] = _num(oc, S.live["online"])
            S.live["online_str"] = str(oc)
            # 收到过一次就标记「已知」，界面就不会再显示「—」
            S.live["online_known"] = True
        if tc not in ("", None):
            S.live["total_viewers"] = _num(tc, S.live["total_viewers"])
            S.live["total_viewers_str"] = str(tc)
        if lk:
            S.live["likes"] = max(S.live["likes"], _num(lk))
        await S.push("live_stats", dict(S.live))
        return

    if method == "WebcastRoomStatsMessage":
        # 有些房间只推这个（累计观看 + 点赞），在线人数拿不到
        tc = d.get("totalUserStr") or d.get("displayShort")
        if tc:
            S.live["total_viewers_str"] = str(tc)
            S.live["total_viewers"] = _num(tc, S.live["total_viewers"])
        await S.push("live_stats", dict(S.live))
        return

    user = d.get("user") or {}
    name = (user.get("nickname") or "").strip()
    if not name:
        return

    # 主播自己的消息忽略
    if user.get("isAnchor") or name == "主播":
        return

    # ⚠️ 身份令牌（2026-10-28 新增记忆库）：
    #    user.id 是**永不变的数字 ID**，用它做身份；
    #    nickname 只是显示名（会改），绝不能当身份用。
    #    同一个人改昵称 → id 不变 → 还是同一个人；
    #    同一个昵称不同 id → 算两个人。
    # ⚠️ 必须用实例 MEM 而不是模块 memory（2026-10-28 线上事故）：
    #    identity 是 Memory 类的**方法**，写成 memory.identity() 会抛
    #    AttributeError: module 'memory' has no attribute 'identity'
    #    后果：每条弹幕都在这一行崩掉，AI 一条都不回复，直播等于白开。
    #    这是换 Cookie 后第一次真正收到弹幕才暴露出来的（之前一直没数据流）。
    uid, uname = MEM.identity(user, name)

    S.stats["events"] += 1
    S.roll_over_if_new_day()

    if method == "WebcastChatMessage":
        text = (d.get("content") or "").strip()
        if not text:
            return
        MEM.touch(uid, uname)
        MEM.remember(uid, "chat", text[:80])
        await S.push("event", {"type": "弹幕", "name": name, "text": text})
        feed_add({"type": "chat", "name": name, "text": text, "t": time.strftime("%H:%M:%S")})
        await S.push("feed", {"item": S.feed[-1]})
        await on_danmaku(name, text, uid=uid)

    elif method == "WebcastGiftMessage":
        gift = d.get("gift") or {}
        gname = gift.get("name", "")
        num = d.get("repeatCount") or d.get("totalCount") or 1
        # ⚠️ 字段名与层级是**实测确认**的（2026-10-27 真实开播抓包）：
        #    diamondCount 在 **gift 对象里**，而且是**驼峰**写法，
        #    不是下划线的 diamond_count（那是 protobuf 原名，
        #    douyinLive 输出的 JSON 用的是驼峰）。
        #
        #    原来代码读 d.get("diamond_count") / gift.get("diamond_count")
        #    → 两个都取不到 → diamond 恒为 0 → 「今日礼物」永远是 ¥0.00。
        #    实测礼物对象：
        #      gift键=image,describe,id,combo,type,diamondCount,name,icon,...
        diamond = _num(
            gift.get("diamondCount")
            or gift.get("diamond_count")
            or d.get("diamondCount")
            or d.get("diamond_count"),
            0,
        )
        # ⚠️ 连击礼物（小心心 ×2）会连推多条，只有 repeatEnd=true 那条才算最终数量。
        #    整条连击段里 repeatCount 是**累计数**（1→2→2），直接累加会重复计费。
        #    这里只在 repeatEnd 或「没有连击标记」时入账。
        is_combo_mid = bool(d.get("comboCount")) and not d.get("repeatEnd")
        if not is_combo_mid:
            yuan = apply_gift_stats(name, gname, num, diamond, source="直播")
            # 记忆库：送礼是最值得记住的事（权重最高）
            MEM.touch(uid, uname)
            MEM.remember(uid, "gift", f"送了{gname}×{num}")
            try:
                MEM.add_gift_value(uid, yuan or 0)
            except Exception:
                pass
        log_event("礼物原始", 观众=name, 礼物=gname, 数量=num,
                  diamond=diamond, 连击结束=d.get("repeatEnd"),
                  跳过重复=d.get("comboCount"),
                  gift键=",".join(gift.keys())[:120])
        await S.push("event", {"type": "礼物", "name": name, "text": f"{gname} ×{num}"})
        feed_add({"type": "gift", "name": name, "text": f"{gname} ×{num}",
                  "icon": "🎁", "t": time.strftime("%H:%M:%S")})
        await S.push("feed", {"item": S.feed[-1]})
        await S.push("gift_fx", {"name": name, "gift": gname, "num": num})
        await S.push("live_stats", dict(S.live))
        await on_gift(name, gname, num)

    elif method == "WebcastMemberMessage":
        # 记忆库：进场时判断是不是老粉，老粉要打招呼（用户核心需求）
        MEM.touch(uid, uname, is_enter=True)
        MEM.bump_visit(uid, uname)
        MEM.remember(uid, "enter", "进入直播间")
        await S.push("event", {"type": "进场", "name": name, "text": ""})
        feed_add({"type": "enter", "name": name, "text": "进入了直播间",
                  "t": time.strftime("%H:%M:%S")})
        await S.push("feed", {"item": S.feed[-1]})
        # 老粉欢迎（异步，不阻塞进场处理）
        asyncio.create_task(welcome_returning(uid, uname))

    elif method == "WebcastLikeMessage":
        cnt = _num(d.get("count") or 1, 1)
        S.live["likes"] += cnt
        MEM.touch(uid, uname)
        await S.push("event", {"type": "点赞", "name": name, "text": f"+{cnt}"})
        await S.push("live_stats", dict(S.live))

    elif method == "WebcastSocialMessage":
        # 关注
        MEM.touch(uid, uname)
        MEM.remember(uid, "follow", "关注了主播")
        feed_add({"type": "follow", "name": name, "text": "关注了主播",
                  "icon": "⭐", "t": time.strftime("%H:%M:%S")})
        await S.push("feed", {"item": S.feed[-1]})


# ---------- 点歌 / 回复（全程不阻塞）----------
# 架构（2026-10-27 重构，解决「唱歌时不能回复」「不能连唱」）：
#   SING_Q ──► sing_worker ──► PLAY_Q ──► play_worker
#                （翻唱 50~80s）           （一首接一首）
#   弹幕回复走 speak() → VOICE_Q → voice_worker，与音乐并行，Mixer 自动压低音乐。

# ============ 统一的「待播放清单」============
#
# ⚠️ 为什么要把「点歌」和「放原曲」合并成一条队列（2026-10-27 重构）
#
# 旧结构有两条链路，各自调用 Mixer.play_music：
#     SING_Q → sing_worker → PLAY_Q → play_worker  ─┐
#     RAW_Q  → raw_worker  ─────────────────────────┴→ 都往同一个 Mixer 写
# 而 play_music 是**覆盖式**的（新歌顶掉旧歌）。后果：
#   · raw_worker 边下载边自己播，播的时候还阻塞，
#     期间 play_worker 若也开播，两者互相顶掉 → 顺序完全不可控
#   · 「放 X」会打断正在唱的翻唱，或者反过来被翻唱顶掉
#
# 新结构：**一个入口，一个出口**
#     JOB_Q（请求，FIFO）→ prep_worker（按顺序准备 mp3）→ PLAY_Q → play_worker（依次播放）
#
# 这就是用户要的「先放进队列，唱（放）完了再看下一个」：
#   · 点歌 / 放原曲 都只往 JOB_Q 塞一条请求
#   · prep_worker 按**到达顺序**准备（该翻唱就翻唱，该下载就下载）
#   · play_worker 是**唯一**播放者，一首一首来，播完才下一条
#   · 流水线仍然保留：正在播 A 的时候，prep_worker 已经在准备 B 了
JOB_Q: asyncio.Queue = asyncio.Queue(maxsize=10)   # 待准备（翻唱 or 下载）
PLAY_Q: asyncio.Queue = asyncio.Queue(maxsize=10)  # 已就绪、待播放（顺序 = 请求顺序）

# 兼容旧名字（外部脚本/测试可能还在用）
SING_Q = JOB_Q
RAW_Q = JOB_Q

STOP_WORDS = {"一首", "首", "歌", "歌曲", "歌吧", "什么", "啥", "这个", "那个",
              "吧", "呗", "吗", "一下", "点", "点点", "个", "点歌", "来", "放",
              "唱", "一下歌", "首歌", "来首", "唱首", "下",
              # ⚠️ 放开「1 字歌名」后要更严：只挡**纯语气词/量词**，
              #    不要挡实词 —— 实测网易云上有《谁》（廖俊涛）、《光》（陈粒）
              #    这类单字歌名的真歌，挡掉就等于观众点不到。
              "啊", "呀", "哦", "嗯", "哈", "呢", "嘛", "哇", "诶", "欸",
              "啥", "啦", "咯", "哟", "喔", "诶"}

SONG_PATTERNS = [
    # 明确点歌指令：点歌/点首 + 歌名
    r"^\s*[/／]?\s*(?:点歌|點歌|点首|點首)\s*[:：,，]?\s*(.+)$",
    # 唱/来 + 量词 + 歌名（翻唱语义）。
    # ⚠️ 不能把「放」写进来 ——「放X」是播原曲，走 parse_raw_request。
    # ⚠️ 必须带量词，否则「唱得真好听」会被当成点歌（实测踩到过）。
    r"^\s*[/／]?\s*(?:唱|来)\s*(?:一首|首|一个|个)\s*[:：,，]?\s*《?([^》」』]+?)》?\s*$",
    # 歌名 + 唱一下
    r"^\s*(.+?)\s*唱一下$",
]

# 夸赞/评价类词汇：出现这些就判定「不是在点歌」，直接放行给 AI 回复
PRAISE_WORDS = (
    "好听", "好聽", "不错", "好棒", "真棒", "厉害", "唱得", "唱持", "唱歌",
    "跑调", "好听呀", "好听啊", "难听", "喜欢", "爱了", "厉害啊", "牛", "牛啊",
    "太棒", "绝了", "可以啊", "好听诶", "好听呢", "好感动", "有感觉",
    "掌声", "鼓掌", "再来", "加油", "支持", "给力", "顶", "赞",
)


def _looks_like_praise(text: str) -> bool:
    """判断是不是夸赞/评价，而不是点歌"""
    return any(w in text for w in PRAISE_WORDS)


# ============ 「放 X」= 播原曲（不翻唱）============
# 语义区分（重要）：
#   「点歌:X」/「唱一首X」 → 走 RVC 翻唱（用我的音色唱）
#   「放 X」              → 直接播网易云原曲（原唱，不做任何处理）
# 用户明确要求这两条链路分开，不要混。

RAW_SONG_PATTERNS = [
    # 「放 X」/「播放 X」/「放一首 X」
    # ⚠️ 量词必须能被吃掉，否则「放首小情歌」会解析成「首小情歌」
    r"^\s*[/／]?\s*(?:播放|放)\s*(?:一首|首|一个|个)?\s*[:：,，]?\s*《?([^》」』]+?)》?\s*$",
    # 「听一下 X」
    r"^\s*(?:听|来)\s*(?:一下|一下下)\s*《?([^》」』]+?)》?\s*$",
]

RAW_STOP = {"一首", "首", "歌", "歌曲", "吧", "呗", "呀", "啊", "个", "点",
            "一下", "首歌", "来首", "放首", "原曲", "原唱",
            # 脏话 / 无意义词，避免被当成歌名去搜
            "个屁", "屁", "狗屁", "什么", "啥", "这个", "那个", "的", "了"}


def parse_raw_request(text):
    """判断是不是「放原曲」请求，返回歌名或 None

    注意：这个要优先于 parse_song_request 检查，因为「放」字
    在两边都出现过，语义不同。
    """
    import re

    text = text.strip()
    if len(text) > 25:
        return None
    if _looks_like_praise(text):
        return None

    for pat in RAW_SONG_PATTERNS:
        m = re.match(pat, text)
        if not m:
            continue
        cand = m.group(1).strip().strip("《》「」『』\"' ")
        for suf in ("吧", "呗", "呀", "啊", "哦", "呢", "哈"):
            if cand.endswith(suf) and len(cand) > len(suf) + 1:
                cand = cand[:-len(suf)]
        # ⚠️ 同 parse_song_request：放原曲也允许 1 字歌名
        #    （观众点「放 谁」就是想听《谁》原唱，别拦掉）
        if not (1 <= len(cand) <= 18):
            continue
        if any(p in cand for p in "。！？，、；：,.!?;:"):
            continue
        if cand in RAW_STOP or cand in STOP_WORDS:
            continue
        return cand
    return None


def parse_song_request(text):
    """从弹幕里解析点歌请求，返回歌名或 None

    设计原则：宁可漏判（交给 AI 正常回复），也不要误判（把夸赞当成歌名去翻唱）。
    实测踩过的坑：弹幕「唱得真好听」被解析成歌名「得真好听」，
    结果 RVC 去搜这首不存在的歌，浪费 80 秒还报错。
    """
    import re

    text = text.strip()
    # 夸赞类直接不认，走 AI 回复
    if _looks_like_praise(text):
        return None
    # 太长的肯定不是歌名（一般歌名 2~15 字）
    if len(text) > 25:
        return None

    for pat in SONG_PATTERNS:
        m = re.match(pat, text)
        if not m:
            continue
        cand = m.group(1).strip().strip("《》「」『』\"' ")
        for suf in ("吧", "呗", "呀", "啊", "哦", "呢", "哈"):
            if cand.endswith(suf) and len(cand) > len(suf) + 1:
                cand = cand[:-len(suf)]
        # ⚠️ 歌名长度：允许 1 字（2026-10-27 真实开播踩到）
        #
        # 之前写 2~18 字，结果观众点「唱一首 谁」「放 谁」全部解析失败，
        # 落到 AI 回复「好嘞，谁这首安排上」——**根本没去唱**。
        # 网易云上确实有单字歌名的歌（《谁》《光》《年》等），不能一律排除。
        #
        # 单字的风险是「唱一首歌」→ 歌名「歌」，所以：
        #   · 1 字歌名必须在 STOP_WORDS 之外（下面已有检查）
        #   · 且不能是数量词/语气词（"首/个/下/啊/呀…"）
        # STOP_WORDS 已经涵盖这些，枚举得比较全，所以放开 1 字是安全的。
        if not (1 <= len(cand) <= 18):
            continue
        if any(p in cand for p in "。！？，、；：,.!?;:"):
            continue
        if cand in STOP_WORDS:
            continue
        return cand
    return None


async def safe_speak(text, timeout=20, bubble=None):
    """尽力而为地说一句话：RVC 忙/超时都不抛异常，绝不阻塞调用方。

    @param bubble  气泡信息。传给 speak 后，气泡会在语音**开始播放**的
                   那一刻才上屏（语音和文字同步出现）。
    """
    try:
        return await asyncio.wait_for(speak(text, bubble=bubble), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    except Exception as e:
        print(f"[safe_speak] 跳过（{type(e).__name__}）: {text[:22]}")
        return None


def is_singing() -> bool:
    """当前是否处于「唱歌流程中」。

    ⚠️ 这个判断是「唱歌时不说话、只上气泡」的总开关，也是避免 RVC 抢锁的关键。

    覆盖三个阶段（2026-10-27 修正）：
      1. 待翻唱队列有货        —— 马上要占 GPU
      2. 翻唱正在进行          —— RVC /sing 持有锁（27~70 秒）
      3. 音乐正在播放          —— 观众在听歌

    之前只判 (3)，结果翻唱那 27~70 秒里 is_singing() 返回 False，
    语音照常去调 /speak 抢到了锁，RVC 就拒绝 /sing：
        [cover] 拒绝 /sing：服务忙，当前持锁 /speak
    这是实测抓到的真实现象，必须把 (1)(2) 也算进去。
    """
    try:
        if S.sing_busy:
            return True
        if JOB_Q.qsize() > 0 or PLAY_Q.qsize() > 0:
            return True
        return bool(S.mixer.playing_music)
    except Exception:
        return False


async def reply_to_bubble(name: str, text: str, tag: str = "气泡", ms: int = None):
    """把一句要给观众看的话推到气泡。

    气泡是「所有文字输出的统一出口」：
      · 弹幕回复、礼物感谢、点歌确认、翻唱安抚 —— 一律先上气泡
      · 是否同时发声由 speak 参数决定（见 say）
    """
    if ms is None:
        ms = int(CFG.get("bubble_ms_singing" if is_singing() else "bubble_ms", 6000))
    # name 为空时气泡只显示正文（进度播报这类没有昵称的场合）
    S.latest_bubble = {"name": name or "", "text": text, "ms": ms}
    await S.push("reply", {"name": name or "", "text": text, "ms": ms})
    if name:
        await S.push("log", {"level": "info", "msg": f"[{tag}] {name}: {text}"})
    else:
        await S.push("log", {"level": "info", "msg": f"[{tag}] {text}"})


async def say(name: str, text: str, tag: str = "回复", force_voice: bool = None):
    """统一输出：气泡 + 语音（同步）。

    发声规则（config: voice_mode）：
      · "auto"   唱歌中不出声（只气泡），平时出声        ← 默认，推荐
      · "always" 总是出声
      · "never"  从不说话，纯气泡（省 GPU）

    ⚠️ 时序关键（2026-10-27 用户需求：「语音和它的回复气泡一起出现」）
      旧版是 **先上气泡、再生成语音**：RVC 生成一句要 3.8 秒左右，
      观众看到文字后要等好几秒才听到声音，而且气泡固定停 6 秒，
      长句子语音还没念完字就没了（或反过来），观感很乱。

      现在改成：
        · 要发声时：把气泡**交给 speak**，等语音真正开始播的那一瞬间
          （voice_worker 里）才把气泡推上屏 —— 两者同一时刻出现，
          气泡停留时长 = 这条语音的真实时长。
        · 不发声时（唱歌中 / voice_mode=never）：气泡立即上屏。
    """
    mode = CFG.get("voice_mode", "auto")
    want_voice = True
    if mode == "never":
        want_voice = False
    elif mode == "auto" and is_singing() and not S.sing_voice_reply:
        want_voice = False
    if force_voice is not None:
        want_voice = force_voice
    # 翻唱进行中默认一律不出声（保护 GPU 锁，见 speak 的注释）。
    # ⚠️ 开关 sing_voice_reply 打开时仍然尝试：speak 会把这句话攒起来，
    #    等翻唱结束、GPU 锁放开之后补说 —— 这样观众既看到文字也听到声音。
    if S.sing_busy and not S.sing_voice_reply:
        want_voice = False

    if want_voice:
        # 气泡跟着语音走：交给 speak，等开播那一刻才上屏
        got = await safe_speak(text, bubble={"name": name, "text": text, "tag": tag})
        if got:
            return
        # 语音没生成出来（RVC 忙/失败）→ 退化成「只上气泡」，不能连文字都没有
        await reply_to_bubble(name, text, tag=tag)
        return

    # 不发声：气泡立即上屏（这是观众唯一的出口）
    await reply_to_bubble(name, text, tag=tag)


# ============ 「放 X」：播原曲 ============


async def rvc_raw(keyword, half=True):
    """调 RVC /raw 下载原曲（不翻唱）。返回 (bytes, song_name, kept_sec, err)"""
    try:
        async with aiohttp.ClientSession() as s:
            form = aiohttp.FormData(charset="utf-8")
            form.add_field("keyword", keyword)
            form.add_field("half", "true" if half else "false")
            form.add_field("fade", str(CFG.get("raw_fade", 1.5)))
            async with s.post(CFG["cover_api"] + "/raw", data=form,
                              timeout=aiohttp.ClientTimeout(total=180)) as r:
                body = await r.read()
                ct = r.headers.get("Content-Type", "")
                song = _hdr_text(r.headers, "X-Song-B64", "X-Song")
                kept = r.headers.get("X-Kept-Seconds", "0")
                if r.status != 200 or "json" in ct:
                    detail = body.decode("utf-8", "ignore")[:200]
                    msg = ""
                    try:
                        msg = json.loads(detail).get("error", "")
                    except Exception:
                        msg = detail
                    return None, "", 0.0, msg
                return body, song, float(kept or 0), ""
    except Exception as e:
        return None, "", 0.0, f"{type(e).__name__}: {e}"


async def fallback_to_raw(kw, who, reason="", msg=None):
    """直播永不静音的兜底：把这首歌改放原唱，塞进待播队列。

    什么时候用（2026-10-29）：
      ① RVC 翻唱失败/超时 —— 观众点的是这首歌，听原唱也比冷场强；
      ② 队列饿太久 —— 待播队列空了、上一首早播完了，翻唱还得好几分钟，
         这时候直接放原唱把空档填上，不让直播间断音。

    ⚠️ 用的还是「放 X」那条链路（/raw 下载原曲，不碰 GPU 锁），
       所以不会和正在翻唱的那首抢锁，也不会重复占 GPU。
    @param msg 给观众看的气泡文案；不传就用一句通用的
    @return True = 已经塞进待播队列；False = 原唱也没拿到，别指望了
    """
    half = bool(CFG.get("raw_half", True))
    mp3, song, kept, err = await rvc_raw(kw, half=half)
    if not mp3:
        log_error("原唱兜底", err or "未知", 歌名=kw, 原因=reason)
        await S.push("log", {"level": "error",
                             "msg": f"原唱兜底也失败 [{kw}]: {err or '未知'}"})
        return False
    log_event("原唱兜底", 歌名=song or kw, 观众=who, 原因=reason or "队列饿太久",
              长度=f"{kept:.0f}s")
    await S.push("log", {"level": "warn",
                         "msg": f"改放原唱顶上: {song or kw}（{reason}）"})
    await reply_to_bubble(
        who or "",
        msg or f"《{kw}》翻唱没赶上，先放原唱给你们听～", tag="转原唱")
    await PLAY_Q.put({"mp3": mp3, "title": song or kw, "name": who,
                      "raw": True, "ready": True, "fallback": True,
                      "restored": False})
    return True


def starving() -> bool:
    """待播队列空着，而且上一首已经播完很久了 —— 直播室正在断音。"""
    if PLAY_Q.qsize() > 0:
        return False
    if S.hot_q is not None and S.hot_q.qsize() > 0:
        return False        # 插队队列里还有歌，马上就有声音
    if S.mode in ("sing", "speak"):
        return False        # 还有声音在放，不算饿
    idle = time.time() - (S.last_play_end or 0.0)
    return idle >= float(CFG.get("sing_starve_fallback", 45))


async def start_raw_song(name, kw):
    """发起「放 X」：进统一队列（和点歌排在一起，按到达顺序播）"""
    if JOB_Q.qsize() >= JOB_Q.maxsize:
        await say(name, "排队的歌太多啦，等会儿再放吧", tag="放满")
        return
    await JOB_Q.put({"kind": "raw", "name": name, "kw": kw})
    S.stats["events"] += 1
    S.live["gift_today"] += 1
    await S.push("log", {"level": "info",
                         "msg": f"{name} 放原曲: {kw}（队列 {JOB_Q.qsize()}）"})
    await reply_to_bubble(name, f"好嘞，放《{kw}》原曲～", tag="放原曲")


async def prep_worker():
    """准备 worker：按**到达顺序**把 JOB_Q 里的请求变成可播放的 mp3。

    两种请求走同一个队列、同一个 worker，所以顺序天然是 FIFO：
      · kind="sing" → 调 RVC 翻唱（要 GPU，会占 /sing 锁）
      · kind="raw"  → 调 RVC /raw 下载原曲（纯下载+转码，**不碰 GPU 锁**）

    ⚠️ 为什么合并（2026-10-27 重构）：
      旧版 raw_worker 和 sing_worker 是两条独立链路，各自都会调
      Mixer.play_music（覆盖式），谁后到谁顶掉前一首。
      「放 X」和「点歌」同时来的时候，谁先播完全随机，
      还会把对方已经播了一半的歌顶掉。
      现在只在这里**准备**，播放统一交给 play_worker。
    """
    while True:
        item = await JOB_Q.get()
        kw = item["kw"]
        kind = item.get("kind", "sing")
        who = item.get("name", "")

        if kind == "raw":
            # ---- 放原曲：纯下载，不碰 GPU 锁 ----
            await S.push("log", {"level": "info", "msg": f"下载原曲: {kw}"})
            t0 = time.time()
            half = bool(CFG.get("raw_half", True))
            mp3, song, kept, err = await rvc_raw(kw, half=half)
            cost = time.time() - t0
            if not mp3:
                log_error("下载原曲", err or "未知", 歌名=kw, 观众=who, 耗时=f"{cost:.1f}s")
                await S.push("log", {"level": "error",
                                     "msg": f"原曲下载失败 [{kw}]: {err}"})
                await reply_to_bubble(who, f"抱歉，《{kw}》原曲没找到", tag="放失败")
                continue
            log_event("原曲就绪", 歌名=song or kw, 观众=who,
                      耗时=f"{cost:.1f}s", 长度=f"{kept:.0f}s")
            await S.push("log", {"level": "info",
                                 "msg": f"原曲就绪: {song or kw} ({kept:.0f}s)"})
            await PLAY_Q.put({"mp3": mp3, "title": song or kw, "name": who,
                              "raw": True, "ready": True,
                              "restored": item.get("restored", False)})
            continue

        # ---- 点歌：走 RVC 翻唱 ----
        #
        # ⚠️ 直播永不静音（2026-10-29）：
        #    这里**不**在转换开始前就降级成原唱 —— 那样只要观众隔一会儿才点歌，
        #    那首歌就永远听不到我的音色了，本末倒置。
        #    正确做法：先照常翻唱；如果翻唱期间直播间一直没声音（超过
        #    sing_starve_fallback 秒），才并行去下原唱顶上（见下面的循环）。
        #
        # ⚠️ 唱歌时也语音回复（2026-10-29）：
        #    队列连着的时候 GPU 锁几乎一直被翻唱占着，攒下的回复等不到空档。
        #    上一首刚翻完、这一首还没开始翻 —— 就是唯一能腾出锁的时刻，
        #    在这里把攒的话说掉（最多占 8 秒），再开始下一首的翻唱。
        if S.sing_voice_reply and DEFER_VOICE:
            try:
                said = await _flush_deferred_voice(8.0)
                if said:
                    log_event("补说攒下的语音", 条数=said, 歌名=kw)
            except Exception as e:
                log_error("补说语音", e)     # 绝不能因为补说失败把翻唱卡死

        await S.push("log", {"level": "info", "msg": f"开始翻唱: {kw}"})
        log_event("翻唱开始", 歌名=kw, 观众=who, 待播=PLAY_Q.qsize())

        # ⚠️ 标记必须在任何 await 之前设好（2026-10-27 二次修正）。
        #    sing_busy 早于 reply_to_bubble，这样「刚点完歌」这一瞬间
        #    弹幕回复也不会去调 /speak 抢锁。
        S.sing_busy = True

        await reply_to_bubble(who, f"点的《{kw}》去翻唱啦，稍等一下哦", tag="翻唱中")
        t_sing = time.time()
        mp3, song, err = None, "", ""
        used_raw = False          # 已经用原唱顶上，这首不用再走翻唱收尾
        raw_task = None           # 并行的原唱下载任务
        raw_failed = False        # 原唱也没下到，别再反复去下
        starve_after = float(CFG.get("sing_starve_fallback", 45))
        try:
            task = asyncio.create_task(rvc_sing(kw))
            waited = 0.0
            while not task.done():
                await asyncio.sleep(5)
                waited += 5
                if waited in (30, 60):
                    await S.push("log", {"level": "info", "msg": f"翻唱进行中 {waited:.0f}s"})
                    await reply_to_bubble("", f"《{kw}》还在翻唱中，{waited}秒了…", tag="进度")

                # ⚠️ 断音降级（2026-10-29）：
                #    翻唱还在转，而直播间已经连续 starve_after 秒一个音都没有
                #    （没歌在播、待播队列也没货）—— 那就并行去下原唱垫上。
                #    原唱一到手立刻播，翻唱那首作废；GPU 上的转换不会白费，
                #    cover_service 会缓存，下次点同一首就是秒出。
                if (raw_task is None and not raw_failed and not task.done()
                        and waited >= starve_after and starving()):
                    raw_task = asyncio.create_task(
                        rvc_raw(kw, half=bool(CFG.get("raw_half", True))))
                    await S.push("log", {"level": "warn",
                                         "msg": f"直播间断音 {waited:.0f}s，改用原唱顶上: {kw}"})
                    await reply_to_bubble("", f"《{kw}》翻唱还要一会儿，先放原唱给大家垫一下～",
                                          tag="转原唱")

                if raw_task is not None and raw_task.done() and not task.done():
                    raw_mp3, raw_song, _kept, raw_err = raw_task.result()
                    if raw_mp3:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            pass
                        log_event("断音降级改原唱", 歌名=raw_song or kw, 观众=who,
                                  翻唱等待=f"{waited:.0f}s")
                        await PLAY_Q.put({"mp3": raw_mp3, "title": raw_song or kw,
                                          "name": who, "raw": True, "ready": True,
                                          "fallback": True, "restored": False})
                        used_raw = True
                        break
                    else:
                        await S.push("log", {"level": "warn",
                                             "msg": f"原唱顶上也失败 [{kw}]: {raw_err}，继续等翻唱"})
                        raw_task = None
                        raw_failed = True     # 别再反复去下，继续等翻唱

            if not used_raw:
                mp3, song, err = task.result()
        finally:
            S.sing_busy = False

        if used_raw:
            continue      # 这首已经用原唱顶上了，下一条

        if not mp3:
            log_error("翻唱", err or "未知失败", 歌名=kw, 观众=who)
            await S.push("log", {"level": "error", "msg": f"翻唱失败 [{kw}]: {err or '未知'}"})
            # ⚠️ 直播永不静音（2026-10-29）：翻唱没成（超时/服务忙/挂了），
            #    改放原唱顶上，别让观众点的这首歌凭空消失 + 直播间断音。
            #    RVC 挂住时原来要干等 15 分钟（900s 超时），现在 300s 就认输转原唱。
            if await fallback_to_raw(kw, who, reason=f"翻唱失败: {err or '未知'}"):
                continue
            # 原唱也拿不到，才真的放弃
            await reply_to_bubble("", f"抱歉，《{kw}》没唱成，换一首吧", tag="失败")
            continue

        cost = time.time() - t_sing
        log_event("翻唱完成", 歌名=song or kw, 观众=who, 耗时=f"{cost:.1f}s",
                  大小=f"{len(mp3)//1024}KB")
        await S.push("log", {"level": "info", "msg": f"翻唱完成: {song or kw}"})
        await PLAY_Q.put({"mp3": mp3, "title": song or kw, "name": who,
                          "raw": False, "ready": True,
                          "restored": item.get("restored", False)})


async def next_play_job():
    """取下一条要播的歌：**插队队列优先**，然后才是正常队列。

    ⚠️ 这是「方案 A」插队的核心（2026-10-27）：
       插队 = 不打断当前这首，但**当前这首播完就轮到它**。
       所以 hot_q 优先取；取不到再等 PLAY_Q。

    ⚠️ 两个队列都要等，不能用 `await PLAY_Q.get()` 然后检查 hot_q，
       否则插队的那首要等正常队列里来新歌才被看见。
       这里用轮询式（0.3s）取，够快且实现简单；
       asyncio 没有内置「多队列优先阻塞取」。
    """
    while True:
        try:
            return S.hot_q.get_nowait(), True     # True = 插队的歌
        except Exception:
            pass
        try:
            return PLAY_Q.get_nowait(), False
        except Exception:
            pass
        await asyncio.sleep(0.3)


async def play_worker():
    """播放 worker：一首接一首，播完自动接下一首。

    ⚠️ 关键约束：RVC 只有一块 GPU，`/sing` 和 `/speak` 共用一把锁。
    报幕如果去调 /speak，就会和下一首的 /sing 抢锁 → 翻唱被拒。
    所以这里默认**不报幕**（改由气泡显示歌名），只有显式开启
    config.announce_before_song=true 才尝试说话，且失败不影响播放。
    """
    while True:
        job, from_hot = await next_play_job()

        # 恢复回来的歌：未开播时不自动播（避免重启后对着空房间把歌唱完）。
        # ⚠️ 不阻塞 play_worker，而是暂存到 S.restored_hold，开播后由
        #    restored_flush_loop 放回队列 —— 阻塞会卡死整条播放链路，
        #    而且被卡住的那首不在队列快照里，再重启一次就真丢了。
        if job.get("restored") and S.live_state.get("online") is not True:
            S.restored_hold.append(job)
            await S.push("log", {"level": "info",
                                 "msg": f"恢复的歌《{job['title']}》等开播再播"
                                        f"（暂存 {len(S.restored_hold)} 首）"})
            continue

        S.stats["songs"] += 1

        # ⚠️ 每首新歌开播前先清掉 skip 标志（2026-10-27）：
        #    否则「切歌 → 下一首刚开始播 → 发现 skip 还是 set 状态 → 又被切掉」，
        #    会连着切好几首，观众听到的全是开头几秒。
        if S.skip is not None:
            S.skip.clear()

        if from_hot:
            await S.push("log", {"level": "info",
                                 "msg": f"插队播放：{job['title']}"})
            log_event("插队播放", 歌名=job["title"], 观众=job.get("name", ""))

        if CFG.get("announce_before_song", False):
            # 报幕：气泡和语音同步（交给 speak，等开播那一刻才上屏）
            try:
                got = await asyncio.wait_for(
                    speak(f"下面唱{job['title']}，送给大家", wait=True,
                          bubble={"name": "", "text": f"♪ {job['title']}",
                                  "tag": "报幕"}),
                    timeout=15
                )
                if not got:
                    # 语音没生成出来，气泡不能丢
                    await reply_to_bubble("", f"♪ {job['title']}", tag="报幕")
            except asyncio.TimeoutError:
                await reply_to_bubble("", f"♪ {job['title']}", tag="报幕")
                await S.push("log", {"level": "warn", "msg": "RVC 忙，跳过报幕直接播歌"})
            except Exception as e:
                await reply_to_bubble("", f"♪ {job['title']}", tag="报幕")
                await S.push("log", {"level": "warn", "msg": f"报幕失败（忽略）: {type(e).__name__}"})
        else:
            await reply_to_bubble("", f"♪ {job['title']}", tag="报幕")

        # 原曲（放 X）给略高的增益；翻唱略低，避免 RVC 输出偏响。
        gain = 0.6 if job.get("raw") else 0.55
        log_event("播放开始", 歌名=job["title"], 观众=job.get("name", ""),
                  类型="原曲" if job.get("raw") else "翻唱",
                  剩余队列=PLAY_Q.qsize())
        t_play = time.time()
        # 记录「什么时候开始/结束的」，给 starving() 判断直播间是不是断音用。
        S.last_play_start = t_play
        try:
            await play_song_bytes(job["mp3"], job["title"], by=job.get("name", ""),
                                  raw=bool(job.get("raw")), gain=gain)
            log_event("播放结束", 歌名=job["title"],
                      实际时长=f"{time.time() - t_play:.1f}s")
        except Exception as e:
            # ⚠️ 播放异常绝不能让 play_worker 死掉，否则后面所有歌都不播了
            log_error("播放", e, 歌名=job["title"])
            await S.push("log", {"level": "error",
                                 "msg": f"播放异常 [{job['title']}]: {type(e).__name__}"})
        finally:
            # 无论正常播完还是报错，都要记下「声音什么时候停的」，
            # 否则 starving() 会一直以为直播间还在响。
            S.last_play_end = time.time()


async def welcome_returning(uid: str, nickname: str):
    """老粉进场时打招呼。**只在值得打招呼时才说**。

    ⚠️ 为什么需要克制（2026-10-28 设计决定）：
       每个进场都欢迎会变成刷屏机器人，观众会觉得吵。
       所以只在下面情况打招呼：
         · 来过 >= 2 次（回头客）
         · 且这次进场距上次 > 30 分钟（不是刚出去又进来）
         · 且 90 秒内没打过招呼（防止多次进出刷屏）
       而且欢迎词交给 LLM 生成，带上记忆，才能说出
       「又来了啊，还想听之前你点的那首歌吗」这种话。
    """
    if not uid:
        return
    try:
        f = MEM.get(uid)
        if not f:
            return
        visits = f.get("visits") or 0
        last_seen = f.get("last_seen") or 0
        # 回头客才打招呼
        if visits < 2:
            return
        # 刚出去又进来的不算
        gap = time.time() - (S.last_welcome.get(uid, 0))
        if gap < 90:
            return

        # 是否值得说：点过歌 或 送过礼 或 来过 3 次以上
        interesting = (
            (f.get("songs_ordered") or 0) > 0
            or (f.get("gifts_value") or 0) > 0
            or visits >= 3
        )
        if not interesting:
            return

        S.last_welcome[uid] = time.time()

        prof = MEM.profile_for_prompt(uid, nickname, keyword="")
        prompt = (
            f"观众「{nickname}」又进直播间了。根据下面的记忆，"
            f"用一句话欢迎他（不超过{CFG.get('max_chars', 40)}字，口语，"
            f"可以提到他以前点过的歌或送过的礼物，但别啰嗦，别报数字）：\n"
            f"{prof or '（没有特别的记忆）'}"
        )
        reply = await llm_reply(prompt, nickname, use_history=False)
        if not reply:
            return
        log_event("老粉欢迎", 观众=nickname, 回复=reply,
                  来过次数=visits, 点歌=f.get("songs_ordered"),
                  送礼=f.get("gifts_value"))
        # force_voice=False：只上气泡，不出声（2026-10-28 用户需求）
        await say(nickname, reply, tag="欢迎老粉", force_voice=False)
    except Exception as e:
        log_error("老粉欢迎", e, 观众=nickname)


async def on_danmaku(name, text, uid: str = ""):
    """弹幕 → 点歌 或 AI 回复。

    关键策略（唱歌中 vs 平时）：
      · 平时          → 回复走 RVC 出声（safe_speak）
      · 唱歌中        → 回复只上气泡，不调 RVC（config: reply_while_singing）
                       原因：RVC 单 GPU，唱歌时 /speak 必然「服务忙」，
                       强行调用只会失败 + 浪费一次请求。
                       隔壁观众的听感也更好：听歌时不希望被 AI 说话打断。
    """
    import random

    # ⚠️ 顺序很重要：「放X」先判，因为「放」和「唱」语义不同
    raw_kw = parse_raw_request(text)
    if raw_kw:
        # 点歌开关对「放原曲」同样生效（都是往队列加歌）
        if S.order_paused:
            log_event("识别拒", 类型="放原曲", 观众=name, 歌名=raw_kw,
                      原因="点歌已暂停", 原话=text)
            await say(name, "点歌暂时关啦，正在播的歌放完先～", tag="暂停点歌")
            return
        # 黑名单对「放原曲」同样生效：不让放的歌，原曲也别放
        if is_blacklisted(raw_kw):
            log_event("识别拒", 类型="放原曲", 观众=name, 歌名=raw_kw,
                      原因="黑名单", 原话=text)
            await say(name, f"《{raw_kw}》这首播不了哦，换一首吧～", tag="黑名单")
            return
        same = count_in_queue(raw_kw)
        if same >= MAX_DUP_PER_SONG:
            log_event("识别拒", 类型="放原曲", 观众=name, 歌名=raw_kw,
                      原因="重复过多", 已在队列=same)
            await say(name, f"《{raw_kw}》已经排 {same} 首啦～", tag="重复")
            return
        log_event("识别", 类型="放原曲", 观众=name, 原话=text, 歌名=raw_kw)
        await start_raw_song(name, raw_kw)
        return

    kw = parse_song_request(text)
    if kw:
        # ---- 点歌开关：关闭时一律拒绝（已在队列的歌照常播）----
        if S.order_paused:
            log_event("点歌拒", 观众=name, 歌名=kw, 原因="点歌已暂停", 原话=text)
            await say(name, "点歌暂时关啦，正在播的歌放完先～", tag="暂停点歌")
            return

        # ---- 黑名单：主播不想唱的歌直接拒绝 ----
        # 用途：有些歌不好听 / 不适合直播 / 之前翻唱老失败，列进来就别再点了
        if is_blacklisted(kw):
            log_event("点歌拒", 观众=name, 歌名=kw, 原因="黑名单", 原话=text)
            await say(name, f"《{kw}》这首我不唱哦，换一首吧～", tag="黑名单")
            return

        # ---- 去重（方案 C：同一首歌最多排 MAX_DUP_PER_SONG 次）----
        #
        # 为什么是「限次数」而不是「完全不重复」：
        #   完全不重复会让「别人也想听晴天」彻底没机会；
        #   限 2 次既能满足跟风点，又能防止一个人刷 10 遍霸屏。
        same = count_in_queue(kw)
        if same >= MAX_DUP_PER_SONG:
            log_event("点歌拒", 观众=name, 歌名=kw, 原因="重复过多",
                      已在队列=same, 上限=MAX_DUP_PER_SONG, 原话=text)
            await say(name, f"《{kw}》已经排 {same} 首啦，换一首呗～", tag="重复")
            return

        if JOB_Q.qsize() >= JOB_Q.maxsize:
            log_event("点歌拒", 观众=name, 歌名=kw, 原因="队列满",
                      队列=JOB_Q.qsize())
            await say(name, "歌排太满啦，等会儿再点吧", tag="点歌满")
            return
        await JOB_Q.put({"kind": "sing", "name": name, "kw": kw})
        # 「今日点歌」= 收到点歌请求的次数，和「已唱完几首」是两个概念
        # （翻唱失败、被顶掉都不该算作已唱）。之前界面错用了 stats.songs。
        S.live["gift_today"] += 1
        # 记忆库：点歌是最值得记住的事之一（「还想听之前你点的那首吗」靠它）
        if uid:
            MEM.touch(uid, name)
            MEM.remember(uid, "song", kw)
        log_event("点歌", 观众=name, 歌名=kw, 原话=text,
                  已在队列=same, 待准备=JOB_Q.qsize(), 待播=PLAY_Q.qsize())
        await S.push("log", {"level": "info",
                             "msg": f"{name} 点歌: {kw}（待准备 {JOB_Q.qsize()}，待播 {PLAY_Q.qsize()}）"})
        S.stats["events"] += 1
        await say(name, f"收到《{kw}》，马上安排！", tag="点歌")
        return

    now = time.time()
    if now - S.last_seen.get(name, 0) < CFG["cooldown"]:
        return
    S.last_seen[name] = now

    # ⚠️ 回复概率（2026-10-27 真实开播教训）
    #
    # 原来 reply_prob=0.4 → **60% 的弹幕直接被静默丢弃**。
    # 真实开播时观众连发「你会唱歌吗」「哈喽呀」「咋不理我」，
    # 一条都没回，观众当场就觉得「这主播是死的」。
    #
    # 真人直播间里，观众说一句话没被理，基本就不会再说第二句了。
    # 所以默认改成「尽量都回」：
    #   · reply_prob >= 1.0 或未设置 → 全回
    #   · 靠 cooldown 防刷屏（同一个人 N 秒内只说一次）
    #   · 靠 max_chars 和 LLM 提示词控制回复长度，避免话太多
    # 想省钱的可以调小 reply_prob，但开播初期建议全回。
    prob = CFG.get("reply_prob", 1.0)
    if not CFG.get("debug_always_reply") and prob < 1.0:
        if random.random() >= prob:
            log_event("弹幕忽略", 观众=name, 原话=text,
                      原因=f"reply_prob={prob}")
            return

    # ⚠️ 带上 uid：让 LLM 能看到「你对这位观众的印象」（记忆库）
    reply = await llm_reply(text, name, uid=uid)
    if not reply:
        log_error("LLM回复", "返回空（key/网络/额度？）", 观众=name, 原话=text)
        return

    S.stats["replies"] += 1
    log_event("AI回复", 观众=name, 原话=text, 回复=reply)

    # ---- 分流：唱歌中只上气泡 ----
    await say(name, reply, tag="弹幕回复")


async def on_gift(name, gname, num):
    """礼物 → 感谢（气泡必上，声音按 voice_mode）"""
    await say(name, f"谢谢{name}的{gname}！", tag="礼物")


# ---------- 测试注入（开播前验证整条链路用）----------
async def http_test_danmaku(req):
    """模拟一条弹幕：POST /api/test/danmaku  {"name":"测试","text":"点歌:晴天"}

    只绑回环地址，用于开播前自测，不需要真实观众。
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    name = (body.get("name") or "测试观众").strip()[:20]
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"ok": False, "error": "text 不能为空"})
    await S.push("event", {"type": "弹幕(测试)", "name": name, "text": text})
    # 测试也要进弹幕流，否则版面自测看不到弹幕区效果
    feed_add({"type": "chat", "name": name, "text": text,
              "t": time.strftime("%H:%M:%S")})
    await S.push("feed", {"item": S.feed[-1]})

    # 测试端点必须确定性：临时绕开概率/冷却，否则测不出东西
    old_prob, old_cd = CFG.get("reply_prob"), CFG.get("cooldown")
    old_dbg = CFG.get("debug_always_reply")
    CFG["reply_prob"] = 1.0
    CFG["cooldown"] = 0
    CFG["debug_always_reply"] = True
    try:
        await on_danmaku(name, text)
    finally:
        CFG["reply_prob"], CFG["cooldown"] = old_prob, old_cd
        CFG["debug_always_reply"] = old_dbg

    return web.json_response({
        "ok": True,
        "singing": is_singing(),
        "sing_q": JOB_Q.qsize(),
        "play_q": PLAY_Q.qsize(),
    })


async def http_test_reset(req):
    """清空所有面板数据 —— 开播前必做。

    ⚠️ 为什么必须有这个：
        `/api/test/live` 和 `/api/test/gift` 注入的都是**假数据**
        （在线 138、收益 7.2 之类），它们会一直留在内存里。
        如果不清理就开播，观众会看到凭空捏造的在线人数和收益。
        开播前跑一次这个接口，保证面板从 0 开始。
    """
    S.live.update({
        "online": 0, "online_str": "", "total_viewers": 0,
        "total_viewers_str": "", "likes": 0, "gift_value": 0.0,
        "gift_count": 0, "gifters": 0, "gift_names": [], "gift_today": 0,
    })
    S._gift_users.clear()
    S._gift_by_user.clear()
    S._gift_by_kind.clear()
    S.stats.update({"events": 0, "replies": 0, "songs": 0})
    S.feed.clear()
    S.latest_bubble = {"name": "", "text": ""}
    S.current_song = None
    S.llm_history.clear()
    S.last_seen.clear()
    S.stat_date = time.strftime("%Y-%m-%d")

    # ⚠️ 必须连队列一起清（2026-10-27 实测踩到）：
    #    原来 reset 只清统计数据，**不碰队列**。
    #    结果开播前自测点了几首歌，reset 之后歌还在 JOB_Q/PLAY_Q 里，
    #    一开播就突然冒出一首「TEST - 邓典果DDG」这种测试歌播给观众。
    #    开播前的清理必须包含「把没播的测试歌扔掉」。
    dropped = 0
    for q in (JOB_Q, PLAY_Q):
        while True:
            try:
                q.get_nowait()
                dropped += 1
            except asyncio.QueueEmpty:
                break
    if dropped:
        log_event("重置", 丢弃队列项=dropped)

    await S.push("log", {"level": "info",
                         "msg": f"面板数据已重置（开播前清理，丢弃 {dropped} 条排队）"})
    return web.json_response({"ok": True, "live": S.live, "stats": S.stats,
                              "dropped": dropped})


def apply_gift_stats(name, gname, num, diamond, source="直播"):
    """更新礼物统计并记事件日志 —— 真实链路和测试接口共用。

    ⚠️ 为什么抽出来（2026-10-27）：原来 `/api/test/gift` 复制了一份统计逻辑，
    结果真实链路加了 log_event 之后，测试接口没跟上，
    自测时日志里一条礼物记录都没有，白排查半天。
    统计这种「两处必须一致」的逻辑，只能有一份实现。

    ⚠️ 必须做类型转换（2026-10-27 真实开播踩到，这是「礼物恒为 0」的真凶）：
       douyinLive 把 repeatCount 推成**字符串**（"1" 而不是 1），
       而 diamond 那侧是 float 乘数 → `str * float` 直接抛：
           TypeError: can't multiply sequence by non-int of type 'float'
       异常发生在统计函数内部，导致 gift_value/gift_count/gifters 全部不更新，
       界面上「今日礼物」「最近礼物」永远是 0。
       所以这里**先把两个数都强制转成数字**，再计算。
    """
    # 统一转成 int，任何异常都退回安全值（礼物统计不能因为脏数据断掉）
    try:
        n = int(float(str(num).strip() or "1"))
    except Exception:
        n = 1
    try:
        d = int(float(str(diamond).strip() or "0"))
    except Exception:
        d = 0

    yuan = d * n * DIAMOND_TO_YUAN
    S.live["gift_value"] = round(S.live["gift_value"] + yuan, 2)
    S.live["gift_count"] += n
    S._gift_users.add(name)
    S.live["gifters"] = len(S._gift_users)
    S.live["gift_names"] = (S.live["gift_names"] + [gname])[-8:]
    # 明细统计（/api/gifts 用）
    try:
        S._gift_by_user[name] = round(S._gift_by_user.get(name, 0.0) + yuan, 2)
        S._gift_by_kind[gname] = S._gift_by_kind.get(gname, 0) + n
    except Exception:
        pass
    log_event("礼物", 来源=source, 观众=name, 礼物=gname, 数量=n,
              diamond=d, 估算元=f"{yuan:.2f}",
              累计=f"{S.live['gift_value']:.2f}", 总件数=S.live["gift_count"])
    return yuan


async def http_skip(req):
    """切歌：立刻切断当前这首，播下一首。

    POST /api/skip

    ⚠️ 实现要点：不是调 stop_music，而是 set 一个 skip 事件。
       stop_music 会摘掉 on_music_end，play_worker 只能等 15 秒看门狗，
       观众会听到「切歌后差不多 15 秒静音」。skip 事件立刻唤醒。
    """
    if S.skip is None:
        return web.json_response({"ok": False, "error": "skip 未初始化"})
    cur = (S.current_song or {}).get("title", "")
    S.skip.set()
    log_event("切歌请求", 当前歌=cur)
    await S.push("log", {"level": "info", "msg": f"切歌：{cur or '(无)'}"})
    return web.json_response({"ok": True, "skipped": cur})


async def http_skip_reset(req):
    """清除切歌标志。play_worker 播下一首前会自动调它。

    ⚠️ 单独暴露出来是因为：如果玩家手动点了切歌但那一刻恰好没在播歌，
       skip 会一直挂着，导致**下一首刚开始播就被立刻切掉**。
       界面上提供「复位」按钮来救这种情况。
    """
    if S.skip is not None:
        S.skip.clear()
    return web.json_response({"ok": True})


async def http_order_pause(req):
    """暂停 / 恢复点歌。POST /api/order {"action":"pause|resume|toggle"}

    ⚠️ 和 /api/pause（暂停播放）是两个不同的东西：
        · /api/pause        暂停**当前这首的声音**，恢复后接着播
        · /api/order        拒绝**新的点歌请求**，已经在队列里的歌照常播完

    典型用法：
        · 主播想歇口气不接单        → 暂停点歌
        · 队列排太长想先消化        → 暂停点歌，等队列短了再开
        · 快下播了不想再收新歌      → 暂停点歌
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    act = (body.get("action") or "toggle").lower()

    if act == "pause":
        S.order_paused = True
    elif act == "resume":
        S.order_paused = False
    else:
        S.order_paused = not S.order_paused

    q = JOB_Q.qsize() + PLAY_Q.qsize()
    log_event("点歌开关", 状态="暂停" if S.order_paused else "恢复",
              队列剩余=q)
    await S.push("log", {"level": "info",
                         "msg": "点歌已暂停" if S.order_paused else "点歌已恢复"})
    return web.json_response({"ok": True, "order_paused": S.order_paused,
                              "queue_remaining": q})


async def http_sing_voice(req):
    """唱歌时是否也用语音回复弹幕。POST /api/sing_voice {"action":"toggle|on|off"}

    ⚠️ 开关打开后**不会**让 GPU 更忙（RVC 的锁是秒拒的，不会排队卡住），
       代价只是「歌在放的时候可能一边唱一边说话」，以及翻唱期间说的话
       会延后到翻唱结束再补说。
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    act = (body.get("action") or "toggle").lower()
    if act == "on":
        S.sing_voice_reply = True
    elif act == "off":
        S.sing_voice_reply = False
    else:
        S.sing_voice_reply = not S.sing_voice_reply

    if not S.sing_voice_reply:
        DEFER_VOICE.clear()      # 关掉就把攒着的句子作废，别等会儿突然补一串

    # 写回 config.json，重启后依然生效
    try:
        cfg_path = os.path.join(BASE, "config.json")
        disk = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                disk = json.load(f)
        disk["sing_voice_reply"] = bool(S.sing_voice_reply)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(disk, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error("写语音开关", e)

    log_event("唱歌语音开关", 状态="开" if S.sing_voice_reply else "关")
    await S.push("log", {"level": "info",
                         "msg": "唱歌时也允许语音回复" if S.sing_voice_reply
                                else "唱歌时只用气泡回复（默认）"})
    return web.json_response({"ok": True, "sing_voice_reply": S.sing_voice_reply,
                              "pending_voice": len(DEFER_VOICE)})


async def http_pause(req):
    """暂停 / 继续播放。POST /api/pause {"action":"pause|resume|toggle"}

    ⚠️ 暂停不会丢播放位置：mixer.pause() 只翻标志，
       恢复后从原来的秒数继续，不会从头播。
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    act = (body.get("action") or "toggle").lower()

    if act == "pause":
        S.mixer.pause()
    elif act == "resume":
        S.mixer.resume()
        # 继续时顺手清 skip，避免刚恢复就被切
        if S.skip is not None:
            S.skip.clear()
    else:
        if S.mixer.is_paused():
            S.mixer.resume()
        else:
            S.mixer.pause()

    paused = S.mixer.is_paused()
    log_event("暂停" if paused else "继续",
              当前歌=(S.current_song or {}).get("title", ""))
    await S.push("log", {"level": "info",
                         "msg": "已暂停播放" if paused else "已继续播放"})
    return web.json_response({"ok": True, "paused": paused})


async def http_queue_insert(req):
    """插队：把一首歌插到「下一个」，当前这首继续播完（方案 A）。

    POST /api/queue/insert {"kw":"晴天","name":"主播"}  按歌名现翻
    POST /api/queue/insert {"hot": {...}}               已在队列里的直接提前

    ⚠️ 为什么插队要走「准备」这一层：
       插队也要 mp3（要么翻唱、要么下载原曲），
       所以先把请求塞进 JOB_Q 会排到队尾，插队就失去意义了。
       这里直接把请求交给 prep 流程并**优先**，然后放进 hot_q。
       简化做法：调用与 prep_worker 相同的准备函数，完成后再进 hot_q。
    """
    try:
        body = await req.json()
    except Exception:
        body = {}

    kw = (body.get("kw") or "").strip()
    who = (body.get("name") or "主播").strip()[:20]
    kind = (body.get("kind") or "sing").strip()

    if not kw:
        return web.json_response({"ok": False, "error": "kw 不能为空"})

    if is_blacklisted(kw):
        return web.json_response({"ok": False, "error": f"《{kw}》在黑名单里"})

    asyncio.create_task(_prepare_and_hot(kw, who, kind))
    log_event("插队请求", 歌名=kw, 观众=who, 类型=kind)
    return web.json_response({"ok": True, "queued": kw, "note": "正在准备，好了会插到下一个"})


async def _prepare_and_hot(kw, who, kind):
    """后台准备插队歌曲 → 完成后放进 hot_q（下一个播）"""
    try:
        if kind == "raw":
            half = bool(CFG.get("raw_half", True))
            mp3, song, _kept, err = await rvc_raw(kw, half=half)
            if not mp3:
                raise RuntimeError(err or "原曲没找到")
            job = {"mp3": mp3, "title": song or kw, "name": who,
                   "raw": True}
        else:
            # 和 prep_worker 一样要标 sing_busy，防止 /speak 抢锁
            S.sing_busy = True
            try:
                mp3, song, err = await rvc_sing(kw)
            finally:
                S.sing_busy = False
            if not mp3:
                raise RuntimeError(err or "翻唱失败")
            job = {"mp3": mp3, "title": song or kw, "name": who,
                   "raw": False}

        try:
            S.hot_q.put_nowait(job)
        except asyncio.QueueFull:
            # 插队队列满 → 退回正常队列尾部
            await PLAY_Q.put(job)
            await S.push("log", {"level": "warn",
                                 "msg": f"插队队列满，{kw} 改回正常排队"})
            return
        await S.push("log", {"level": "info",
                             "msg": f"插队就绪：{job['title']}（下一个播）"})
        log_event("插队就绪", 歌名=job["title"], 观众=who)
        await reply_to_bubble("", f"插播一首《{job['title']}》，这首完了就放～",
                              tag="插队")
    except Exception as e:
        log_error("插队准备", e, 歌名=kw)
        await S.push("log", {"level": "error",
                             "msg": f"插队失败 [{kw}]：{e}"})


async def http_queue_remove(req):
    """从队列删掉某一首。POST /api/queue/remove {"index": 2} 或 {"kw":"晴天"}

    ⚠️ 只删**队列里还没播**的；正在播的那首请用 /api/skip。

    ⚠️ index 的正确含义（2026-10-27 修了一个差 1 的 bug）：
       前端表格里显示的是 _queue_preview() 的顺序，即
           [PLAY_Q 全部] + [JOB_Q 全部]
       而 index 是**从这个合并列表的 1 开始数**的（表格第 1 行就是 1）。

       旧代码有两个错误：
         ① `len(keep) == idx` —— len(keep) 从 0 起，idx 从 1 起，
            删第 2 首会命中第 3 首（用户实测反馈：双击第 2 首删掉了第 3 首）
         ② 在两个队列里**各自**按 index 找 —— 合并后的第 5 项可能属于
            PLAY_Q 也可能属于 JOB_Q，分开找位置必然错位
       正确做法：先算 index 落在哪个队列的第几项（用绝对位置换算），
       然后只在那一个队列里按 0 基下标删除。
    """
    try:
        body = await req.json()
    except Exception:
        body = {}

    idx = body.get("index")
    kw = (body.get("kw") or "").strip()

    play_items = []
    job_items = []
    try:
        play_items = list(PLAY_Q._queue)
    except Exception:
        pass
    try:
        job_items = list(JOB_Q._queue)
    except Exception:
        pass

    removed = None

    def _rebuild(q, items):
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break
        for it in items:
            try:
                q.put_nowait(it)
            except Exception:
                break

    if kw:
        # ---- 按歌名删：PLAY_Q 优先（它马上要播），其次 JOB_Q ----
        for src, q in (("play", PLAY_Q), ("job", JOB_Q)):
            arr = play_items if src == "play" else job_items
            for i, it in enumerate(arr):
                title = it.get("title") or it.get("kw") or ""
                if _norm_song(kw) in _norm_song(title):
                    removed = title
                    arr.pop(i)
                    _rebuild(q, arr)
                    break
            if removed:
                break
    elif idx is not None:
        try:
            n = int(idx)
        except Exception:
            return web.json_response({"ok": False, "error": "index 必须是数字"})
        total = len(play_items) + len(job_items)
        if n < 1 or n > total:
            return web.json_response({
                "ok": False,
                "error": f"序号超出范围（1~{total}）"})

        # ⚠️ 关键：n 是 1 基的合并序号，先换算成落在哪个队列的 0 基下标
        zero = n - 1
        if zero < len(play_items):
            # 落在 PLAY_Q
            it = play_items.pop(zero)
            removed = it.get("title") or it.get("kw") or ""
            _rebuild(PLAY_Q, play_items)
        else:
            # 落在 JOB_Q
            j = zero - len(play_items)
            it = job_items.pop(j)
            removed = it.get("kw") or it.get("title") or ""
            _rebuild(JOB_Q, job_items)
    else:
        return web.json_response({"ok": False, "error": "需要 index 或 kw"})

    if not removed:
        return web.json_response({"ok": False, "error": "没找到要删的歌"})
    log_event("删队列", 歌名=removed, 序号=idx, 关键词=kw)
    await S.push("log", {"level": "info", "msg": f"已从队列删除：{removed}"})
    return web.json_response({"ok": True, "removed": removed})


async def http_memory(req):
    """记忆库查询：GET /api/memory
                     GET /api/memory?uid=xxx      看某个人
                     GET /api/memory?nickname=小明 按昵称找

    给主播看的：AI 到底记住了谁、记了什么。
    排查「AI 怎么不记得他」时先来这里看。
    """
    # ⚠️ aiohttp 的 Request 没有 .query（那是 Flask 的写法），
    #    必须用 req.rel_url.query，否则运行时 AttributeError。
    qs = req.rel_url.query
    uid = (qs.get("uid") or "").strip()
    nn = (qs.get("nickname") or "").strip()

    if not uid and not nn:
        st = MEM.stats()
        return web.json_response({"ok": True, "stats": st})

    if not uid and nn:
        # 按昵称找最近的那个 id
        try:
            c = MEM._conn()
            row = c.execute(
                "SELECT id FROM fans WHERE nickname LIKE ? "
                "ORDER BY last_seen DESC LIMIT 1", (f"%{nn}%",)).fetchone()
            uid = row["id"] if row else ""
        except Exception:
            uid = ""
        if not uid:
            return web.json_response({"ok": False, "error": f"没找到昵称含「{nn}」的观众"})

    f = MEM.get(uid)
    if not f:
        return web.json_response({"ok": False, "error": "没有这个人的记录"})

    return web.json_response({
        "ok": True,
        "fan": f,
        "summary": MEM.summary(uid),
        "recent": MEM.recent_events(uid, 10),
        "prompt_view": MEM.profile_for_prompt(uid, f.get("nickname", "")),
    })


async def http_memory_forget(req):
    """忘掉某个人（或清空全部记忆）。POST /api/memory/forget {"uid":"..."} / {"all":true}

    ⚠️ 只删记忆，不影响直播和其他功能。
       用途：某人要求删除数据、或者记错了要重来。
    """
    try:
        body = await req.json()
    except Exception:
        body = {}

    if body.get("all"):
        try:
            c = MEM._conn()
            c.execute("DELETE FROM events")
            c.execute("DELETE FROM fans")
            c.commit()
            log_event("记忆清空", 操作="全部")
            return web.json_response({"ok": True, "cleared": "all"})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)[:200]})

    uid = str(body.get("uid") or "").strip()
    if not uid:
        return web.json_response({"ok": False, "error": "需要 uid 或 all"})
    try:
        c = MEM._conn()
        c.execute("DELETE FROM events WHERE fan_id=?", (uid,))
        c.execute("DELETE FROM fans WHERE id=?", (uid,))
        c.commit()
        log_event("记忆删除", uid=uid)
        return web.json_response({"ok": True, "deleted": uid})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)[:200]})


async def http_blacklist(req):
    """黑名单管理：GET 查看 / POST 增删。**改完立即生效，不用重启**。

    GET  /api/blacklist          → 看当前黑名单
    POST /api/blacklist {"add": ["告白气球"]}   → 加入
    POST /api/blacklist {"remove": ["告白气球"]} → 移除

    为什么做成运行时接口而不是只写 config.json：
        直播中才发现某首歌不适合（歌词不合适/翻唱一直失败），
        这时候去改文件再重启会打断直播。走接口立刻生效。
        改动同时会写回 config.json，重启后依然有效。

    ⚠️ 黑名单是「点歌时拦截」，不会删掉已经在队列里的歌。
       要清队列里已有的，得用 /api/test/reset 或等它播完。
    """
    if req.method == "GET":
        return web.json_response({
            "ok": True,
            "builtin": sorted(BLACKLIST_BUILTIN),
            "config": CFG.get("song_blacklist") or [],
            "effective": sorted(blacklist()),
        })

    try:
        body = await req.json()
    except Exception:
        body = {}

    cur = list(CFG.get("song_blacklist") or [])
    added, removed = [], []
    gone = []          # 被顺带清出队列的歌名（加黑名单时才有）

    for x in (body.get("add") or []):
        x = str(x).strip()
        if x and x not in cur:
            cur.append(x)
            added.append(x)
    for x in (body.get("remove") or []):
        x = str(x).strip()
        if x in cur:
            cur.remove(x)
            removed.append(x)

    if added or removed:
        CFG["song_blacklist"] = cur
        # 写回 config.json，重启后依然生效
        try:
            cfg_path = os.path.join(BASE, "config.json")
            disk = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, encoding="utf-8") as f:
                    disk = json.load(f)
            disk["song_blacklist"] = cur
            # ⚠️ 别把 llm_key 之类敏感字段写进 config.json（那个本来就不存）
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(disk, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_error("写黑名单", e)

        # ⚠️ 加了黑名单要**立刻清掉队列里已排的歌**（2026-10-28 用户需求）。
        #    否则「加了黑名单」只是不再接受新点歌，正在排的那首照样唱，
        #    主播看着像没生效。移除时也用 is_blacklisted，规则和点歌入口一致。
        if added:
            try:
                gone = _drop_from_queues(lambda t: is_blacklisted(t))
            except Exception as e:
                log_error("清队列", e)

        log_event("黑名单变更", 新增=added, 移除=removed,
                  清队列=gone, 当前=",".join(cur)[:200])
        msg = f"黑名单已更新：+{added} -{removed}"
        if gone:
            msg += f"　已从队列移除 {len(gone)} 首：{'、'.join(gone[:3])}"
        await S.push("log", {"level": "info", "msg": msg})

    return web.json_response({
        "ok": True, "builtin": sorted(BLACKLIST_BUILTIN),
        "config": cur, "added": added, "removed": removed,
        "dropped_from_queue": gone if (added or removed) else [],
        "effective": sorted(blacklist()),
    })


async def http_gifts(req):
    """礼物明细：GET /api/gifts

    面板只显示「今日礼物」总额，这个接口给主播看细节：
    谁送了什么、多少件、折算多少钱。按累计金额倒序。
    """
    return web.json_response({
        "ok": True,
        "total_value": S.live.get("gift_value", 0.0),
        "total_count": S.live.get("gift_count", 0),
        "gifters": S.live.get("gifters", 0),
        "by_user": sorted(
            [{"name": k, "value": round(v, 2)}
             for k, v in (S._gift_by_user or {}).items()],
            key=lambda x: x["value"], reverse=True),
        "by_gift": sorted(
            [{"gift": k, "count": v}
             for k, v in (S._gift_by_kind or {}).items()],
            key=lambda x: x["count"], reverse=True),
        "recent": (S.live.get("gift_names") or [])[-8:],
    })


async def http_test_gift(req):
    """模拟礼物：POST /api/test/gift  {"name":"测试","gift":"小花花","num":3,"diamond":10}"""
    try:
        body = await req.json()
    except Exception:
        body = {}
    name = (body.get("name") or "测试观众").strip()[:20]
    gname = (body.get("gift") or "小花花").strip()[:20]
    num = int(body.get("num") or 1)
    diamond = _num(body.get("diamond"), 0)
    apply_gift_stats(name, gname, num, diamond, source="测试注入")
    await S.push("event", {"type": "礼物(测试)", "name": name, "text": f"{gname} ×{num}"})
    feed_add({"type": "gift", "name": name, "text": f"{gname} ×{num}",
              "icon": "🎁", "t": time.strftime("%H:%M:%S")})
    await S.push("feed", {"item": S.feed[-1]})
    await S.push("gift_fx", {"name": name, "gift": gname, "num": num})
    await S.push("live_stats", dict(S.live))
    await on_gift(name, gname, num)
    return web.json_response({"ok": True, "singing": is_singing(),
                              "gift_value": S.live["gift_value"]})


async def http_test_live(req):
    """模拟在线人数/累计观看：POST /api/test/live {"online":138,"total":456,"likes":900}"""
    try:
        body = await req.json()
    except Exception:
        body = {}
    if "online" in body:
        S.live["online"] = _num(body["online"])
        S.live["online_str"] = str(body["online"])
    if "total" in body:
        S.live["total_viewers"] = _num(body["total"])
        S.live["total_viewers_str"] = str(body["total"])
    if "likes" in body:
        S.live["likes"] = _num(body["likes"])
    log_event("在线人数", 来源="测试注入", userCountStr=body.get("online", ""),
              totalUserStr=body.get("total", ""), likes=body.get("likes", ""))
    await S.push("live_stats", dict(S.live))
    return web.json_response({"ok": True, "live": S.live})


async def http_test_sing(req):
    """直接开始唱一首（跳过点歌）：POST /api/test/sing {"kw":"晴天"}"""
    try:
        body = await req.json()
    except Exception:
        body = {}
    kw = (body.get("kw") or "晴天").strip()
    await JOB_Q.put({"kind": "sing", "name": "测试", "kw": kw})
    return web.json_response({"ok": True, "sing_q": JOB_Q.qsize()})


async def http_test_raw(req):
    """放原曲：POST /api/test/raw {"kw":"晴天","half":true}"""
    try:
        body = await req.json()
    except Exception:
        body = {}
    kw = (body.get("kw") or "晴天").strip()
    if "half" in body:
        CFG["raw_half"] = bool(body["half"])
    await start_raw_song("测试", kw)
    return web.json_response({"ok": True, "raw_q": JOB_Q.qsize(),
                              "half": CFG.get("raw_half", True)})


async def http_test_stop(req):
    """停止当前音乐：POST /api/test/stop"""
    S.mixer.stop_music()
    S.mixer.stop_voice()
    S.mode = "idle"
    await S.push("motion", {"state": "idle"})
    return web.json_response({"ok": True})


# ---------- Web ----------
async def http_index(_):
    return web.FileResponse(os.path.join(WEB_DIR, "index.html"))


async def http_avatar(_):
    return web.FileResponse(os.path.join(WEB_DIR, "avatar.html"))


async def http_overlay(_):
    return web.FileResponse(os.path.join(WEB_DIR, "overlay.html"))


async def http_asset(req):
    """提供 avatar/ 下的图片和视频"""
    rel = req.match_info.get("path", "")
    base = os.path.realpath(AVATAR_DIR)
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        return web.Response(status=404)
    return web.FileResponse(full)


async def http_state(_):
    return web.json_response(S.snapshot())


async def summary_loop():
    """定期给「活跃观众」生成人物摘要（记忆库的"摘要"那一半）。

    ⚠️ 为什么要后台慢慢做：
       摘要要调 LLM，不能挡在弹幕回复的路径上（会拖慢响应）。
       这里每 5 分钟挑几个"事件够了但还没摘要/摘要过期"的人，
       用他最近的事件生成一段几十字的人物简介存起来。

    只处理最近 3 天活跃过的人，避免翻旧账浪费 token。
    """
    await asyncio.sleep(60)          # 启动后先让服务稳定
    while True:
        try:
            if CFG.get("memory_summary", True):
                await _do_summaries()
            # 顺手清理过老的低权重事件，防止库无限长
            try:
                MEM.prune_events()
            except Exception:
                pass
        except Exception as e:
            log_error("记忆摘要", e)
        await asyncio.sleep(300)     # 5 分钟一轮


async def _do_summaries(batch: int = 3):
    """挑最多 batch 个人生成摘要"""
    if not CFG.get("llm_key"):
        return
    now = int(time.time())
    cutoff = now - 3 * 86400
    try:
        c = MEM._conn()
        rows = c.execute(
            """SELECT id, nickname, last_seen, summary_at FROM fans
               WHERE last_seen > ? ORDER BY last_seen DESC LIMIT 40""",
            (cutoff,)).fetchall()
    except Exception:
        return

    done = 0
    for r in rows:
        if done >= batch:
            break
        uid = r["id"]
        if not MEM.needs_summary(uid):
            continue
        f = MEM.get(uid) or {}
        evs = MEM.recent_events(uid, limit=20)
        if not evs:
            continue
        lines = []
        for e in evs:
            k = {"gift": "送礼", "song": "点歌", "raw": "放原曲",
                 "enter": "进场", "chat": "说话", "follow": "关注"}.get(e["kind"], e["kind"])
            lines.append(f"- {k}：{e['content']}")
        prompt = (
            f"根据下面的记录，用**一到两句话**概括观众「{f.get('nickname') or r['nickname']}」"
            f"是个怎样的人 / 他喜欢什么 / 和主播有什么互动，"
            f"要求：不超过60字，只写客观特点，不要客套话、不要逐条罗列。\n\n"
            + "\n".join(lines[:20])
        )
        try:
            txt = await llm_reply(prompt, f.get("nickname") or "",
                                  use_history=False, mem_keyword="")
            if txt:
                MEM.set_summary(uid, txt)
                log_event("记忆摘要", 观众=f.get("nickname"), 摘要=txt)
                done += 1
        except Exception as e:
            log_error("记忆摘要单人", e, 观众=r["nickname"])
        await asyncio.sleep(1.0)     # 别把 LLM 打太快


async def panel_loop():
    """定时把整块面板状态推给前端（10 Hz）。

    ⚠️ 为什么用「快照推送」而不是让网页自己定时拉：
       OBS 浏览器源里跑的是同一套页面，走 WebSocket 少一层 HTTP 开销，
       频谱条/进度条要 10Hz 才顺滑，fetch 轮询做不到。
    """
    while True:
        try:
            await asyncio.sleep(0.1)
            if S.clients:
                await S.push("panel", S.snapshot())
        except Exception:
            await asyncio.sleep(1)


async def ws_handler(req):
    ws = web.WebSocketResponse()
    await ws.prepare(req)
    S.clients.add(ws)
    await ws.send_str(json.dumps({"kind": "hello", "stats": S.stats,
                                  "panel": S.snapshot()}, ensure_ascii=False))
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT and msg.data == "ping":
                await ws.send_str("pong")
    finally:
        S.clients.discard(ws)
    return ws


def build_app():
    app = web.Application()
    app.router.add_get("/", http_index)
    app.router.add_get("/avatar", http_avatar)
    app.router.add_get("/overlay", http_overlay)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/state", http_state)
    app.router.add_get("/asset/{path:.*}", http_asset)
    # 测试注入（开播前自测用，只绑 127.0.0.1）
    app.router.add_post("/api/test/danmaku", http_test_danmaku)
    app.router.add_post("/api/test/gift", http_test_gift)
    app.router.add_post("/api/test/sing", http_test_sing)
    app.router.add_post("/api/test/raw", http_test_raw)
    app.router.add_post("/api/test/live", http_test_live)
    app.router.add_post("/api/test/reset", http_test_reset)
    # 黑名单管理（GET 查看 / POST 增删，立即生效不用重启）
    app.router.add_get("/api/blacklist", http_blacklist)
    app.router.add_post("/api/blacklist", http_blacklist)
    # 礼物明细（谁送了什么）
    app.router.add_get("/api/gifts", http_gifts)
    # 记忆库（谁被记住了、记了什么、怎么忘掉）
    app.router.add_get("/api/memory", http_memory)
    app.router.add_post("/api/memory/forget", http_memory_forget)
    # 播放控制（切歌 / 暂停 / 插队 / 删队列）
    app.router.add_post("/api/skip", http_skip)
    app.router.add_post("/api/skip/reset", http_skip_reset)
    app.router.add_post("/api/pause", http_pause)
    app.router.add_post("/api/order", http_order_pause)
    # 唱歌时是否允许语音回复（控制台开关）
    app.router.add_post("/api/sing_voice", http_sing_voice)
    app.router.add_post("/api/queue/insert", http_queue_insert)
    app.router.add_post("/api/queue/remove", http_queue_remove)
    app.router.add_post("/api/test/stop", http_test_stop)
    return app


async def main():
    load_cfg()
    # 唱歌时是否允许语音回复：从 config.json 读初值（控制台开关会写回它）
    S.sing_voice_reply = bool(CFG.get("sing_voice_reply", False))
    os.makedirs(WEB_DIR, exist_ok=True)

    if not S.mixer.ensure_stream():
        print("!! 混音器启动失败，音频不会输出")
    else:
        print("音频已输出到 CABLE Input")

    # 切歌信号 & 插队队列必须在事件循环里创建（asyncio 对象绑定 loop）
    S.skip = asyncio.Event()
    S.hot_q = asyncio.Queue(maxsize=4)

    asyncio.create_task(douyin_loop())
    # 三个 worker：语音播报 / 准备（翻唱+下载，按到达顺序）/ 唯一播放出口
    #
    # ⚠️ 2026-10-27 重构：原来的 sing_worker + raw_worker 两个「准备者」合并成
    #    prep_worker，播放统一由 play_worker 负责。
    #    否则两者都调 Mixer.play_music（覆盖式），会互相顶掉正在播的歌。
    asyncio.create_task(voice_worker())
    # 唱歌期间攒下的语音，等 GPU 锁放开补说（开关打开才有活干）
    asyncio.create_task(deferred_voice_worker())
    asyncio.create_task(prep_worker())
    asyncio.create_task(play_worker())
    # 面板状态定时推送（频谱条/进度条用）
    asyncio.create_task(panel_loop())
    # 记忆库：后台生成人物摘要 + 清理旧事件
    asyncio.create_task(summary_loop())
    # 说话时把音乐压低到 30%（可改）
    S.mixer.set_duck(CFG.get("duck", 0.3))

    # 队列恢复：把上次落盘的点歌队列灌回来（防止重启丢歌）。
    # 放在 worker 启动之后、事件循环真正跑起来之前，worker 一跑就能看到。
    n_restored = _load_queue()
    if n_restored:
        print(f"队列已恢复 {n_restored} 首")
        log_event("队列恢复", 条数=n_restored)
    asyncio.create_task(queue_save_loop())
    asyncio.create_task(restored_flush_loop())

    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", CFG["web_port"])
    await site.start()
    print(f"控制台  http://127.0.0.1:{CFG['web_port']}/")
    print(f"皮套源  http://127.0.0.1:{CFG['web_port']}/avatar")
    print(f"字幕源  http://127.0.0.1:{CFG['web_port']}/overlay")

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
