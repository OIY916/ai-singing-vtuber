# -*- coding: utf-8 -*-
"""
网易云登录器（持久化 profile）
- 弹出可见的浏览器窗口，用户手动登录
- 登录成功后自动保存 cookie 到 netease_cookie.json
- profile 永久保存在 <项目根>\netease_profile，之后 cookie 一直有效
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

# --- 路径自定位（自动生成，勿手改）：工具在 tools/ 下，项目根是上一层 ---
import os as _os
import sys as _sys
ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, ROOT)
import pathcfg
PYPATH = pathcfg.python()
# --- 路径自定位结束 ---

PROFILE_DIR = _os.path.join(ROOT, "netease_profile")
COOKIE_OUT = _os.path.join(ROOT, "cover_service", "netease_cookie.json")
CHROME = _os.environ.get("PLAYWRIGHT_CHROME", "")   # 留空则用 playwright 自带的浏览器

os.makedirs(PROFILE_DIR, exist_ok=True)


def main():
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

        # 复用已有标签页或新建
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://music.163.com/", timeout=60000)
        except Exception as e:
            print("goto warn:", e)

        print("=" * 56)
        print("  浏览器窗口已打开，请在上面登录网易云音乐")
        print("  支持：扫码登录 / 手机号登录")
        print("  登录成功后本程序会自动保存 cookie 并退出")
        print("=" * 56)

        deadline = time.time() + 600  # 最多等 10 分钟
        saved = False
        while time.time() < deadline:
            try:
                cookies = ctx.cookies()
                music_u = [
                    c for c in cookies
                    if c.get("name") == "MUSIC_U" and "music.163.com" in (c.get("domain") or "")
                ]
                if music_u and music_u[0].get("value"):
                    # 校验一下是否真的有效
                    try:
                        resp = page.evaluate(
                            """async () => {
                                const r = await fetch('https://music.163.com/api/nuser/account/get', {credentials:'include'});
                                return await r.json();
                            }"""
                        )
                    except Exception:
                        resp = {}
                    acct = (resp or {}).get("account") or {}
                    nickname = acct.get("nickname") or (resp or {}).get("profile", {}).get("nickname")
                    data = {c["name"]: c["value"] for c in cookies if "music.163.com" in (c.get("domain") or "")}
                    with open(COOKIE_OUT, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=1)
                    print(f"\n✅ 登录成功！用户：{nickname or '(已获取 cookie)'}")
                    print(f"✅ cookie 已保存：{COOKIE_OUT}（共 {len(data)} 项）")
                    print("✅ profile 已持久化：", PROFILE_DIR)
                    saved = True
                    break
            except Exception as e:
                pass
            time.sleep(3)
        else:
            print("\n⏰ 等待超时（10分钟），未检测到登录。可以重新运行本程序。")

        try:
            ctx.close()
        except Exception:
            pass

        if not saved:
            sys.exit(2)


if __name__ == "__main__":
    main()
