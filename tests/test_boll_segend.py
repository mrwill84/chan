"""90课 BOLL 三种段结束口径的行为夹具。"""
from datetime import date, timedelta

import numpy as np

import chan.segend as se
from chan.indicators import Bollinger
from chan.types import Bar, Direction, Seg
from conftest import mk_bis


def _make(prices):
    bis = mk_bis(prices, m_step=12)
    closes = [bis[0].start_price]
    for b in bis:
        a, z = b.start.peak_idx, b.end.peak_idx
        for i in range(a + 1, z + 1):
            closes.append(b.start_price + (b.end_price - b.start_price)
                          * (i - a) / (z - a))
    start = date(2020, 1, 6)
    bars = [Bar(i, (start + timedelta(days=i)).isoformat(), c, c, c, c)
            for i, c in enumerate(closes)]
    tail = Seg(index=0, start_bi=0, end_bi=len(bis) - 1,
               direction=Direction.UP, start=bis[0].start, end=bis[-1].end,
               confirmed=False)
    return bis, bars, tail


def _boll(n, upper, squeeze=False):
    upper = np.broadcast_to(np.asarray(upper, dtype=float), (n,)).copy()
    sq = np.zeros(n, dtype=bool)
    sq[-1] = squeeze
    return Bollinger(upper - 5, upper, upper - 10,
                     np.full(n, .1), sq)


def test_boll_first_requires_new_extreme_without_reclaiming_strong_zone(monkeypatch):
    bis, bars, tail = _make([10, 40, 25, 52, 42, 58, 52, 60.5,
                             56, 61.5, 58.5, 62, 60.5, 62.5])
    last, prev = 12, 10
    l0 = bis[last].start.peak_idx
    upper = np.full(len(bars), 61.5)
    upper[l0:] = 63.0       # 本次新高，但收盘始终回不到超强区
    monkeypatch.setattr(se, "higher_bollinger", lambda _: _boll(len(bars), upper))
    assert se._m_boll_first(bis, bars, tail) == last
    upper[l0:] = 60.0       # 又回到超强区，1类辅助条件消失
    monkeypatch.setattr(se, "higher_bollinger", lambda _: _boll(len(bars), upper))
    assert se._m_boll_first(bis, bars, tail) is None


def test_boll_first_through_the_gate_waits_for_the_new_high_to_finish(monkeypatch):
    """上一条直接调 `_m_boll_first`，绕过了 `_legalize`——那里的 `last=12`
    正是还在走的最后一笔，走完整流程旧实现会把它挪到更低的第 10 笔。"""
    from chan.macd import macd

    prices = [10, 40, 25, 52, 42, 58, 52, 60.5, 56, 61.5, 58.5, 62, 60.5, 62.5]
    for extra, want in (([], None), ([61.0], 12)):
        bis, bars, tail = _make(prices + extra)
        l0 = bis[12].start.peak_idx
        upper = np.full(len(bars), 61.5)
        upper[l0:] = 63.0
        monkeypatch.setattr(se, "higher_bollinger",
                            lambda _, u=upper: _boll(len(u), u))
        m = macd([b.close for b in bars])
        assert se.provisional_end(bis, m, tail, "boll_first", bars) == want


def test_boll_second_waits_for_band_turn_and_a_lower_retest(monkeypatch):
    bis, bars, tail = _make([10, 40, 25, 52, 42, 58, 52, 60, 50, 55])
    last, prev = 8, 6
    p1, l0, l1 = (bis[prev].end.peak_idx, bis[last].start.peak_idx,
                  bis[last].end.peak_idx)
    upper = np.full(len(bars), 59.0)
    upper[l0:] = 56.0       # 上轨转向；55 的回升高点在轨内
    monkeypatch.setattr(se, "higher_bollinger", lambda _: _boll(len(bars), upper))
    assert se._m_boll_second(bis, bars, tail) == prev
    upper[l0:] = 60.0       # 轨道没有转向
    monkeypatch.setattr(se, "higher_bollinger", lambda _: _boll(len(bars), upper))
    assert se._m_boll_second(bis, bars, tail) is None


def test_boll_squeeze_locates_existing_extreme_not_current_unfinished_leg(monkeypatch):
    bis, bars, tail = _make([10, 40, 25, 52, 42, 58, 52, 60, 50, 55])
    monkeypatch.setattr(se, "higher_bollinger",
                        lambda _: _boll(len(bars), 56.0, squeeze=True))
    assert se._m_boll_squeeze(bis, bars, tail) == 6
    monkeypatch.setattr(se, "higher_bollinger",
                        lambda _: _boll(len(bars), 56.0, squeeze=False))
    assert se._m_boll_squeeze(bis, bars, tail) is None
