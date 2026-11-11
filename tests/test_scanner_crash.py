"""子进程死掉时扫描还能不能跑完。

这是 `chan.verify` 的三号盲区：segfault 时 `_one` 的 `except` 一行都执行不到，
进程直接没了。所以这里**真的杀**一个子进程（`os._exit`），不是 mock 一个异常——
要验的恰恰是「进程没了」这件事本身在池子那一层是什么样子。

单独一个文件：这两条要起真的解释器进程，比整个套件其余部分加起来还慢，
放在一起会让人误以为划线测试变慢了。
"""

from __future__ import annotations

import os
from pathlib import Path

from scan import scanner
from scan.scanner import FAIL, MISS, _iterate


def _crashy(code: str) -> dict:
    """第一次碰到 ``"kill"`` 就弄死自己，之后老实干活。

    用文件当「已经杀过一次」的记号，因为进程都没了，内存里的标志位留不下来。
    这也正是真实情形：损坏是随机的，重算一遍大概率就过去了。
    """
    marker = Path(os.environ["CHAN_CRASH_MARKER"])
    if code == "kill" and not marker.exists():
        marker.write_text("x", encoding="utf-8")
        os._exit(1)
    return {"code": code, "status": MISS}


def _always_crashy(code: str) -> dict:
    if code == "kill":
        os._exit(1)
    return {"code": code, "status": MISS}


def test_a_dead_worker_does_not_lose_the_rest(tmp_path, monkeypatch):
    """一只票弄死 worker，另外几只照样要有结果。

    这条要是坏了，表现**不是**测试失败而是测试挂住——`multiprocessing.Pool`
    对死掉的 worker 的反应就是永远等下去，那才是换掉它的理由。
    """
    monkeypatch.setenv("CHAN_CRASH_MARKER", str(tmp_path / "killed"))
    codes = ["a", "kill", "b", "c"]

    got = list(_iterate(codes, {}, 2, fn=_crashy))

    assert (tmp_path / "killed").exists(), "没真杀成，这条测试什么也没验到"
    assert {r["code"] for r in got} == set(codes)
    assert [r["code"] for r in got].count("kill") == 1, "重算了但不该重复落盘"
    assert all(r["status"] == MISS for r in got)


def test_a_worker_that_always_dies_becomes_a_fail_not_a_hang(tmp_path, monkeypatch):
    """重建到上限就认输，但**要给出一条记录**。

    静悄悄少一只票比记一条 FAIL 糟得多：续传时它和「还没算」长得一模一样，
    于是每一轮都重试、每一轮都崩，永远跑不完。
    """
    monkeypatch.setattr(scanner, "MAX_POOL_RESTARTS", 0)   # 重建 0 次 = 只试一轮

    got = list(_iterate(["a", "kill"], {}, 2, fn=_always_crashy))

    by_code = {r["code"]: r for r in got}
    assert by_code["a"]["status"] == MISS
    assert by_code["kill"]["status"] == FAIL
    assert "崩溃" in by_code["kill"]["error"]
