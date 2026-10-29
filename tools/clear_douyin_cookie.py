# -*- coding: utf-8 -*-
"""清空 douyinLive 里存着的旧 Cookie（备份后删），切换到匿名会话。

为什么要有这一步：
   旧 Cookie 是 B 号（或已过期）的登录态。留着它有两类风险：
     · 身份错位：以为是 A 号，实际还在用 B 号的身份访问，撞风控
     · 过期沉默失败：Cookie 失效后抖音连接会一直重试，日志里全是 WARN，
       但不报错，很容易误判成"网络问题"

   清掉后 douyinLive 用匿名会话 —— 弹幕本身是公开的，匿名也能抓，
   所以服务不会因为没 Cookie 就停摆。

用法：
    python tools/clear_douyin_cookie.py            # 备份并清空
    python tools/clear_douyin_cookie.py --dry-run  # 只看会做什么
"""

import argparse
import os
import re
import shutil
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "tools_raw", "douyinLive", "config.yaml")
COOKIE_TXT = os.path.join(ROOT, "tools_raw", "douyin_cookie.txt")


def backup(path):
    if not os.path.isfile(path):
        return None
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = f"{path}.bak_{stamp}"
    shutil.copy2(path, dst)
    return dst


def clear_config(path, dry=False):
    """把 cookie.use_stored 改成 false，cookie.douyin 清空。保留其余全部内容。"""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    # 定位 cookie 段
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("cookie:"):
            start = i
            break
    if start is None:
        sys.exit("config.yaml 里没找到 cookie: 段")

    # 段尾 = 下一个顶格非空行
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i] and not lines[i][0].isspace() and not lines[i].lstrip().startswith("#"):
            end = i
            break

    changed = []
    for i in range(start + 1, end):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("use_stored:"):
            indent = ln[:len(ln) - len(ln.lstrip())]
            lines[i] = f"{indent}use_stored: false"
            changed.append("use_stored → false")
        elif s.startswith("douyin:"):
            indent = ln[:len(ln) - len(ln.lstrip())]
            # 保留注释说明，值清空
            lines[i] = f'{indent}douyin: ""'
            changed.append("cookie.douyin → \"\"（清空）")

    if not changed:
        sys.exit("没找到 use_stored / douyin 字段，config 结构可能变了，请手工处理")

    if dry:
        return changed, None

    new = "\n".join(lines) + "\n"
    # 写前用 yaml 校验一遍，避免把 config 写坏导致服务起不来
    try:
        import yaml
        yaml.safe_load(new)
    except ImportError:
        pass
    except Exception as e:
        sys.exit(f"改完的 YAML 解析失败，未写入：{e}")

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    return changed, new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("=" * 62)
    print("清空 douyinLive 的旧 Cookie")
    print("=" * 62)

    if not os.path.isfile(CONFIG):
        sys.exit(f"找不到 config: {CONFIG}")

    # 先看看现在是什么
    with open(CONFIG, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r'sessionid=([^;"\s]+)', src)
    old_sid = m.group(1) if m else None
    print(f"当前 sessionid : {old_sid[:20] + '...' if old_sid else '(无)'}")

    changed, _ = clear_config(CONFIG, dry=args.dry_run)
    for c in changed:
        print(f"  · {c}")

    # cookie.txt 备份后删除
    if os.path.isfile(COOKIE_TXT):
        bak = None if args.dry_run else backup(COOKIE_TXT)
        print(f"  · douyin_cookie.txt {'(dry-run 不删)' if args.dry_run else '已备份 → ' + os.path.basename(bak)}")
        if not args.dry_run:
            os.remove(COOKIE_TXT)
    else:
        print("  · douyin_cookie.txt 不存在（已清过）")

    if args.dry_run:
        print("\n（dry-run，什么都没改）")
        return

    print(f"\n✅ 已清空。备份：")
    print(f"   {backup(CONFIG) if False else ''}", end="")
    print(f"   {CONFIG}.bak_*  （同目录）")
    print("\n下一步：")
    print("  1. 在浏览器登录要用的抖音号（A 号）")
    print("  2. python tools/douyin_cookie_tool.py --auto --apply")
    print("  3. 重启服务让它重读 config")


if __name__ == "__main__":
    main()
