"""把 RVC 模型和它的 index 归到单独文件夹

用户需求：每个模型一个文件夹，比如「青橘」文件夹里放
    青橘40k.pth + 青橘.index

源目录：E:\\game\\rvc模型\\106nvsheng
    56 个 .pth
    15个index/ 子目录 → 15 个 .index（命名不规范，需手工映射）

输出：E:\\game\\rvc模型\\整理后\\<模型名>\\
    模型名取 .pth 去掉扩展名
"""

from __future__ import annotations

import os
import re
import shutil

SRC = r"E:\game\rvc模型\106nvsheng"
SRC_IDX = os.path.join(SRC, "15个index")
OUT = r"E:\game\rvc模型\整理后"

# 手工映射：index 文件名 -> 对应模型文件名
# 依据 2026-10-26 实际文件名核对
INDEX_TO_MODEL = {
    "added_IVF256_Flat_nprobe_1_shaoluosusui_v2.index": "shaoluosusu.pth",
    "feifei.index": "feifei48K.pth",
    "nikou48K.index": "nikou48K1999.pth",
    "suannai.index": "suannai40k.pth",
    "xiaoju.index": "xiaojuDLC.pth",
    "yujie.index": "quan_yujie.pth",
    "七妹.index": "七妹1000模型.pth",
    "多多added_IVF3756_Flat_nprobe_1_duoduo_v2.index": "御姐多多.pth",
    "月亮.index": "月亮.pth",
    "熙然.index": "熙然6666666.pth",
    "莉莉added_IVF256_Flat_nprobe_1_lili_v2.index": "少御莉莉.pth",
    "雾雾05.index": "雾雾の少御音05(1).pth",
    "雾雾06.index": "雾雾の少萝音06(1).pth",
    "雾雾40K.index": "雾雾40k.pth",
    "青橘.index": "青橘40k.pth",
}


def main() -> int:
    if not os.path.isdir(SRC):
        print(f"源目录不存在: {SRC}")
        return 1

    pths = sorted(f for f in os.listdir(SRC) if f.lower().endswith(".pth"))
    print(f"找到 {len(pths)} 个模型")

    # 建 模型名 -> index 路径 的反查表
    model_to_index: dict[str, str] = {}
    for idx_name, model_name in INDEX_TO_MODEL.items():
        p = os.path.join(SRC_IDX, idx_name)
        if os.path.exists(p):
            model_to_index[model_name] = p
        else:
            print(f"  [警告] index 不存在: {idx_name}")

    print(f"已映射 {len(model_to_index)} 个 index")

    os.makedirs(OUT, exist_ok=True)
    done = 0
    with_index = 0
    errors = []

    for pth in pths:
        name = os.path.splitext(pth)[0]
        # 文件夹名清理：去掉非法字符
        folder_name = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
        folder = os.path.join(OUT, folder_name)

        try:
            os.makedirs(folder, exist_ok=True)
            # 复制模型
            shutil.copy2(os.path.join(SRC, pth), os.path.join(folder, pth))
            # 复制 index（如有），重命名为跟模型同名，便于识别
            idx_src = model_to_index.get(pth)
            if idx_src:
                # index 保留原名（RVC 靠路径，不靠名字，改名也无所谓，
                # 但保留原名更利于溯源）
                idx_basename = os.path.basename(idx_src)
                shutil.copy2(idx_src, os.path.join(folder, idx_basename))
                with_index += 1
            done += 1
        except Exception as e:
            errors.append(f"{pth}: {e}")

    print(f"\n完成 {done} 个文件夹，其中 {with_index} 个带 index")
    if errors:
        print(f"失败 {len(errors)} 个:")
        for e in errors[:10]:
            print("  " + e)

    # 列出结果
    print(f"\n输出目录: {OUT}")
    print("目录清单:")
    for d in sorted(os.listdir(OUT)):
        files = os.listdir(os.path.join(OUT, d))
        mark = "  [含index]" if any(f.endswith(".index") for f in files) else ""
        print(f"  {d}/{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
