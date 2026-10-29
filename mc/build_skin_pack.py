# -*- coding: utf-8 -*-
"""把 大肥鱼.png 做成资源包，替换默认 Steve/Alex 皮肤纹理（离线玩家用默认纹理）。"""
import hashlib
import json
import os
import shutil
import zipfile

SRC = r"E:\game\MC\皮肤\大肥鱼.png"
BUILD = r"E:\qq-bot\mc\cam_skin_pack"
OUT = r"E:\qq-bot\mc\http"

if os.path.isdir(BUILD):
    shutil.rmtree(BUILD)
for d in ("wide", "slim"):
    os.makedirs(os.path.join(BUILD, "assets", "minecraft", "textures", "entity", "player", d), exist_ok=True)
os.makedirs(OUT, exist_ok=True)

# 1.20.1 的默认皮肤纹理路径；离线玩家（无 Mojang 皮肤属性）会回落到这几个文件
for d in ("wide", "slim"):
    for f in ("steve.png", "alex.png"):
        shutil.copyfile(SRC, os.path.join(BUILD, "assets", "minecraft", "textures", "entity", "player", d, f))

meta = {"pack": {"pack_format": 15, "description": "DaFeiYu skin (offline player texture)"}}
with open(os.path.join(BUILD, "pack.mcmeta"), "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False)

zp = os.path.join(OUT, "dfy_skin.zip")
if os.path.exists(zp):
    os.remove(zp)
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(BUILD):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, BUILD).replace(os.sep, "/")
            z.write(full, rel)

sha1 = hashlib.sha1(open(zp, "rb").read()).hexdigest()
print("资源包:", zp, os.path.getsize(zp), "bytes")
print("sha1:", sha1)
with open(os.path.join(OUT, "sha1.txt"), "w") as f:
    f.write(sha1)
print("zip 内容:")
with zipfile.ZipFile(zp) as z:
    for n in z.namelist():
        print("  ", n)
