# -*- coding: utf-8 -*-
"""用 Photoshop + ExtendScript 处理立绘。

思路：用 photoshop-python-api 的 ps.echo（执行任意 ExtendScript）来做
PS 才能做的事：
  1. 内容识别填充 —— 把原图的小嘴抹掉，换成我们可控的嘴
  2. 抠图（选择主体）
  3. 分层导出
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from photoshop import Session

SRC = r"E:\qq-bot\avatar\front_cut.png"
OUT = r"E:\qq-bot\avatar"

# ExtendScript：选中嘴部矩形 → 内容识别填充
JSX_REMOVE_MOUTH = r"""
var d = app.activeDocument;
// 先把单位设为像素
app.preferences.rulerUnits = Units.PIXELS;

var idsetd = charIDToTypeID( "setd" );
var desc = new ActionDescriptor();
var idnull = charIDToTypeID( "null" );
var ref = new ActionReference();
var idChnl = charIDToTypeID( "Chnl" );
var idfsel = charIDToTypeID( "fsel" );
ref.putProperty( idChnl, idfsel );
desc.putReference( idnull, ref );
var idT = charIDToTypeID( "T   " );
var desc2 = new ActionDescriptor();
var idTop = charIDToTypeID( "Top " );
var idPxl = charIDToTypeID( "#Pxl" );
desc2.putUnitDouble( idTop, idPxl, 306 );
var idLeft = charIDToTypeID( "Left" );
desc2.putUnitDouble( idLeft, idPxl, 194 );
var idBtom = charIDToTypeID( "Btom" );
desc2.putUnitDouble( idBtom, idPxl, 330 );
var idRght = charIDToTypeID( "Rght" );
desc2.putUnitDouble( idRght, idPxl, 214 );
var idRctn = charIDToTypeID( "Rctn" );
desc.putObject( idT, idRctn, desc2 );
executeAction( idsetd, desc, DialogModes.NO );

// 内容识别填充
var idfill = charIDToTypeID( "fill" );
var desc3 = new ActionDescriptor();
var idUsng = charIDToTypeID( "Usng" );
var idFlCn = charIDToTypeID( "FlCn" );
var idcontentAware = stringIDToTypeID( "contentAware" );
desc3.putEnumerated( idUsng, idFlCn, idcontentAware );
var idOpct = charIDToTypeID( "Opct" );
var idPrc = charIDToTypeID( "#Prc" );
desc3.putUnitDouble( idOpct, idPrc, 100.0 );
var idMd = charIDToTypeID( "Md  " );
var idBlnM = charIDToTypeID( "BlnM" );
var idNrml = charIDToTypeID( "Nrml" );
desc3.putEnumerated( idMd, idBlnM, idNrml );
executeAction( idfill, desc3, DialogModes.NO );

// 取消选区
app.activeDocument.selection.deselect();
"OK";
"""


def main():
    with Session(action="", auto_close=False) as ps:
        ps.app.displayDialogs = ps.DialogModes.DisplayNoDialogs
        doc = ps.app.open(SRC)
        print(f"打开 {SRC}: {doc.width}x{doc.height}")

        try:
            r = ps.echo(JSX_REMOVE_MOUTH)
            print("ExtendScript 返回:", r)
            ok = True
        except Exception as e:
            print("ExtendScript 失败:", str(e)[:300])
            ok = False

        if ok:
            out = os.path.join(OUT, "_ps_nomouth.png")
            opts = ps.PNGSaveOptions()
            doc.saveAs(out, opts, True)
            print("已保存:", out)

        doc.close(2)
    print("RESULT:", "OK" if ok else "FAIL")


if __name__ == "__main__":
    main()
