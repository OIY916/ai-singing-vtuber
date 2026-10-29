# -*- coding: utf-8 -*-
"""从 Chrome profile 读取抖音 Cookie（Windows / DPAPI / v10 加密）。

为什么需要这个工具：
   抖音的登录态 Cookie 是加密存在 Chrome 的 Cookies 库里（v10 + DPAPI），
   手工去浏览器 F12 里一条条复制既慢又容易漏。这里直接解出来，
   然后可以一键写进 douyinLive 的 config.yaml。

⚠️ 安全须知：
   · 解出来的 Cookie 等价于**账号登录凭证**，拿到就能以该账号身份操作抖音
   · 本工具的输出默认打码（只显示前后几位），确认身份够用了
   · 带 --export 才会输出完整值，且只在命令行打印，不写文件

用法：
    # 1) 先看这个 profile 登录的是哪个号（推荐第一步）
    python tools/douyin_cookie_tool.py --profile douyin_profile --whoami

    # 2) 确认无误后导出完整 Cookie 串（用于替换 config.yaml）
    python tools/douyin_cookie_tool.py --profile douyin_profile --export

    # 3) 直接写进 douyinLive 的 config.yaml（会自动备份）
    python tools/douyin_cookie_tool.py --profile douyin_profile --apply

    # 看 Chrome 默认 profile（平时自己用的浏览器）
    python tools/douyin_cookie_tool.py --profile default --whoami
"""

import argparse
import base64
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

# 该带上的 Cookie 名（顺序不重要，douyinLive 只要求整串合法）
WANT = [
    "ttwid", "sessionid", "sessionid_ss", "sid_tt", "sid_guard",
    "uid_tt", "uid_tt_ss", "n_mh", "passport_assist_user",
    "passport_csrf_token", "passport_csrf_token_default",
    "csrf_session_id", "s_v_web_id", "UIFID", "UIFID_TEMP",
    "odin_tt", "is_dbsc", "x_tt_token", "d_ticket",
]

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "tools_raw", "douyinLive", "config.yaml")


# ---------------- Chrome Cookie 解密（Windows） ----------------

def _dpapi_unwrap(encrypted_key: bytes) -> bytes:
    """解开 Chrome 的 Local State master key（DPAPI）"""
    import win32crypt
    # 前缀 'DPAPI' 是 Chrome 自己加的标记，解密前要去掉
    blob = base64.b64decode(encrypted_key)
    assert blob[:5] == b"DPAPI", "不是预期的 DPAPI 格式"
    return win32crypt.CryptUnprotectData(blob[5:], None, None, None, 0)[1]


