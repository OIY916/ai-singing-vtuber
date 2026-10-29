# -*- coding: utf-8 -*-
"""用即梦生成的视频做直播间循环素材：找循环点 + 抠绿幕 + 输出 webm。

设计说明（2026-10-28）：
   即梦给的视频是「首帧图 → 首帧图」的完整闭环（因为尾帧也填了同一张图）。
   这带来一个副作用：**末尾会硬回弹**。实测 sing.mp4 在第 224 帧还是大幅摆动
   （与首帧差 25.8），到 232 帧突然回到静止（差 2.15）—— 只差 0.13 秒。
   直接整段循环，观众每 10 秒就会看到人物"啪"地跳回原位。

   所以必须：
     1. 找到一段「首尾视觉接近、且内部动作连贯」的子区间
     2. 只截这一段做循环（去掉结尾回弹区）
     3. 抠掉绿幕 → 透明 webm，直接能叠在直播画面上

   判断用的度量：逐帧转灰度后的平均绝对差（MAE）。
   为什么不用 SIFT/SSIM 那些：这个场景是同一角色同一机位，
   亮度差就够了；MAE 快、稳、不容易被发丝抖动骗到。

用法：
    python tools/make_avatar_clips.py                # 处理全部三段
    python tools/make_avatar_clips.py --only idle    # 只处理一段
    python tools/make_avatar_clips.py --analyze      # 只分析不输出
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIPS = os.path.join(ROOT, "avatar", "clips")
OUT = os.path.join(ROOT, "avatar", "clips_out")
WORK = os.path.join(ROOT, "avatar", "clips_work")

FPS = 60


def run(cmd, quiet=True):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 and not quiet:
        print(" ".join(cmd))
        print(r.stderr[-800:])
    return r


def probe(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
             "-show_entries", "format=duration", "-of", "json", path])
    try:
        d = json.loads(r.stdout)
        st = d["streams"][0]
        return {
            "w": int(st["width"]), "h": int(st["height"]),
            "fps_num": st.get("r_frame_rate", "60/1"),
            "n": int(st.get("nb_frames") or 0),
            "dur": float(d["format"]["duration"]),
        }
    except Exception:
        return None


def extract_frames(path, step, tmpdir):
    """抽帧成灰度 png，返回 [(idx, path)]"""
    info = probe(path)
    n = info["n"] or int(info["dur"] * FPS)
    out = []
    for idx in range(0, n, step):
        p = os.path.join(tmpdir, f"f{idx:04d}.png")
        run(["ffmpeg", "-y", "-v", "error", "-i", path,
             "-vf", f"select=eq(n\\,{idx}),scale=180:-1,format=gray",
             "-vframes", "1", p])
        if os.path.isfile(p):
            out.append((idx, p))
    return out, n


def load_gray(path):
    """不依赖 numpy 的极简 PGMA 读取 —— ffmpeg 已缩放成 180 宽灰度"""
    from PIL import Image
    im = Image.open(path).convert("L")
    return im


def mae(a, b):
    """两帧平均绝对差（PIL）"""
    import numpy as np
    aa = np.asarray(a, dtype=np.int16)
    bb = np.asarray(b, dtype=np.int16)
    if aa.shape != bb.shape:
        return 999.0
    return float(np.abs(aa - bb).mean())


def analyze(path, step=4, tail_guard=16):
    """找最佳循环区间。

    tail_guard: 结尾留多少帧不参与（即梦的硬回弹区）。
    返回 (start_frame, end_frame, score)
    """
    tmpdir = tempfile.mkdtemp(prefix="jk_")
    frames, n = extract_frames(path, step, tmpdir)
    if len(frames) < 10:
        return None

    imgs = {i: load_gray(p) for i, p in frames}
    keys = sorted(imgs)
    limit = max(keys) - tail_guard

    best = None
    # 对每个起点，向后找「与起点最像」的帧，跨度优先
    for a_i, s in enumerate(keys):
        for e in keys[a_i + 6:]:
            if e > limit:
                continue
            span = e - s
            d = mae(imgs[s], imgs[e])
            # 评分：跨度越长越好、差值越小越好
            score = d - span * 0.02
            if d < 6.0 and (best is None or score < best[2]):
                best = (s, e, score, d, span)

    if not best:
        return None
    s, e, score, d, span = best

    # 统计这段内部的最大帧间差，确认不是「静止片段」
    peak = 0.0
    prev = None
    for k in keys:
        if k < s or k > e:
            continue
        if prev is not None:
            peak = max(peak, mae(imgs[prev], imgs[k]))
        prev = k

    return {
        "start": s, "end": e, "frames": span,
        "seconds": round(span / FPS, 2),
        "seam_diff": round(d, 2),
        "motion_peak": round(peak, 2),
        "usable": bool(d < 6.0 and peak > 3.0),
    }


def cut_loop(src, start, end, dst_tmp):
    """截出 [start,end] 这段（不含 end 帧，保证循环时首尾不重复）。

    ⚠️ 不要用 trim=start_frame/setpts（2026-10-28 踩了大坑）：
       这样写出来的文件帧数是对的（184），但时间戳被算成 7.68 秒
       = 184 帧 ÷ 24fps。播放器按 24fps 解释 → 动作慢放 2.5 倍，
       呼吸变成缓慢抽动。加 -r 60 只会插值补帧，治不了根。
       改用**基于时间**的 -ss/-t 截取，时间轴天然正确。
    """
    ss = start / FPS
    dur = (end - start) / FPS
    run(["ffmpeg", "-y", "-v", "error",
         "-ss", f"{ss:.4f}", "-i", src, "-t", f"{dur:.4f}",
         "-r", str(FPS), "-fps_mode", "cfr",
         "-an", "-c:v", "libx264", "-crf", "16", "-preset", "medium", dst_tmp],
        quiet=False)
    return dur


def trim_empty(src, dst, alpha_threshold=20, force_box=None):
    """裁掉视频里「角色够不到」的空白区域。

    ⚠️ 为什么必须裁（2026-10-28）：
       即梦按我给的 720×1280 首帧生成，但角色实际只占 y 314~1202
       （上方 24.5% 全是空白）。直接当皮套用，角色在直播画面里
       显得很小，和原来静态图 470px 高的观感完全不匹配。

    ⚠️ 为什么不能裁到「刚好贴住包围盒」：
       动作中角色会上下浮动，裁太紧会在动画中途把头发或发梢切掉。
       四周留余量（左右 4%、上下 5%），给呼吸/摆头空间。

    force_box: (w, h, x, y) —— 直接用指定框，不再测量。
       三段必须用**同一个框**（由 main 先算并集），
       否则 idle/speak/sing 切换时人物大小会跳变 —— 那是非常致命的观感问题。
    """
    from PIL import Image
    import numpy as np

    if force_box:
        cw, ch, cx0, cy0 = force_box
        run(["ffmpeg", "-y", "-v", "error", "-i", src,
             "-vf", f"crop={cw}:{ch}:{cx0}:{cy0}",
             "-fps_mode", "cfr", "-r", str(FPS),
             "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
             "-b:v", "0", "-crf", "32", "-row-mt", "1", "-auto-alt-ref", "0",
             dst], quiet=False)
        return {"crop": [cx0, cy0, cw, ch], "char_bbox": None}

    # 用中段帧量包围盒（此时动作幅度大，包围盒最宽）
    probe = os.path.join(WORK, "_probe.png")
    run(["ffmpeg", "-y", "-v", "error", "-c:v", "libvpx-vp9", "-i", src,
         "-vf", "select=eq(n\\,30),format=rgba", "-vframes", "1", probe], quiet=False)
    if not os.path.isfile(probe):
        return None

    im = Image.open(probe).convert("RGBA")
    a = np.array(im)[..., 3]
    ys, xs = np.where(a > alpha_threshold)
    if len(ys) == 0:
        return None

    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    w, h = im.size

    pad_x = max(8, int(w * 0.04))
    pad_y = max(10, int(h * 0.05))

    cx0 = max(0, x0 - pad_x)
    cx1 = min(w, x1 + pad_x)
    cy0 = max(0, y0 - pad_y)
    cy1 = min(h, y1 + pad_y)

    # VP9 要求偶数
    cw = (cx1 - cx0) // 2 * 2
    ch = (cy1 - cy0) // 2 * 2

    run(["ffmpeg", "-y", "-v", "error", "-i", src,
         "-vf", f"crop={cw}:{ch}:{cx0}:{cy0}",
         "-fps_mode", "cfr", "-r", str(FPS),
         "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
         "-b:v", "0", "-crf", "32", "-row-mt", "1", "-auto-alt-ref", "0",
         dst], quiet=False)

    os.remove(probe)
    return {"crop": [cx0, cy0, cw, ch], "char_bbox": [x0, y0, x1 - x0, y1 - y0]}


def key_and_crop(src, dst, chroma_sim=0.12, chroma_blend=0.06, crop_box=None):
    """一步完成：抠绿幕 + 裁切 + 输出透明 webm。

    ⚠️ 为什么必须「一步」（2026-10-28 血的教训）：
       早期是两步 —— 先 chroma_key 出 webm，再裁剪出另一个 webm。
       结果 alpha 在这两次编码之间丢了：第一次输出带 alpha 的文件，
       第二次再读进来编码时没有 `format=yuva420p`，alpha 平面被丢弃，
       成品看起来正常，实际完全不透明（显示成一个绿方块）。

       VP9 的 alpha 是**独立平面**，管线上任何一环没显式声明 yuva420p
       都会悄悄丢掉它，而且不报错。所以最稳的做法是全程只编码一次。

    参数：
      chroma_sim   : 绿幕相似度阈值（0.12 = 只吃掉接近纯绿的像素）
      chroma_blend : 过渡带（0.06 = 边缘半透明，避免锯齿）
      crop_box     : (w, h, x, y)；None 表示不裁
    """
    vf = [f"chromakey=0x00b140:{chroma_sim}:{chroma_blend}"]
    if crop_box:
        cw, ch, cx, cy = crop_box
        vf.append(f"crop={cw}:{ch}:{cx}:{cy}")
    vf.append("format=yuva420p")

    run(["ffmpeg", "-y", "-v", "error", "-i", src,
         "-vf", ",".join(vf),
         "-fps_mode", "cfr", "-r", str(FPS),
         "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
         "-b:v", "0", "-crf", "32", "-row-mt", "1",
         "-auto-alt-ref", "0",          # ← 关键：不用交替参考帧，否则 alpha 丢失
         dst], quiet=False)
    return os.path.getsize(dst) if os.path.isfile(dst) else 0


def measure_bbox(src, alpha_threshold=20):
    """量出视频里角色的包围盒（用来算统一裁切框）。"""
    from PIL import Image
    import numpy as np

    probe = os.path.join(WORK, "_bbox.png")
    run(["ffmpeg", "-y", "-v", "error", "-i", src,
         "-vf", "chromakey=0x00b140:0.12:0.06,format=yuva420p,select=eq(n\\,30)",
         "-vframes", "1", probe], quiet=False)
    if not os.path.isfile(probe):
        return None

    im = Image.open(probe).convert("RGBA")
    a = np.array(im)[..., 3]
    ys, xs = np.where(a > alpha_threshold)
    os.remove(probe)
    if len(ys) == 0:
        return None
    return {"x": int(xs.min()), "y": int(ys.min()),
            "w": int(xs.max() - xs.min() + 1), "h": int(ys.max() - ys.min() + 1),
            "img": im.size}


def chroma_key(src, dst):
    """抠绿幕 → 透明 webm（VP9 + alpha）。

    为什么用 VP9/webm 而不是 h264：h264 不支持 alpha 通道，
    透明背景只能靠 webm(VP9) 或 mov(ProRes4444)，后者太大。
    OBS 的浏览器源（Chromium）原生支持 VP9 alpha。

    ⚠️ 必须显式锁定帧率（2026-10-28 踩了两个坑）：
       坑1：libvpx-vp9 没指定帧率时**默认按 24fps 输出**，
            184 帧被摊成 7.67 秒 → 动作慢放 2.5 倍，呼吸变抽动。
       坑2：只加 -r 60 是"插值补帧"，帧数暴涨 184→461 且时长不变。
            根因在上游时间轴；上游改用 -ss/-t 后，
            这里用 -fps_mode cfr 保持恒定帧率即可，不要重复插值。

    ⚠️ 必须加 -auto-alt-ref 0（2026-10-28，最关键的一个坑）：
       VP9 默认开启"交替参考帧"(alt-ref frames) 做码率优化，
       但它会**破坏 alpha 平面的独立编码** —— 生成的 webm 看起来
       一切正常，解出来 alpha 却全是 255（完全不透明）。
       我在这上面绕了很久：测试图能解出 alpha，真实产物却不行，
       差别就在自动 alt-ref。加上 -auto-alt-ref 0 之后：
         alpha 唯一值 256 个 / 透明区 59.8%，完全正确。
    """
    # chromakey 的 similarity/blend 需要按素材调：
    #   0.12 吃掉纯绿，0.06 的 blend 让边缘平滑过渡（不产生硬锯齿）
    #
    # ⚠️ 用 -fps_mode cfr 而不是单纯 -r 60（2026-10-28）：
    #    -r 60 对 24fps 源是"插值补帧"，帧数会暴涨（184 → 461）且时长不变。
    #    真正的问题是上游时间轴错乱；上游用 -ss/-t 修好后，
    #    这里只要保持恒定帧率输出即可，不要重复插值。
    run(["ffmpeg", "-y", "-v", "error", "-i", src,
         "-vf", "chromakey=0x00b140:0.12:0.06,format=yuva420p",
         "-fps_mode", "cfr", "-r", str(FPS),
         "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
         "-b:v", "0", "-crf", "32", "-row-mt", "1", "-auto-alt-ref", "0",
         dst], quiet=False)
    return os.path.getsize(dst) if os.path.isfile(dst) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只处理某一段 idle/speak/sing")
    ap.add_argument("--analyze", action="store_true", help="只分析不输出")
    ap.add_argument("--step", type=int, default=4, help="抽帧步长（越小越准越慢）")
    ap.add_argument("--no-crop", action="store_true", help="不裁空白（保留原始 9:16）")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)

    names = [args.only] if args.only else ["idle", "speak", "sing"]

    # ⚠️ 先算一遍所有段的裁切框，取并集 —— 三段必须用**同一个框**（2026-10-28）：
    #    如果各裁各的，idle/speak/sing 切换时人物会在画面里忽大忽小，
    #    这比"人物小一点"还难看。
    union = None
    if not args.no_crop:
        print("=" * 60)
        print("第一步：计算统一裁切框（三段共用）")
        print("=" * 60)
        boxes = []
        for name in names:
            src = os.path.join(CLIPS, f"{name}.mp4")
            if not os.path.isfile(src):
                continue
            a = analyze(src, step=max(args.step, 10))
            if not a:
                continue
            cut = os.path.join(WORK, f"{name}_probe_loop.mp4")
            cut_loop(src, a["start"], a["end"], cut)
            bb = measure_bbox(cut)
            if os.path.isfile(cut):
                os.remove(cut)
            if not bb:
                continue
            print(f"  {name}: 角色框=({bb['x']},{bb['y']}) {bb['w']}x{bb['h']}")
            boxes.append((bb, name))

        if boxes:
            # 取所有段包围盒的并集，再四周留余量（从最宽的那个算）
            x0 = min(b["x"] for b, _ in boxes)
            y0 = min(b["y"] for b, _ in boxes)
            x1 = max(b["x"] + b["w"] for b, _ in boxes)
            y1 = max(b["y"] + b["h"] for b, _ in boxes)
            W0, H0 = boxes[0][0]["img"]
            pad_x = max(8, int(W0 * 0.03))
            pad_y = max(10, int(H0 * 0.04))
            cx0 = max(0, x0 - pad_x)
            cy0 = max(0, y0 - pad_y)
            cx1 = min(W0, x1 + pad_x)
            cy1 = min(H0, y1 + pad_y)
            cw = (cx1 - cx0) // 2 * 2
            ch = (cy1 - cy0) // 2 * 2
            union = (cw, ch, cx0, cy0)
            print(f"  合并裁切框: {cw}x{ch}+{cx0}+{cy0}（三段共用）")
        print()

    report = {}

    for name in names:
        src = os.path.join(CLIPS, f"{name}.mp4")
        if not os.path.isfile(src):
            print(f"⚠️ 缺少 {src}")
            continue
        info = probe(src)
        print(f"\n{'='*60}\n{name}.mp4  {info['w']}x{info['h']}  "
              f"{info['n']} 帧  {info['dur']:.2f}s\n{'='*60}")

        a = analyze(src, step=args.step)
        if not a:
            print("  ❌ 没找到可用循环区间（动作太小或全段漂移）")
            report[name] = {"error": "no_loop"}
            continue

        print(f"  循环区间: 帧 {a['start']} → {a['end']}  "
              f"({a['seconds']}s, {a['frames']} 帧)")
        print(f"  接缝差: {a['seam_diff']}   内部动作幅度: {a['motion_peak']}")
        print(f"  可用: {'✅' if a['usable'] else '⚠️ 动作可能太小'}")
        report[name] = a

        if args.analyze:
            continue

        cut = os.path.join(WORK, f"{name}_loop.mp4")
        dur = cut_loop(src, a["start"], a["end"], cut)
        print(f"  截取: {dur:.2f}s → {os.path.getsize(cut)//1024}KB")

        # ⚠️ 抠图 + 裁切**一次编码完成**（见 key_and_crop 的注释）：
        #    分两步会丢掉 VP9 的 alpha 平面，产物变成不透明的绿方块。
        final = os.path.join(OUT, f"{name}.webm")
        size = key_and_crop(cut, final, crop_box=union)
        if union:
            cw, ch, cx, cy = union
            print(f"  抠绿幕+裁切: {size//1024}KB  裁切={cw}x{ch}+{cx}+{cy}")
            report[name]["crop"] = list(union)
        else:
            print(f"  抠绿幕: {size//1024}KB  → {final}")

        if os.path.isfile(cut):
            os.remove(cut)

    rep_path = os.path.join(OUT, "loop_report.json")
    with open(rep_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告: {rep_path}")


if __name__ == "__main__":
    main()
