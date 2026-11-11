"""指标自身的口径测试：重点是没有未来函数，而不是指标是否赚钱。"""
from __future__ import annotations

import numpy as np
from datetime import date, timedelta

from chan.indicators import _higher_keys, higher_bollinger, rsi, sma
from chan.types import Bar


def _bars(weeks: list[list[float]]) -> list[Bar]:
    out = []
    for w, closes in enumerate(weeks):
        for d, c in enumerate(closes):
            ts = (date(2024, 1, 1) + timedelta(weeks=w, days=d)).isoformat()
            out.append(Bar(len(out), ts, c, c, c, c))
    return out


def test_rsi_wilder_and_neutral_warmup():
    r = rsi(list(range(1, 20)))
    assert np.all(r.rsi[:14] == 50.0)
    assert np.all(r.rsi[14:] == 100.0)
    flat = rsi([10.0] * 20)
    assert np.all(flat.rsi == 50.0)
    # Wilder 初始 14 根平均，之后递推；这个例子手算可核对。
    x = [1, 2, 3, 2, 4, 5, 4, 6, 7, 6, 8, 9, 8, 10, 11]
    got = rsi(x).rsi[14]
    delta = np.diff(x)
    ag = np.maximum(delta, 0).mean()
    al = np.maximum(-delta, 0).mean()
    assert got == 100.0 - 100.0 / (1.0 + ag / al)


def test_sma_does_not_use_a_short_warmup_window():
    got = sma([1, 2, 3, 4], 3)
    assert np.isnan(got[:2]).all()
    assert got[2:].tolist() == [2.0, 3.0]


def test_weekly_boll_uses_only_the_partial_current_week():
    bars = _bars([[10, 11], [12, 13], [14, 15], [16, 30], [18, 19]])
    full = higher_bollinger(bars, n=3, k=2.0)
    # 第 3 周第 1 天：窗口是第1、2周收盘 + 当下16，不能偷看本周后面的30。
    t = 6
    expected = np.asarray([13.0, 15.0, 16.0])
    assert full.mid[t] == expected.mean()
    assert full.upper[t] == expected.mean() + 2 * expected.std()
    prefix = higher_bollinger(bars[:t + 1], n=3, k=2.0)
    for name in ("mid", "upper", "lower", "bandwidth", "squeeze"):
        a, b = getattr(full, name), getattr(prefix, name)
        if a.dtype == bool:
            assert np.array_equal(a[:t + 1], b)
        else:
            assert np.allclose(a[:t + 1], b, equal_nan=True)


def _five(day, times):
    return [Bar(i, f"{day} {t}", 1, 1, 1, 1) for i, t in enumerate(times)]


def test_five_minute_groups_follow_the_clock_not_the_index():
    """真实的5分K线（时间戳是结束时刻）。旧测试用的是 09:30、09:31…… 1分钟
    间隔、又从对齐的位置开始，正好藏住了「按下标每6根一组」的错位。"""
    # 从盘中 10:15 开始：10:15~10:30 归 10:30 那根，10:35~11:00 归 11:00
    times = ["10:15", "10:20", "10:25", "10:30", "10:35", "10:40", "10:45",
             "10:50", "10:55", "11:00", "11:05"]
    keys = _higher_keys(_five("2024-01-02", times))
    assert len(set(keys[:4])) == 1
    assert len(set(keys[4:10])) == 1
    assert keys[3] != keys[4] and keys[9] != keys[10]


def test_five_minute_groups_survive_a_missing_bar_and_lunch_break():
    # 10:40 缺一根；11:30 与 13:05 之间是午休
    times = ["10:35", "10:45", "10:50", "10:55", "11:00",
             "11:05", "11:10", "11:15", "11:20", "11:25", "11:30",
             "13:05", "13:10"]
    keys = _higher_keys(_five("2024-01-02", times))
    assert len(set(keys[:5])) == 1, "缺一根不能让同一根30分被劈开"
    assert len(set(keys[5:11])) == 1
    assert keys[10] != keys[11], "11:30 和 13:05 不是同一根30分"


def test_higher_level_mapping_follows_the_microscope_not_always_weekly():
    thirty = [Bar(0, "2024-01-02 14:00", 1, 1, 1, 1),
              Bar(1, "2024-01-02 14:30", 1, 1, 1, 1),
              Bar(2, "2024-01-02 15:00", 1, 1, 1, 1),
              Bar(3, "2024-01-03 09:30", 1, 1, 1, 1)]
    keys = _higher_keys(thirty)
    assert keys[0] == keys[2] and keys[2] != keys[3]


def test_squeeze_requires_narrowing_and_a_lookback_minimum():
    bars = _bars([[10, 11], [12, 20], [14, 15], [16, 16.1], [17, 17.1]])
    b = higher_bollinger(bars, n=3, lookback=3)
    final_closes = [11.0, 20.0, 15.0, 16.1, 17.1]

    def final_bw(w: int) -> float:
        x = np.asarray(final_closes[w - 2:w + 1])
        return 4 * x.std() / x.mean()

    for t in np.flatnonzero(b.squeeze):
        w = t // 2
        old = [final_bw(i) for i in range(w - 2, w)]
        assert b.bandwidth[t] < final_bw(w - 1)
        assert b.bandwidth[t] <= min(old)
    assert b.squeeze.dtype == bool
