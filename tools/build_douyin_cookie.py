# -*- coding: utf-8 -*-
"""用给定的 sessionid 验证登录态，并自动补齐其余必要 Cookie。

为什么只需要 sessionid：
  · sessionid   = 登录凭证本体（唯一必需）
  · sessionid_ss / sid_tt = 与 sessionid 同值（抖音自己就是这么写的）
  · ttwid       = 抖音服务器在**任何**页面请求时自动下发，不需要手工复制
  · uid_tt      = 同样由 ttwid 关联下发
  · n_mh        = 设备指纹辅助项，通常可以不要

  所以「只拿到 sessionid」完全够用 —— 本工具会用它在请求里让服务器
  把 ttwid / uid_tt 发回来，拼成完整可用的 Cookie 串。

用法：
    python tools/build_douyin_cookie.py --sessionid db2470f3...
    python tools/build_douyin_cookie.py --sessionid db2470f3... --apply
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")


def http_get(url, cookie="", referer="https://www.douyin.com/"):
    """发一个 GET，返回 (状态码, body文本, Set-Cookie 列表)"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        body = resp.read().decode("utf-8", "replace")
        # urllib 会把多个 Set-Cookie 合并，这里手工解析 headers
        raw = resp.headers.get_all("Set-Cookie") or []
        return resp.status, body, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), (e.headers.get_all("Set-Cookie") or [])
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", []


def harvest_cookies(set_cookie_list):
    """从 Set-Cookie 里抽出 name=value"""
    out = {}
    for sc in set_cookie_list:
        # 一条 Set-Cookie 可能含多个 cookie（用逗号分隔的 Expires 不能误切）
        for part in re.split(r",(?=[^;]+\s*=)", sc):
            first = part.split(";", 1)[0].strip()
            if "=" in first:
                k, v = first.split("=", 1)
                k, v = k.strip(), v.strip()
                if k and v:
                    out[k] = v
    return out


def probe_login(cookie_str):
    """问抖音「这个登录态是谁」。返回 (是否有效, 昵称, 原始响应)"""
    # 抖音 web 的「当前用户」接口；不同版本路径有变化，逐个试
    urls = [
        "https://www.douyin.com/aweme/v1/web/im/user/info/",
        "https://www.douyin.com/aweme/v1/web/user/profile/self/",
        "https://www.douyin.com/passport/web/account/info/",
    ]
    for u in urls:
        code, body, _ = http_get(u, cookie_str)
        if code != 200 or not body:
            continue
        try:
            d = json.loads(body)
        except Exception:
            continue
        sc = d.get("status_code")
        if sc == 0:
            u_ = d.get("user") or d.get("data") or {}
            nick = (u_.get("nickname") or u_.get("screen_name")
                    or u_.get("name") or "")
            return True, nick, d
        # status_code=8 通常表示未登录/登录态失效
        last = d
    return False, "", locals().get("last", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessionid", required=True, help="抖音 sessionid 的值")
    ap.add_argument("--apply", action="store_true", help="通过后写进 douyinLive config.yaml")
    args = ap.parse_args()

    sid = args.sessionid.strip().strip('"').strip("'")
    if len(sid) < 20:
        sys.exit(f"sessionid 看起来不对（长度 {len(sid)}）：{sid}")

    print("=" * 66)
    print("第一步：确认 sessionid 格式")
    print("=" * 66)
    print(f"  sessionid : {sid[:12]}...{sid[-6:] if len(sid) > 18 else ''} (len={len(sid)})")

    # ---- 第二步：拿服务器下发的 ttwid / uid_tt ----
    print("\n" + "=" * 66)
    print("第二步：让抖音服务器下发配套 Cookie")
    print("=" * 66)

    base = {"sessionid": sid, "sessionid_ss": sid, "sid_tt": sid}
    seed = "; ".join(f"{k}={v}" for k, v in base.items())

    # 访问首页 / 直播间页，服务器会自动 Set-Cookie: ttwid 等
    harvested = {}
    for url in ["https://www.douyin.com/",
                "https://live.douyin.com/",
                "https://www.douyin.com/discover"]:
        code, body, sc = http_get(url, "; ".join(f"{k}={v}" for k, v in {**base, **harvested}.items()))
        got = harvest_cookies(sc)
        add = {k: v for k, v in got.items() if k not in harvested}
        if add:
            harvested.update(add)
            print(f"  [{url.split('//')[1][:22]}] 新下发: {', '.join(sorted(add))}")
        if "ttwid" in harvested and "uid_tt" in harvested:
            break

    if "ttwid" not in harvested:
        print("  ⚠️ 服务器没下发 ttwid —— 可能被风控拦了，先继续用 sessionid 试")

    full = {**base, **harvested}
    cookie_str = "; ".join(f"{k}={v}" for k, v in full.items())

    print(f"\n  已拼出 {len(full)} 个 Cookie：")
    for k in full:
        v = full[k]
        print(f"    {k:14s} {v[:16] + ('...' if len(v) > 16 else '')}")

    # ---- 第三步：验证 ----
    print("\n" + "=" * 66)
    print("第三步：验证登录态是否有效")
    print("=" * 66)
    ok, nick, raw = probe_login(cookie_str)
    if ok:
        print(f"  ✅ 登录有效" + (f"，昵称：{nick}" if nick else "（接口没返回昵称）"))
    else:
        print("  ❌ 登录无效 / 已过期")
        sample = json.dumps(raw, ensure_ascii=False)[:300] if raw else "(无响应)"
        print(f"     响应片段: {sample}")
        print("\n  可能原因：")
        print("    · sessionid 复制错了（末尾漏字符 / 混入空格）")
        print("    · 该号已在别处退出登录")
        print("    · 复制的是别的域名下的 sessionid（要 www.douyin.com 下的）")
        sys.exit(1)

    print("\n" + "=" * 66)
    print("最终 Cookie 串（等同于登录凭证，别外传）")
    print("=" * 66)
    print(cookie_str)

    if args.apply:
        from douyin_cookie_tool import apply_to_config, CONFIG_PATH
        print(f"\n写入 {CONFIG_PATH} ...")
        apply_to_config(cookie_str, CONFIG_PATH)


if __name__ == "__main__":
    main()
