"""RVC 音色统一管理 —— 一处修改，说话/唱歌/电话同时生效

## 为什么需要这个

原来看模型写死在 3 个插件的配置文件里，改一个音色要动 3 个文件 + 重启 AstrBot，
而且用户没法在群里换（2026-10-26 用户需求：「能让我切换 rvc 的模型吗，
因为有些地方我感觉没原来的那个模型好」）。

于是收敛成一个**单一事实来源**：

    data/rvc_model.txt      ← 就一行，存模型文件名

下面三个地方都从它读（读不到才退化到各自配置里的旧值）：
    1. astrbot_plugin_rvc_tts        —— 群聊说话
    2. astrbot_plugin_cover_sing     —— 唱歌
    3. astrbot_plugin_volc_voice_call + tools/call_tts_engine.py —— 电话

## 用法

    # 查看当前
    python tools/rvc_model_manager.py show

    # 列出所有可用
    python tools/rvc_model_manager.py list

    # 切换
    python tools/rvc_model_manager.py set shaoluosusu.pth

切换是**即时生效**的（各插件每轮都重读文件），不需要重启 AstrBot。
"""

from __future__ import annotations

import io
import json
import os
import sys
import time

ROOT = os.environ.get("ASTRBOT_ROOT") or r"E:\qq-bot"
MODEL_FILE = os.path.join(ROOT, "data", "rvc_model.txt")
COVER_API = os.environ.get("COVER_API", "http://127.0.0.1:8899")

# 各插件配置文件里的字段名（用于回写，保持两边一致，避免混淆）
PLUGIN_CONFIGS = {
    "data/config/astrbot_plugin_rvc_tts_config.json": "model",
    "data/config/astrbot_plugin_cover_sing_config.json": "default_model",
    "data/config/astrbot_plugin_volc_voice_call_config.json": "rvc_model",
}

DEFAULT_MODEL = "nikou48K1999.pth"


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def read_model() -> str:
    """读当前模型。文件 > 插件配置 > 默认值。"""
    try:
        if os.path.exists(MODEL_FILE):
            with io.open(MODEL_FILE, encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
    except Exception:  # noqa: BLE001
        pass

    # 退化：读 rvc_tts 的配置
    try:
        p = os.path.join(ROOT, "data/config/astrbot_plugin_rvc_tts_config.json")
        with io.open(p, encoding="utf-8-sig") as f:
            d = json.load(f)
        v = (d.get("model") or "").strip()
        if v:
            return v
    except Exception:  # noqa: BLE001
        pass

    return DEFAULT_MODEL


def write_model(name: str) -> bool:
    """写当前模型，并同步回 3 个插件配置（保持一致，免得以后看着混乱）。"""
    name = name.strip()
    if not name.endswith(".pth"):
        name += ".pth"

    # 1. 主文件
    try:
        os.makedirs(os.path.dirname(MODEL_FILE), exist_ok=True)
        with io.open(MODEL_FILE, "w", encoding="utf-8") as f:
            f.write(name)
    except Exception as e:  # noqa: BLE001
        log(f"写 {MODEL_FILE} 失败: {e}")
        return False

    # 2. 同步插件配置（失败不致命，只提示）
    for rel, field in PLUGIN_CONFIGS.items():
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        try:
            with io.open(p, encoding="utf-8-sig") as f:
                d = json.load(f)
            if d.get(field) == name:
                continue
            d[field] = name
            with io.open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            log(f"  同步 {rel} -> {field}={name}")
        except Exception as e:  # noqa: BLE001
            log(f"  同步 {rel} 失败（不影响使用）: {e}")

    return True


def list_models() -> list[str]:
    """从 RVC 服务拿可用模型列表。"""
    try:
        import httpx
        with httpx.Client(timeout=10, trust_env=False) as c:
            r = c.get(f"{COVER_API}/models")
        if r.status_code == 200:
            return r.json().get("models") or []
    except Exception as e:  # noqa: BLE001
        log(f"从 {COVER_API} 拿模型列表失败: {e}")
    return []


def resolve(name: str, models: list[str]) -> str | None:
    """模糊匹配模型名。支持不写后缀、部分名。"""
    name = name.strip()
    if not name:
        return None
    # 精确
    if name in models:
        return name
    if name + ".pth" in models:
        return name + ".pth"
    # 忽略大小写精确
    lower = name.lower()
    for m in models:
        if m.lower() == lower or m.lower() == lower + ".pth":
            return m
    # 包含
    hits = [m for m in models if lower in m.lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # 多个命中时优先最短的（通常是最干净的基名）
        hits.sort(key=len)
        return hits[0]
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    cmd = sys.argv[1].lower()

    if cmd == "show":
        cur = read_model()
        log(f"当前音色: {cur}")
        log(f"配置文件: {MODEL_FILE}")
        return 0

    if cmd == "list":
        models = list_models()
        cur = read_model()
        if not models:
            log("拿不到模型列表（RVC 服务可能没起）")
            return 1
        log(f"可用模型 {len(models)} 个（★ = 当前）:")
        for m in models:
            mark = "★" if m == cur else " "
            log(f"  {mark} {m}")
        return 0

    if cmd == "set":
        if len(sys.argv) < 3:
            log("用法: rvc_model_manager.py set <模型名>")
            return 2
        want = sys.argv[2]
        models = list_models()
        if models:
            got = resolve(want, models)
            if not got:
                log(f"找不到匹配「{want}」的模型。先跑 list 看看有哪些。")
                return 1
        else:
            got = want if want.endswith(".pth") else want + ".pth"

        log(f"切换: {read_model()} -> {got}")
        if write_model(got):
            log(f"✅ 已切换到 {got}")
            log("   说话/唱歌/电话都会立刻用它，不用重启 AstrBot")
            return 0
        log("❌ 切换失败")
        return 1

    log(f"未知命令: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
