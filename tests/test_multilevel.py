"""多级别联立（27课 p67 的区间套）。

这一层本身不划线，所以测的不是划线对不对，而是**收缩过程的语义**：
窗口怎么算、跨粒度时间戳怎么比、次级别没数据和次级别没买卖点要分得开、
方向冲突时以谁为准。

最后一条尤其要紧——C-min 让「30 分钟数据只有一个月」成了常态，而把它说成
「这段时间没有买卖点」会让模型给出一个完全错误的安心结论。
"""

from __future__ import annotations

import pytest
from conftest import mk_bis, zigzag

from chan.buysell import BSPoint
from chan.multilevel import (
    AGREE,
    CONFLICT,
    INCOMPLETE,
    NONE,
    OK,
    PARTIAL,
    LevelAnalysis,
    _refine,
    _within,
    analyze_level,
    nest,
)
from chan.types import Bar


# --------------------------------------------------------------------------
# 跨粒度时间戳
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ts,lo,hi,want",
    [
        # 同粒度：退化成普通字符串比较
        ("2024-03-05", "2024-03-01", "2024-03-10", True),
        ("2024-03-11", "2024-03-01", "2024-03-10", False),
        ("2024-02-28", "2024-03-01", "2024-03-10", False),
        # 日线窗口 + 分钟线点：窗口最后一天的**整天**都要算进来
        ("2024-03-10 14:30:00", "2024-03-01", "2024-03-10", True),
        ("2024-03-01 09:30:00", "2024-03-01", "2024-03-10", True),
        ("2024-03-11 09:30:00", "2024-03-01", "2024-03-10", False),
    ],
)
def test_window_compares_at_the_parent_granularity(ts, lo, hi, want):
    """直接比字符串的话，窗口最后一天的分钟线全被排除——而转折点多半就在那天。"""
    assert _within(ts, lo, hi) is want


# --------------------------------------------------------------------------
# analyze_level
# --------------------------------------------------------------------------


def _saw(n: int = 40, step: float = 8.0):
    pivots = [100.0]
    for i in range(n):
        pivots.append(pivots[-1] + (step if i % 2 == 0 else -step * 0.6))
    return zigzag(pivots)


def test_analyze_level_runs_the_whole_pipeline():
    a = analyze_level("day", _saw(), zs_from="bi")
    assert a.ok and a.bis and a.legs and a.trend
    assert len(a.ts) == len(_saw())


def test_short_history_is_an_error_not_an_exception():
    """次级别数据不够是常态（C-min），不能为此把整次联立炸掉。"""
    a = analyze_level("5min", zigzag([100.0, 110.0, 105.0]))
    assert not a.ok and "不足" in a.error
    assert a.points == () and a.trend == "none"


def test_broken_data_is_caught_not_propagated():
    bad = [Bar(idx=i, ts=f"T{i:03d}", open=1, high=1, low=1, close=1) for i in range(80)]
    a = analyze_level("day", bad)
    # 全平盘K线划不出东西，但不该抛到调用方那里
    assert a.error is None or isinstance(a.error, str)


def test_ts_of_is_out_of_range_safe():
    a = analyze_level("day", _saw(), zs_from="bi")
    assert a.ts_of(-1) == "" and a.ts_of(10**6) == ""


# --------------------------------------------------------------------------
# 收缩一层：_refine
#
# 手搓 LevelAnalysis，不走划线。窗口语义要能逐种情况精确拨，真数据做不到
# ——「恰好窗口内只有一个反向点」这种局面不是想造就能造出来的
# --------------------------------------------------------------------------


def _pt(bar: int, *, buy: bool, kind: int = 1, leg: int = 0) -> BSPoint:
    return BSPoint(kind=kind, is_buy=buy, leg=leg, bar=bar, price=100.0,
                   zs_index=0, source="test")


