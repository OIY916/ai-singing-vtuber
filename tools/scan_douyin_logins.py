# -*- coding: utf-8 -*-
"""扫描本机所有浏览器 profile，列出每个抖音登录态的身份线索。

用途：弄清楚「config.yaml 里现在用的 Cookie 是谁的」「A 号登录态在哪个浏览器」。

⚠️ 只读，不写任何东西。Edge / Chrome 开着也能读（SQLite immutable 模式）。

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools/scan_douyin_logins.py
"""

import base64
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

import win32crypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_FIELDS = ["sessionid", "sessionid_ss", "uid_tt", "uid_tt_ss",
              "n_mh", "ttwid", "sid_tt", "passport_assist_user"]


def dpapi_key(local_state_path):
    """读浏览器 master key（DPAPI 解开）"""
    with open(local_state_path, encoding="utf-8") as f:
        ls = json.load(f)
    blob = base64.b64decode(ls["os_crypt"]["encrypted_key"])
    if not blob.startswith(b"DPAPI"):
        raise ValueError("非 DPAPI 格式")
    return win32crypt.CryptUnprotectData(blob[5:], None, None, None, 0)[1]


def read_ck(db_path, key):
    """只读打开 Cookies 库，解出所有 douyin cookie"""
    # immutable=1：不建锁、不写 journal，浏览器开着也能读
    con = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        rows = con.execute(
            "SELECT name, encrypted_value FROM cookies "
            "WHERE host_key LIKE '%douyin%'").fetchall()
    finally:
        con.close()

    out = {}
    for name, enc in rows:
        if not enc:
            continue
        enc = bytes(enc)
        if enc[:3] != b"v10":
            continue
        try:
            pt = AESGCM(key).decrypt(enc[3:15], enc[15:], None)
        except Exception:
            continue
        # Chrome/Edge 127+ 明文前有 32 字节主机绑定前缀
        try:
            out[name] = pt[32:].decode("utf-8")
        except UnicodeDecodeError:
            out[name] = pt.decode("utf-8")
    return out


def scan():
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        ("Chrome 默认", os.path.join(local, "Google", "Chrome", "User Data", "Default")),
        ("Edge 默认",   os.path.join(local, "Microsoft", "Edge", "User Data", "Default")),
        ("B号/项目 douyin_profile", r"E:\qq-bot\douyin_profile\Default"),
        ("live_watch 插件",
         r"E:\qq-bot\data\plugin_data\astrbot_plugin_live_watch\chrome_profile\Default"),
    ]

    # config.yaml 里正在用的
    cfg_ck = None
    cfg_path = r"E:\qq-bot\tools_raw\douyinLive\config.yaml"
    if os.path.isfile(cfg_path):
        import re
        with open(cfg_path, encoding="utf-8") as f:
            m = re.search(r'sessionid=([^;"\s]+)', f.read())
            cfg_ck = m.group(1) if m else None

    print("=" * 74)
    print(f"{'来源':<26} {'sessionid':<20} {'状态'}")
    print("=" * 74)
    if cfg_ck:
        print(f"{'config.yaml (正在用)':<26} {cfg_ck[:18]:<20} ← douyinLive 当前读的")

    results = {}
    for label, prof in targets:
        parent = os.path.dirname(prof)
        local_state = os.path.join(parent, "Local State")
        db = os.path.join(prof, "Network", "Cookies")
        if not os.path.isfile(db):
            db = os.path.join(prof, "Cookies")
        if not os.path.isfile(db):
            print(f"{label:<26} {'—':<20} 无 Cookie 库")
            continue
        if not os.path.isfile(local_state):
            print(f"{label:<26} {'—':<20} 无 Local State")
            continue
        try:
            key = dpapi_key(local_state)
            ck = read_ck(db, key)
        except PermissionError:
            print(f"{label:<26} {'—':<20} 被独占锁定（浏览器正在写入）")
            continue
        except Exception as e:
            print(f"{label:<26} {'—':<20} {type(e).__name__}: {e}")
            continue

        sid = ck.get("sessionid", "")
        tag = ""
        if sid and cfg_ck and sid == cfg_ck:
            tag = "与 config 相同（正在生效）"
        elif sid:
            tag = "有登录态，但不同于 config"
        else:
            tag = "未登录抖音"
        print(f"{label:<26} {(sid[:18] if sid else '—'):<20} {tag}")
        results[label] = ck

        if sid:
            uid = ck.get("uid_tt", "")
            print(f"{'':<26} uid_tt={uid[:18]}  ttwid={ck.get('ttwid','')[:22]}")
    print("=" * 74)

    # 顺手看哪个 profile 的 Cookie 库最近被改过 —— 推断「最近在哪个浏览器登的」
    print("\n各 Cookie 库最后修改时间（越新 = 最近在使用）:")
    import time
    for label, prof in targets:
        for cand in (os.path.join(prof, "Network", "Cookies"), os.path.join(prof, "Cookies")):
            if os.path.isfile(cand):
                mt = os.path.getmtime(cand)
                print(f"  {label:<26} {time.strftime('%m-%d %H:%M', time.localtime(mt))}")
                break


if __name__ == "__main__":
    scan()
