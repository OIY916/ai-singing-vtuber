# -*- coding: utf-8 -*-
"""把皮肤资源包写进 server.properties（进服自动下发）。"""
import re

SHA = open(r"E:\qq-bot\mc\http\sha1.txt", encoding="utf-8").read().strip()
P = r"E:\qq-bot\mc\server\server.properties"

KV = {
    "resource-pack": "http://127.0.0.1:8088/dfy_skin.zip",
    "resource-pack-sha1": SHA,
    "require-resource-pack": "false",
}

s = open(P, encoding="utf-8").read()


def setkv(text, k, v):
    if re.search(rf"^{re.escape(k)}=.*$", text, re.M):
        return re.sub(rf"^{re.escape(k)}=.*$", f"{k}={v}".replace("\\", "\\\\"), text, flags=re.M)
    return text.rstrip("\n") + f"\n{k}={v}\n"


for k, v in KV.items():
    s = setkv(s, k, v)

with open(P, "w", encoding="utf-8") as f:
    f.write(s)

print("resource-pack:", KV["resource-pack"])
print("sha1:", SHA)
print("require-resource-pack:", KV["require-resource-pack"])
