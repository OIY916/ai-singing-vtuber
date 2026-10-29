"""把整理后的每个模型文件夹单独压缩成一个 zip

用户需求：每个文件夹一个压缩包，名称为「模型名+pth和index双全」

输入：E:\\game\\rvc模型\\整理后\\<模型名>\\  （pth + index 两个文件）
输出：E:\\game\\rvc模型\\压缩包\\<模型名>+pth和index双全.zip

用 7-Zip（本机 C:\\Program Files\\7-Zip\\7z.exe）做存储压缩（-mx0 或 -mx1）：
模型 .pth / .index 本身是二进制，压缩率极低，用 -mx1 快速模式省时间。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

SRC = r"E:\game\rvc模型\整理后"
OUT = r"E:\game\rvc模型\压缩包"
SEVEN_ZIP = r"C:\Program Files\7-Zip\7z.exe"


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def main() -> int:
    if not os.path.isdir(SRC):
        log(f"源目录不存在: {SRC}")
        return 1
    if not os.path.exists(SEVEN_ZIP):
        log(f"找不到 7z: {SEVEN_ZIP}")
        return 1

    os.makedirs(OUT, exist_ok=True)

    folders = sorted(d for d in os.listdir(SRC) if os.path.isdir(os.path.join(SRC, d)))
    log(f"找到 {len(folders)} 个文件夹")

    ok = 0
    fail = 0
    t_all = time.time()

    for i, folder in enumerate(folders, 1):
        src_dir = os.path.join(SRC, folder)
        # 压缩包名 = 模型名 + pth和index双全
        zip_name = f"{folder}+pth和index双全.zip"
        zip_path = os.path.join(OUT, zip_name)

        # 校验：文件夹里必须同时有 pth 和 index
        files = os.listdir(src_dir)
        has_pth = any(f.lower().endswith(".pth") for f in files)
        has_idx = any(f.lower().endswith(".index") for f in files)
        if not (has_pth and has_idx):
            log(f"[{i:2d}/{len(folders)}] {folder:24s} 跳过（缺 pth 或 index）")
            continue

        if os.path.exists(zip_path):
            log(f"[{i:2d}/{len(folders)}] {folder:24s} 已存在，跳过")
            ok += 1
            continue

        log(f"[{i:2d}/{len(folders)}] {folder:24s} 压缩中……")
        t0 = time.time()
        try:
            # -tzip 格式（Windows 通用）；-mx1 快速压缩
            r = subprocess.run(
                [SEVEN_ZIP, "a", "-tzip", "-mx1", "-y", zip_path,
                 os.path.join(src_dir, "*")],
                capture_output=True, timeout=1800,
            )
            if r.returncode != 0:
                log(f"      失败 rc={r.returncode}: "
                    f"{(r.stderr or b'').decode('utf-8', 'replace')[:200]}")
                fail += 1
                continue
            size_mb = os.path.getsize(zip_path) / 1024 / 1024
            log(f"      ✅ {zip_name}  ({size_mb:.0f} MB, {time.time()-t0:.1f}s)")
            ok += 1
        except Exception as e:  # noqa: BLE001
            log(f"      异常: {e}")
            fail += 1

    log("")
    log("=" * 60)
    log(f"完成 {ok} 个，失败 {fail} 个，总耗时 {time.time()-t_all:.1f}s")
    log(f"输出目录: {OUT}")

    # 列清单
    zips = sorted(f for f in os.listdir(OUT) if f.endswith(".zip"))
    total = sum(os.path.getsize(os.path.join(OUT, z)) for z in zips)
    log(f"压缩包数: {len(zips)}，总计 {total/1024/1024/1024:.2f} GB")
    for z in zips:
        sz = os.path.getsize(os.path.join(OUT, z)) / 1024 / 1024
        log(f"  {sz:6.0f} MB  {z}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
