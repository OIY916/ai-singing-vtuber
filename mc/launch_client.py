# -*- coding: utf-8 -*-
"""直接启动 1.20.1 原版客户端（不经过 PCL），用于开第二个「直播机位」账号。

用法：
    python mc/launch_client.py                      # 默认 StreamCam 连 127.0.0.1
    python mc/launch_client.py --name Test1 --port 25565
    python mc/launch_client.py --no-join            # 只开到主菜单

原理：读 .minecraft/versions/1.20.1/1.20.1.json 里的库列表 → 拼 classpath →
解压 natives → 用离线 UUID 启动 net.minecraft.client.main.Main。
"""
import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import uuid as uuidlib

MC = r"E:\game\MC\.minecraft"
VERSION = "1.20.1"
JAVA = r"E:\java\java17\bin\javaw.exe"


def offline_uuid(name):
    """Java UUID.nameUUIDFromBytes 等价物（离线账号 UUID）。"""
    md5 = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()
    b = bytearray(md5)
    b[6] = (b[6] & 0x0F) | 0x30  # version 3
    b[8] = (b[8] & 0x3F) | 0x80  # variant
    return str(uuidlib.UUID(bytes=bytes(b)))


def build():
    vpath = os.path.join(MC, "versions", VERSION, VERSION + ".json")
    vj = json.load(open(vpath, encoding="utf-8"))

    cp = []
    natives_dirs = []
    native_tmp = os.path.join(MC, "versions", VERSION, "natives-autolaunch")
    os.makedirs(native_tmp, exist_ok=True)

    for lib in vj.get("libraries", []):
        art = lib.get("downloads", {}).get("artifact")
        if art and art.get("path"):
            p = os.path.join(MC, "libraries", art["path"])
            if os.path.exists(p):
                cp.append(p)
        # natives
        classifier = lib.get("natives", {}).get("windows")
        if classifier:
            classifier = classifier.replace("${arch}", "64")
            nd = lib.get("downloads", {}).get("classifiers", {}).get(classifier)
            if nd and nd.get("path"):
                np = os.path.join(MC, "libraries", nd["path"])
                if os.path.exists(np):
                    with __import__("zipfile").ZipFile(np) as z:
                        for n in z.namelist():
                            if n.endswith(".dll"):
                                z.extract(n, native_tmp)
                    natives_dirs.append(native_tmp)

    client_jar = os.path.join(MC, "versions", VERSION, VERSION + ".jar")
    cp.append(client_jar)

    asset_index = vj.get("assetIndex", {}).get("id", "5")
    return cp, native_tmp, asset_index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="StreamCam")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", default="25565")
    ap.add_argument("--no-join", action="store_true")
    ap.add_argument("--memory", default="2G")
    args = ap.parse_args()

    cp, natives, asset_index = build()
    sep = ";"  # Windows classpath 分隔符
    cmd = [
        JAVA, f"-Xmx{args.memory}", "-Dfile.encoding=UTF-8",
        f"-Djava.library.path={natives}",
        "-cp", sep.join(cp),
        "net.minecraft.client.main.Main",
        "--username", args.name,
        "--version", VERSION,
        "--gameDir", MC,
        "--assetsDir", os.path.join(MC, "assets"),
        "--assetIndex", asset_index,
        "--uuid", offline_uuid(args.name),
        "--accessToken", "0",
        "--userType", "legacy",
        "--versionType", "release",
    ]
    if not args.no_join:
        # 1.20+ 用 quickPlay 才能自动进服（--server/--port 已失效）
        cmd += ["--quickPlayMultiplayer", f"{args.host}:{args.port}"]

    print("启动:", args.name, "(离线)")
    subprocess.Popen(cmd, cwd=MC)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
