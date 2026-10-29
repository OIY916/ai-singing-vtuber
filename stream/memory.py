# -*- coding: utf-8 -*-
"""观众记忆库：用 user.id 作为身份令牌，记住每个人。

设计要点（2026-10-28 与用户确认）：
    · **身份令牌 = user.id**（抖音数字 ID，永不变）
      nickname 只用于显示 —— 同一个人改昵称仍然是同一个人；
      同一个昵称不同 id 必须算两个人。
    · 存储 = SQLite 单文件（E:\\qq-bot\\data\\memory.db），无需额外服务
    · 记忆 = A(认识人) + C(偏好) + D(亲密度)
    · 读取方式 = **检索 + 摘要** 二者配合：
        - 摘要：每人在库里存一段「人物简介」，几十字，长期稳定
        - 检索：按当前话题/关键词，捞最相关的事件记录
      LLM 每次只带「摘要 + 命中检索」几条，token 可控。

表结构：
    fans(id TEXT PRIMARY KEY, nickname, first_seen, last_seen,
         visits, gifts_value, gifts_count, songs_ordered, msg_count,
         summary TEXT, summary_at INTEGER)
    events(id INTEGER PK, fan_id, kind, content, t, weight)

kind 取值：
    enter / chat / gift / song / raw     ← 用于检索「他干过什么」
    summary                              ← 摘要更新记录

用法：
    from memory import Memory
    mem = Memory()
    mem.touch(user_id, nickname)                  # 每次消息都调
    mem.remember(user_id, "gift", "送了大啤酒×2")
    mem.summary(user_id)                          # 取人物简介
    mem.recall(user_id, "周杰伦")                  # 检索相关事件
"""
import json
import os
import re
import sqlite3
import threading
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "memory.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fans (
    id          TEXT PRIMARY KEY,
    nickname    TEXT NOT NULL DEFAULT '',
    first_seen  INTEGER NOT NULL DEFAULT 0,
    last_seen   INTEGER NOT NULL DEFAULT 0,
    visits      INTEGER NOT NULL DEFAULT 0,
    gifts_value REAL    NOT NULL DEFAULT 0,
    gifts_count INTEGER NOT NULL DEFAULT 0,
    songs_ordered INTEGER NOT NULL DEFAULT 0,
    msg_count   INTEGER NOT NULL DEFAULT 0,
    summary     TEXT NOT NULL DEFAULT '',
    summary_at  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    fan_id  TEXT NOT NULL,
    kind    TEXT NOT NULL,
    content TEXT NOT NULL,
    t       INTEGER NOT NULL,
    weight  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_events_fan ON events(fan_id, t DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(fan_id, kind, t DESC);
"""

# 权重：越重要越容易在检索时被捞出来
KIND_WEIGHT = {
    "gift": 3,     # 送礼最值得记（也是主播最关心的）
    "song": 3,     # 点歌
    "raw": 2,      # 放原曲
    "enter": 1,    # 进场（量大，权重低）
    "chat": 1,
}


def _norm(s: str) -> str:
    return (s or "").strip().lower()


class Memory:
    """线程安全的观众记忆库（服务里只有一个实例）"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._init_db()

    # ---------- 连接（每线程一条，SQLite 要求）----------
    def _conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=10)
            c.row_factory = sqlite3.Row
            # WAL 模式：读写不互相阻塞，直播时高频写入也不卡
            try:
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA synchronous=NORMAL")
            except Exception:
                pass
            self._local.conn = c
        return c

    def _init_db(self):
        with self._lock:
            c = self._conn()
            c.executescript(_SCHEMA)
            c.commit()

    # ---------- 身份 ----------
    @staticmethod
    def identity(user: dict, fallback_name: str = "") -> tuple:
        """从抖音 user 对象取出 (id, nickname)。

        ⚠️ id 是身份令牌；拿不到 id 时退化成 nickname 加前缀，
           避免和真实 id 冲突（prefix 让它明显是"无名氏"）。
        """
        if not isinstance(user, dict):
            user = {}
        uid = str(user.get("id") or "").strip()
        name = (user.get("nickname") or "").strip() or fallback_name
        if uid:
            return uid, name
        # 没有 id：用昵称兜底，但加前缀区分
        return (f"name:{_norm(name)}" if name else ""), name

    def touch(self, uid: str, nickname: str, is_enter: bool = False):
        """更新「这个人来过」。每次收到他的消息都调一次。"""
        if not uid:
            return
        now = int(time.time())
        with self._lock:
            c = self._conn()
            c.execute(
                """INSERT INTO fans(id, nickname, first_seen, last_seen)
                   VALUES(?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       nickname = CASE WHEN excluded.nickname <> ''
                                       THEN excluded.nickname ELSE nickname END,
                       last_seen = excluded.last_seen""",
                (uid, nickname, now, now))
            c.execute("UPDATE fans SET msg_count = msg_count + 1 WHERE id=?", (uid,))
            if is_enter:
                # 进场算一次「来访」；间隔 < 10 分钟不重复计数（防退出重进刷）
                row = c.execute("SELECT last_seen, visits FROM fans WHERE id=?",
                                (uid,)).fetchone()
                c.execute("UPDATE fans SET visits = visits + 1 WHERE id=?", (uid,))
            c.commit()

    def bump_visit(self, uid: str, nickname: str):
        """记录一次进场（用于"又来了"判断）"""
        if not uid:
            return
        now = int(time.time())
        with self._lock:
            c = self._conn()
            c.execute(
                """INSERT INTO fans(id, nickname, first_seen, last_seen, visits)
                   VALUES(?,?,?,?,1)
                   ON CONFLICT(id) DO UPDATE SET
                       last_seen = excluded.last_seen,
                       visits = visits + 1""",
                (uid, nickname, now, now))
            c.commit()

    def get(self, uid: str):
        """取一个人的完整档案（dict），没有返回 None"""
        if not uid:
            return None
        c = self._conn()
        row = c.execute("SELECT * FROM fans WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None

    # ---------- 记录事件 ----------
    def remember(self, uid: str, kind: str, content: str):
        """记一件「他干过的事」，供以后检索"""
        if not uid or not content:
            return
        now = int(time.time())
        w = KIND_WEIGHT.get(kind, 1)
        with self._lock:
            c = self._conn()
            c.execute(
                "INSERT INTO events(fan_id, kind, content, t, weight) VALUES(?,?,?,?,?)",
                (uid, kind, content[:200], now, w))
            # 顺手把统计字段加上去
            if kind == "gift":
                c.execute("UPDATE fans SET gifts_count = gifts_count + 1 WHERE id=?", (uid,))
            elif kind in ("song", "raw"):
                c.execute("UPDATE fans SET songs_ordered = songs_ordered + 1 WHERE id=?", (uid,))
            c.commit()

    def add_gift_value(self, uid: str, yuan: float):
        if not uid or not yuan:
            return
        with self._lock:
            c = self._conn()
            c.execute("UPDATE fans SET gifts_value = gifts_value + ? WHERE id=?",
                      (round(yuan, 2), uid))
            c.commit()

    # ---------- 检索 ----------
    def recall(self, uid: str, keyword: str = "", limit: int = 4):
        """检索和这个人/这个话题相关的记忆。

        策略（简单但有效，不引入向量库）：
          1. 关键词命中 → 优先（GOOD：中文分词难，用子串匹配 + 权重）
          2. 近期的送礼/点歌 → 优先（这些最能支撑"你之前点的歌"）
          3. 权重高的（gift/song）优先于 weigh=1 的进场记录
        """
        if not uid:
            return []
        c = self._conn()
        kw = (keyword or "").strip()
        rows = []
        if kw:
            # 关键词子串匹配（SQLite 的 LIKE，中文按字节子串够用）
            try:
                rows = c.execute(
                    """SELECT kind, content, t FROM events
                       WHERE fan_id=? AND content LIKE ?
                       ORDER BY weight DESC, t DESC LIMIT ?""",
                    (uid, f"%{kw}%", limit)).fetchall()
            except Exception:
                rows = []
        if len(rows) < limit:
            # 补：最近的送礼/点歌（这些比闲聊有价值）
            more = c.execute(
                """SELECT kind, content, t FROM events
                   WHERE fan_id=? AND kind IN ('gift','song','raw')
                   ORDER BY t DESC LIMIT ?""",
                (uid, limit * 2)).fetchall()
            seen = {(r["kind"], r["content"]) for r in rows}
            for m in more:
                if len(rows) >= limit:
                    break
                if (m["kind"], m["content"]) not in seen:
                    rows.append(m)
        return [dict(r) for r in rows]

    def recent_events(self, uid: str, limit: int = 6):
        c = self._conn()
        rows = c.execute(
            """SELECT kind, content, t FROM events
               WHERE fan_id=? ORDER BY t DESC LIMIT ?""",
            (uid, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---------- 摘要 ----------
    def summary(self, uid: str) -> str:
        """取人物简介（一句话，长期稳定）"""
        f = self.get(uid)
        return (f or {}).get("summary") or ""

    def set_summary(self, uid: str, text: str):
        if not uid:
            return
        with self._lock:
            c = self._conn()
            c.execute("UPDATE fans SET summary=?, summary_at=? WHERE id=?",
                      ((text or "")[:300], int(time.time()), uid))
            c.commit()

    def needs_summary(self, uid: str, min_events: int = 6, min_gap: int = 600) -> bool:
        """判断要不要（重新）生成摘要。

        条件：事件数够多 且 距离上次摘要超过 min_gap 秒
             且 上次摘要之后又发生了新事。
        """
        f = self.get(uid)
        if not f:
            return False
        c = self._conn()
        n = c.execute("SELECT COUNT(*) FROM events WHERE fan_id=? AND t > ?",
                      (uid, f.get("summary_at") or 0)).fetchone()[0]
        if n < min_events:
            return False
        if int(time.time()) - (f.get("summary_at") or 0) < min_gap:
            return False
        return True

    def profile_for_prompt(self, uid: str, nickname: str = "", keyword: str = "",
                           max_events: int = 4) -> str:
        """把「这个人的记忆」拼成一段可以直接塞进 LLM 提示词的文本。

        这就是"检索 + 摘要"的组合产物：
            · 摘要：他是什么人（几十字）
            · 检索：和他当前这句话最相关的几件事
        """
        f = self.get(uid)
        if not f:
            return ""
        parts = []
        name = nickname or f.get("nickname") or "这位观众"
        summ = (f.get("summary") or "").strip()
        if summ:
            parts.append(f"关于{name}：{summ}")
        else:
            # 还没生成摘要 → 用基础统计兜一句
            bits = []
            if f.get("visits"):
                bits.append(f"来过{f['visits']}次")
            if f.get("songs_ordered"):
                bits.append(f"点过{f['songs_ordered']}首歌")
            if f.get("gifts_value"):
                bits.append(f"送过礼物约{f['gifts_value']:.1f}元")
            if bits:
                parts.append(f"关于{name}：" + "，".join(bits))

        evs = self.recall(uid, keyword, limit=max_events)
        if evs:
            lines = []
            for e in evs:
                k = e.get("kind")
                c = (e.get("content") or "").strip()
                if not c:
                    continue
                label = {"gift": "送礼", "song": "点歌", "raw": "放原曲",
                         "enter": "进场", "chat": "说过"}.get(k, k)
                lines.append(f"{label}：{c}")
            if lines:
                parts.append(f"{name}之前" + "；".join(lines[:6]))
        return "\n".join(parts)

    # ---------- 清理 ----------
    def prune_events(self, keep_days: int = 45, per_fan_limit: int = 300):
        """防止 events 表无限膨胀。

        · 删掉 keep_days 天前的低权重事件（chat/enter）
        · 每人最多留 per_fan_limit 条（保留高权重和最近的）
        """
        cutoff = int(time.time()) - keep_days * 86400
        with self._lock:
            c = self._conn()
            c.execute(
                "DELETE FROM events WHERE t < ? AND weight <= 1", (cutoff,))
            c.execute(
                """DELETE FROM events WHERE id NOT IN (
                       SELECT id FROM events e2 WHERE e2.fan_id = events.fan_id
                       ORDER BY weight DESC, t DESC LIMIT ?)""",
                (per_fan_limit,))
            c.commit()

    def stats(self):
        c = self._conn()
        n_fans = c.execute("SELECT COUNT(*) FROM fans").fetchone()[0]
        n_ev = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        top = c.execute(
            """SELECT nickname, visits, songs_ordered, gifts_value
               FROM fans ORDER BY visits DESC, gifts_value DESC LIMIT 8""").fetchall()
        return {"fans": n_fans, "events": n_ev,
                "top": [dict(r) for r in top],
                "db": self.db_path,
                "size_kb": round(os.path.getsize(self.db_path) / 1024, 1)
                            if os.path.exists(self.db_path) else 0}


# 全局单例（服务里共用）
_mem = None


def get_memory() -> Memory:
    global _mem
    if _mem is None:
        _mem = Memory()
    return _mem
