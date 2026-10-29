# -*- coding: utf-8 -*-
"""第一人称相机驱动：让 StreamCam 的眼睛贴在 BigFatFish 上。

为什么不用数据包：vanilla 服务端有「moved too quickly」反作弊，一次传送超过 ~100 格
会被判定瞬移并打回原位（日志里 `StreamCam moved too quickly!`）。
所以这里用 RCON 每 90ms 主动传送一次，距离超过 90 格时分步逼近。

用法：
    python mc/camera_driver.py                 # 前台跑
    python mc/camera_driver.py --name StreamCam --target BigFatFish
"""
import argparse
import io
import math
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import importlib.util

spec = importlib.util.spec_from_file_location("rcon", "mc/rcon.py")
rcon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rcon)


def parse_vec(resp):
    # "X has the following entity data: [-256.0d, 71.0d, 480.6d]"
    s = resp.split("entity data:")[-1].strip().strip("[]")
    parts = [p.strip().rstrip("dfF") for p in s.split(",")]
    return [float(p) for p in parts]


def q(cmd):
    return rcon.run([cmd])[0][1].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="StreamCam")
    ap.add_argument("--target", default="BigFatFish")
    ap.add_argument("--offset", type=float, default=0.6, help="眼睛前方多少格")
    ap.add_argument("--hz", type=float, default=11.0)
    args = ap.parse_args()

    step_limit = 90.0
    period = 1.0 / max(1.0, args.hz)
    print(f"[camera] 跟随 {args.target} → {args.name}，{args.hz}Hz")

    while True:
        t0 = time.time()
        try:
            tp = q(f"data get entity {args.target} Pos")
            rot = q(f"data get entity {args.target} Rotation")
            if "No entity" in tp or "No entity" in rot:
                time.sleep(1)
                continue
            px, py, pz = parse_vec(tp)
            yaw, pitch = parse_vec(rot)

            cy, cp = math.radians(yaw), math.radians(pitch)
            fx = -math.sin(cy) * math.cos(cp)
            fy = -math.sin(cp)
            fz = math.cos(cy) * math.cos(cp)
            tx = px + fx * args.offset
            ty = py + 1.62 + fy * args.offset
            tz = pz + fz * args.offset

            cpos = q(f"data get entity {args.name} Pos")
            if "No entity" in cpos:
                time.sleep(0.5)
                continue
            cx, cyy, cz = parse_vec(cpos)
            dist = math.sqrt((tx - cx) ** 2 + (ty - cyy) ** 2 + (tz - cz) ** 2)

            if dist > step_limit:
                k = step_limit / dist
                sx, sy, sz = cx + (tx - cx) * k, cyy + (ty - cyy) * k, cz + (tz - cz) * k
            else:
                sx, sy, sz = tx, ty, tz

            rcon.run([f"tp {args.name} {sx:.2f} {sy:.2f} {sz:.2f} {yaw:.2f} {pitch:.2f}"])
        except Exception as e:
            print("[camera] 异常:", e)
            time.sleep(0.5)
        dt = time.time() - t0
        if dt < period:
            time.sleep(period - dt)


if __name__ == "__main__":
    main()
