# -*- coding: utf-8 -*-
"""
ncmdump 独立版 —— 网易云音乐 .ncm 加密文件解密器（单文件，无需装 ncmdump 包）

用法：
    # 1) 解单个文件
    python ncmdump_standalone.py "E:\\CloudMusic\\晴天.ncm"

    # 2) 解整个文件夹（自动递归找 .ncm）
    python ncmdump_standalone.py "E:\\CloudMusic"

    # 3) 指定输出目录 + 自定义文件名
    python ncmdump_standalone.py music.ncm -o "D:\\转好的" -f "%artist% - %title%"

    # 4) 解完删除源 .ncm
    python ncmdump_standalone.py "E:\\CloudMusic" -d

    # 5) 覆盖同名文件（默认跳过已存在的）
    python ncmdump_standalone.py music.ncm -c

依赖（二选一，装一个就行）：
    pip install pycryptodome    # 推荐
    pip install cryptography    # 备选
可选：
    pip install mutagen         # 想保留封面和歌名/歌手标签就装，不装也能解出音频

格式说明：
    .ncm 是网易云客户端的加密缓存格式。
    文件头 8 字节 magic = 43 54 45 4E 46 44 41 4D ('CTENFDAM')
    内部 = AES-ECB 保护的 RC4 密钥 + AES-ECB 保护的 JSON 元信息 + XOR 加密的音频流
    解出来的音频是原始 flac 或 mp3，无损音质就是原始 flac，不会二次压缩。

原理参考：https://github.com/nondanee/ncmdump （MIT）
本文件是独立重写版，去掉了对同名 pip 包的依赖，方便分发。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import struct
import sys

# ---------------------------------------------------------------- 常量

NCM_MAGIC = b"CTENFDAM"

# 保护 RC4 密钥的 AES 密钥（固定值，所有 ncm 通用）
CORE_KEY = binascii.a2b_hex("687A4852416D736F356B496E62617857")
# 保护元信息 JSON 的 AES 密钥
META_KEY = binascii.a2b_hex("2331346C6A6B5F215C5D2630553C2728")

# 元信息前缀：`163 key(Don't modify):` 共 22 字节，后面接 base64
META_PREFIX_LEN = 22

# 老版本 ncm 可能没有元信息，靠文件大小猜格式
FLAC_GUESS_THRESHOLD = 1024 * 1024 * 16  # 16 MB


# ---------------------------------------------------------------- AES 后端

class _AesBackend:
    """AES-128-ECB 解密 + PKCS7 去填充，自动在 pycryptodome / cryptography 间切换。"""

    def __init__(self):
        self.kind = None
        try:
            from Crypto.Cipher import AES  # noqa: F401
            from Crypto.Util.Padding import unpad  # noqa: F401

            self.kind = "pycryptodome"
        except ImportError:
            try:
                from cryptography.hazmat.primitives.ciphers import Cipher  # noqa: F401

                self.kind = "cryptography"
            except ImportError:
                raise RuntimeError(
                    "缺少 AES 库。请先装一个：\n"
                    "    pip install pycryptodome\n"
                    "  或\n"
                    "    pip install cryptography"
                )

    def decrypt_ecb_unpad(self, key: bytes, data: bytes) -> bytes:
        """AES-128-ECB 解密后去掉 PKCS7 填充。"""
        if len(data) % 16 != 0:
            raise ValueError(f"密文长度不是 16 的倍数({len(data)})，文件可能已损坏")

        if self.kind == "pycryptodome":
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad

            raw = AES.new(key, AES.MODE_ECB).decrypt(data)
            return unpad(raw, 16)

        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        raw = dec.update(data) + dec.finalize()
        # 手动去 PKCS7
        if not raw:
            raise ValueError("解密结果为空")
        pad = raw[-1]
        if pad < 1 or pad > 16 or raw[-pad:] != bytes([pad]) * pad:
            return raw  # 填充不标准就原样返回，交给调用方处理
        return raw[:-pad]


_AES = None


def _aes() -> _AesBackend:
    global _AES
    if _AES is None:
        _AES = _AesBackend()
    return _AES


# ---------------------------------------------------------------- 核心解密

def _build_rc4_state(key: bytes) -> bytes:
    """标准 RC4 KSA，生成 256 字节的置换表 S。"""
    S = bytearray(range(256))
    j = 0
    klen = len(key)
    for i in range(256):
        j = (j + S[i] + key[i % klen]) & 0xFF
        S[i], S[j] = S[j], S[i]
    return bytes(S)


def _build_keystream(S: bytes, length: int) -> bytes:
    """由 KSA 结果生成密钥流。

    ⚠️ 关键细节：真实 ncm 的密钥流跳过第 0 个字节，从索引 1 开始。
    （等价于 RC4 PRGA 的 drop-N 惯例，网易这里 drop=1）
    写成 [1:1+length] 才和官方实现对得上；写成 [:length] 会整首歌错位一字节，
    解出来就是纯噪声。
    """
    block = bytes(S[(S[i] + S[(i + S[i]) & 0xFF]) & 0xFF] for i in range(256))
    reps = length // 256 + 1
    return (block * reps)[1:1 + length]


def _xor(data: bytes, stream: bytes) -> bytes:
    """等长异或。用 int.from_bytes 走大整数异或，比逐字节快很多。"""
    n = len(data)
    a = int.from_bytes(data, "big")
    b = int.from_bytes(stream[:n], "big")
    return (a ^ b).to_bytes(n, "big")


def decrypt_bytes(input_path: str) -> dict:
    """只解密，不写文件、不碰标签。方便做逐字节校验。

    @returns {audio, format, meta, identifier, image}
    """
    with open(input_path, "rb") as f:
        header = f.read(8)
        if header != NCM_MAGIC:
            raise ValueError(
                f"不是 ncm 文件（magic={binascii.b2a_hex(header)!r}，"
                f"应为 {binascii.b2a_hex(NCM_MAGIC)!r}）"
            )

        f.seek(2, 1)  # 跳过 2 字节 gap

        # --- 1. 读出被 AES 保护的 RC4 密钥
        (key_len,) = struct.unpack("<I", f.read(4))
        key_data = bytes(b ^ 0x64 for b in f.read(key_len))
        key_data = _aes().decrypt_ecb_unpad(CORE_KEY, key_data)[17:]  # 前 17 字节是 "neteasecloudmusic"
        if not key_data:
            raise ValueError("RC4 密钥解析失败")
        S = _build_rc4_state(key_data)

        # --- 2. 读出被 AES 保护的元信息 JSON
        (meta_len,) = struct.unpack("<I", f.read(4))
        if meta_len:
            meta_raw = bytes(b ^ 0x63 for b in f.read(meta_len))
            identifier = meta_raw.decode("utf-8", errors="replace")
            payload = base64.b64decode(meta_raw[META_PREFIX_LEN:])
            meta_txt = _aes().decrypt_ecb_unpad(META_KEY, payload).decode("utf-8", errors="replace")
            meta = json.loads(meta_txt[6:])  # 前 6 字节是 "music:"
        else:
            identifier = None
            fmt = "flac" if os.path.getsize(input_path) > FLAC_GUESS_THRESHOLD else "mp3"
            meta = {"format": fmt, "musicName": None, "album": None, "artist": None}

        f.seek(5, 1)  # 跳过 5 字节 gap

        # --- 3. 封面
        (image_space,) = struct.unpack("<I", f.read(4))
        (image_size,) = struct.unpack("<I", f.read(4))
        image_data = f.read(image_size) if image_size else None
        f.seek(image_space - image_size, 1)

        # --- 4. 剩余全部是加密音频流
        encrypted = f.read()

    audio = _xor(encrypted, _build_keystream(S, len(encrypted)))
    return {
        "audio": audio,
        "format": meta.get("format") or "mp3",
        "meta": meta,
        "identifier": identifier,
        "image": image_data,
    }


def dump(input_path: str, output_path: str | None = None, skip_existing: bool = True) -> str | None:
    """解密单个 .ncm 文件。

    @param input_path      源 .ncm 路径
    @param output_path     目标路径；None 则自动用 歌名.格式 命名
    @param skip_existing  目标已存在时跳过，返回 None
    @returns               实际写出的文件路径
    """
    info = decrypt_bytes(input_path)
    audio = info["audio"]
    fmt = info["format"]
    meta = info["meta"]
    identifier = info["identifier"]
    image_data = info["image"]

    fmt = meta.get("format") or "mp3"
    out_path = output_path or _default_out_path(input_path, meta)

    if skip_existing and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return None

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "wb") as out:
        out.write(audio)

    # --- 5. 写回封面 + 标签（mutagen 可选，失败不影响音频）
    _embed_tags(out_path, fmt, meta, identifier, image_data)
    return out_path


def _default_out_path(input_path: str, meta: dict) -> str:
    """默认输出名：同目录下 歌名-歌手.flac / .mp3"""
    fmt = meta.get("format") or "mp3"
    name = meta.get("musicName")
    artists = meta.get("artist") or []
    if name and artists:
        try:
            artist = "/".join(a[0] if isinstance(a, (list, tuple)) else str(a) for a in artists)
        except Exception:
            artist = ""
        stem = f"{name}-{artist}" if artist else name
    else:
        stem = os.path.splitext(os.path.basename(input_path))[0]

    stem = "".join(c for c in stem if c not in '\\/:*?"<>|').strip() or "output"
    return os.path.join(os.path.dirname(input_path), f"{stem}.{fmt}")


def _embed_tags(path: str, fmt: str, meta: dict, identifier, image_data) -> None:
    """给解出的音频写封面和文字标签。mutagen 缺失或文件不支持就直接放弃。

    ⚠️ 不要用 ID3.RegisterTextKey —— mutagen ≥1.46 已删除这个方法，
    官方 ncmdump 的 MP3 分支因此在现代 mutagen 上直接抛 AttributeError。
    这里改成显式构造标准帧（TIT2/TPE1/TALB/COMM/APIC），跨版本都能用。
    """
    try:
        from mutagen import flac, id3
    except ImportError:
        print("    (未装 mutagen，跳过封面/标签写入；音频本身已可用)")
        return

    mime = "image/png" if image_data and image_data[:4] == b"\x89PNG" else "image/jpeg"
    artists = meta.get("artist") or []

    def _artist_str() -> str | None:
        if not artists:
            return None
        try:
            return "/".join(a[0] if isinstance(a, (list, tuple)) else str(a) for a in artists)
        except Exception:
            return None

    try:
        if fmt == "flac":
            audio = flac.FLAC(path)
            if image_data:
                pic = flac.Picture()
                pic.encoding = 0
                pic.type = 3  # Cover (front)
                pic.mime = mime
                pic.data = image_data
                audio.clear_pictures()
                audio.add_picture(pic)
            if identifier:
                audio["description"] = identifier
            if meta.get("musicName"):
                audio["title"] = meta["musicName"]
            if meta.get("album"):
                audio["album"] = meta["album"]
            artist = _artist_str()
            if artist:
                audio["artist"] = artist
            audio.save()
            return

        # ---- MP3：显式构造 ID3 帧
        from mutagen.id3 import ID3, APIC, COMM, TALB, TIT2, TPE1

        try:
            tags = ID3(path)
        except Exception:
            tags = ID3()

        if image_data:
            tags.delall("APIC")
            tags.add(
                APIC(
                    encoding=0,
                    mime=mime,
                    type=3,  # Cover (front)
                    desc="",
                    data=image_data,
                )
            )
        if identifier:
            tags.delall("COMM")
            tags.add(COMM(encoding=3, lang="eng", desc="", text=[identifier]))
        if meta.get("musicName"):
            tags.add(TIT2(encoding=3, text=[meta["musicName"]]))
        if meta.get("album"):
            tags.add(TALB(encoding=3, text=[meta["album"]]))
        artist = _artist_str()
        if artist:
            tags.add(TPE1(encoding=3, text=[artist]))

        tags.save(path, v2_version=4)
    except Exception as e:
        print(f"    (写标签失败，音频已正常解出：{e})")


# ---------------------------------------------------------------- 批量

def find_ncm(target: str) -> list[str]:
    """路径或目录 → .ncm 文件列表（目录递归）"""
    target = os.path.abspath(target)
    if os.path.isfile(target):
        return [target] if target.lower().endswith(".ncm") else []
    out = []
    for root, _dirs, files in os.walk(target):
        for name in files:
            if name.lower().endswith(".ncm"):
                out.append(os.path.join(root, name))
    return sorted(out)


def name_from_format(fmt: str, meta: dict) -> str | None:
    """按 %artist% - %title% 这类模板生成文件名（不含扩展名）"""
    if not fmt:
        return None
    info = {
        "artist": (
            ",".join(a[0] if isinstance(a, (list, tuple)) else str(a) for a in meta["artist"])
            if meta.get("artist")
            else None
        ),
        "title": meta.get("musicName"),
        "album": meta.get("album"),
    }
    out = fmt
    for k, v in info.items():
        out = out.replace(f"%{k}%", v or "")
    return "".join(c for c in out if c not in '\\/:*?"<>|').strip() or None


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ncmdump_standalone",
        description="网易云 .ncm 解密器（单文件独立版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例子：\n"
            "  python ncmdump_standalone.py D:\\CloudMusic\n"
            "  python ncmdump_standalone.py a.ncm -o D:\\out -f \"%artist% - %title%\"\n"
            "  python ncmdump_standalone.py D:\\CloudMusic -d -c\n"
        ),
    )
    parser.add_argument("input", nargs="+", help="ncm 文件或文件夹")
    parser.add_argument("-o", "--output", default=None, help="输出目录（默认与源文件同目录）")
    parser.add_argument(
        "-f", "--format", default="",
        # ⚠️ argparse 的 help 文本会走 % 格式化，这里的 %artist% 必须写成 %%artist%%，
        # 否则 `-h` 会直接崩：TypeError: not enough arguments for format string
        help='文件名模板，支持 %%artist%% %%title%% %%album%%，如 "%%artist%% - %%title%%"',
    )
    parser.add_argument("-d", "--delete", action="store_true", help="转换成功后删除源 .ncm")
    parser.add_argument("-c", "--cover", action="store_true", help="覆盖已存在的同名输出文件")
    parser.add_argument("-q", "--quiet", action="store_true", help="只打印结果，不打印细节")
    args = parser.parse_args(argv)

    files: list[str] = []
    for t in args.input:
        got = find_ncm(t)
        if not got and not args.quiet:
            print(f"[跳过] 没有找到 .ncm：{t}")
        files.extend(got)
    files = sorted(set(files))

    if not files:
        print("没有可处理的 .ncm 文件")
        return 1

    if args.output:
        os.makedirs(args.output, exist_ok=True)

    total = len(files)
    ok = skipped = failed = 0

    for idx, src in enumerate(files, 1):
        if not args.quiet:
            print(f"[{idx}/{total}] {os.path.basename(src)}")

        out_path = None
        try:
            out_path = _resolve_output(src, args)
            got = dump(src, out_path, skip_existing=not args.cover)

            if got is None:
                skipped += 1
                if not args.quiet:
                    print(f"    已存在，跳过：{out_path}")
                continue

            ok += 1
            if not args.quiet:
                size_mb = os.path.getsize(got) / 1024 / 1024
                print(f"    -> {os.path.basename(got)}  ({size_mb:.1f} MB)")

            if args.delete:
                try:
                    os.remove(src)
                except OSError as e:
                    print(f"    (删除源文件失败：{e})")

        except Exception as e:
            failed += 1
            print(f"    !! 失败：{e}")

    print()
    print(f"完成：成功 {ok} / 跳过 {skipped} / 失败 {failed}（共 {total}）")
    return 0 if failed == 0 else 2


def _resolve_output(src: str, args) -> str | None:
    """决定输出路径。要支持 -o / -f，必须先把元信息读出来拼名字。"""
    if not args.output and not args.format:
        return None  # 交给 dump 用默认命名（同目录 歌名-歌手.格式）

    meta = _read_meta_only(src)
    stem = name_from_format(args.format, meta) if args.format else None
    if stem is None:
        stem = _default_stem(src, meta)
    fmt = meta.get("format") or "mp3"
    folder = args.output or os.path.dirname(os.path.abspath(src))
    return os.path.join(folder, f"{stem}.{fmt}")


def _default_stem(src: str, meta: dict) -> str:
    name = meta.get("musicName")
    artists = meta.get("artist") or []
    if name and artists:
        artist = "/".join(a[0] if isinstance(a, (list, tuple)) else str(a) for a in artists)
        stem = f"{name}-{artist}"
    else:
        stem = os.path.splitext(os.path.basename(src))[0]
    return "".join(c for c in stem if c not in '\\/:*?"<>|').strip() or "output"


def _read_meta_only(src: str) -> dict:
    """只读元信息，不写音频（给 -o/-f 拼文件名用）。"""
    with open(src, "rb") as f:
        if f.read(8) != NCM_MAGIC:
            raise ValueError("不是 ncm 文件")
        f.seek(2, 1)
        (key_len,) = struct.unpack("<I", f.read(4))
        f.read(key_len)
        (meta_len,) = struct.unpack("<I", f.read(4))
        if not meta_len:
            fmt = "flac" if os.path.getsize(src) > FLAC_GUESS_THRESHOLD else "mp3"
            return {"format": fmt}
        meta_raw = bytes(b ^ 0x63 for b in f.read(meta_len))
        payload = base64.b64decode(meta_raw[META_PREFIX_LEN:])
    txt = _aes().decrypt_ecb_unpad(META_KEY, payload).decode("utf-8", errors="replace")
    return json.loads(txt[6:])


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
