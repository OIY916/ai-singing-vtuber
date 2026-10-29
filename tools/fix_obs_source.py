# -*- coding: utf-8 -*-
"""把 OBS 的「皮套」浏览器源改成 1920×1080（全画布）。

为什么必须改：
    原来浏览器源是 1080×1080 的方形，新界面是按 1920×1080 设计的，
    方形源会把左右两栏切掉（页面自身会等比缩放，但只剩中间一条）。
    OBS 的画布是 1920×1080，源也应该是 1920×1080 才能铺满。

用法：
    python tools\\fix_obs_source.py
"""
import json
import sys

import obsws_python as obsws

sys.stdout.reconfigure(encoding="utf-8")

HOST, PORT, PWD = "127.0.0.1", 4455, "ezz664hcTWsXaAjH"
SRC_NAME_KW = "皮套"
W, H = 1920, 1080


def main():
    cli = obsws.ReqClient(host=HOST, port=PORT, password=PWD, timeout=5)
    scene = cli.get_current_program_scene().current_program_scene_name
    print("当前场景:", scene)

    items = cli.get_scene_item_list(scene).scene_items
    target = None
    for it in items:
        print(f"  [{it['sceneItemId']}] {it['sourceName']} (enabled={it['sceneItemEnabled']})")
        if SRC_NAME_KW in it["sourceName"]:
            target = it
    if not target:
        sys.exit(f"没找到包含「{SRC_NAME_KW}」的源")

    sid = target["sceneItemId"]
    print(f"\n目标源: {target['sourceName']} (id={sid})")

    # 1) 浏览器源自身的宽高（决定网页的渲染视口）
    st = cli.get_input_settings(target["sourceName"]).input_settings
    print("  改前:", {k: st.get(k) for k in ("url", "width", "height")})
    st["width"] = W
    st["height"] = H
    cli.set_input_settings(target["sourceName"], st, overlay=True)
    print(f"  已设为 {W}×{H}")

    # 2) 场景里这个源的变换（位置/缩放），铺满画布
    tr = cli.get_scene_item_transform(scene, sid).scene_item_transform
    print("  改前 transform:", tr)
    cli.set_scene_item_transform(scene, sid, {
        "positionX": 0.0, "positionY": 0.0,
        "rotation": 0.0,
        "scaleX": 1.0, "scaleY": 1.0,
        "alignment": 5,          # 左上角对齐
        "boundsType": "OBS_BOUNDS_NONE",
    })
    print("  已铺满画布 (0,0) 1:1")

    # 读回来确认
    st2 = cli.get_input_settings(target["sourceName"]).input_settings
    tr2 = cli.get_scene_item_transform(scene, sid).scene_item_transform
    print("\n改后 settings:", {k: st2.get(k) for k in ("url", "width", "height")})
    print("改后 transform:", {k: tr2.get(k) for k in
                             ("positionX", "positionY", "scaleX", "scaleY")})


if __name__ == "__main__":
    main()
