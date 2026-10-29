# -*- coding: utf-8 -*-
"""把深灰背景的图抠成透明 PNG（连通域填充，不误伤角色内部的深色）。

为什么不能简单用颜色阈值（2026-10-28 实测数据）：
   这张爬行图的背景是纯灰 RGB(16,16,16)，非常均匀（标准差 1.17），
   但**角色内部有 19.88% 的像素亮度 < 40** —— 深蓝衣服、黑色线稿，
   和背景色几乎一样。一刀切阈值会把角色的衣服和轮廓抠出洞来。

   所以用「从图片四边往里灌」的连通域填充（flood fill）：
     · 只有与边缘相连的背景才会被标记为透明
     · 角色内部的深色像素被轮廓围住，灌不进去 → 完整保留

用法：
    python tools/crawler_key.py <输入图> [--out 输出前缀]
    python tools/crawler_key.py --auto     # 自动找 avatar/jimeng 下最新的爬行图
"""

import argparse
import glob
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "avatar", "crawler")

CHROMA = (0, 177, 64)          # 影视标准绿幕色


def flood_fill_from_edges(candidate):
    """从四边向内做连通域填充，返回「确认是背景」的布尔数组。

    candidate: bool 数组，True = 颜色上像背景（可能被角色内部暗区误判）

    ⚠️ 为什么自己写而不用 PIL 的 ImageDraw.floodfill（2026-10-28）：
       本机 Pillow 12.3.0 的 floodfill **调用了但不生效** ——
       最小复现：60×60 黑底白块，floodfill((0,0),128) 之后
       128 的像素数仍然是 0。签名对、不报错、就是不改图。
       所以只能自己实现。

    用 BFS + deque：只访问「候选背景」像素（约占 68%），
    实测 2560×1440 在 3~6 秒内完成，够用。
    """
    from collections import deque

    h, w = candidate.shape
    visited = np.zeros((h, w), dtype=bool)
    dq = deque()

    # 四条边上的所有候选点都作为种子
    for x in range(w):
        for y in (0, h - 1):
            if candidate[y, x] and not visited[y, x]:
                visited[y, x] = True
                dq.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if candidate[y, x] and not visited[y, x]:
                visited[y, x] = True
                dq.append((y, x))

    n = 0
    while dq:
        y, x = dq.popleft()
        n += 1
        # 上下左右
        if y > 0 and candidate[y - 1, x] and not visited[y - 1, x]:
            visited[y - 1, x] = True; dq.append((y - 1, x))
        if y < h - 1 and candidate[y + 1, x] and not visited[y + 1, x]:
            visited[y + 1, x] = True; dq.append((y + 1, x))
        if x > 0 and candidate[y, x - 1] and not visited[y, x - 1]:
            visited[y, x - 1] = True; dq.append((y, x - 1))
        if x < w - 1 and candidate[y, x + 1] and not visited[y, x + 1]:
            visited[y, x + 1] = True; dq.append((y, x + 1))

    print(f"  BFS 访问 {n} 个像素")
    return visited