def _lv(name: str, ts: list[str], pts: list[BSPoint]) -> LevelAnalysis:
    return LevelAnalysis(name=name, ts=tuple(ts), trend="panzheng", points=tuple(pts))


DAYS = [f"2024-03-{d:02d}" for d in range(1, 21)]
MINS = [f"2024-03-{d:02d} {h:02d}:30:00" for d in range(1, 21) for h in (10, 11, 14)]


def test_refine_picks_the_last_same_side_point_in_the_window():
    pts = [_pt(3, buy=True), _pt(20, buy=True), _pt(50, buy=True)]
    a = _lv("30min", MINS, pts)
    st = _refine(a, "2024-03-01", "2024-03-10", want_buy=True)
    assert st.coverage == OK
    assert st.point is pts[1]  # bar 50 已经出了窗口（3-18）
    assert st.ts == MINS[20]


def test_refine_returns_the_engine_point_itself_not_a_copy():
    """收缩的是**答案**不是输入：次级别点必须来自它自己完整历史上的划线结果。

    切片重划会得到另一批笔和段（包含处理不满足传递律，65课 p168），拿它冒充
    次级别结构是假精确。这条用对象同一性把这件事钉住。
    """
    pts = [_pt(10, buy=True)]
    st = _refine(_lv("30min", MINS, pts), "2024-03-01", "2024-03-10", want_buy=True)
    assert st.point is pts[0]


def test_no_bars_in_window_is_not_no_points():
    """C-min 的核心区别。说错了，模型会给出一个完全错误的安心结论。"""
    a = _lv("30min", [f"2026-10-{d:02d} 10:30:00" for d in range(1, 10)], [])
    st = _refine(a, "2024-03-01", "2024-03-10", want_buy=True)
    assert st.coverage == NONE
    assert "没有K线" in st.note and "C-min" in st.note


def test_partial_coverage_is_flagged():
    """数据够不到窗口左端时，「窗口内没有更早的点」这句话是不可信的。"""
    a = _lv("30min", MINS[30:], [_pt(5, buy=True)])
    st = _refine(a, "2024-03-01", "2024-03-20", want_buy=True)
    assert st.coverage == PARTIAL
    assert "窗口左段缺失" in st.note


def test_opposite_side_points_are_recorded_not_silently_dropped():
    against = _pt(20, buy=False)
    st = _refine(_lv("30min", MINS, [against]), "2024-03-01", "2024-03-10",
                 want_buy=True)
    assert st.point is None
    assert st.against == (against,)
    assert "反向" in st.note


def test_same_side_wins_but_conflict_is_still_reported():
    """27课 p67 的程序是自上而下的：小级别不覆盖大级别方向，但也不能瞒着。"""
    same, against = _pt(10, buy=True), _pt(20, buy=False)
    st = _refine(_lv("30min", MINS, [same, against]), "2024-03-01", "2024-03-10",
                 want_buy=True)
    assert st.point is same
    assert st.against == (against,)
    assert "以大级别为准" in st.note


# --------------------------------------------------------------------------
# 整条区间套：nest
# --------------------------------------------------------------------------


def _day_level(pts: list[BSPoint]) -> LevelAnalysis:
    """带笔和段的日线层——`leg_start_ts` 要靠它们算窗口左端。

    `mk_bis` 造的笔，第 i 笔起点分型的 `peak_idx` 是 `i*4`，所以 leg k 的
    窗口左端落在 `DAYS[4k]` 上。
    """
    from chan.types import Direction, Leg

    bis = mk_bis([100.0, 112.0, 104.0, 118.0, 106.0])
    legs = [Leg(index=i, direction=Direction.UP if i % 2 == 0 else Direction.DOWN,
                start_price=b.start_price, end_price=b.end_price,
                low=b.low, high=b.high, src_start=i, src_end=i)
            for i, b in enumerate(bis)]
    return LevelAnalysis(name="day", ts=tuple(DAYS), trend="panzheng",
                         bis=tuple(bis), legs=tuple(legs), points=tuple(pts))