def _decrypt_v10(blob: bytes, key: bytes) -> str:
    """解开单个 v10 Cookie（AES-256-GCM）"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    assert blob[:3] == b"v10", "不是 v10 加密，可能是新版 app-bound 加密"
    nonce = blob[3:15]
    payload = blob[15:]
    # GCM 的密文末尾 16 字节是 tag
    pt = AESGCM(key).decrypt(nonce, payload, None)
    # ⚠️ Chrome 127+ 会在明文前面塞 32 字节「主机绑定前缀」（host_key 的 SHA256），
    #    不去掉的话，解出来的 Cookie 前面全是乱码字节，整串直接不能用。
    #    这是 2026-10-28 踩到的：解出来了但 sessionid 对不上，白折腾一轮。
    if len(pt) > 32:
        try:
            pt[32:].decode("utf-8")
            return pt[32:].decode("utf-8")
        except UnicodeDecodeError:
            pass
    return pt.decode("utf-8")


def resolve_profile(arg: str) -> str:
    """把 --profile 参数变成实际的 profile 目录"""
    if arg == "default":
        p = os.path.join(os.environ["LOCALAPPDATA"],
                         "Google", "Chrome", "User Data")
    else:
        p = arg
    p = os.path.abspath(p)
    if not os.path.isdir(p):
        sys.exit(f"profile 目录不存在: {p}")
    return p


def read_cookies(profile_dir: str) -> dict:
    """读出一个 profile 里所有 douyin cookie（已解密）"""
    local_state = os.path.join(profile_dir, "Local State")
    if not os.path.isfile(local_state):
        sys.exit(f"找不到 Local State: {local_state}")
    with open(local_state, encoding="utf-8") as f:
        ls = json.load(f)
    master = _dpapi_unwrap(ls["os_crypt"]["encrypted_key"])

    # Cookies 库有两个可能位置（新版在 Network/ 下）
    db = None
    for cand in (os.path.join(profile_dir, "Default", "Network", "Cookies"),
                 os.path.join(profile_dir, "Default", "Cookies")):
        if os.path.isfile(cand):
            db = cand
            break
    if not db:
        sys.exit(f"找不到 Cookies 数据库: {profile_dir}/Default")

    # 复制出来查，避免锁住浏览器正在用的库
    tmp = os.path.join(tempfile.gettempdir(), f"ck_{os.getpid()}.db")
    shutil.copy2(db, tmp)

    out = {}
    con = sqlite3.connect(tmp)
    try:
        rows = con.execute(
            "SELECT name, encrypted_value FROM cookies "
            "WHERE host_key LIKE '%douyin%'").fetchall()
    finally:
        con.close()
        try:
            os.remove(tmp)
        except OSError:
            pass

    for name, enc in rows:
        if not enc:
            continue
        try:
            out[name] = _decrypt_v10(enc, master)
        except Exception:
            # 单条解不开不影响整体（可能是 app-bound 加密的新 cookie）
            continue
    return out


# ---------------- 身份确认 ----------------

def whoami(cookies: dict) -> dict:
    """用解锁出的 ttwid 去问抖音这个登录态是谁。

    为什么不用 uid_tt 猜：
      uid_tt 是设备级的伪匿名 id，跟账号不是一一对应，看起来像个 uid 其实不是。
      只有服务端返回的 nickname / 主播身份才作数。
    """
    ttwid = cookies.get("ttwid", "")
    # 先问「当前登录用户」接口
    for url, hdr_name in [
        ("https://www.douyin.com/passport/web/account/info/", None),
        ("https://webcast.amemv.com/webcast/user/", None),
    ]:
        try:
            req = urllib.request.Request(url, headers={
                "Cookie": f"ttwid={ttwid}",
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/149.0.0.0 Safari/537.36"),
                "Referer": "https://www.douyin.com/",
            })
            body = urllib.request.urlopen(req, timeout=10).read()
            d = json.loads(body)
            return {"source": url, "raw": d}
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    return {"source": None, "err": last}


def mask(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 12:
        return v[:4] + "***"
    return f"{v[:8]}...{v[-6:]} (len={len(v)})"


def build_cookie_string(cookies: dict) -> str:
    parts = []
    for name in WANT:
        if name in cookies and cookies[name]:
            parts.append(f"{name}={cookies[name]}")
    # WANT 没列到的也补上（保持完整，抖音有些校验很敏感）
    for name, v in cookies.items():
        if name not in WANT and v:
            parts.append(f"{name}={v}")
    return "; ".join(parts)


def apply_to_config(cookie_str: str, path: str) -> None:
    """把新 Cookie 写进 douyinLive 的 config.yaml（保留其余内容，先备份）"""
    if not os.path.isfile(path):
        sys.exit(f"找不到 config.yaml: {path}")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    # 找 cookie: 段里的 douyin: 那一行
    in_cookie = False
    hit = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("cookie:"):
            in_cookie = True
            continue
        if in_cookie:
            if stripped.startswith("douyin:"):
                hit = i
                break
            # 出了 cookie 段（遇到顶格的非空行）
            if line and not line[0].isspace():
                break

    if hit < 0:
        sys.exit("config.yaml 里没找到 cookie.douyin 这一行，请手工确认结构")

    backup = path + ".bak"
    shutil.copy2(path, backup)
    indent = lines[hit][:len(lines[hit]) - len(lines[hit].lstrip())]
    lines[hit] = f'{indent}douyin: "{cookie_str}"'

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"✅ 已写入 {path}")
    print(f"   备份: {backup}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="douyin_profile",
                    help="profile 目录，或 'default' 表示 Chrome 默认 profile")
    ap.add_argument("--whoami", action="store_true", help="只看登录的是哪个号")
    ap.add_argument("--export", action="store_true", help="打印完整 Cookie 串")
    ap.add_argument("--apply", action="store_true", help="写进 douyinLive config.yaml")
    ap.add_argument("--no-restart-hint", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    profile = resolve_profile(args.profile)
    print(f"profile: {profile}\n")

    ck = read_cookies(profile)
    if not ck:
        sys.exit("没读到任何 douyin cookie —— profile 可能没登录过抖音")

    print("=== 解出的关键 Cookie（打码）===")
    for k in ["sessionid", "uid_tt", "n_mh", "ttwid"]:
        if k in ck:
            print(f"  {k:12s} {mask(ck[k])}")

    if not ck.get("sessionid"):
        print("\n⚠️ 没有 sessionid —— 这个 profile 是**未登录**状态")
        print("   请先在浏览器里登录抖音，再重跑本工具")
        return

    if args.whoami or not (args.export or args.apply):
        print("\n=== 这个登录态是谁 ===")
        info = whoami(ck)
        print(json.dumps(info, ensure_ascii=False, indent=2)[:900])

    if args.export:
        print("\n=== 完整 Cookie 串（等同于登录凭证，别外传）===")
        print(build_cookie_string(ck))

    if args.apply:
        s = build_cookie_string(ck)
        print(f"\n=== 写入 config.yaml（共 {len(s)} 字符）===")
        apply_to_config(s, CONFIG_PATH)
        print("\n⚠️ douyinLive 需要重启才会用新 Cookie：")
        print("   powershell -ExecutionPolicy Bypass -File 启动直播.ps1 -Stop")
        print("   powershell -ExecutionPolicy Bypass -File 启动直播.ps1")


if __name__ == "__main__":
    main()
