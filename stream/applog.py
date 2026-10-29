# -*- coding: utf-8 -*-
"""统一日志：把控制台输出 + 业务事件都落到文件里。

为什么需要这个（2026-10-27）：
    之前排查「连唱第二首不播」「点歌被吃掉」这些问题时，几乎是瞎猜——
    因为启动脚本用 Start-Process 起服务，**没有重定向 stdout**，
    主服务和 RVC 的所有 print() 全部丢失。只能靠 /api/state 猜内部状态，
    还多次把旧日志当成新日志，得出过错误结论。

本模块解决三件事：
    1. **捕获 stdout/stderr**：所有 print() 同时写控制台和日志文件
    2. **业务事件单独记**：点歌/礼物/翻唱/播放这类关键动作单独一条事件日志，
       方便直播后复盘「哪首歌没播、哪次 503 了」
    3. **按天 + 按大小轮转**：直播可能连开几小时，日志不能无限长

用法（在 main.py 里）：
    from applog import setup_logging, log_event, get_logger
    setup_logging("main")                 # 尽早调用
    log_event("点歌", name="小明", kw="晴天")

日志位置：
    E:\\qq-bot\\logs\\main-2026-10-27.log      控制台输出（含 traceback）
    E:\\qq-bot\\logs\\events-2026-10-27.log    业务事件（点歌/礼物/翻唱/播放）
"""
import logging
import logging.handlers
import os
import sys
import threading
import traceback

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

# 单个日志文件最大 8MB，保留 5 个备份 —— 一场直播几十 MB 绰绰有余
MAX_BYTES = 8 * 1024 * 1024
BACKUP_COUNT = 5

_initialized = {"console": False, "event": False}
_lock = threading.Lock()

# 业务事件专用 logger
event_logger = logging.getLogger("event")
console_logger = logging.getLogger("console")


class _Tee:
    """把写入同时送给原控制台和日志文件。

    ⚠️ 为什么要自己做：
       Windows 控制台默认 GBK 编码，直接 print 中文/emoji 可能抛
       UnicodeEncodeError。这层兜底保证写日志永远不会因为编码炸掉主链路。
    """

    def __init__(self, stream, logfile):
        self.stream = stream
        self.logfile = logfile

    def write(self, data):
        # 1) 先尽力写回原控制台（失败不影响）
        if self.stream is not None:
            try:
                self.stream.write(data)
            except Exception:
                try:
                    # 控制台编码不支持时，替换掉无法编码的字符
                    enc = getattr(self.stream, "encoding", None) or "ascii"
                    self.stream.write(
                        data.encode(enc, "replace").decode(enc, "replace"))
                except Exception:
                    pass
        # 2) 再写日志文件（UTF-8，永不失败）
        if self.logfile is not None:
            try:
                self.logfile.write(data)
                if data.endswith("\n"):
                    self.logfile.flush()
            except Exception:
                pass

    def flush(self):
        for s in (self.stream, self.logfile):
            try:
                if s is not None:
                    s.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return bool(self.stream and self.stream.isatty())
        except Exception:
            return False

    def fileno(self):
        return self.stream.fileno()

    @property
    def encoding(self):
        return getattr(self.stream, "encoding", "utf-8")


def _make_handler(filename_prefix):
    """建一个按大小轮转的 UTF-8 文件 handler"""
    os.makedirs(LOG_DIR, exist_ok=True)
    # 文件名带日期，方便按「哪天直播」找
    import time as _t
    day = _t.strftime("%Y-%m-%d")
    path = os.path.join(LOG_DIR, f"{filename_prefix}-{day}.log")
    h = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)-5s] %(message)s",
        datefmt="%H:%M:%S")
    h.setFormatter(fmt)
    return h, path


def setup_logging(name="main", capture_stdout=True):
    """初始化日志。应在程序启动最早期调用。"""
    with _lock:
        # ---- 控制台/异常日志 ----
        if not _initialized["console"]:
            h, path = _make_handler(name)
            console_logger.setLevel(logging.DEBUG)
            console_logger.addHandler(h)
            console_logger.propagate = False
            _initialized["console"] = True

            if capture_stdout:
                # 把 stdout/stderr 全部 tee 到同一个文件：
                # 这样任何 print() / traceback 都不会丢
                logfile = open(path, "a", encoding="utf-8", buffering=1)
                sys.stdout = _Tee(sys.__stdout__, logfile)
                sys.stderr = _Tee(sys.__stderr__, logfile)

                # 未捕获异常也要进日志（否则进程崩了只留个空文件）
                def _excepthook(exc_type, exc, tb):
                    try:
                        console_logger.error(
                            "未捕获异常:\n" + "".join(
                                traceback.format_exception(exc_type, exc, tb)))
                    except Exception:
                        pass
                sys.excepthook = _excepthook

            console_logger.info(f"===== 日志已启动（{name}）=====")

        # ---- 业务事件日志 ----
        if not _initialized["event"]:
            h, path = _make_handler("events")
            event_logger.setLevel(logging.INFO)
            event_logger.addHandler(h)
            event_logger.propagate = False
            _initialized["event"] = True


def get_logger():
    return console_logger


def log_event(category, **fields):
    """记一条业务事件（结构化，方便事后 grep 复盘）。

    例：
        log_event("点歌", name="小明", kw="晴天")
        →  20:15:01.123 [点歌] name=小明 kw=晴天

    这些事件是「直播后想知道发生了什么」的核心依据：
      · 点歌/放曲 → 观众点了什么、有没有排上
      · 翻唱     → RVC 成功/失败/503 重试了几次
      · 播放     → 哪首歌真的播出去了、播了多久
      · 礼物     → 谁送了什么、金额估算
      · 错误     → 任何异常都要能回溯
    """
    try:
        parts = [f"[{category}]"]
        for k, v in fields.items():
            s = str(v)
            if " " in s or "=" in s:
                s = f'"{s}"'
            parts.append(f"{k}={s}")
        event_logger.info(" ".join(parts))
    except Exception:
        pass


def log_error(where, err, **extra):
    """记一条错误，**带堆栈**，比 log_event 更结构化。

    ⚠️ 为什么必须带堆栈（2026-10-27 踩过）：
       之前只记「TypeError: can't multiply sequence by non-int of type 'float'」，
       但完全没有行号，翻遍代码也不知道是哪个 float 乘了字符串，
       最后只能靠猜。错误日志的价值就在「能直接定位到行」，
       所以这里把最后几层调用栈一起写进事件日志。
    """
    try:
        text = f"{type(err).__name__}: {err}" if isinstance(err, BaseException) else str(err)
        parts = [f"[错误] 位置={where}", f"原因={text}"]
        for k, v in extra.items():
            parts.append(f"{k}={v}")
        event_logger.error(" ".join(parts))

        # 关键：把调用栈写进日志（只取最后 4 层，避免日志爆炸）
        if isinstance(err, BaseException):
            tb = err.__traceback__
            frames = []
            while tb is not None:
                frames.append(tb)
                tb = tb.tb_next
            if frames:
                for tb in frames[-4:]:
                    code = tb.tb_frame.f_code
                    event_logger.error(
                        f"    ↳ {os.path.basename(code.co_filename)}:{tb.tb_lineno}"
                        f" in {code.co_name}")
    except Exception:
        pass
