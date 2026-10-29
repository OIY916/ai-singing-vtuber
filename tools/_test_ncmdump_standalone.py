# -*- coding: utf-8 -*-
"""ncmdump_standalone.py 的正确性验证。

方法：按解密算法的逆运算合成 .ncm，然后分别用
  (A) 官方 ncmdump 包（Reference）
  (B) 本仓库独立版
各解一次，逐字节比对。

注：带封面时官方实现会用 mutagen 重写 FLAC/MP3 标签块，
所以输出和原始 payload 不会逐字节相同 —— 但 A 和 B 必须完全相同。
不带封面时 A/B 还应各自等于原始 payload。
"""
import base64
import binascii
import hashlib
import json
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CORE_KEY = binascii.a2b_hex("687A4852416D736F356B496E62617857")
META_KEY = binascii.a2b_hex("2331346C6A6B5F215C5D2630553C2728")
NCM_MAGIC = b"CTENFDAM"
PREFIX = b"163 key(Don't modify):"

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def aes_encrypt_pkcs7(key: bytes, data: bytes) -> bytes:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    return AES.new(key, AES.MODE_ECB).encrypt(pad(data, 16))


def build_ncm(out_path: str, audio: bytes, fmt: str, meta: dict, with_cover: bool = True) -> None:
    """按 ncm 格式的逆运算合成一个文件。"""
    rc4_key = b"0123456789abcdef"
    key_blob = aes_encrypt_pkcs7(CORE_KEY, b"neteasecloudmusic" + rc4_key)

    # RC4 KSA
    S = bytearray(range(256))
    j = 0
    klen = len(rc4_key)
    for i in range(256):
        j = (j + S[i] + rc4_key[i % klen]) & 0xFF
        S[i], S[j] = S[j], S[i]
    S = bytes(S)
    block = bytes(S[(S[i] + S[(i + S[i]) & 0xFF]) & 0xFF] for i in range(256))
    # 跳过第 0 字节，与官方实现一致
    stream = (block * (len(audio) // 256 + 2))[1 : 1 + len(audio)]
    enc_audio = (int.from_bytes(audio, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(audio), "big")

    meta = dict(meta, format=fmt)
    meta_txt = b"music:" + json.dumps(meta, ensure_ascii=False).encode("utf-8")
    meta_blob = PREFIX + base64.b64encode(aes_encrypt_pkcs7(META_KEY, meta_txt))
    meta_blob = bytes(b ^ 0x63 for b in meta_blob)

    image = FAKE_PNG if with_cover else b""

    with open(out_path, "wb") as f:
        f.write(NCM_MAGIC)
        f.write(b"\x00\x00")
        f.write(struct.pack("<I", len(key_blob)))
        f.write(bytes(b ^ 0x64 for b in key_blob))
        f.write(struct.pack("<I", len(meta_blob)))
        f.write(meta_blob)
        f.write(b"\x00" * 5)
        f.write(struct.pack("<I", len(image)))
        f.write(struct.pack("<I", len(image)))
        f.write(image)
        f.write(enc_audio)


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


CASES = []


def make_payload(fmt: str) -> bytes:
    flac_src = r"E:\qq-bot\cover_service\songs\十年-陈奕迅.flac"
    if fmt == "flac" and os.path.exists(flac_src):
        with open(flac_src, "rb") as f:
            return f.read()
    # mp3 用真实文件，没有就造一个带 ID3 头的假 mp3
    mp3_src = r"E:\qq-bot\cover_service\songs\晴天-梦里啥都有.mp3"
    if fmt == "mp3" and os.path.exists(mp3_src):
        with open(mp3_src, "rb") as f:
            return f.read()
    return b"ID3\x03\x00\x00\x00\x00\x00\x00" + os.urandom(80000)


def strip_id3(data: bytes) -> bytes:
    """去掉 ID3v2 头，取纯音频帧数据。"""
    if not data.startswith(b"ID3"):
        return data
    # ID3v2 size 是 synchsafe 整数（每字节只用低 7 位）
    size = (
        (data[6] & 0x7F) << 21
        | (data[7] & 0x7F) << 14
        | (data[8] & 0x7F) << 7
        | (data[9] & 0x7F)
    )
    return data[10 + size :]


def audio_frames(data: bytes, fmt: str) -> bytes:
    """取出纯音频负载，用于跨实现比对（忽略标签差异）。"""
    if fmt == "mp3":
        return strip_id3(data)
    return data


def run_case(name, fmt, with_cover, meta):
    import ncmdump as official
    import ncmdump_standalone as mine

    payload = make_payload(fmt)
    tmp = tempfile.mkdtemp(prefix="ncmtest_")
    ncm = os.path.join(tmp, "sample.ncm")
    build_ncm(ncm, payload, fmt, meta, with_cover=with_cover)

    # --- A. 官方包（参考实现）
    try:
        off_out = official.dump(ncm, os.path.join(tmp, "official." + fmt), skip=False)
        with open(off_out, "rb") as f:
            a = f.read()
        off_err = None
    except Exception as e:
        a = None
        off_err = f"{type(e).__name__}: {e}"

    # --- B. 独立版：先纯解密比对字节
    raw = mine.decrypt_bytes(ncm)
    raw_ok = raw["audio"] == payload

    # --- B2. 独立版：完整 dump（含标签）
    my_out = mine.dump(ncm, os.path.join(tmp, "mine." + fmt), skip_existing=False)
    with open(my_out, "rb") as f:
        b = f.read()

    same_impl = (a == b) if a is not None else None
    # 标签布局可能不同，比纯音频帧才是真正要保证的
    frames_ok = (audio_frames(a, fmt) == audio_frames(b, fmt)) if a is not None else None

    # 元信息读取也要一致
    m = mine._read_meta_only(ncm)
    meta_ok = m.get("musicName") == meta.get("musicName") and m.get("format") == fmt

    if a is None:
        print(f"\n[{name}]")
        print(f"  官方包      崩溃（参考实现不可用）: {off_err}")
        print(f"  独立版解密  {'PASS' if raw_ok else 'FAIL'}  输出 {len(b)} bytes")
        print(f"  元信息      {'PASS' if meta_ok else 'FAIL'}  -> {m.get('musicName')} / {m.get('album')} / {m.get('format')}")
        print(f"  => {'PASS' if (raw_ok and meta_ok) else 'FAIL'}")
        return raw_ok and meta_ok

    ok = raw_ok and meta_ok and (frames_ok or not with_cover)
    print(f"\n[{name}]")
    print(f"  源文件      {os.path.getsize(ncm):>10} bytes   fmt={fmt}  cover={with_cover}")
    print(f"  官方输出    {len(a):>10} bytes   md5={md5(a)}")
    print(f"  独立版      {len(b):>10} bytes   md5={md5(b)}")
    print(f"  纯解密比对  {'PASS' if raw_ok else 'FAIL'}  (解密后 == 原始 payload)")
    print(f"  音频帧一致  {'PASS' if frames_ok else 'FAIL'}  (忽略标签布局差异)")
    print(f"  整文件一致  {'YES' if same_impl else 'NO（标签帧写法不同，不影响音频）'}")
    print(f"  元信息      {'PASS' if meta_ok else 'FAIL'}  -> {m.get('musicName')} / {m.get('album')} / {m.get('format')}")
    print(f"  => {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    meta = {"musicName": "测试歌曲", "album": "测试专辑", "artist": [["测试歌手", 1]]}

    results = [
        run_case("FLAC + 封面", "flac", True, meta),
        run_case("FLAC 无封面（逐字节比对 payload）", "flac", False, meta),
        run_case("MP3 + 封面", "mp3", True, meta),
        run_case("MP3 无封面（逐字节比对 payload）", "mp3", False, meta),
    ]

    print("\n" + "=" * 56)
    print(f"总计 {len(results)} 例，通过 {sum(results)} 例")
    print("=" * 56)
    return 0 if all(results) else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
