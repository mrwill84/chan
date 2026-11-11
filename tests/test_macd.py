"""MACD。24课 pp.52-53 的量化测量工具。"""

from __future__ import annotations

import numpy as np

from chan.macd import crosses_zero, dif_extreme, ema, hist_area, macd


def test_ema_of_a_constant_series_is_that_constant():
    assert np.allclose(ema(np.full(50, 7.0), 12), 7.0)


def test_ema_first_value_is_the_first_sample():
    """口径登记在 CONVENTIONS C-ema：首值取 x[0]，不是前 N 根的 SMA。"""
    x = np.array([10.0, 20.0, 30.0])
    out = ema(x, 2)  # alpha = 2/3
    assert out[0] == 10.0
    assert np.isclose(out[1], 2 / 3 * 20 + 1 / 3 * 10)


def test_ema_tracks_but_lags_a_rising_series():
    x = np.arange(100, dtype=float)
    out = ema(x, 12)
    assert (np.diff(out) > 0).all()
    assert (out[20:] < x[20:]).all()  # 上涨时均线在价格下方


def test_ema_of_empty_is_empty():
    assert ema(np.array([]), 12).size == 0


def test_macd_of_a_flat_series_is_all_zero():
    m = macd([5.0] * 60)
    assert np.allclose(m.dif, 0.0)
    assert np.allclose(m.dea, 0.0)
    assert np.allclose(m.hist, 0.0)


def test_dif_is_positive_in_an_uptrend_negative_in_a_downtrend():
    up = macd(list(np.linspace(10, 50, 120)))
    down = macd(list(np.linspace(50, 10, 120)))
    assert (up.dif[30:] > 0).all()
    assert (down.dif[30:] < 0).all()


def test_hist_is_twice_the_dif_dea_spread():
    """国内软件口径。系数不影响背驰比较，但要能和软件对上。"""
    m = macd(list(np.linspace(10, 50, 80)))
    assert np.allclose(m.hist, 2.0 * (m.dif - m.dea))


# --------------------------------------------------------------------------
# 面积：24课 p53 的核心度量
# --------------------------------------------------------------------------


def _m(hist: list[float]):
    from chan.macd import Macd

    a = np.array(hist, dtype=float)
    return Macd(dif=a, dea=np.zeros_like(a), hist=a)


def test_hist_area_only_sums_same_sign_bars():
    """上涨段比红柱、下跌段比绿柱。正负相加会把剧烈震荡抹平成 0。"""
    m = _m([3.0, -5.0, 2.0, -1.0])
    assert hist_area(m, 0, 3, positive=True) == 5.0
    assert hist_area(m, 0, 3, positive=False) == 6.0


def test_hist_area_returns_absolute_value_for_negative_bars():
    assert hist_area(_m([-2.0, -3.0]), 0, 1, positive=False) == 5.0


def test_hist_area_clamps_out_of_range_indices():
    m = _m([1.0, 2.0, 3.0])
    assert hist_area(m, -10, 99, positive=True) == 6.0
    assert hist_area(m, 2, 1, positive=True) == 0.0


def test_hist_area_shrinks_when_momentum_fades():
    """背驰的典型形态：同样创新高，但柱子面积变小。"""
    strong = _m([1.0, 4.0, 6.0, 4.0, 1.0])
    weak = _m([1.0, 2.0, 2.0, 1.0, 0.5])
    assert hist_area(weak, 0, 4, positive=True) < hist_area(strong, 0, 4, positive=True)


# --------------------------------------------------------------------------
# 黄白线：27课 p66 的第二判据
# --------------------------------------------------------------------------


def test_dif_extreme_picks_max_or_min_in_window():
    m = _m([1.0, 5.0, -3.0, 2.0])
    assert dif_extreme(m, 0, 3, want_max=True) == 5.0
    assert dif_extreme(m, 0, 3, want_max=False) == -3.0
    assert dif_extreme(m, 2, 3, want_max=True) == 2.0


def test_dif_extreme_of_empty_window_is_nan():
    assert np.isnan(dif_extreme(_m([1.0]), 5, 9, want_max=True))


def test_crosses_zero_needs_both_signs():
    assert crosses_zero(_m([-1.0, 0.5]), 0, 1)
    assert not crosses_zero(_m([1.0, 2.0]), 0, 1)
    assert not crosses_zero(_m([-1.0, -2.0]), 0, 1)


def test_crosses_zero_is_false_outside_the_window():
    """两段还在同一波里（没回抽 0 轴）时，黄白线判据不适用。"""
    m = _m([-1.0, 1.0, 2.0, 3.0])
    assert not crosses_zero(m, 1, 3)
    assert crosses_zero(m, 0, 3)
