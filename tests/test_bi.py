"""笔。对照 77课 p195。"""

from __future__ import annotations

import pytest
from conftest import zigzag

from chan.bi import (
    assert_bi_invariants,
    build_bi,
    min_gap_for,
    pair_is_valid,
    reduce_fractals,
)
from chan.fractal import find_fractals
from chan.merge import merge_bars
from chan.types import Direction, Fractal, FractalKind, MergeConfig


def f(kind: FractalKind, m_idx: int, high: float, low: float) -> Fractal:
    return Fractal(
        kind=kind,
        m_idx=m_idx,
        high=high,
        low=low,
        begin_idx=m_idx - 1,
        end_idx=m_idx + 1,
        peak_idx=m_idx,
    )


T = FractalKind.TOP
B = FractalKind.BOTTOM


# --------------------------------------------------------------------------
# 步骤二 + 步骤三：同性质分型压缩
# --------------------------------------------------------------------------


def test_reduce_keeps_highest_top():
    """「对于顶，前面的低于后面的，只保留后面的」。"""
    out = reduce_fractals([f(T, 1, 10, 9), f(T, 5, 12, 11), f(T, 9, 11, 10)])
    assert [x.m_idx for x in out] == [5]


def test_reduce_keeps_lowest_bottom():
    out = reduce_fractals([f(B, 1, 6, 5), f(B, 5, 4, 3), f(B, 9, 5, 4)])
    assert [x.m_idx for x in out] == [5]


def test_reduce_tie_keeps_earliest():
    """「不满足上面情况的，例如相等的，都可以先保留」+ 步骤三「把这连续的顶
    中**最先一个**……连在一起」。两条合起来 = 相等取最早。"""
    out = reduce_fractals([f(T, 1, 10, 9), f(T, 5, 10, 8), f(T, 9, 10, 9.5)])
    assert [x.m_idx for x in out] == [1]


def test_reduce_leaves_alternating_untouched():
    src = [f(T, 1, 10, 9), f(B, 5, 4, 3), f(T, 9, 12, 11)]
    assert [x.m_idx for x in reduce_fractals(src)] == [1, 5, 9]


# --------------------------------------------------------------------------
# 两个约束
# --------------------------------------------------------------------------


def test_gap_constraint_old_vs_new():
    top, bottom = f(T, 1, 10, 9), f(B, 4, 4, 3)  # 间隔 3
    assert pair_is_valid(top, bottom, min_gap_for("new"))
    assert not pair_is_valid(top, bottom, min_gap_for("old"))


def test_gap_values_match_the_derivation():
    # old：顶占 i-1,i,i+1，底占 j-1,j,j+1，中间独立K线 j-i-3 >= 1 → j-i >= 4
    assert min_gap_for("old") == 4
    # new（社区约定，非原文）：只要求不共用K线 → j-i >= 3
    assert min_gap_for("new") == 3


def test_unknown_bi_mode_raises():
    with pytest.raises(ValueError):
        min_gap_for("whatever")


def test_price_constraint_compares_the_two_highs():
    """「顶分型中最高那K线的区间至少要有一部分高于底分型中最低那K线的区间」
    → g_顶 > g_底，比的是两个**高点**，不是 g_顶 > d_底。

    这里顶的高点 10 低于底的高点 12（「顶都在低的范围内」），不成笔；
    但若按错误的 g_顶 > d_底 来判（10 > 3）就会误判成立。
    """
    top, bottom = f(T, 1, 10, 9), f(B, 9, 12, 3)
    assert not pair_is_valid(top, bottom, 4)
    assert top.price > bottom.low  # 错误口径下会通过
    assert pair_is_valid(f(T, 1, 13, 9), bottom, 4)


def test_same_kind_never_pairs():
    assert not pair_is_valid(f(T, 1, 10, 9), f(T, 9, 12, 11), 4)


# --------------------------------------------------------------------------
# 划笔
# --------------------------------------------------------------------------


