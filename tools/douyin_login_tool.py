# -*- coding: utf-8 -*-
"""
抖音登录器（持久化 profile）
- 弹出可见浏览器，用户手动扫码/手机号登录
- 登录成功后自动导出 Cookie 到 douyin_cookie.txt
- profile 永久保存在 E:\qq-bot\douyin_profile，之后 cookie 可复用

用法：
    python douyin_login_tool.py            # 打开浏览器等登录
    python douyin_login_tool.py --check    # 只检查已有 cookie 是否还有效
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

PROFILE_DIR = os.environ.get("DOUYIN_PROFILE_DIR", r"E:\qq-bot\douyin_profile")
COOKIE_OUT = os.environ.get("DOUYIN_COOKIE_OUT", r"E:\qq-bot\tools_raw\douyin_cookie.txt")
CHROME = os.environ.get("PLAYWRIGHT_CHROME", r"C:\Users\Administrator\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe")

# 抖音 cookie 里这几个是关键字段
KEY_FIELDS = ("ttwid", "sessionid", "sid_guard", "uid_tt", "passport_csrf_token", "s_v_web_id", "msToken")

os.makedirs(PROFILE_DIR, exist_ok=True)
os.makedirs(os.path.dirname(COOKIE_OUT), exist_ok=True)


def _pick_domains(cookies):
    """只保留抖音相关域名的 cookie"""
    out = []
    for c in cookies:
        d = (c.get("domain") or "").lower()
        if "douyin.com" in d or "douyin" in d:
            out.append(c)
    return out


def _to_header(cookies):
    """转成 `k1=v1; k2=v2` 形式，同名取最长（一般是主域那份）"""
    best = {}
    for c in cookies:
        n, v = c.get("name"), c.get("value")
        if not n or v is None:
            continue
        if n not in best or len(v) > len(best[n]):
            best[n] = v
    return "; ".join(f"{k}={v}" for k, v in best.items()), best


def main():
    check_only = "--check" in sys.argv

    with sync_playwright() as p:
        kwargs = dict(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
            no_viewport=True,
        )
        if os.path.exists(CHROME):
            kwargs["executable_path"] = CHROME
        ctx = p.chromium.launch_persistent_context(**kwargs)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        if check_only:
            # 只验证已有登录态
            try:
                page.goto("https://live.douyin.com/", timeout=60000)
                time.sleep(3)
            except Exception as e:
                print("goto warn:", e)
            cookies = _pick_domains(ctx.cookies())
            header, kv = _to_header(cookies)
            has_sess = "sessionid" in kv
            print(f"cookie 字段数: {len(kv)}")
            print(f"有 sessionid : {has_sess}")
            try:
                body = page.evaluate("document.body.innerText.slice(0,300)")
                print("页面片段:", body.replace("\n", " ")[:200])
            except Exception:
                pass
            ctx.close()
            return 0 if has_sess else 1

        try:
            page.goto("https://live.douyin.com/", timeout=90000)
        except Exception as e:
            print("goto warn:", e)

        print("=" * 60)
        print("  浏览器已打开，请在上面登录抖音")
        print("  建议：先用【小号】测通链路，确认没问题再换开播大号")
        print("  登录成功后本程序会自动导出 Cookie 并退出")
        print("=" * 60)

        deadline = time.time() + 900  # 最多等 15 分钟
        saved = False
        last_log = 0.0

        while time.time() < deadline:
            try:
                cookies = _pick_domains(ctx.cookies())
                header, kv = _to_header(cookies)

                # 判断"真的登录了"：必须有 sessionid
                if "sessionid" in kv and len(kv) >= 5:
                    # 再校验一次页面是否处于登录态
                    logged_in = False
                    try:
                        logged_in = bool(page.evaluate(
                            """() => {
                                const t = document.body.innerText || '';
                                // 未登录时页面会显示"登录"按钮/扫码提示
                                return !/登录后|扫码登录|请登录/.test(t);
                            }"""
                        ))
                    except Exception:
                        logged_in = True

                    if logged_in:
                        with open(COOKIE_OUT, "w", encoding="utf-8") as f:
                            f.write(header)
                        print(f"\n✅ Cookie 已导出：{COOKIE_OUT}")
                        print(f"   字段数 {len(kv)}，长度 {len(header)} 字符")
                        print(f"   关键字段：")
                        for k in KEY_FIELDS:
                            if k in kv:
                                v = kv[k]
                                print(f"     {k:22s} = {v[:24]}{'...' if len(v) > 24 else ''}")
                            else:
                                print(f"     {k:22s} = (无)")
                        print(f"   profile 已持久化：{PROFILE_DIR}")
                        saved = True
                        break

                if time.time() - last_log > 20:
                    last_log = time.time()
                    print(f"  等待登录中… 当前 cookie 字段 {len(kv)} 个"
                          f"{'（已有 sessionid）' if 'sessionid' in kv else '（还没有 sessionid）'}")
            except Exception:
                pass
            time.sleep(3)
        else:
            print("\n⏰ 等待超时（15 分钟），未检测到登录。可以重新运行。")

        ctx.close()
        if not saved:
            sys.exit(2)


if __name__ == "__main__":
    raise SystemExit(main())
