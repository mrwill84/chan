"""分型。对照 62课 p161（定义）与 82课 pp.211-212（强弱标注）。"""

from __future__ import annotations

from conftest import mk_bars

from chan.fractal import annotate_strength, find_fractals
from chan.merge import merge_bars
from chan.types import FractalKind


def test_top_fractal():
    m = merge_bars(mk_bars([(1, 3), (2, 5), (1.5, 4)]))
    fs = find_fractals(m)
    assert len(fs) == 1
    assert fs[0].kind is FractalKind.TOP
    assert fs[0].m_idx == 1
    assert fs[0].price == 5


def test_bottom_fractal():
    m = merge_bars(mk_bars([(4, 6), (1, 3), (2, 5)]))
    fs = find_fractals(m)
    assert len(fs) == 1
    assert fs[0].kind is FractalKind.BOTTOM
    assert fs[0].price == 1


def test_monotonic_has_no_fractal():
    m = merge_bars(mk_bars([(1, 2), (2, 3), (3, 4), (4, 5), (5, 6)]))
    assert find_fractals(m) == []


def test_fractal_is_price_only_not_candle_color():
    """62课 p161：分型只看高低点，与阴阳、开收盘价完全无关。

    同一组高低点，把K线全改成阴线，分型结果必须一字不差。
    """
    ranges = [(1, 3), (2, 5), (1.5, 4)]
    m = merge_bars(mk_bars(ranges))
    bullish = find_fractals(m)
    bearish = find_fractals(merge_bars(mk_bars(ranges, ohlc=[(3, 1), (5, 2), (4, 1.5)])))
    assert [(f.kind, f.m_idx, f.price) for f in bullish] == [
        (f.kind, f.m_idx, f.price) for f in bearish
    ]


def test_fractal_spans_raw_bar_indices():
    m = merge_bars(mk_bars([(1, 3), (2, 5), (1.5, 4)]))
    f = find_fractals(m)[0]
    assert (f.begin_idx, f.end_idx) == (0, 2)
    assert f.peak_idx == 1


def test_zigzag_yields_alternating_fractals():
    from conftest import zigzag

    m = merge_bars(zigzag([10, 20, 12, 25, 15], leg_bars=4))
    kinds = [f.kind for f in find_fractals(m)]
    assert kinds == [
        FractalKind.TOP,
        FractalKind.BOTTOM,
        FractalKind.TOP,
    ]


# --------------------------------------------------------------------------
# 82课 强弱标注
# --------------------------------------------------------------------------


def test_strength_weakest_top():
    """82课 p212：「第三根K线跌破第一根K线的底而且不能高收到第一根K线区间
    的一半之上，属于最弱的一种」。

    k1=[1,3] 中点 2；k3 低点 0.5 跌破 k1 的底 1，收在 1.0 低于中点 2。
    """
    bars = mk_bars(
        [(1, 3), (2, 5), (0.5, 4)],
        ohlc=[(1, 3), (2, 5), (4, 1.0)],
    )
    m = merge_bars(bars)
    fs = find_fractals(m)
    annotate_strength(fs, m, bars)
    assert fs[0].kind is FractalKind.TOP
    assert fs[0].strength == "weakest"


def test_strength_weakest_bottom_mirrors():
    """82课 p211：「而底分型的情况，反过来就是」。"""
    bars = mk_bars(
        [(2, 4), (0, 3), (1, 4.5)],
        ohlc=[(4, 2), (3, 0), (1, 4.0)],
    )
    m = merge_bars(bars)
    fs = find_fractals(m)
    annotate_strength(fs, m, bars)
    assert fs[0].kind is FractalKind.BOTTOM
    assert fs[0].strength == "weakest"


def test_strength_marks_inclusion():
    """「最弱的一种」原文明确限定「非包含关系处理后的」，三根里只要有一根
    是合并出来的就不判强弱，只标 inclusive。"""
    bars = mk_bars([(0, 0.5), (1, 3), (2, 5), (3, 4), (0.5, 2)])
    m = merge_bars(bars)
    fs = find_fractals(m)
    annotate_strength(fs, m, bars)
    assert len(fs) == 1
    assert m[fs[0].m_idx].is_merged
    assert fs[0].strength == "inclusive"


def test_strength_never_filters():
    """强弱标注只写字段，绝不删分型——它依赖开收盘价，而 62课 的定义
    明确把开收盘价排除在外。"""
    bars = mk_bars([(1, 3), (2, 5), (1.5, 4)])
    m = merge_bars(bars)
    fs = find_fractals(m)
    before = [(f.kind, f.m_idx) for f in fs]
    annotate_strength(fs, m, bars)
    assert [(f.kind, f.m_idx) for f in fs] == before
