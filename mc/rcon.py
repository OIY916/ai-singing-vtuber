# -*- coding: utf-8 -*-
"""极简 RCON 客户端：给 MC 服务端发指令。

用法：
    python mc/rcon.py "time set day"
    python mc/rcon.py "weather clear" "gamerule doDaylightCycle true"

后面主服务 main.py 也能 import 它（阶段3：大肥鱼自己切时间/天气/传送）。
"""
import socket
import struct
import sys

HOST = "127.0.0.1"
PORT = 25575
PASSWORD = "fishcam"


def _pack(req_id, ptype, payload):
    body = struct.pack("<ii", req_id, ptype) + payload.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(body)) + body


def _recv(sock):
    raw = sock.recv(4)
    if len(raw) < 4:
        return None
    (length,) = struct.unpack("<i", raw)
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            break
        data += chunk
    if len(data) < 8:
        return None
    req_id, ptype = struct.unpack("<ii", data[:8])
    return req_id, ptype, data[8:-2].decode("utf-8", "replace")


def run(cmds, host=HOST, port=PORT, password=PASSWORD):
    out = []
    with socket.create_connection((host, port), timeout=5) as s:
        s.sendall(_pack(1, 3, password))
        r = _recv(s)
        if not r or r[0] == -1:
            raise RuntimeError("RCON 密码错误")
        rid = 2
        for cmd in cmds:
            s.sendall(_pack(rid, 2, cmd))
            r = _recv(s)
            out.append((cmd, r[2] if r else ""))
            rid += 1
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmds = sys.argv[1:] or ["list"]
    for cmd, resp in run(cmds):
        print(f"> {cmd}")
        print(resp.strip() or "(ok)")
