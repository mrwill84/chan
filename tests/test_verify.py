"""算两遍比摘要。这一层防的是硬件位翻，不是 bug，所以测法也不一样：

没法真的去翻一个位，于是**注入**——让被包的函数前 k 遍返回坏结果，断言这一层
认得出来、重算、取多数。注入点就是损坏可能落在的地方（返回值）。
"""

from __future__ import annotations

import pytest

from chan.types import Bar
from chan.verify import Check, bars_digest, digest, stable


# --------------------------------------------------------------------------
# 摘要本身
# --------------------------------------------------------------------------


def test_digest_is_deterministic():
    x = {"a": [1, 2.5, "x"], "b": {"c": None, "d": True}}
    assert digest(x) == digest({"b": {"d": True, "c": None},
                                "a": [1, 2.5, "x"]}), "键序不该影响摘要"


@pytest.mark.parametrize("a, b", [
    ([1, 2], (1, 2)),          # 列表 vs 元组
    ("1", 1),                  # 字符串 vs 整数
    (True, 1),                 # bool 必须在 int 前面判，否则这两个撞车
    (False, 0),
    (0.0, -0.0),               # 值相等、**位不同**——我们找的正是位翻
    (1, 1.0),                  # 整数 vs 浮点
    (None, "None"),
    ([1, [2]], [[1], 2]),
])
def test_digest_does_not_collide(a, b):
    """撞车在这里不是性能问题是正确性问题：两遍结果真不一样却算出同一个摘要，
    这一层就白加了。"""
    assert digest(a) != digest(b)


def test_digest_sees_one_bit_of_a_float():
    import struct
    x = 83.93951588
    y = struct.unpack("<d", struct.pack("<Q", struct.unpack("<Q", struct.pack("<d", x))[0] ^ 1))[0]
    assert x != y and abs(x - y) < 1e-12, "构造失败：要的是最低位那一个位"
    assert digest([x]) != digest([y])


def test_digest_covers_dataclass_fields():
    b1 = Bar(ts="2024-01-02 10:00:00", open=1.0, high=2.0, low=0.5,
             close=1.5, volume=100.0, idx=0)
    b2 = Bar(ts="2024-01-02 10:00:00", open=1.0, high=2.0, low=0.5,
             close=1.5000001, volume=100.0, idx=0)
    assert digest(b1) == digest(b1)
    assert digest(b1) != digest(b2)
    assert bars_digest([b1]) != bars_digest([b2])


def test_digest_of_an_exception_ignores_the_traceback():
    """异常要参与投票，所以同类同消息的两个异常必须给同一个摘要。

    带上 traceback 的话每次抛出的行号和帧都不同，所有异常互不相同，多数票
    永远凑不齐——那会把「两遍都老老实实抛了 InvariantError」误判成损坏。
    """
    def boom():
        raise ValueError("一样的话")

    es = []
    for _ in range(2):
        try:
            boom()
        except ValueError as e:
            es.append(e)
    assert es[0] is not es[1]
    assert digest(es[0]) == digest(es[1])
    assert digest(es[0]) != digest(ValueError("别的话"))
    assert digest(ValueError("x")) != digest(TypeError("x"))


# --------------------------------------------------------------------------
# stable：注入损坏
# --------------------------------------------------------------------------


def _flaky(outcomes):
    """按 `outcomes` 依次返回/抛出，调用次数记在 `.calls` 上。"""
    it = iter(outcomes)

    def fn():
        fn.calls += 1
        v = next(it)
        if isinstance(v, BaseException):
            raise v
        return v

    fn.calls = 0
    return fn


def test_clean_function_runs_exactly_twice():
    """正常情况下的代价恰好是 2 倍，不多不少。"""
    fn = _flaky([{"x": 1}, {"x": 1}, {"x": 1}])
    out, chk = stable(fn, label="600009")
    assert out == {"x": 1}
    assert fn.calls == 2 and chk.runs == 2
    assert chk.ok and not chk.retried
    assert "两遍一致" in str(chk)


def test_one_corrupted_run_is_outvoted():
    fn = _flaky([{"x": 999}, {"x": 1}, {"x": 1}])
    out, chk = stable(fn)
    assert out == {"x": 1}, "坏的那一遍被多数票盖掉"
    assert chk.ok and chk.retried and chk.runs == 3
    assert len(set(chk.digests)) == 2


def test_corruption_in_the_second_run_is_outvoted_too():
    fn = _flaky([{"x": 1}, {"x": 999}, {"x": 1}])
    out, chk = stable(fn)
    assert out == {"x": 1}
    assert chk.runs == 3 and chk.ok


def test_three_different_results_is_not_ok():
    """凑不齐多数就老实说不可信，**但不抛异常**——一只票不该带崩一整晚的扫描。"""
    fn = _flaky([{"x": 1}, {"x": 2}, {"x": 3}])
    out, chk = stable(fn, label="600009")
    assert out == {"x": 3}
    assert not chk.ok and chk.runs == 3
    assert len(set(chk.digests)) == 3
    assert "不可信" in str(chk)


def test_a_real_exception_is_raised_through():
    """两遍都抛同一个，那就是真的该抛——调用方感觉不到这一层。"""
    fn = _flaky([ValueError("真的错了"), ValueError("真的错了")])
    with pytest.raises(ValueError, match="真的错了"):
        stable(fn)
    assert fn.calls == 2


def test_a_bogus_exception_from_one_run_is_outvoted():
    """内存损坏最常见的样子就是抛一个不可能的异常。它只是一票。"""
    fn = _flaky([SystemError("unknown opcode 255"), {"x": 1}, {"x": 1}])
    out, chk = stable(fn)
    assert out == {"x": 1}
    assert chk.ok and chk.runs == 3


def test_an_exception_can_win_the_vote_even_against_a_value():
    fn = _flaky([{"x": 1}, ValueError("boom"), ValueError("boom")])
    with pytest.raises(ValueError, match="boom"):
        stable(fn)


def test_keyboard_interrupt_goes_straight_through():
    """按一次 Ctrl-C 就该停。被收进投票的话得按两次，而且中间还会再算一遍。"""
    fn = _flaky([KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        stable(fn)
    assert fn.calls == 1


def test_tries_below_two_runs_once_but_still_keeps_the_books():
    """显式关掉自检时不替调用方改主意，但 `runs=1` 要留着——汇总表不能把
    「没查过」和「查过没事」混成一个数。"""
    fn = _flaky([{"x": 1}])
    out, chk = stable(fn, tries=1)
    assert out == {"x": 1} and fn.calls == 1
    assert chk.runs == 1 and chk.ok and not chk.retried


def test_tries_caps_the_work():
    fn = _flaky([{"x": i} for i in range(10)])
    _, chk = stable(fn, tries=5)
    assert fn.calls == 5 and chk.runs == 5 and not chk.ok


def test_check_str_names_the_subject():
    chk = Check(ok=False, runs=3, digests=["a", "b", "c"], label="600519 day")
    assert "600519 day" in str(chk)
