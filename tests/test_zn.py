"""92课 Zn 监视器：中轴、越界提醒、外推的方向约束。"""
from dataclasses import replace

from chan.types import Direction, Leg, ZhongShu
from chan.zn import forecast_next, zn_series


def _leg(i, direction, low, high, end, confirmed=True):
    return Leg(i, direction, low if direction is Direction.UP else high,
               end, low, high, i, i, confirmed)


def _fixture():
    legs = [
        _leg(0, Direction.UP, 10, 20, 20),
        _leg(1, Direction.DOWN, 11, 21, 11),
        _leg(2, Direction.UP, 19, 23, 23),
        _leg(3, Direction.DOWN, 13, 19, 13),
        _leg(4, Direction.UP, 14, 22, 22),
    ]
    zs = ZhongShu(index=0, direction=Direction.UP, start_leg=0, end_leg=4,
                  zg=20, zd=10, gg=23, dd=10, g=20, d=23)
    return zs, legs


def test_zn_strength_and_crossing_are_different_questions():
    zs, legs = _fixture()
    got = zn_series(zs, legs)
    assert (zs.zd + zs.zg) / 2 == 15
    assert got[2].zn == 21
    assert got[2].strength == "above_z"
    assert got[2].position == "above_b" and got[2].crossed
    assert got[3].zn == 16 and got[3].position == "inside"


def test_linear_forecast_anchors_at_the_known_shared_endpoint():
    zs, legs = _fixture()
    f = forecast_next(zs, legs)
    assert f is not None
    # 最近 Zn=16,18 → 下一个20；当前向上段终点22，下一段向下，
    # 所以另一个端点是 2*20-22=18。
    assert f.direction is Direction.DOWN
    assert (f.near, f.zn, f.far, f.low, f.high) == (22, 20, 18, 18, 22)


def test_forecast_abstains_when_extrapolation_reverses_the_leg():
    zs, legs = _fixture()
    legs[3] = _leg(3, Direction.DOWN, 0, 0, 0)
    assert forecast_next(zs, legs) is None
    zs.depart_leg = 4
    assert forecast_next(zs, legs) is None


def test_unconfirmed_tail_is_not_used_by_default():
    zs, legs = _fixture()
    legs[-1] = replace(legs[-1], confirmed=False)
    assert len(zn_series(zs, legs)) == 4
    assert len(zn_series(zs, legs, require_confirmed=False)) == 5
