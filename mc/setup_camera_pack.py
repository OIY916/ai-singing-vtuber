# -*- coding: utf-8 -*-
"""生成「自动跟拍摄像机」数据包，放进 mc/server/world/datapacks/。

作用：任何以 FishCam 进服的玩家 → 自动进入旁观模式，并被每 tick 传送/转向，
      始终盯着 BigFatFish（大肥鱼）。摄像机位置在服务端每 tick 刷新，画面是真实客户端渲染。

用法（在服务端启动前跑，或跑完重启服务端）：
    python mc/setup_camera_pack.py 1.21.1

版本差异：
- 1.21+ 数据包目录用单数：data/<ns>/function/、tags/function/
- 1.20.x 用复数：data/<ns>/functions/、tags/functions/
- pack_format 必须对，否则服务端视作不兼容直接不加载
"""
import json
import os
import shutil
import sys

BOT = "BigFatFish"
CAM = "FishCam"
SERVER = r"E:\qq-bot\mc\server"
PACK_NAME = "cam_follow"

# datapack pack_format
FORMATS = {
    "1.20.1": 15, "1.20.2": 18, "1.20.3": 26, "1.20.4": 26,
    "1.20.5": 41, "1.20.6": 45,
    "1.21": 48, "1.21.1": 48, "1.21.2": 57, "1.21.3": 57,
    "1.21.4": 61, "1.21.5": 71, "1.21.6": 80, "1.21.7": 80, "1.21.8": 80,
}

# 每 tick 执行：把 FishCam 设为旁观者并跟拍大肥鱼
TICK = f"""# 摄像机账号：
#   首次进服强制旁观一次（防掉进岩浆），之后游戏模式完全由你自己决定
#   左键点 {BOT} 可附身她的第一人称视角
#   /tag <名字> add follow  → 自动跟拍（默认关）
tag @a[name=FishCam] add cam
tag @a[name=StreamCam] add cam
# 只强制一次：用 cam_init 标签做哨兵，之后 /gamemode survival 不会再被改回来
execute as @a[tag=cam,tag=!cam_init] run gamemode spectator @s
execute as @a[tag=cam,tag=!cam_init] run tag @s add cam_init
# 伽马覆写：给机位账号常驻夜视，黑夜/洞穴都看得清
execute as @a[tag=cam] run effect give @s minecraft:night_vision 3600 0 true
# 第一人称机位由 mc/camera_driver.py 用 RCON 驱动（分步传送，避开"moved too quickly"）
# 第三人称跟拍：离太远才拉过去
execute at @a[name={BOT},limit=1] run tp @a[tag=cam,tag=follow,distance=12..] ~5 ~4 ~5 facing entity @a[name={BOT},limit=1]
execute as @a[tag=cam,tag=follow] at @s run tp @s ~ ~ ~ facing entity @a[name={BOT},limit=1]
"""


def main():
    ver = sys.argv[1] if len(sys.argv) > 1 else "1.21.1"
    fmt = FORMATS.get(ver, 48)
    singular = tuple(int(x) for x in ver.split(".")[:2]) >= (1, 21)
    fn_dir = "function" if singular else "functions"
    tag_dir = "function" if singular else "functions"

    root = os.path.join(SERVER, "world", "datapacks", PACK_NAME)
    if os.path.isdir(root):
        shutil.rmtree(root)

    os.makedirs(os.path.join(root, "data", "fishcam", fn_dir), exist_ok=True)
    os.makedirs(os.path.join(root, "data", "minecraft", "tags", tag_dir), exist_ok=True)

    meta = {"pack": {"pack_format": fmt, "description": "大肥鱼摄像机跟拍"}}
    # supported_formats 是 1.20.5+ 的字段，老版本会当未知键，别加
    if tuple(int(x) for x in ver.split(".")[:2]) >= (1, 20) and ver not in ("1.20.1", "1.20.2", "1.20.3", "1.20.4"):
        meta["pack"]["supported_formats"] = [min(15, fmt), max(99, fmt)]
    with open(os.path.join(root, "pack.mcmeta"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    with open(os.path.join(root, "data", "fishcam", fn_dir, "tick.mcfunction"),
              "w", encoding="utf-8") as f:
        f.write(TICK)

    with open(os.path.join(root, "data", "minecraft", "tags", tag_dir, "tick.json"),
              "w", encoding="utf-8") as f:
        json.dump({"values": ["fishcam:tick"]}, f)

    print(f"数据包已生成: {root}")
    print(f"  版本={ver}  pack_format={fmt}  目录={'单数(1.21+)' if singular else '复数(<=1.20.6)'}")
    print(f"  规则: 任何叫 {CAM} 的玩家进服 → 旁观模式 + 跟拍 {BOT}")


if __name__ == "__main__":
    main()
