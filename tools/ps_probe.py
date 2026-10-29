# -*- coding: utf-8 -*-
"""Photoshop COM 探针 v2：逐个试 API，找出哪些真的能用。

结论会打印成一份能力清单。
"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import win32com.client

psPIXELS = 1
psREPLACE = 1
psNORMAL = 1
psCONTENT_AWARE = 9


def try_(name, fn):
    try:
        r = fn()
        print(f"  [OK]   {name}" + (f"  -> {r}" if r is not None else ""))
        return True
    except Exception as e:
        msg = str(e)
        # 缩短 COM 错误信息
        if "com_error" in type(e).__name__:
            try:
                msg = e.args[2][2] if len(e.args) > 2 and e.args[2] else msg
            except Exception:
                pass
        print(f"  [FAIL] {name}  -> {msg[:100]}")
        return False


def main():
    ps = win32com.client.Dispatch("Photoshop.Application")
    ps.Visible = True
    ps.Preferences.RulerUnits = psPIXELS
    print(f"Photoshop {ps.Version}  单位=像素\n")

    doc = ps.Open(r"E:\qq-bot\avatar\front_cut.png")
    print(f"文档: {doc.Width:.0f} x {doc.Height:.0f}\n")

    print("=== 选择/填充能力 ===")
    # 方式1：直接传矩形数组
    try_("Selection.Select(矩形数组)", lambda: doc.Selection.Select(([186, 302], [220, 336])))

    # 方式2：用 Descriptor（PS 2022 推荐）
    def select_via_action():
        # 先做一个矩形选框技巧：用 document.Selection.Select 的另一种签名
        d = win32com.client.Dispatch("Photoshop.ActionDescriptor")
        r = win32com.client.Dispatch("Photoshop.ActionDescriptor")
        r.PutUnitDouble(0, 0, 186.0)  # placeholder — 下面用较稳的方式
        return None

    # 方式3：SelectAll / 反选 这类简单操作
    try_("Selection.SelectAll", lambda: doc.Selection.SelectAll())
    try_("Selection.Deselect", lambda: doc.Selection.Deselect())

    print("\n=== 图层能力（分层的关键）===")
    ln = doc.Layers
    try_("读取图层数", lambda: ln.Count)

    def add_layer():
        L = ln.Add()
        L.Name = "测试层"
        return L.Name

    try_("新建图层", add_layer)

    def layer_kind():
        L = ln.Add()
        return L.Kind

    try_("读图层 Kind", layer_kind)

    print("\n=== 图像操作 ===")
    try_("翻转画布", lambda: doc.FlipCanvas(1))  # 1=horizontal
    try_("再翻回来", lambda: doc.FlipCanvas(1))

    print("\n=== 智能对象/置入 ===")
    try_("ActiveLayer", lambda: doc.ActiveLayer.Name)

    print("\n=== 导出能力 ===")
    opts = win32com.client.Dispatch("Photoshop.PNGSaveOptions")
    out = r"E:\qq-bot\avatar\_ps_probe_out.png"

    def save():
        doc.SaveAs(out, opts, True)
        return out

    try_("PNG 导出", save)

    doc.Close(1)
    print("\n探测完成")


if __name__ == "__main__":
    main()
