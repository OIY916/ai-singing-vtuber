# -*- coding: utf-8 -*-
"""逐帧抠绿幕 → 透明 webm（Python 实现，不依赖 ffmpeg 的 chromakey）。

为什么不用 ffmpeg 的 chromakey（2026-10-28 实测踩坑）：
   本机是 ffmpeg 9.0.2，`chromakey` 滤镜输出的是 YUV，
   **alpha 通道全被丢弃**（实测解出来 alpha 唯一值只有 1 个，恒为 255）。
   `colorkey` 一样不行。所以画面直接显示成一个绿方块。
   试过 format=yuva420p 也没用 —— 滤镜本身就不产生 alpha。

   结论：这个版本必须自己算 alpha。Python 逐帧处理虽然慢
   （720×1280 每帧约 30ms，200 帧约 6 秒），但结果可控且正确。

抠图算法：
   · 绿幕色 #00b140（影视标准 chroma green）
   · 色距 < sim → 全透明
   · sim ~ sim+blend → 半透明过渡（避免硬锯齿）
   · 其余 → 完全不透明
   · 额外做「去溢色」：把残留的绿色分量压下去，避免边缘发绿光

用法：
    python tools/key_green_frame.py <输入.webm> <输出.webm>
"""

import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

FPS = 60
CHROMA = np.array([0, 177, 64], dtype=np.float32)


def key_frame(arr, sim=0.16, blend=0.10, despill=True):
    """单帧 RGB(A) → 带 alpha 的 RGBA 数组。"""
    rgb = arr[..., :3].astype(np.float32)

    # 到绿幕色的归一化距离（用 0~1 尺度，和 ffmpeg 的相似度参数同量级）
    d = np.linalg.norm(rgb - CHROMA[None, None, :], axis=2) / 255.0

    # alpha：距离 sim 以内全透明；sim~sim+blend 线性过渡
    alpha = np.clip((d - sim) / max(blend, 1e-6), 0.0, 1.0)

    if despill:
        # 去溢色：绿色分量不该超过 R/B 的最大值太多，否则边缘会有绿光
        g = rgb[..., 1]
        rb_max = np.maximum(rgb[..., 0], rgb[..., 2])
        excess = np.clip(g - rb_max, 0, None)
        rgb[..., 1] = g - excess * 0.85

    out = np.zeros(arr.shape[:2] + (4,), dtype=np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = (alpha * 255).astype(np.uint8)
    return out


def main():
    if len(sys.argv) < 3:
        sys.exit("用法: python tools/key_green_frame.py <in.webm> <out.webm>")

    src, dst = sys.argv[1], sys.argv[2]
    tmpdir = tempfile.mkdtemp(prefix="key_")

    # 1) 解出所有帧为 PNG（保留原始分辨率）
    print(f"解帧: {src}")
    pngs = []
    i = 0
    while True:
        p = os.path.join(tmpdir, f"f{i:04d}.png")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src,
                            "-vf", f"select=eq(n\\,{i})", "-vframes", "1", p],
                           capture_output=True)
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            break
        pngs.append(p)
        i += 1
        if i > 2000:      # 安全上限
            break
    print(f"  共 {len(pngs)} 帧")

    if not pngs:
        sys.exit("没解出任何帧")

    # 2) 逐帧抠图
    outdir = os.path.join(tmpdir, "keyed")
    os.makedirs(outdir, exist_ok=True)
    for n, p in enumerate(pngs):
        im = Image.open(p).convert("RGBA")
        arr = np.array(im)
        keyed = key_frame(arr)
        Image.fromarray(keyed, "RGBA").save(os.path.join(outdir, f"k{n:04d}.png"))
        if n % 40 == 0:
            print(f"  抠图 {n}/{len(pngs)}")

    # 3) 合成透明 webm
    print("合成 webm ...")
    pattern = os.path.join(outdir, "k%04d.png")
    r = subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-framerate", str(FPS), "-i", pattern,
                        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                        "-b:v", "0", "-crf", "30", "-row-mt", "1",
                        dst], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-600:])
        sys.exit("合成失败")

    print(f"✅ {dst}  {os.path.getsize(dst)//1024}KB  {len(pngs)} 帧")
    # 清理
    for p in pngs:
        try:
            os.remove(p)
        except OSError:
            pass
    for n in range(len(pngs)):
        try:
            os.remove(os.path.join(outdir, f"k{n:04d}.png"))
        except OSError:
            pass


if __name__ == "__main__":
    main()
