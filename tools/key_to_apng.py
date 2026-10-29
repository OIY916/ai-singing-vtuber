# -*- coding: utf-8 -*-
"""绿幕视频 → 透明 APNG（因为本机 ffmpeg 的 VP9 编码器不支持 alpha）。

背景（2026-10-28 实测）：
   本机 ffmpeg 9.0.2 的 `libvpx-vp9` **完全不支持 alpha**：
      · 用一张「左半不透明、右半全透明」的测试图编码再解码
      · 解回来的 alpha 唯一值只有 [255]，透明区全变成不透明
    `chromakey` / `colorkey` 滤镜同样输出不带 alpha。
    试过 webp 动图、GIF、qtrle mov：
      · WebP 动图（ffmpeg 和 Pillow 都一样）**丢 alpha**
      · GIF 只有二值透明，边缘会锯齿
      · **APNG 保留完整 8bit alpha** ✅
    浏览器的 `canPlayType('video/webm; codecs="vp9"')` 返回 probably，
    说明只是编码端不行，解码没问题 —— 但既然产不出带 alpha 的 webm，
    就用 APNG。体积大一些，但画质和透明都是对的。

    APNG 的浏览器支持：Chrome/Edge/Firefox/Safari 全支持，
    OBS 的 Chromium 浏览器源天然支持。

用法：
    python tools/key_to_apng.py <输入.mp4> <输出.png>
    python tools/key_to_apng.py --all        # 处理 clips_work 下所有 *_loop.mp4
"""

import argparse
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "avatar", "clips_work")
OUT = os.path.join(ROOT, "avatar", "clips_out")
FPS = 60

CHROMA = np.array([0, 177, 64], dtype=np.float32)


def key_frame(arr, sim=0.16, blend=0.10, despill=0.85):
    """单帧 → 带 alpha 的 RGBA。

    sim   : 到绿幕色的归一化距离阈值（< sim 全透明）
    blend : sim 之外的过渡带宽度（产生半透明边缘，避免硬锯齿）
    despill: 去溢色强度（压掉边缘残留的绿光）
    """
    rgb = arr[..., :3].astype(np.float32)
    d = np.linalg.norm(rgb - CHROMA[None, None, :], axis=2) / 255.0
    alpha = np.clip((d - sim) / max(blend, 1e-6), 0.0, 1.0)

    # 去溢色：绿色分量不该远高于 R/B
    g = rgb[..., 1]
    rb = np.maximum(rgb[..., 0], rgb[..., 2])
    rgb[..., 1] = g - np.clip(g - rb, 0, None) * despill

    out = np.zeros(arr.shape[:2] + (4,), dtype=np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = (alpha * 255).astype(np.uint8)
    return out


def extract(src, tmpdir):
    """解出所有帧 PNG。用 -f image2 明确输出序列，避免单帧覆盖。"""
    pattern = os.path.join(tmpdir, "f%04d.png")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", src,
         "-pix_fmt", "rgba", "-f", "image2", pattern],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-500:])
    return sorted(p for p in os.listdir(tmpdir) if p.endswith(".png"))


def process(src, dst, verbose=True):
    tmpdir = tempfile.mkdtemp(prefix="apng_")
    names = extract(src, tmpdir)
    if not names:
        print(f"❌ 没解出帧: {src}")
        return None
    if verbose:
        print(f"  帧数: {len(names)}")

    frames = []
    for i, n in enumerate(names):
        p = os.path.join(tmpdir, n)
        im = Image.open(p).convert("RGBA")
        arr = key_frame(np.array(im))
        frames.append(Image.fromarray(arr, "RGBA"))
        os.remove(p)
        if verbose and i % 60 == 0:
            print(f"  抠图 {i}/{len(names)}")

    # ⚠️ disposal=1 + blend=0 是 APNG 的关键参数：
    #    disposal=1 表示每帧画完不还原画布，
    #    blend=0（SOURCE）表示新帧直接覆盖而非混合。
    #    少了这两个，半透明边缘会和上一帧叠加，动画会变成"拖影"。
    frames[0].save(dst, save_all=True, append_images=frames[1:],
                   duration=int(1000 / FPS), loop=0,
                   format="PNG", disposal=1, blend=0)
    size = os.path.getsize(dst)
    if verbose:
        print(f"  ✅ {os.path.basename(dst)}  {size//1024}KB  {len(frames)} 帧")
    return size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="*", help="输入视频 + 输出路径")
    ap.add_argument("--all", action="store_true", help="处理 clips_work/*_loop.mp4")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)

    if args.all:
        jobs = []
        for name in ["idle", "speak", "sing"]:
            src = os.path.join(WORK, f"{name}_loop.mp4")
            if os.path.isfile(src):
                jobs.append((src, os.path.join(OUT, f"{name}.png")))
        if not jobs:
            sys.exit(f"{WORK} 下没有 *_loop.mp4，先跑 make_avatar_clips.py")
    elif len(args.inputs) == 2:
        jobs = [(args.inputs[0], args.inputs[1])]
    else:
        sys.exit("用法: key_to_apng.py <in.mp4> <out.png>  或  --all")

    for src, dst in jobs:
        print(f"\n{os.path.basename(src)} → {os.path.basename(dst)}")
        process(src, dst)


if __name__ == "__main__":
    main()
