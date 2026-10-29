# -*- coding: utf-8 -*-
"""从三视图立绘里切出正面视图，并把白底做成透明（泛洪抠图）。

关键：只抠"连到画布边缘"的浅色区域 → 人物内部的白色（脸、围裙、头饰）不会被误删。
这是和"按颜色阈值抠图"的本质区别。

输出：
  avatar/front_cut.png      正面抠图（透明背景，原始尺寸）
  avatar/front_small.png    缩放到适合网页的尺寸
"""
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

sys.stdout.reconfigure(encoding="utf-8")

SRC = r"E:\qq-bot\大肥鱼参考图.jpg"
OUT_DIR = r"E:\qq-bot\avatar"
os.makedirs(OUT_DIR, exist_ok=True)

# 三视图的切割边界（实测得出）
COL_FRONT = (32, 472)     # 正面
COL_SIDE = (497, 853)     # 侧面
COL_BACK = (856, 1262)    # 背面
ROW_CROP = (36, 680)      # 上下留一点


def flood_remove_bg(rgb: np.ndarray, tol: int = 22) -> np.ndarray:
    """从四条边泛洪，把与边缘连通的近白色区域标为透明。

    @returns RGBA uint8
    """
    h, w, _ = rgb.shape
    # 边缘种子：四条边上所有接近白的像素
    edge = np.zeros((h, w), bool)
    edge[0, :] = edge[-1, :] = True
    edge[:, 0] = edge[:, -1] = True

    near_white = (rgb.min(axis=2) > 255 - tol * 3).astype(bool) | (
        (rgb.max(axis=2) - rgb.min(axis=2) < tol) & (rgb.min(axis=2) > 200)
    )

    # 连通域标记：只在 near_white 上做，取与边缘相连的那一块
    labeled, n = ndimage.label(near_white)
    bg_labels = set(labeled[edge & near_white].ravel().tolist())
    bg_labels.discard(0)

    bg = np.isin(labeled, list(bg_labels))
    # 轻微膨胀一点，吃掉抗锯齿白边
    bg = ndimage.binary_dilation(bg, iterations=2)

    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., :3] = rgb
    rgba[..., 3] = np.where(bg, 0, 255)

    # 边界柔化：对 alpha 做一次轻微模糊，避免锯齿
    alpha = rgba[..., 3].astype(np.float32)
    alpha = ndimage.uniform_filter(alpha, size=3)
    rgba[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)
    return rgba


def trim(rgba: np.ndarray, pad: int = 8) -> np.ndarray:
    """裁到不透明内容的包围盒，留一点边距"""
    a = rgba[..., 3]
    ys, xs = np.where(a > 8)
    if len(ys) == 0:
        return rgba
    y0, y1 = ys.min(), ys.max()
    x0, x1 = xs.min(), xs.max()
    h, w = a.shape
    y0 = max(0, y0 - pad)
    x0 = max(0, x0 - pad)
    y1 = min(h, y1 + pad + 1)
    x1 = min(w, x1 + pad + 1)
    return rgba[y0:y1, x0:x1]


def process(name: str, box: tuple):
    x0, x1 = box
    y0, y1 = ROW_CROP
    crop = np.array(Image.open(SRC).convert("RGB"))[y0:y1, x0:x1]
    rgba = flood_remove_bg(crop)
    rgba = trim(rgba)

    img = Image.fromarray(rgba, "RGBA")
    out1 = os.path.join(OUT_DIR, f"{name}_cut.png")
    img.save(out1)
    print(f"{name}: {img.size}  -> {out1}")

    # 网页用尺寸（高 640，保持比例）
    h = img.height
    if h > 640:
        scale = 640 / h
        small = img.resize((max(1, int(img.width * scale)), 640), Image.LANCZOS)
        out2 = os.path.join(OUT_DIR, f"{name}_small.png")
        small.save(out2)
        print(f"{name}: {small.size}  -> {out2}")

    # 统计透明比例
    arr = np.array(img)
    transparent = (arr[..., 3] < 8).mean()
    print(f"{name}: 透明像素占比 {transparent*100:.1f}%")


if __name__ == "__main__":
    process("front", COL_FRONT)
    process("side", COL_SIDE)
    process("back", COL_BACK)
    print("\n完成。素材在:", OUT_DIR)
