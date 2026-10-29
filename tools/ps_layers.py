# -*- coding: utf-8 -*-
"""用 Photoshop 精细处理立绘，产出可动图层。

步骤（全部在 PS 里做，代码做不到的部分交给 PS）：
  1. 内容识别填充抹掉原来的小嘴
  2. 用「色彩范围」+ 蒙版把左右眼分别抠出来 → 单独图层
  3. 输出 PSD（分层）+ 每层 PNG
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from photoshop import Session

SRC = r"E:\qq-bot\avatar\front_cut.png"
OUT = r"E:\qq-bot\avatar"

# 实测坐标（440x638）
MOUTH_CLEAR = (194, 306, 214, 330)     # left, top, right, bottom
EYE_L = (120, 248, 195, 300)
EYE_R = (215, 248, 290, 300)

JSX_CLEAR_MOUTH = r"""
app.preferences.rulerUnits = Units.PIXELS;
var d = app.activeDocument;
d.selection.select([[194,306],[214,330]]);
var desc3 = new ActionDescriptor();
desc3.putEnumerated(charIDToTypeID("Usng"), charIDToTypeID("FlCn"), stringIDToTypeID("contentAware"));
desc3.putUnitDouble(charIDToTypeID("Opct"), charIDToTypeID("#Prc"), 100.0);
desc3.putEnumerated(charIDToTypeID("Md  "), charIDToTypeID("BlnM"), charIDToTypeID("Nrml"));
executeAction(charIDToTypeID("fill"), desc3, DialogModes.NO);
d.selection.deselect();
"OK";
"""


def jsx_select_rect(left, top, right, bottom):
    """生成矩形选区的 ExtendScript 片段"""
    return f"""
app.preferences.rulerUnits = Units.PIXELS;
var d = app.activeDocument;
d.selection.select([[{left},{top}],[{right},{bottom}]]);
"""


def main():
    with Session(action="", auto_close=False) as ps:
        ps.app.displayDialogs = ps.DialogModes.DisplayNoDialogs
        doc = ps.app.open(SRC)
        print(f"打开: {doc.width}x{doc.height}")

        # --- 1. 抹掉原嘴 ---
        ps.echo(JSX_CLEAR_MOUTH)
        print("1. 嘴已抹除（内容识别填充）")

        # --- 2. 抠出左右眼为独立图层 ---
        # 做法：复制整层 → 用矩形选区 + 蒙版裁成眼睛区域
        for name, box in (("eye_L", EYE_L), ("eye_R", EYE_R)):
            jsx = f"""
app.preferences.rulerUnits = Units.PIXELS;
var d = app.activeDocument;
// 复制当前层
var dup = d.activeLayer.duplicate();
dup.name = "{name}";
d.activeLayer = dup;
// 矩形选区
d.selection.select([[{box[0]},{box[1]}],[{box[2]},{box[3]}]], SelectionType.REPLACE, 0, true);
// 用选区做蒙版（PS: 选区 → 图层蒙版）
var idMk = charIDToTypeID("Mk  ");
var desc = new ActionDescriptor();
var idNw = charIDToTypeID("Nw  ");
var idChnl = charIDToTypeID("Chnl");
desc.putClass(idNw, idChnl);
var idAt = charIDToTypeID("At  ");
var ref = new ActionReference();
var idChnl2 = charIDToTypeID("Chnl");
var idMsk = charIDToTypeID("Msk ");
ref.putEnumerated(idChnl2, idChnl2, idMsk);
desc.putReference(idAt, ref);
var idUsng = charIDToTypeID("Usng");
var idUsrM = charIDToTypeID("UsrM");
var idRvlS = charIDToTypeID("RvlS");
desc.putEnumerated(idUsng, idUsrM, idRvlS);
executeAction(idMk, desc, DialogModes.NO);
d.selection.deselect();
// 隐藏，只留原始层可见
d.activeLayer.visible = false;
"OK";
"""
            try:
                ps.echo(jsx)
                print(f"2. 眼睛图层 {name} 已创建 (box={box})")
            except Exception as e:
                print(f"   眼睛 {name} 失败: {str(e)[:150]}")

        # --- 3. 保存分层 PSD ---
        psd = os.path.join(OUT, "avatar_layers.psd")
        try:
            opts = ps.PhotoshopSaveOptions()
            doc.saveAs(psd, opts, True)
            print("3. PSD 已保存:", psd)
        except Exception as e:
            print("   PSD 保存失败:", str(e)[:150])

        # --- 4. 每层导出 PNG ---
        cnt = doc.layers.length
        print(f"   共 {cnt} 个图层")
        for i in range(cnt):
            ly = doc.layers[i]
            try:
                nm = ly.name
                ly.visible = True
                for j in range(cnt):
                    doc.layers[j].visible = (j == i)
                png = os.path.join(OUT, f"_layer_{i}_{nm}.png")
                o = ps.PNGSaveOptions()
                doc.saveAs(png, o, True)
                print(f"   导出第 {i} 层 [{nm}] -> {os.path.basename(png)}")
            except Exception as e:
                print(f"   层 {i} 导出失败: {str(e)[:100]}")

        doc.close(2)

    print("\n完成")


if __name__ == "__main__":
    main()