def test_window_is_the_leg_that_ends_at_the_anchor():
    """窗口 = 大级别那个买卖点**所在走势段**的起点到终点（27课 p67）。"""
    p = _pt(bar=11, buy=True, leg=2)
    n = nest([_day_level([p])])
    assert n.steps[0].window == (DAYS[8], DAYS[11])  # leg2 的 src_start=2 → 笔2 → peak 8


def test_anchor_and_precise_are_different_jobs():
    """大级别定方向，小级别只定时机。两者分开取，不许混成一个「结论」。"""
    day = _day_level([_pt(bar=11, buy=True, leg=2)])
    m30 = _lv("30min", MINS, [_pt(26, buy=True)])  # 2024-03-09 14:30
    n = nest([day, m30])
    assert n.agreement == AGREE
    assert n.anchor.level == "day" and n.precise.level == "30min"
    assert n.precise.ts == MINS[26]


def test_chain_stops_where_coverage_runs_out():
    """次级别够不到就断在那里，不能拿上一层的窗口硬塞给再下一级。

    继续往下只是换个级别重新找点，那不是区间套——而输出上看起来一模一样，
    所以必须在这里断掉并说明原因。
    """
    day = _day_level([_pt(bar=11, buy=True, leg=2)])
    m30 = _lv("30min", [f"2026-10-{d:02d} 10:30:00" for d in range(1, 10)], [])
    m5 = _lv("5min", MINS, [_pt(26, buy=True)])
    n = nest([day, m30, m5])
    assert [s.level for s in n.steps] == ["day", "30min"]  # 5min 根本没跑
    assert n.agreement == INCOMPLETE
    assert n.precise.level == "day"
    assert any("C-min" in x for x in n.notes)


def test_conflict_survives_to_the_top_level_verdict():
    day = _day_level([_pt(bar=11, buy=True, leg=2)])
    m30 = _lv("30min", MINS, [_pt(26, buy=True), _pt(28, buy=False)])
    assert nest([day, m30]).agreement == CONFLICT


def test_conflict_is_not_overwritten_by_a_later_dead_end():
    """先冲突、后断链，结论该是 conflict——断链只是没能再收缩，冲突是实质分歧。"""
    day = _day_level([_pt(bar=11, buy=True, leg=2)])
    m30 = _lv("30min", MINS, [_pt(26, buy=True), _pt(28, buy=False)])
    m5 = _lv("5min", [], [])
    assert nest([day, m30, m5]).agreement == CONFLICT


def test_no_anchor_says_so_instead_of_pretending():
    n = nest([_day_level([])])
    assert n.agreement == INCOMPLETE and n.steps == ()
    assert "没有出发点" in n.notes[0]


def test_unusable_top_level_is_reported_not_crashed():
    n = nest([LevelAnalysis("day", (), "none", error="没数据")])
    assert n.agreement == INCOMPLETE and "没数据" in n.notes[0]
    assert n.anchor is None and n.precise is None


def test_side_and_kind_filters_choose_the_anchor():
    pts = [_pt(bar=7, buy=True, kind=1, leg=1), _pt(bar=11, buy=False, kind=3, leg=2)]
    day = _day_level(pts)
    assert nest([day]).anchor.point is pts[-1]            # 默认取最后一个
    assert nest([day], side=True).anchor.point is pts[0]  # 只要买点
    assert nest([day], kinds={1}).anchor.point is pts[0]


def test_explicit_anchor_overrides_the_filters():
    pts = [_pt(bar=7, buy=True, leg=1), _pt(bar=11, buy=True, leg=2)]
    day = _day_level(pts)
    assert nest([day], anchor=pts[0]).anchor.point is pts[0]


# --------------------------------------------------------------------------
# 端到端：真的走一遍划线，不手搓结构
# --------------------------------------------------------------------------


