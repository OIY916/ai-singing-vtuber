# -*- coding: utf-8 -*-
"""大肥鱼直播间 · 桌面控制台

功能：
    · 实时看：正在播什么、进度、队列、在线人数、礼物
    · 切歌：立刻切断当前这首，播下一首
    · 暂停 / 继续：暂停不丢播放位置，继续从原处播
    · 插队：把一首歌排到"下一个"（当前这首继续播完，不打断）
    · 删歌：从队列里删掉指定的一首
    · 黑名单：让某些歌不再被点

启动：
    直接双击 tools/直播控制台.pyw（无黑窗）
    或  E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\直播控制台.py

⚠️ 这是**独立进程**，只管发 HTTP 请求给主服务，
   不影响直播、不占 GPU、崩溃了也不影响主服务。
"""
import json
import os
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import messagebox, ttk

BASE = "http://127.0.0.1:8790"
REFRESH_MS = 1500          # 界面刷新间隔

# ---- 配色（和直播画面风格一致）----
BG = "#0e1a2b"
CARD = "#16263d"
FG = "#dbe9fb"
DIM = "#7d95b5"
ACCENT = "#4fb0ff"
DANGER = "#ff6b6b"
GOOD = "#5bd98a"
GOLD = "#ffc95c"


def api(path, payload=None, timeout=15):
    """调主服务接口。返回 dict；失败返回 {"ok":False,"error":...}"""
    url = BASE + path
    try:
        if payload is None:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class App:
    def __init__(self, root):
        self.root = root
        root.title("🐋 大肥鱼直播间 · 控制台")
        root.geometry("760x780")
        root.minsize(680, 700)
        root.configure(bg=BG)

        self.state_data = {}
        self.busy = False

        self._build_top()
        self._build_nowplaying()
        self._build_actions()
        self._build_queue()
        self._build_blacklist()

        self.refresh()
        self._auto_refresh()

    # ---------- 顶部状态条 ----------
    def _build_top(self):
        bar = tk.Frame(self.root, bg=CARD, height=46)
        bar.pack(fill="x", padx=10, pady=(10, 6))
        bar.pack_propagate(False)

        self.dot = tk.Label(bar, text="●", fg=DIM, bg=CARD,
                            font=("Microsoft YaHei", 14))
        self.dot.pack(side="left", padx=(12, 4), pady=8)

        self.conn_lbl = tk.Label(bar, text="连接中…", fg=FG, bg=CARD,
                                 font=("Microsoft YaHei", 11, "bold"))
        self.conn_lbl.pack(side="left", padx=2)

        self.stat_lbl = tk.Label(bar, text="", fg=DIM, bg=CARD,
                                 font=("Microsoft YaHei", 10))
        self.stat_lbl.pack(side="right", padx=14)

    # ---------- 正在播放 ----------
    def _build_nowplaying(self):
        card = tk.Frame(self.root, bg=CARD)
        card.pack(fill="x", padx=10, pady=6)

        tk.Label(card, text="正在播放", fg=DIM, bg=CARD,
                 font=("Microsoft YaHei", 10)).pack(anchor="w", padx=14, pady=(10, 0))

        self.np_title = tk.Label(card, text="—", fg=FG, bg=CARD,
                                 font=("Microsoft YaHei", 15, "bold"),
                                 anchor="w", justify="left")
        self.np_title.pack(fill="x", padx=14, pady=(2, 2))

        self.np_sub = tk.Label(card, text="", fg=DIM, bg=CARD,
                               font=("Microsoft YaHei", 9), anchor="w")
        self.np_sub.pack(fill="x", padx=14)

        # 进度条
        pf = tk.Frame(card, bg=CARD)
        pf.pack(fill="x", padx=14, pady=(8, 4))
        self.prog = ttk.Progressbar(pf, orient="horizontal", length=400,
                                    mode="determinate", maximum=100)
        self.prog.pack(side="left", fill="x", expand=True)
        self.prog_lbl = tk.Label(pf, text="0:00 / 0:00", fg=DIM, bg=CARD,
                                 font=("Consolas", 10))
        self.prog_lbl.pack(side="right", padx=(10, 0))

        tk.Frame(card, bg=BG, height=6).pack()

    # ---------- 操作按钮 ----------
    def _build_actions(self):
        card = tk.Frame(self.root, bg=CARD)
        card.pack(fill="x", padx=10, pady=6)

        tk.Label(card, text="播放控制", fg=DIM, bg=CARD,
                 font=("Microsoft YaHei", 10)).pack(anchor="w", padx=14, pady=(10, 4))

        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=14, pady=(0, 10))

        self.btn_skip = self._btn(row, "⏭  切歌", self.on_skip, DANGER)
        self.btn_skip.pack(side="left", padx=(0, 8))

        self.btn_pause = self._btn(row, "⏸  暂停", self.on_pause, ACCENT)
        self.btn_pause.pack(side="left", padx=(0, 8))

        self.btn_reset = self._btn(row, "↺  复位切歌", self.on_skip_reset, DIM)
        self.btn_reset.pack(side="left", padx=(0, 8))

        # 点歌开关（和上面的「暂停播放」是两回事，所以单独一行更清楚）
        row3 = tk.Frame(card, bg=CARD)
        row3.pack(fill="x", padx=14, pady=(0, 10))

        tk.Label(row3, text="接单", fg=DIM, bg=CARD,
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 8))

        self.btn_order = self._btn(row3, "🚫 暂停点歌", self.on_order_toggle, GOLD)
        self.btn_order.pack(side="left", padx=(0, 10))

        self.order_lbl = tk.Label(row3, text="", fg=DIM, bg=CARD,
                                  font=("Microsoft YaHei", 9))
        self.order_lbl.pack(side="left")

        # 唱歌时是否用语音回复（2026-10-29 新增）
        # 默认关：唱歌时只说气泡，保护 RVC 的 GPU 锁。
        # 打开后：歌在放的时候可以直接说话（音乐自动压到 30%）；
        #         翻唱正在转的时候说的话会攒着，等翻唱完补说。
        row_voice = tk.Frame(card, bg=CARD)
        row_voice.pack(fill="x", padx=14, pady=(0, 10))

        tk.Label(row_voice, text="语音回复", fg=DIM, bg=CARD,
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 8))

        self.btn_voice = self._btn(row_voice, "🔇 唱歌时只气泡",
                                   self.on_voice_toggle, DIM)
        self.btn_voice.pack(side="left", padx=(0, 10))

        self.voice_lbl = tk.Label(row_voice, text="", fg=DIM, bg=CARD,
                                  font=("Microsoft YaHei", 9))
        self.voice_lbl.pack(side="left")

        # 插队一行
        row2 = tk.Frame(card, bg=CARD)
        row2.pack(fill="x", padx=14, pady=(0, 12))

        tk.Label(row2, text="插队", fg=DIM, bg=CARD,
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 6))
        self.ent_insert = tk.Entry(row2, bg="#0b1626", fg=FG, insertbackground=FG,
                                   relief="flat", font=("Microsoft YaHei", 11))
        self.ent_insert.pack(side="left", fill="x", expand=True, ipady=5)
        self.ent_insert.insert(0, "歌名")
        self._placeholder(self.ent_insert, "歌名")
        self.ent_insert.bind("<Return>", lambda e: self.on_insert())

        self._btn(row2, "插入下一首", self.on_insert, GOOD).pack(side="left", padx=(8, 0))

    # ---------- 队列 ----------
    def _build_queue(self):
        card = tk.Frame(self.root, bg=CARD)
        card.pack(fill="both", expand=True, padx=10, pady=6)

        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=14, pady=(10, 4))
        self.q_head = tk.Label(head, text="接下来 · 队列", fg=DIM, bg=CARD,
                               font=("Microsoft YaHei", 10))
        self.q_head.pack(side="left")
        tk.Label(head, text="双击某首可删除", fg=DIM, bg=CARD,
                 font=("Microsoft YaHei", 9)).pack(side="right")

        # 队列列表（可滚动）
        wrap = tk.Frame(card, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.tree = ttk.Treeview(wrap, columns=("idx", "name", "who", "tag"),
                                 show="headings", height=8)
        self.tree.heading("idx", text="#")
        self.tree.heading("name", text="歌名")
        self.tree.heading("who", text="点歌人")
        self.tree.heading("tag", text="状态")
        self.tree.column("idx", width=36, anchor="center")
        self.tree.column("name", width=380, anchor="w")
        self.tree.column("who", width=110, anchor="w")
        self.tree.column("tag", width=80, anchor="center")

        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self.on_remove)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", background="#0b1626", fieldbackground="#0b1626",
                        foreground=FG, borderwidth=0, rowheight=26)
        style.configure("Treeview.Heading", background=CARD, foreground=DIM,
                        borderwidth=0, font=("Microsoft YaHei", 9))
        style.map("Treeview", background=[("selected", "#1d3a5c")])
        style.configure("TProgressbar", troughcolor="#0b1626",
                        background=ACCENT, borderwidth=0)

    # ---------- 黑名单 ----------
    def _build_blacklist(self):
        card = tk.Frame(self.root, bg=CARD)
        card.pack(fill="x", padx=10, pady=(6, 4))

        # 记忆库概况（一行，占位小）
        memrow = tk.Frame(card, bg=CARD)
        memrow.pack(fill="x", padx=14, pady=(8, 2))
        tk.Label(memrow, text="🧠 记忆库", fg=DIM, bg=CARD,
                 font=("Microsoft YaHei", 10)).pack(side="left")
        self.mem_lbl = tk.Label(memrow, text="读取中…", fg=ACCENT, bg=CARD,
                                font=("Microsoft YaHei", 9), anchor="w")
        self.mem_lbl.pack(side="left", padx=(8, 0))

        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=14, pady=(6, 2))
        tk.Label(head, text="黑名单（这些歌不会被点）", fg=DIM, bg=CARD,
                 font=("Microsoft YaHei", 10)).pack(side="left")

        # ⚠️ 黑名单内容单独一行显示（原来挤在标题右边，一长串就看不全）
        self.bl_lbl = tk.Label(card, text="（空）", fg=GOLD, bg=CARD,
                               font=("Microsoft YaHei", 9),
                               anchor="w", justify="left", wraplength=700)
        self.bl_lbl.pack(fill="x", padx=14, pady=(0, 6))

        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=14, pady=(0, 10))
        self.ent_bl = tk.Entry(row, bg="#0b1626", fg=FG, insertbackground=FG,
                               relief="flat", font=("Microsoft YaHei", 11))
        self.ent_bl.pack(side="left", fill="x", expand=True, ipady=5)
        self._placeholder(self.ent_bl, "歌名（可只写歌名部分）")
        self.ent_bl.bind("<Return>", lambda e: self.on_bl_add())
        self._btn(row, "加入", self.on_bl_add, DANGER).pack(side="left", padx=(8, 0))

    # ---------- 小工具 ----------
    def _btn(self, parent, text, cmd, color):
        b = tk.Button(parent, text=text, command=cmd, bg=color, fg="#08121f",
                      activebackground=color, activeforeground="#08121f",
                      relief="flat", font=("Microsoft YaHei", 10, "bold"),
                      padx=14, pady=6, cursor="hand2", bd=0)
        return b

    def _placeholder(self, entry, hint):
        """输入框占位提示（聚焦时消失）"""
        def on_focus_in(_):
            if entry.get() == hint:
                entry.delete(0, "end")
                entry.config(fg=FG)
        def on_focus_out(_):
            if not entry.get().strip():
                entry.insert(0, hint)
                entry.config(fg=DIM)
        entry.config(fg=DIM)
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)

    def _entry_value(self, entry, hint):
        v = entry.get().strip()
        return "" if v == hint else v

    # ---------- 刷新 ----------
    def _auto_refresh(self):
        self.refresh()
        self.root.after(REFRESH_MS, self._auto_refresh)

    def refresh(self):
        d = api("/api/state", timeout=5)
        if not d.get("ok", True) or "mode" not in d:
            self.dot.config(fg=DANGER)
            self.conn_lbl.config(text="连不上主服务", fg=DANGER)
            return
        self.state_data = d
        self.dot.config(fg=GOOD)
        self.conn_lbl.config(text="已连接", fg=GOOD)

        live = d.get("live") or {}
        au = d.get("audio") or {}
        self.stat_lbl.config(
            text=f"在线 {live.get('online', 0)}   "
                 f"礼物 ¥{live.get('gift_value', 0):.2f}   "
                 f"已唱 {d.get('stats', {}).get('songs', 0)} 首")

        # 正在播放
        cur = d.get("current") or {}
        title = cur.get("title") or "（空闲）"
        self.np_title.config(text=title)
        by = cur.get("by") or ""
        kind = "原曲" if cur.get("raw") else "翻唱"
        self.np_sub.config(text=(f"{kind} · 点歌人：{by}" if by else kind)
                           if cur else "队列空着，等观众点歌")

        pos = au.get("music_pos") or 0
        dur = au.get("music_dur") or 0
        if dur > 0:
            self.prog["value"] = min(100, pos / dur * 100)
        else:
            self.prog["value"] = 0
        self.prog_lbl.config(text=f"{self._t(pos)} / {self._t(dur)}")

        # 暂停按钮文案
        if au.get("paused"):
            self.btn_pause.config(text="▶  继续", bg=GOOD)
        else:
            self.btn_pause.config(text="⏸  暂停", bg=ACCENT)

        # 点歌开关状态
        order_paused = bool(d.get("order_paused"))
        if order_paused:
            self.btn_order.config(text="✅ 恢复点歌", bg=GOOD)
            self.order_lbl.config(text="点歌已关（队列里的照常播）", fg=GOLD)
        else:
            self.btn_order.config(text="🚫 暂停点歌", bg=GOLD)
            self.order_lbl.config(text="正在接单", fg=DIM)

        # 唱歌语音开关状态
        if d.get("sing_voice_reply"):
            self.btn_voice.config(text="🎤 唱歌时也说话", bg=GOOD)
            self.voice_lbl.config(text="已开（歌在放时会出声）", fg=GOLD)
        else:
            self.btn_voice.config(text="🔇 唱歌时只气泡", bg=DIM)
            self.voice_lbl.config(text="唱歌时不出声（默认）", fg=DIM)

        # 队列
        items = d.get("queue_items") or []
        self.q_head.config(text=f"接下来 · 队列（{len(items)} 首）")
        self._render_queue(items)

        # 黑名单
        bl = api("/api/blacklist", timeout=5)
        if bl.get("ok"):
            cfg = bl.get("config") or []
            self.bl_lbl.config(text="、".join(cfg) if cfg else "（空）")

        # 记忆库概况（每 5 次刷新查一次，别太频繁）
        self._mem_tick = getattr(self, "_mem_tick", 0) + 1
        if self._mem_tick % 5 == 1:
            mem = api("/api/memory", timeout=5)
            if mem.get("ok"):
                s = mem.get("stats") or {}
                top = s.get("top") or []
                names = "、".join(x.get("nickname", "") for x in top[:3]
                                 if x.get("nickname"))
                self.mem_lbl.config(
                    text=f"记住 {s.get('fans', 0)} 位观众 / "
                         f"{s.get('events', 0)} 条记忆"
                         + (f"　常客：{names}" if names else ""))

    def _render_queue(self, items):
        """只在内容变化时重建，避免闪烁"""
        key = "|".join(f"{x.get('kw')}#{x.get('name')}{int(bool(x.get('ready')))}"
                       for x in items)
        if getattr(self, "_qkey", None) == key:
            return
        self._qkey = key
        self.tree.delete(*self.tree.get_children())
        for i, it in enumerate(items, 1):
            tag = "待播" if it.get("ready") else "准备中"
            if it.get("raw"):
                tag = "原曲·" + tag
            self.tree.insert("", "end", values=(
                i, it.get("kw", ""), it.get("name", ""), tag))

    @staticmethod
    def _t(sec):
        sec = max(0, int(sec or 0))
        return f"{sec // 60}:{sec % 60:02d}"

    def _toast(self, msg, error=False):
        self.conn_lbl.config(text=msg, fg=DANGER if error else GOOD)
        self.root.after(3000, lambda: self.conn_lbl.config(
            text="已连接", fg=GOOD) if not error else None)

    # ---------- 操作 ----------
    def on_skip(self):
        cur = (self.state_data.get("current") or {}).get("title") or "当前这首"
        if not messagebox.askyesno("切歌", f"立刻切断：\n{cur}\n\n然后播下一首？"):
            return
        r = api("/api/skip", {}, timeout=8)
        self._toast("已切歌" if r.get("ok") else f"失败：{r.get('error')}",
                    not r.get("ok"))
        self.root.after(600, self.refresh)

    def on_skip_reset(self):
        r = api("/api/skip/reset", {}, timeout=8)
        self._toast("切歌标志已复位" if r.get("ok") else "复位失败",
                    not r.get("ok"))

    def on_pause(self):
        r = api("/api/pause", {"action": "toggle"}, timeout=8)
        if r.get("ok"):
            self._toast("已暂停" if r.get("paused") else "已继续")
        else:
            self._toast(f"失败：{r.get('error')}", True)
        self.root.after(400, self.refresh)

    def on_order_toggle(self):
        """暂停/恢复点歌（不动正在播和已排队的歌）"""
        d = self.state_data or {}
        paused = bool(d.get("order_paused"))
        # 恢复前提醒：队列还有多少首
        if paused:
            q = len(d.get("queue_items") or [])
            if q:
                self._toast(f"已恢复接单（队列还有 {q} 首继续播）")
        else:
            self._toast("已暂停接单，已在队列的歌会照常播完")
        r = api("/api/order", {"action": "toggle"}, timeout=8)
        if not r.get("ok"):
            self._toast(f"失败：{r.get('error')}", True)
        self.root.after(400, self.refresh)

    def on_voice_toggle(self):
        """唱歌时是否也用语音回复弹幕（会写回 config.json，重启也生效）"""
        d = self.state_data or {}
        on = bool(d.get("sing_voice_reply"))
        if on:
            self._toast("已关：唱歌时只用气泡回复")
        else:
            self._toast("已开：歌在放时会说话，翻唱期间的话会攒着稍后补说")
        r = api("/api/sing_voice", {"action": "toggle"}, timeout=8)
        if not r.get("ok"):
            self._toast(f"失败：{r.get('error')}", True)
        self.root.after(400, self.refresh)

    def on_insert(self):
        kw = self._entry_value(self.ent_insert, "歌名")
        if not kw:
            messagebox.showinfo("插队", "请先输入歌名")
            return
        self.ent_insert.delete(0, "end")
        self.ent_insert.insert(0, "歌名")
        self.ent_insert.config(fg=DIM)
        r = api("/api/queue/insert", {"kw": kw, "name": "主播"}, timeout=20)
        if r.get("ok"):
            self._toast(f"《{kw}》正在准备，好了插到下一个")
        else:
            self._toast(f"失败：{r.get('error')}", True)

    def on_remove(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        if not vals:
            return
        idx, name = vals[0], vals[1]
        if not messagebox.askyesno("删歌", f"从队列删掉：\n{name}？"):
            return
        r = api("/api/queue/remove", {"index": int(idx)}, timeout=8)
        if r.get("ok"):
            self._toast(f"已删除：{r.get('removed')}")
        else:
            self._toast(f"失败：{r.get('error')}", True)
        self.root.after(500, self.refresh)

    def on_bl_add(self):
        kw = self._entry_value(self.ent_bl, "歌名（可只写歌名部分）")
        if not kw:
            return
        self.ent_bl.delete(0, "end")
        self.ent_bl.insert(0, "歌名（可只写歌名部分）")
        self.ent_bl.config(fg=DIM)
        r = api("/api/blacklist", {"add": [kw]}, timeout=8)
        if r.get("ok"):
            self._toast(f"已加入黑名单：{kw}")
        else:
            self._toast("加入失败", True)
        self.root.after(500, self.refresh)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
