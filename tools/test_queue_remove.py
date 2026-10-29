# -*- coding: utf-8 -*-
"""复现并验证「双击第 N 首却删掉了第 N+1 首」的 bug。

用户实测反馈（2026-10-27）：
    控制台里歌2 在第2行、歌3 在第3行，双击删歌2 → 结果歌2 还在，歌3 没了。

根因（两个）：
    ① 差 1：前端 index 从 1 开始（表格第 1 行 = 1），
       旧代码用 `len(keep) == idx` 比较，len(keep) 从 0 开始 → 整体偏移 1
    ② 跨队列错位：界面显示的是 PLAY_Q + JOB_Q 的合并列表，
       旧代码在两个队列里各自按 index 找，位置对不上

本测试就按用户的场景构造队列，断言「删第 2 首就只删第 2 首」。

用法：
    E:\\qq-bot\\astrbot-venv\\Scripts\\python.exe tools\\test_queue_remove.py
"""
import asyncio
import sys

sys.path.insert(0, r"E:\qq-bot\stream")
sys.stdout.reconfigure(encoding="utf-8")

import main as M

PASS, FAIL = [], []


def check(desc, got, want):
    ok = (got == want)
    (PASS if ok else FAIL).append(desc)
    print(f"  {'✅' if ok else '❌'} {desc}: got={got!r} want={want!r}")


def clear():
    for q in (M.JOB_Q, M.PLAY_Q):
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break


def setup():
    """构造和用户场景一样的队列：
       界面显示（合并顺序）:
         1  A  (PLAY_Q[0])
         2  B  (PLAY_Q[1])   ← 用户要删这个
         3  C  (PLAY_Q[2])
         4  D  (JOB_Q[0])
         5  E  (JOB_Q[1])
    """
    clear()
    M.PLAY_Q.put_nowait({"title": "A", "mp3": b"", "name": "一号", "raw": False})
    M.PLAY_Q.put_nowait({"title": "B", "mp3": b"", "name": "二号", "raw": False})
    M.PLAY_Q.put_nowait({"title": "C", "mp3": b"", "name": "三号", "raw": False})
    M.JOB_Q.put_nowait({"kw": "D", "kind": "sing", "name": "四号"})
    M.JOB_Q.put_nowait({"kw": "E", "kind": "sing", "name": "五号"})


def shown():
    """界面实际会显示的顺序（和 _queue_preview 一致）"""
    return [x["kw"] for x in M._queue_preview()]


class FakeReq:
    def __init__(self, payload):
        self._p = payload

    async def json(self):
        return self._p


def _body(resp):
    import json
    return json.loads(resp.body.decode("utf-8"))


async def run():
    M.load_cfg()
    M.S.skip = asyncio.Event()
    M.S.hot_q = asyncio.Queue(maxsize=4)

    print("\n【1】用户场景：删第 2 首，必须只删第 2 首")
    setup()
    before = shown()
    check("构造的显示顺序", before, ["A", "B", "C", "D", "E"])

    r = _body(await M.http_queue_remove(FakeReq({"index": 2})))
    after = shown()
    check("返回成功", r.get("ok"), True)
    check("删掉的是第 2 首 B", r.get("removed"), "B")
    check("删后顺序", after, ["A", "C", "D", "E"])
    check("B 确实不在了", "B" in after, False)
    check("A 还在（没被误删）", "A" in after, True)
    check("C 还在（旧 bug 会删掉它）", "C" in after, True)

    print("\n【2】删第 1 首（边界）")
    setup()
    r = _body(await M.http_queue_remove(FakeReq({"index": 1})))
    check("删掉的是 A", r.get("removed"), "A")
    check("剩余顺序", shown(), ["B", "C", "D", "E"])

    print("\n【3】删第 5 首（合并列表的最后一个，落在 JOB_Q）")
    setup()
    r = _body(await M.http_queue_remove(FakeReq({"index": 5})))
    check("删掉的是 E", r.get("removed"), "E")
    check("剩余顺序", shown(), ["A", "B", "C", "D"])

    print("\n【4】删第 4 首（PLAY_Q 与 JOB_Q 的交界）")
    setup()
    r = _body(await M.http_queue_remove(FakeReq({"index": 4})))
    check("删掉的是 D（JOB_Q 第一个）", r.get("removed"), "D")
    check("剩余顺序", shown(), ["A", "B", "C", "E"])

    print("\n【5】按歌名删（不受顺序影响）")
    setup()
    r = _body(await M.http_queue_remove(FakeReq({"kw": "C"})))
    check("删掉的是 C", r.get("removed"), "C")
    check("剩余顺序", shown(), ["A", "B", "D", "E"])

    print("\n【6】越界与非法输入")
    setup()
    r = _body(await M.http_queue_remove(FakeReq({"index": 99})))
    check("越界返回失败", r.get("ok"), False)
    check("越界后队列没动", shown(), ["A", "B", "C", "D", "E"])
    r = _body(await M.http_queue_remove(FakeReq({})))
    check("什么都不传返回失败", r.get("ok"), False)

    clear()


def main():
    print("=" * 72)
    print("  删队列 bug 复现测试（用户反馈：删第2首却删了第3首）")
    print("=" * 72)
    asyncio.run(run())
    print("\n" + "=" * 72)
    print(f"  结果：✅ {len(PASS)} 项通过   ❌ {len(FAIL)} 项失败")
    if FAIL:
        for f in FAIL:
            print("   失败:", f)
    print("=" * 72)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