def _restamp(bars: list[Bar], fmt):
    return [Bar(idx=b.idx, ts=fmt(b.idx), open=b.open, high=b.high, low=b.low,
                close=b.close, volume=b.volume) for b in bars]


def _dated(bars: list[Bar]):
    from datetime import date, timedelta

    d0 = date(2020, 1, 1)
    return _restamp(bars, lambda i: str(d0 + timedelta(days=i)))


def _intraday(bars: list[Bar], per_day: int = 8):
    """把一串K线铺到同样的日期上，每天 `per_day` 根，时间戳带时分秒。

    价格形态和日线层没有任何经济学上的对应关系——这里要验的是**机制**
    （窗口能不能跨粒度命中、收缩链接不接得上），不是两个级别的走势一致性。
    """
    from datetime import date, timedelta

    d0 = date(2020, 1, 1)
    return _restamp(
        bars,
        lambda i: f"{d0 + timedelta(days=i // per_day)} {9 + i % per_day:02d}:30:00",
    )


def test_end_to_end_two_levels():
    import random

    rng = random.Random(29)
    pv = [100.0]
    for i in range(60):
        step = rng.uniform(5, 30) * rng.uniform(0.5, 1.5)
        pv.append(max(pv[-1] + (step if i % 2 == 0 else -step), 10.0))
    day = analyze_level("day", _dated(zigzag(pv)), zs_from="bi")

    rng2 = random.Random(7)
    pv2 = [100.0]
    for i in range(480):
        step = rng2.uniform(2, 12) * rng2.uniform(0.5, 1.5)
        pv2.append(max(pv2[-1] + (step if i % 2 == 0 else -step), 10.0))
    m30 = analyze_level("30min", _intraday(zigzag(pv2)), zs_from="bi")

    assert day.ok and m30.ok and day.points and m30.points

    # 默认出发点（最后一个点）这里恰好收缩不下去，窗口内只有反向点。这本身
    # 是条真实的路径，但别让它顶替「收缩成功」那条——两条都要走到。
    n = nest([day, m30])
    assert n.anchor.point is day.points[-1]
    assert n.agreement == CONFLICT and n.steps[1].against

    # 换个出发点，走通收缩那条路。逐个试而不是写死某一个：夹具一改，
    # 写死的那个点很可能就不再是能收缩的那个，测试会变成假通过。
    ok = [p for p in day.points
          if (st := _refine(m30, day.leg_start_ts(p), day.ts_of(p.bar),
                            want_buy=p.is_buy)).point is not None]
    assert ok, "夹具里没有一个日线点能在 30 分钟上收缩，这条测试验不到正向路径"

    n2 = nest([day, m30], anchor=ok[-1])
    lo, hi = n2.steps[1].window
    assert len(lo) == 10 and ":" in n2.precise.ts  # 日期粒度的窗口命中了分钟戳
    assert n2.precise.level == "30min"
    assert n2.precise.point in m30.points
    assert n2.precise.point.is_buy == ok[-1].is_buy
    assert _within(n2.precise.ts, lo, hi)


def test_level_trend_is_the_trailing_reading():
    """`LevelAnalysis.trend` 回答「这个级别**现在**什么走势」。

    原先用 `zhongshu.classify`，那是「一个**完成的**走势类型」的口径，在整段
    中枢序列上恒为 panzheng（实测 15 只票 × 日线/30分/5分全中）。多级别联立
    的整套判断——大级别定方向、小级别定时机——建立在这个字段上，它恒为常数
    就等于「以大级别为准」永远无据可依。
    """
    from conftest import trending_tail_bars

    from chan.forward import trailing_trend
    from chan.zhongshu import classify

    a = analyze_level("day", trending_tail_bars(), zs_from="bi")
    assert a.ok
    assert classify(list(a.zhongshus)) == "panzheng", "夹具得是两个口径分叉的形状"
    assert a.trend == trailing_trend(list(a.zhongshus))[0] == "down"
