# -*- coding: utf-8 -*-
"""把正面立绘切成「头」「身体」两层，并生成一个"嘴部开口"图层。

思路（不出真 Live2D，但能骗过观众的眼睛）：
  - head.png   : 头部（含头发、脸、眼、嘴）
  - mouth_open : 在嘴的位置画一个小椭圆嘴巴，按音量显隐/缩放
  - 眨眼       : 用两条肤色横条模拟眼睑，闭合时盖住眼睛

输出到 E:\\qq-bot\\avatar\\
"""
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

sys.stdout.reconfigure(encoding="utf-8")

SRC = r"E:\qq-bot\avatar\front_cut.png"
OUT = r"E:\qq-bot\avatar"

# 实测坐标（front_cut.png 440x638）
MOUTH = (203, 318)          # 嘴中心
EYE_L = (155, 272)          # 左眼中心
EYE_R = (252, 272)          # 右眼中心
EYE_W, EYE_H = 46, 34       # 单眼大致包围盒

# 脖子分界线（头/身体）
NECK_Y = 385


def sample_skin(rgb: np.ndarray, center, r=16):
    """在中心附近采样"肤色"（排除线稿黑边）"""
    cx, cy = center
    patch = rgb[cy - r:cy + r, cx - r:cx + r].reshape(-1, 3)
    bright = patch[patch.min(axis=1) > 190]
    if len(bright) == 0:
        return (255, 240, 230)
    return tuple(int(v) for v in np.median(bright, axis=0))


def make_mouth_open(rgb, alpha, mouth, skin):
    """生成"张开嘴"图层：上唇弧 + 口腔 + 舌头，画得像线稿而不是纯椭圆"""
    h, w = alpha.shape          # ⚠️ numpy 是 (高, 宽)，别写反
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    from PIL import ImageDraw

    d = ImageDraw.Draw(layer)
    cx, cy = mouth
    mw, mh = 17, 14
    x0, x1 = cx - mw // 2, cx + mw // 2
    y0, y1 = cy - mh // 2, cy + mh // 2

    # 口腔（深红褐）
    d.ellipse([x0, y0 + 2, x1, y1], fill=(112, 46, 58, 255))
    # 舌头（粉）
    d.ellipse([cx - 6, cy + 2, cx + 6, y1 - 1], fill=(214, 112, 126, 255))
    # 上唇线（深色，压住口腔上缘，让边缘干净）
    d.arc([x0 - 1, y0, x1 + 1, y1], start=185, end=355, fill=(56, 44, 64, 255), width=2)
    return layer


def make_eyelid(rgb, alpha, eye, skin):
    """生成眼睑图层（闭眼用）。

    ⚠️ 直接用肤色矩形盖眼睛很丑（像贴了创可贴）。改用：
       从原图该眼睛区域取像素，做「垂直压缩 + 移位」生成闭合眼，
       这样颜色、线稿、阴影都来自原图，不会有色差。
    """
    h, w = alpha.shape
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cx, cy = eye
    half_w, half_h = EYE_W // 2, EYE_H // 2
    x0, x1 = cx - half_w, cx + half_w
    y0, y1 = cy - half_h, cy + half_h
    # 往外扩一点，保证盖住眼眶
    x0, x1 = max(0, x0 - 2), min(w, x1 + 2)
    y0, y1 = max(0, y0 - 2), min(h, y1 + 2)

    src = Image.fromarray(rgb[y0:y1, x0:x1])
    sw, sh = src.size
    # 垂直压到 35% 高度 → 看起来像眼睛闭上
    closed = src.resize((sw, max(2, int(sh * 0.35))), Image.LANCZOS)
    # 上下补满，用顶部像素延展（额头肤色）
    canvas = Image.new("RGB", (sw, sh))
    top_pad = (sh - closed.height) // 2
    canvas.paste(src.crop((0, 0, sw, top_pad)), (0, 0)) if top_pad > 0 else None
    canvas.paste(closed, (0, top_pad))
    rest = sh - top_pad - closed.height
    if rest > 0:
        canvas.paste(src.crop((0, sh - rest, sw, sh)), (0, top_pad + closed.height))

    # 只保留椭圆区域，边缘羽化，避免方形硬边
    mask = Image.new("L", (sw, sh), 0)
    from PIL import ImageDraw

    md = ImageDraw.Draw(mask)
    md.ellipse([0, 0, sw - 1, sh - 1], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(2))

    out = canvas.convert("RGBA")
    out.putalpha(mask)
    layer.paste(out, (x0, y0), out)
    return layer


def main():
    im = Image.open(SRC).convert("RGBA")
    rgb = np.array(im)[..., :3]
    alpha = np.array(im)[..., 3]
    w, h = im.size

    skin = sample_skin(rgb, MOUTH)

    # 1) 嘴张开图层
    mo = make_mouth_open(rgb, alpha, MOUTH, skin)
    mo.save(os.path.join(OUT, "mouth_open.png"))
    print(f"mouth_open.png  {mo.size}")

    # 2) 两个眼睑图层合并成一个
    el = make_eyelid(rgb, alpha, EYE_L, skin)
    er = make_eyelid(rgb, alpha, EYE_R, skin)
    lids = Image.alpha_composite(el, er)
    lids.save(os.path.join(OUT, "eyelids.png"))
    print(f"eyelids.png     {lids.size}")

    # 3) 头/身分层：脖子以上 = 头
    head = im.copy()
    head_arr = np.array(head)
    head_arr[NECK_Y:, :, 3] = 0
    Image.fromarray(head_arr).save(os.path.join(OUT, "head.png"))
    print(f"head.png        (y<{NECK_Y})")

    # 4) 输出坐标给前端
    import json

    meta = {
        "size": [w, h],
        "mouth": list(MOUTH),
        "eyes": [list(EYE_L), list(EYE_R)],
        "eye_box": [EYE_W, EYE_H],
        "neck_y": NECK_Y,
        "skin": list(skin),
    }
    with open(os.path.join(OUT, "avatar_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print("\nmeta:", json.dumps(meta, ensure_ascii=False))
    print("\n素材已生成到:", OUT)


if __name__ == "__main__":
    main()