def test_zigzag_bis():
    m = merge_bars(zigzag([10, 20, 12, 25, 15], leg_bars=4))
    bis, kept = build_bi(find_fractals(m))
    assert len(bis) == 2
    assert [b.direction for b in bis] == [Direction.DOWN, Direction.UP]
    assert [x.m_idx for x in kept] == [4, 8, 12]
    assert_bi_invariants(bis, min_gap_for("new"))


def test_bi_mode_changes_the_result():
    """间隔恰好 3 时，新笔成笔、老笔不成笔。"""
    m = merge_bars(zigzag([10, 20, 12, 25, 15], leg_bars=3))
    assert [x.m_idx for x in find_fractals(m)] == [3, 6, 9]

    new_bis, _ = build_bi(find_fractals(m), MergeConfig(bi_mode="new"))
    old_bis, old_kept = build_bi(find_fractals(m), MergeConfig(bi_mode="old"))
    assert len(new_bis) == 2
    assert len(old_bis) == 0
    # 老笔下，太近的底被丢掉后两个顶相邻，压缩成更高的那个
    assert [x.m_idx for x in old_kept] == [9]


def test_backtracking_revises_an_earlier_choice():
    """配对失败会反过来推翻步骤二已经做出的取舍，所以必须迭代到不动点。

    顶@0(20) 底@2(5) 顶@8(25) 底@14(3)，老笔间隔门槛 4：
      1. 顶@0 与 底@2 间隔只有 2，不成笔 → 丢掉较晚的 底@2
      2. 丢完之后 顶@0 与 顶@8 变成相邻同性质 → 压缩，保留更高的 顶@8
      3. 顶@8 与 底@14 成笔
    第 2 步只有在第 1 步之后才会发生，单向一趟扫描做不到。
    """
    src = [f(T, 0, 20, 19), f(B, 2, 6, 5), f(T, 8, 25, 24), f(B, 14, 4, 3)]
    bis, kept = build_bi(src, MergeConfig(bi_mode="old"))
    assert [x.m_idx for x in kept] == [8, 14]
    assert len(bis) == 1
    assert bis[0].direction is Direction.DOWN


def test_last_bi_is_provisional():
    """77课 p195「笔没完成」：末端笔的终点分型还可能被更极端的同性质分型
    取代，盘后扫描绝不能当成已确认信号。"""
    m = merge_bars(zigzag([10, 20, 12, 25, 15, 30], leg_bars=4))
    bis, _ = build_bi(find_fractals(m))
    assert len(bis) >= 2
    assert all(b.confirmed for b in bis[:-1])
    assert not bis[-1].confirmed


def test_bis_are_head_to_tail_and_alternating():
    m = merge_bars(zigzag([10, 20, 12, 25, 15, 30, 18], leg_bars=4))
    bis, _ = build_bi(find_fractals(m))
    assert len(bis) >= 3
    for prev, cur in zip(bis, bis[1:]):
        assert prev.end is cur.start
        assert prev.direction is not cur.direction
    assert_bi_invariants(bis, min_gap_for("new"))


def test_empty_and_degenerate_inputs():
    assert build_bi([]) == ([], [])
    assert build_bi([f(T, 1, 10, 9)]) == ([], [f(T, 1, 10, 9)])
    assert merge_bars([]) == []
    assert find_fractals([]) == []


def test_bi_geometry_helpers():
    # 首尾两个转折点落在序列端点上，形不成分型，所以要四个转折点才有一笔
    m = merge_bars(zigzag([10, 20, 12, 25], leg_bars=4))
    bis, _ = build_bi(find_fractals(m))
    assert len(bis) == 1
    b = bis[0]
    assert b.high == b.start_price
    assert b.low == b.end_price
    assert b.amplitude == pytest.approx(b.start_price - b.end_price)
    assert b.m_span == (b.start.m_idx, b.end.m_idx)
