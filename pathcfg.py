"""统一的本机路径配置 —— 源码里不再写死盘符。

读取顺序（后者覆盖前者）：
   1. 代码里的智能默认值（比如 venv 的 python、项目根目录）
   2. <项目根>/paths.json          ← 本机配置，不进仓库（见 paths.example.json）
   3. 环境变量 DFY_<KEY>           ← 临时覆盖、CI/容器里用

用法：
    import pathcfg
    pathcfg.rvc_home()      # RVC 安装根目录（含 RVC_Core / ffmpeg.exe）
    pathcfg.python()        # 跑直播主服务的解释器
    pathcfg.music_dir()     # 网易云下载目录（ncm 解密用）

设计说明：
   · 每个值都允许为空 —— 用不到 RVC 的人不该被迫填 RVC 路径；
   · 找不到就返回空串，调用方自己决定报错还是降级（比如 ffmpeg 用系统 PATH 里的）；
   · 本文件不涉及任何密钥，路径配置与凭证配置分开管理。
"""

import io
import json
import os
import sys

# 项目根目录 = 本文件所在目录
ROOT = os.path.dirname(os.path.abspath(__file__))

_CFG = None
_KEYS = ("rvc_home", "python", "music_dir", "douyin_live_exe")


def _load():
    """读 paths.json（只读一次）。文件不存在 / 格式错都不抛异常。"""
    global _CFG
    if _CFG is None:
        _CFG = {}
        path = os.path.join(ROOT, "paths.json")
        if os.path.exists(path):
            try:
                with io.open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    _CFG = data
            except Exception as e:
                print(f"[pathcfg] paths.json 读取失败（忽略，用默认值）: {e}",
                      file=sys.stderr)
    return _CFG


def get(key, default=""):
    """取一项路径配置：环境变量 > paths.json > default。"""
    env = os.environ.get("DFY_" + key.upper())
    if env:
        return os.path.expanduser(env)
    val = _load().get(key)
    if val:
        # 允许在 json 里用 ${root} 之类的相对写法
        return os.path.expanduser(
            str(val).replace("${root}", ROOT).replace("$root", ROOT))
    return default


def _first_existing(*cands):
    for c in cands:
        if c and os.path.exists(c):
            return c
    return ""


def rvc_home():
    """RVC 安装根目录（其下应有 RVC_Core/ 和 Runtime/python.exe）。"""
    return get("rvc_home")


def rvc_core():
    """RVC_Core 目录（模型、ffmpeg、infer 包都在这里）。"""
    home = rvc_home()
    if home:
        return os.path.join(home, "RVC_Core")
    return get("rvc_core")


def ffmpeg():
    """ffmpeg 可执行文件：优先 RVC 自带的，其次系统 PATH。"""
    for base in (rvc_core(), rvc_home()):
        if base:
            for name in ("ffmpeg.exe", "ffmpeg"):
                p = os.path.join(base, name)
                if os.path.exists(p):
                    return p
    return get("ffmpeg", "ffmpeg")


def python():
    """跑直播主服务/工具的解释器：paths.json > 本机 venv > 当前解释器。"""
    cand = get("python")
    if cand and os.path.exists(cand):
        return cand
    for rel in ("astrbot-venv/Scripts/python.exe", "astrbot-venv/bin/python",
                ".venv/Scripts/python.exe", "venv/Scripts/python.exe"):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            return p
    return sys.executable


def music_dir():
    """网易云音乐下载目录（放 .ncm 的地方）。"""
    return get("music_dir")


def as_dict():
    """当前生效的路径（给自检脚本打印用，不含任何密钥）。"""
    return {k: get(k) for k in _KEYS}
