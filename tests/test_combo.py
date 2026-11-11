"""组合配对的时间因果：提醒必须在结构信号当时已经存在且未失效。"""
from backtest.combo import BollAlert, attach_alerts, evaluate_combo
from backtest.walk import Sighting
from conftest import zigzag


def _sighting(t, side="buy", bar=None):
    bar = t - 5 if bar is None else bar
    return Sighting(kind=3, side=side, label="3买" if side == "buy" else "3卖",
                    bar=bar, ts=f"T{bar}", price=100.0,
                    first_seen=t, last_seen=t)


def _alert(seen, side="buy"):
    a = BollAlert(side, 8, 90.0, 2, seen[0])
    a.seen = list(seen)
    return a


def test_future_alert_sightings_cannot_freshen_a_past_structure_signal():
    a = _alert([10, 90])
    assert attach_alerts([_sighting(85)], [a], window=60) == []
    pairs = attach_alerts([_sighting(95)], [a], window=60)
    assert len(pairs) == 1 and pairs[0].last_alert == 90
    assert pairs[0].notice == 85


def test_invalidation_and_direction_are_checked_at_the_signal_time():
    a = _alert([10], side="sell")
    a.invalidated_at = 40
    assert attach_alerts([_sighting(39, side="sell")], [a], window=60)
    assert attach_alerts([_sighting(40, side="sell")], [a], window=60) == []
    assert attach_alerts([_sighting(39, side="buy")], [a], window=60) == []


def test_one_alert_episode_confirms_at_most_one_structure_point():
    a = _alert([10, 20])
    points = [_sighting(25), _sighting(30)]
    pairs = attach_alerts(points, [a], window=60)
    assert len(pairs) == 1 and pairs[0].sighting.first_seen == 25


def test_window_is_days_since_the_last_actual_alert_not_the_first():
    a = _alert([10, 45])
    assert attach_alerts([_sighting(66)], [a], window=20) == []
    assert len(attach_alerts([_sighting(65)], [a], window=20)) == 1


def test_alert_walk_cannot_see_future_bars():
    """投毒未来K线后，cut 之前的提醒 episode 必须逐字段不变。"""
    from pathlib import Path
    import pytest
    from feeds import Level, cache
    from backtest.combo import walk_boll_alerts
    from chan.types import Bar

    if not Path(cache.DEFAULT_ROOT, cache.DEFAULT_SOURCE, "day", "300308.csv").exists():
        pytest.skip("没有300308缓存")
    bars = cache.load("300308", Level.DAY)[:900]
    cut = 600
    poisoned = list(bars[:cut + 1]) + [
        Bar(b.idx, b.ts, 1e6, 1e6, 1e6, 1e6) for b in bars[cut + 1:]]
    full = walk_boll_alerts(bars, step=8, verify=1)
    poison = walk_boll_alerts(poisoned, step=8, verify=1)

    def at_cut(alerts):
        return {(a.side, a.candidate_bar, a.segment_start, a.first_seen):
                ([x for x in a.seen if x <= cut],
                 a.invalidated_at if a.invalidated_at is not None
                 and a.invalidated_at <= cut else None)
                for a in alerts if a.first_seen <= cut}

    assert at_cut(full) == at_cut(poison)


def test_empty_combo_is_a_valid_result_not_an_exception():
    bars = zigzag([100, 110, 95, 120, 90, 115])
    out = evaluate_combo(bars, [], [], windows=(60,))
    assert out["n_structure"] == 0
    assert out["windows"]["60"]["matched"] == 0
