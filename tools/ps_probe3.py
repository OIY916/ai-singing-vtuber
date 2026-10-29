# -*- coding: utf-8 -*-
"""Photoshop COM 探针 v3：用 gencache 早期绑定，拿到正确的方法签名。

动态绑定（Dispatch）很多方法签名对不上；
gencache.EnsureDispatch 会读类型库生成 Python 包装，参数类型才对。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import win32com.client
import win32com.client.gencache


def try_(name, fn):
    try:
        r = fn()
        print(f"  [OK]   {name}" + (f"  -> {r}" if r is not None else ""))
        return True
    except Exception as e:
        msg = str(e)
        try:
            if e.args and isinstance(e.args[0], tuple) and len(e.args[0]) > 2:
                msg = e.args[0][2][2] if e.args[0][2] else msg
        except Exception:
            pass
        print(f"  [FAIL] {name}  -> {msg[:120].replace(chr(10),' ')}")
        return False


def main():
    print("尝试早期绑定 …")
    try:
        ps = win32com.client.gencache.EnsureDispatch("Photoshop.Application")
        print(f"  ✅ 早期绑定成功: Photoshop {ps.Version}")
        early = True
    except Exception as e:
        print(f"  ❌ 早期绑定失败: {e}")
        ps = win32com.client.Dispatch("Photoshop.Application")
        early = False

    ps.Visible = True

    # 用生成的常量模块
    try:
        from win32com.client import gencache as gc

        mod = gc.GetModuleForProgID("Photoshop.Application")
        has_const = mod is not None
        print(f"  常量模块: {'有' if has_const else '无'}")
    except Exception as e:
        print("  常量模块读取失败:", e)
        has_const = False

    psPIXELS = 1
    psREPLACE = 1
    psNORMAL = 1
    psCONTENT_AWARE = 9

    ps.Preferences.RulerUnits = psPIXELS
    doc = ps.Open(r"E:\qq-bot\avatar\front_cut.png")
    print(f"\n文档: {doc.Width:.0f} x {doc.Height:.0f}  图层数={doc.Layers.Count}\n")

    print("=== 选区 ===")

    def sel_array():
        # 早期绑定下，Select 期待 PS 的「矩形数组」
        a = win32com.client.VARIANT(
            win32com.client.pythoncom.VT_ARRAY | win32com.client.pythoncom.VT_R8,
            [186.0, 302.0, 220.0, 336.0],
        )
        doc.Selection.Select(a, psREPLACE, 2.0, True)
        return doc.Selection.Bounds

    try_("Select(4个double的VARIANT数组)", sel_array)

    print("\n=== 图层 ===")

    def add_layer():
        L = doc.Layers.Add()
        return f"{L.Name} kind={L.Kind}"

    try_("Layers.Add()", add_layer)

    def add_group():
        G = doc.LayerSets.Add()
        return G.Name

    try_("LayerSets.Add()", add_group)

    print("\n=== 通道 ===")
    try_("读通道数", lambda: doc.Channels.Count)

    print("\n=== 动作描述符（万能钥匙）===")

    def exec_desc():
        d = win32com.client.Dispatch("Photoshop.ActionDescriptor")
        r = win32com.client.Dispatch("Photoshop.ActionReference")
        r.PutProperty(win32com.client.Dispatch("Photoshop.Application"), 0)
        return "descriptor 对象可创建"

    try_("创建 ActionDescriptor", exec_desc)

    try:
        doc.Close(1)
    except Exception:
        pass
    print("\n完成")


if __name__ == "__main__":
    main()
