"""包含关系处理。对照 65课 p168。"""

from __future__ import annotations

from conftest import mk_bars

from chan.merge import assert_no_inclusion, has_gap, has_inclusion, merge_bars
from chan.types import Direction, MergeConfig


def test_no_inclusion_passthrough():
    bars = mk_bars([(1, 2), (2, 3), (3, 4), (4, 5)])
    m = merge_bars(bars)
    assert len(m) == 4
    assert all(not x.is_merged for x in m)
    assert_no_inclusion(m)


def test_up_merge_takes_max_max():
    """向上合并取 [max d, max g]。"""
    bars = mk_bars([(1, 3), (2, 5), (3, 4)])
    m = merge_bars(bars)
    assert len(m) == 2
    assert (m[1].low, m[1].high) == (3, 5)
    assert m[1].direction is Direction.UP
    assert m[1].merged_count == 2
    assert m[1].begin_idx == 1 and m[1].end_idx == 2


def test_down_merge_takes_min_min():
    """向下合并取 [min d, min g]。"""
    bars = mk_bars([(3, 10), (2, 8), (3, 7)])
    m = merge_bars(bars)
    assert len(m) == 2
    assert (m[1].low, m[1].high) == (2, 7)
    assert m[1].direction is Direction.DOWN


def test_direction_comes_from_the_bar_before_last():
    """方向取自 merged[-2]，不是取自参与合并的那两根本身。

    后两根 [1,6] 与 [2,5] 的包含关系本身既可以读成向上也可以读成向下，
    方向完全由**再前一根**决定：参照低（[0,0.5]）就是向上，参照高
    （[7,10]）就是向下，合并结果完全相反。
    """
    up = merge_bars(mk_bars([(0, 0.5), (1, 6), (2, 5)]))
    down = merge_bars(mk_bars([(7, 10), (1, 6), (2, 5)]))
    assert (up[1].low, up[1].high) == (2, 6)
    assert (down[1].low, down[1].high) == (1, 5)


def test_non_transitive_order_matters():
    """65课 p168：包含关系不满足传递律，必须顺序处理。

    Z=[0,2] A=[1,10] B=[3,6] C=[2,8]
      顺序处理：A 吃掉 B 得到 [3,10]，[3,10] 与 C=[2,8] 不再包含 → 3 根。
      若先算两两包含再统一合并：A=[1,10] 同时包含 B 和 C，会错误地并成 1 根。
    """
    m = merge_bars(mk_bars([(0, 2), (1, 10), (3, 6), (2, 8)]))
    assert len(m) == 3
    assert (m[1].low, m[1].high) == (3, 10)
    assert m[1].merged_count == 2
    assert (m[2].low, m[2].high) == (2, 8)
    assert_no_inclusion(m)


def test_chained_merge_is_sequential():
    """连续三根被同一根吃掉，end_idx 要一路推进。"""
    bars = mk_bars([(1, 3), (2, 9), (3, 8), (4, 7), (5, 6)])
    m = merge_bars(bars)
    assert len(m) == 2
    assert m[1].merged_count == 4
    assert m[1].end_idx == 4
    assert (m[1].low, m[1].high) == (5, 9)


def test_identical_bars_collapse():
    """完全相同的相邻K线互相包含，会被并成一根。

    这正是停牌日**不能**用前值补齐的原因：补出来的平盘K线会被吃掉，
    虽然不至于造出假分型，但会把真实的K线计数搞乱，进而影响笔的间隔约束。
    数据层必须剔除停牌日而不是补零。
    """
    m = merge_bars(mk_bars([(1, 2), (5, 6), (5, 6), (5, 6), (7, 8)]))
    assert len(m) == 3
    assert m[1].merged_count == 3


def test_limit_up_run_does_not_merge():
    """连续一字板涨停：每根都是零振幅的点，且逐日走高。

    点与点之间不存在包含关系（互不覆盖），所以**不会**被合并——合并需要
    区间覆盖。真实表现是一字板期间压根形不成分型（没有局部极值），
    也就是走势没结束，这与 20课 p42 的结论一致。
    """
    bars = mk_bars([(10.0, 10.0), (11.0, 11.0), (12.1, 12.1), (13.31, 13.31)])
    m = merge_bars(bars)
    assert len(m) == 4
    assert all(not x.is_merged for x in m)


def test_initial_drop_policy():
    """起手方向是原文空白。默认 "drop"：丢掉开头互相包含的K线。

    前三根层层包含，没有 n-1 可以定方向，直接从第一对非包含的相邻K线起算。
    """
    bars = mk_bars([(0, 100), (10, 90), (20, 80), (30, 95)])
    m = merge_bars(bars, MergeConfig(initial_direction="drop"))
    assert m[0].begin_idx == 2
    assert len(m) == 2
    assert_no_inclusion(m)


def test_initial_forced_direction():
    bars = mk_bars([(0, 100), (10, 90), (20, 80), (30, 95)])
    up = merge_bars(bars, MergeConfig(initial_direction="up"))
    down = merge_bars(bars, MergeConfig(initial_direction="down"))
    # 向上：[max d, max g]，四根层层吞进去 → [30, 100]，剩一根
    assert len(up) == 1
    assert (up[0].low, up[0].high) == (30, 100)
    # 向下：[min d, min g] → 前三根并成 [0, 80]，第四根 [30,95] 与之不再包含
    assert len(down) == 2
    assert (down[0].low, down[0].high) == (0, 80)


def test_gap_detection_between_raw_bars():
    bars = mk_bars([(10, 12), (18, 20)])
    assert has_gap(bars[0], bars[1])
    assert not has_gap(*mk_bars([(10, 12), (11, 20)]))


def test_gap_blocks_merge_switch():
    """跳空是否阻断合并，原文**一个字都没写**，这里是可配置的我方约定。

    [5,25] 吃过前几根后区间很宽，新K线 [18,19] 相对**上一根原始K线** [6,7]
    是向上跳空的，但它落在已合并区间内部。默认按字面读法照常合并；
    打开开关则另起一根。
    """
    ranges = [(0, 1), (5, 25), (6, 7), (18, 19)]
    off = merge_bars(mk_bars(ranges), MergeConfig(gap_blocks_merge=False))
    on = merge_bars(mk_bars(ranges), MergeConfig(gap_blocks_merge=True))
    assert len(off) < len(on)
    assert on[-1].begin_idx == 3


def test_has_inclusion_is_symmetric():
    assert has_inclusion(1, 10, 2, 9)
    assert has_inclusion(2, 9, 1, 10)
    assert has_inclusion(1, 10, 1, 10)
    assert not has_inclusion(1, 5, 2, 6)
