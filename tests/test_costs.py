"""交易摩擦：费率算术、印花税换挡、涨跌停封板的顺延与放弃。"""
import pytest

from backtest.costs import (Costs, MAX_ENTRY_DEFER, limit_pct, locked_down,
                            locked_up, net_long, simulate_long)
from chan.types import Bar


def _bar(i, close, high=None, low=None, ts=None):
    return Bar(i, ts or f"2024-01-{i + 1:02d}", close,
               high if high is not None else close,
               low if low is not None else close, close)


def test_round_trip_cost_matches_hand_arithmetic():
    c = Costs()
    # 价格不变：只剩成本。买 100.1×1.00026，卖 99.9×(1−0.00026−0.0005)
    got = net_long(100.0, 100.0, "2024-01-02", c)
    want = 99.9 * (1 - 0.00026 - 0.0005) / (100.1 * 1.00026) - 1
    assert got == pytest.approx(want)
    # 约 0.2% 滑点 + 0.052% 佣金过户 + 0.05% 印花税 ≈ 0.30%
    assert got == pytest.approx(-0.00302, abs=1e-5)


def test_stamp_duty_halves_on_2023_08_28_and_only_on_the_sell_side():
    c = Costs(slippage=0.0, commission=0.0, transfer=0.0)
    assert net_long(100, 100, "2023-08-25", c) == pytest.approx(-0.001)
    assert net_long(100, 100, "2023-08-28", c) == pytest.approx(-0.0005)


def test_board_limits_follow_code_and_chinext_reform_date():
    assert limit_pct("600519", "2024-01-02") == 0.10
    assert limit_pct("300308", "2020-08-21") == 0.10
    assert limit_pct("300308", "2020-08-24") == 0.20
    assert limit_pct("688981", "2019-08-01") == 0.20


def test_lock_needs_both_the_limit_move_and_closing_at_the_extreme():
    bars = [_bar(0, 10.0), _bar(1, 11.0), _bar(2, 12.1, high=12.2)]
    assert locked_up(bars, 1, "600000")            # +10%，收在最高
    assert not locked_up(bars, 2, "600000")        # +10% 但盘中开板、没收在最高
    assert not locked_up(bars, 1, "300308")        # 创业板（2024）是 20%
    down = [_bar(0, 10.0), _bar(1, 9.0)]
    assert locked_down(down, 1, "600000")


def test_entry_defers_past_a_limit_up_close_and_horizon_starts_from_real_entry():
    closes = [10.0, 11.0, 11.5] + [11.5] * 30
    bars = [_bar(i, p, ts=f"2024-02-{i + 1:02d}" if i < 28 else f"2024-03-{i - 27:02d}")
            for i, p in enumerate(closes)]
    t = simulate_long(bars, 1, 5, "600000", Costs())
    assert t.entry_bar == 2 and t.exit_bar == 7


def test_entry_gives_up_after_max_deferral_instead_of_buying_late_forever():
    closes = [10.0 * 1.1 ** i for i in range(MAX_ENTRY_DEFER + 3)] + [50.0] * 10
    bars = [_bar(i, p, ts=f"2024-01-{i + 1:02d}") for i, p in enumerate(closes)]
    assert simulate_long(bars, 1, 3, "600000", Costs()) == "entry_locked"


def test_exit_defers_past_a_limit_down_close():
    closes = [10.0, 10.0, 10.0, 9.0, 8.5, 8.5]
    bars = [_bar(i, p) for i, p in enumerate(closes)]
    t = simulate_long(bars, 1, 2, "600000", Costs())
    assert t.exit_bar == 4                      # 第3根封跌停，顺延到第4根
    assert t.gross == pytest.approx(-0.15)


def _m30(day, hm, close, high=None, low=None):
    return Bar(0, f"{day} {hm}:00", close, high if high is not None else close,
               low if low is not None else close, close)


def test_intraday_limit_compares_with_previous_day_close_not_previous_bar():
    from backtest.costs import prev_day_close

    bars = [_m30("2024-01-02", "15:00", 10.0),
            _m30("2024-01-03", "10:00", 10.5, high=10.6),
            _m30("2024-01-03", "10:30", 11.0)]          # 对前日收盘 +10%，收在最高
    bars = [Bar(i, b.ts, b.open, b.high, b.low, b.close) for i, b in enumerate(bars)]
    ref = prev_day_close(bars)
    assert ref == [None, 10.0, 10.0]
    assert locked_up(bars, 2, "600000", ref)          # 日内口径：封板
    assert not locked_up(bars, 2, "600000")           # 按上一根只涨 4.8%，会漏判


def test_intraday_t_plus_one_pushes_a_same_day_exit_to_the_next_day():
    times = ["10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"]
    raw = [_m30("2024-01-03", t, 10.0) for t in times] + \
          [_m30("2024-01-04", t, 10.2) for t in times]
    bars = [Bar(i, b.ts, b.open, b.high, b.low, b.close) for i, b in enumerate(raw)]
    t = simulate_long(bars, 1, 3, "600000", Costs(), intraday=True)
    assert bars[t.exit_bar].ts.startswith("2024-01-04"), "当天买的当天不能卖"
    assert t.exit_bar == 8


def test_short_history_is_reported_not_silently_dropped():
    bars = [_bar(i, 10.0) for i in range(5)]
    assert simulate_long(bars, 2, 10, "600000", Costs()) == "no_future"
