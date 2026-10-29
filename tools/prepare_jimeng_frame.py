# -*- coding: utf-8 -*-
"""把三视图裁成「单人首帧图」，并做一版绿幕背景，供即梦图生视频使用。

为什么需要这个（2026-10-28）：
   用户手上的立绘是「正/侧/背」三视图拼在一张图里。直接丢给即梦做
   图生视频有三个问题：
     1. 一帧里有三个人 → 模型会让人物**互相打架**、或者干脆只动中间那个
     2. 宽幅 3:1 → 即梦视频是 16:9 / 9:16 / 1:1，会被拉伸或裁掉
     3. 白底 → 生成完还得抠图，抠白底容易把白围裙、白蕾丝一起吃进去

   所以：先裁出**正面单人**，再给一个**纯绿背景**版本。
   绿幕色 (0,177,64) 是影视标准色，且已确认角色身上没有任何绿色像素。

用法：
    python tools/prepare_jimeng_frame.py                    # 正经比例（原图直接裁）
    python tools/prepare_jimeng_frame.py --input 三视图.png  # 指定输入
"""

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "avatar", "jimeng")

# 影视标准绿幕色
CHROMA = (0, 177, 64)


def find_columns(im, bg_tol=240, min_gap=8, min_col=30):
    """找出图里「有内容」的竖向区块，用来切分三视图。

    做法：逐列统计非背景像素数，把连续有内容的列聚成一段。
    背景按「接近白色」判定（三视图是白底）。
    """
    w, h = im.size
    px = im.convert("RGB").load()
    col_has = []
    for x in range(w):
        n = 0
        for y in range(0, h, 4):                    # 纵向抽样，够用且快
            r, g, b = px[x, y]
            if not (r >= bg_tol and g >= bg_tol and b >= bg_tol):
                n += 1
                if n >= 3:                          # 有几行算几行，不必数满
                    break
        col_has.append(n >= 3)

    spans = []
    start = None
    gap = 0
    for x, has in enumerate(col_has):
        if has:
            if start is None:
                start = x
            gap = 0
        else:
            if start is not None:
                gap += 1
                if gap >= min_gap:
                    if x - gap - start >= min_col:
                        spans.append((start, x - gap))
                    start = None
    if start is not None and w - start >= min_col:
        spans.append((start, w))

    return spans


def to_chroma(im, chroma=CHROMA):
    """白/透明背景 → 纯绿背景。保留原图 alpha 边缘，不产生白边。"""
    im = im.convert("RGBA")
    out = Image.new("RGBA", im.size, chroma + (255,))
    out.alpha_composite(im)
    return out.convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="", help="三视图文件；默认找 avatar 下最像的那张")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    # 默认输入：优先用用户截图保存的位置
    cand = [args.input] if args.input else [
        os.path.join(ROOT, "avatar", "refs_3view.png"),
        os.path.join(ROOT, "avatar", "3view.png"),
        os.path.join(ROOT, "stream", "refs", "three_view.png"),
    ]
    src = next((p for p in cand if p and os.path.isfile(p)), None)
    if not src:
        print("没找到三视图输入文件。请先把它保存到 avatar/refs_3view.png")
        print("（或传 --input 指定路径）")
        return

    print(f"输入: {src}")
    im = Image.open(src)
    print(f"原图: {im.size[0]}x{im.size[1]}  mode={im.mode}")

    spans = find_columns(im)
    print(f"检测到 {len(spans)} 个竖向人物区块: {spans}")
    if not spans:
        print("没检测到人物区块，可能要调 bg_tol；先不处理")
        return

    # 第一块 = 正面
    x0, x1 = spans[0]
    # 纵向也裁一下（去掉顶部/底部的空白）
    crop = im.crop((x0, 0, x1, im.size[1]))
    bb = crop.convert("RGBA").getbbox()
    if bb:
        crop = crop.crop((0, bb[1], crop.size[0], bb[3]))

    front = crop.convert("RGBA")
    print(f"正面单裁: {front.size[0]}x{front.size[1]}  宽高比 {front.size[0]/front.size[1]:.3f}")

    # 即梦的视频是 16:9 / 9:16 / 1:1。人物立绘最合适放 9:16（竖屏），
    # 但直播画面是 16:9 横屏。两种都给，你自己选。
    outputs = []

    # 1) 绿幕原比例（最保真，模型不会被拉伸）
    p1 = os.path.join(OUT_DIR, "jimeng_front_green.png")
    to_chroma(front).save(p1, quality=95)
    outputs.append(p1)

    # 2) 9:16 竖屏，绿幕，人物垂直居中、下方留一点空间（腿脚别贴边）
    W, H = 720, 1280
    canvas = Image.new("RGBA", (W, H), CHROMA + (255,))
    # 等比缩放到画布高度的 82%
    target_h = int(H * 0.82)
    scale = target_h / front.size[1]
    nw = int(front.size[0] * scale)
    f2 = front.resize((nw, target_h), Image.LANCZOS)
    canvas.alpha_composite(f2, ((W - nw) // 2, int(H * 0.10)))
    p2 = os.path.join(OUT_DIR, "jimeng_front_9x16_green.png")
    canvas.convert("RGB").save(p2, quality=95)
    outputs.append(p2)

    # 3) 白底版 9:16（万一即梦不吃绿幕，用白底也稳）
    canvas_w = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    canvas_w.alpha_composite(f2, ((W - nw) // 2, int(H * 0.10)))
    p3 = os.path.join(OUT_DIR, "jimeng_front_9x16_white.png")
    canvas_w.convert("RGB").save(p3, quality=95)
    outputs.append(p3)

    # 4) 透明背景版（本地做贴图用，不发给即梦）
    p4 = os.path.join(OUT_DIR, "front_transparent.png")
    front.save(p4)
    outputs.append(p4)

    print("\n生成的文件:")
    for p in outputs:
        sz = os.path.getsize(p) // 1024
        im2 = Image.open(p)
        print(f"  {os.path.basename(p):32s} {im2.size[0]}x{im2.size[1]}  {sz}KB")

    print(f"\n目录: {OUT_DIR}")
    print("\n下一步（在即梦网页版里）：")
    print("  1. 进「视频生成」→ 选「图生视频」")
    print("  2. 上传 jimeng_front_9x16_green.png")
    print("  3. 面板「上传参考图/首帧」的位置按提示选「首帧」")
    print("  4. 提示词用 tools/jimeng_prompts.md 里的 idle 那段")


if __name__ == "__main__":
    main()