def make_alpha(im, thresh=12, feather=1.2):
    """生成 alpha 遮罩（0=背景透明，255=角色）。

    做法：
      1) 算出每个像素与「背景基准色」的最大通道差 → 背景候选
      2) 从四边 BFS，只有连通到边缘的才算真背景
         （角色内部的深色像素被轮廓围住，灌不进去 → 完整保留）
      3) 对遮罩做轻微羽化，消除锯齿
    """
    rgb = np.array(im.convert("RGB")).astype(np.int16)
    h, w, _ = rgb.shape

    # 背景基准色：取四边一圈的中位数，最稳（避免单点噪声）
    edge = np.concatenate([
        rgb[0:6].reshape(-1, 3), rgb[-6:].reshape(-1, 3),
        rgb[:, 0:6].reshape(-1, 3), rgb[:, -6:].reshape(-1, 3),
    ])
    bg = np.median(edge, axis=0).astype(np.int16)
    print(f"  背景基准色: {tuple(int(x) for x in bg)}")

    # 与背景的最大通道差
    diff = np.abs(rgb - bg).max(axis=2)
    candidate = diff < thresh          # True = 颜色上像背景

    is_bg = flood_fill_from_edges(candidate)
    alpha = np.where(is_bg, 0, 255).astype(np.uint8)

    print(f"  背景占比 {(alpha == 0).mean() * 100:.1f}%   "
          f"角色占比 {(alpha > 0).mean() * 100:.1f}%")

    if feather > 0:
        am = Image.fromarray(alpha, "L").filter(
            ImageFilter.GaussianBlur(radius=feather))
        alpha = np.array(am)

    return alpha


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="*", help="输入图片")
    ap.add_argument("--auto", action="store_true",
                    help="自动找 avatar/jimeng 下最新的图片")
    ap.add_argument("--thresh", type=int, default=12,
                    help="背景判定阈值（默认 12，越大抠得越狠）")
    ap.add_argument("--feather", type=float, default=1.2,
                    help="边缘羽化半径（默认 1.2px）")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    if args.auto:
        cands = glob.glob(os.path.join(ROOT, "avatar", "jimeng", "*.png")) + \
                glob.glob(os.path.join(ROOT, "avatar", "jimeng", "*.jpg"))
        cands = [c for c in cands if os.path.isfile(c)]
        if not cands:
            sys.exit("avatar/jimeng 下没有图片")
        src = max(cands, key=os.path.getmtime)
        inputs = [src]
    elif args.inputs:
        inputs = args.inputs
    else:
        sys.exit("用法: crawler_key.py <图>  或  --auto")

    for src in inputs:
        print(f"\n处理: {os.path.basename(src)}")
        im = Image.open(src)
        print(f"  原图 {im.size[0]}x{im.size[1]}")

        alpha = make_alpha(im, thresh=args.thresh, feather=args.feather)
        rgba = im.convert("RGBA")
        rgba.putalpha(Image.fromarray(alpha, "L"))

        # 裁掉四周全透明的空白（动画时定位更准）
        bbox = rgba.getbbox()
        if bbox:
            trimmed = rgba.crop(bbox)
        else:
            trimmed = rgba
        print(f"  裁掉空白后 {trimmed.size[0]}x{trimmed.size[1]}")

        base = os.path.splitext(os.path.basename(src))[0][:24]
        # 1) 透明版（"在屏幕上爬"用这个）
        p1 = os.path.join(OUT_DIR, "crawler_alpha.png")
        trimmed.save(p1)
        print(f"  ✅ 透明版: {os.path.basename(p1)}  {os.path.getsize(p1)//1024}KB")

        # 2) 绿幕版（万一某处不支持透明）
        green = Image.new("RGBA", trimmed.size, CHROMA + (255,))
        green.alpha_composite(trimmed)
        p2 = os.path.join(OUT_DIR, "crawler_green.png")
        green.convert("RGB").save(p2)
        print(f"  ✅ 绿幕版: {os.path.basename(p2)}  {os.path.getsize(p2)//1024}KB")

        # 3) 深色底预览（肉眼检查边缘）
        dark = Image.new("RGBA", trimmed.size, (10, 22, 44, 255))
        dark.alpha_composite(trimmed)
        p3 = os.path.join(OUT_DIR, "crawler_preview_dark.png")
        dark.convert("RGB").save(p3)
        print(f"  ✅ 深底预览: {os.path.basename(p3)}")

        # 4) 白底预览（检查有没有漏抠的深色残留）
        white = Image.new("RGBA", trimmed.size, (255, 255, 255, 255))
        white.alpha_composite(trimmed)
        p4 = os.path.join(OUT_DIR, "crawler_preview_white.png")
        white.convert("RGB").save(p4)
        print(f"  ✅ 白底预览: {os.path.basename(p4)}")

    print(f"\n输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
