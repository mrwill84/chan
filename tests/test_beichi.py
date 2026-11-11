"""背驰。37课 p96 的结构门槛 + 24课 p53 的量化测量。

本文件的夹具 `TREND_UP` 是一串手工价格，跑出来正好是**两个同级别中枢构成
上涨趋势**的标准 `a+A+b+B+c` 形态（下面注释里标了每一段的归属）。这是
37课 p96 讨论背驰时唯一认可的结构，所以值得固定下来反复用。
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from conftest import mk_bis

from chan.beichi import (
    AREA_RATIO,
    PANZHENG,
    TREND,
    _gate_trend,
    _slice_enter,
    _slice_leave,
    detect,
    detect_all,
    detect_at,
    span_of,
)
from chan.macd import Macd
from chan.types import Direction
from chan.zhongshu import build_zhongshus, legs_from_bis, relation

# 跑出来的结构（已用 build_zhongshus 核对过，见下面的 test_fixture_is_the_textbook_shape）：
#
#   legs 0-3   中枢 A   [ZD 12, ZG 20]   包络 DD 10 / GG 22
#   leg  4     b 段     13 → 50          进入 B 的那一段
#   legs 5-9   中枢 B   [ZD 41, ZG 48]   包络 DD 40 / GG 49
#   legs 10-13 c 段     42 → 80          离开 B，创新高
#
# 37课 p96 的 a+A+b+B+c 里，a 在这串价格之前（夹具不含），所以第 0 个中枢
# 取不到进入段——这正是 `detect_at(k=0)` 返回 None 的原因。
#
# 两个中枢的包络在这里恰好演示了 C-enter 的两侧：中枢 A 没有进入段，GG/DD 原样
# 取全部 Zn；中枢 B 的首 Zn 是 leg5（50→40），它的高点 50 就是 b 段拉上来的
# 终点，不是「围绕 B 震荡」产生的，所以 GG 取 49（leg8 的 49）而不是 50。
TREND_UP = [10, 20, 12, 22, 13, 50, 40, 48, 41, 49, 42, 70, 60, 80, 68]

B_BARS = (16, 20)  # b 段在原始K线上的窗口
C_BARS = (40, 56)  # c 段
N_BARS = 60


@pytest.fixture
def ctx():
    bis = mk_bis([float(x) for x in TREND_UP])
    legs = legs_from_bis(bis)
    return bis, legs, build_zhongshus(legs)


def mk_macd(hist=(), dif=(), n: int = N_BARS) -> Macd:
    """按 (lo, hi, value) 区间填出一条假 MACD。闭区间，和 `hist_area` 一致。"""
    h, d = np.zeros(n), np.zeros(n)
    for lo, hi, v in hist:
        h[lo : hi + 1] = v
    for lo, hi, v in dif:
        d[lo : hi + 1] = v
    return Macd(dif=d, dea=np.zeros(n), hist=h)


# b 段强、c 段弱 —— 24课 p53 说的「C 段对应的红柱子面积明显小于 A 段的」
FADING = mk_macd(hist=[(*B_BARS, 10.0), (*C_BARS, 1.0)])
# c 段比 b 段还猛，量价同步，不是背驰
ACCELERATING = mk_macd(hist=[(*B_BARS, 10.0), (*C_BARS, 20.0)])


# --------------------------------------------------------------------------
# 夹具本身的形状，先钉死
# --------------------------------------------------------------------------


def test_fixture_is_the_textbook_shape(ctx):
    """两个中枢 + 中心定理二判定为上涨趋势。门槛测试全建立在这个前提上。"""
    _, legs, zss = ctx
    assert len(zss) == 2
    assert (zss[0].start_leg, zss[0].end_leg) == (0, 3)
    assert (zss[1].start_leg, zss[1].end_leg) == (5, 9)
    assert (zss[1].zd, zss[1].zg) == (41.0, 48.0)
    # 中枢 A 起于 leg0，没有进入段，GG/DD 取全部 Zn
    assert (zss[0].dd, zss[0].gg) == (10.0, 22.0)
    # 中枢 B 的首 Zn 是 leg5（50→40），高点 50 是 b 段的终点，不进 GG（C-enter）
    assert (zss[1].dd, zss[1].gg) == (40.0, 49.0)
    assert max(legs[i].high for i in (5, 7, 9)) == 50.0  # 不剔的话就是这个数
    # 18课 p39：趋势判定用包络。后 DD 40 > 前 GG 22
    assert relation(zss[0], zss[1]) == "up"
    assert len(legs) == 14


# --------------------------------------------------------------------------
# Span：leg 范围 → 原始K线窗口
# --------------------------------------------------------------------------


def test_span_maps_legs_to_bars_and_keeps_true_extremes(ctx):
    """MACD 在原始K线上算，窗口必须落到K线下标；极值取构成笔的真实高低点。"""
    bis, legs, _ = ctx
    s = span_of(legs, bis, 10, 13)
    assert (s.bar_start, s.bar_end) == C_BARS
    assert (s.low, s.high) == (42.0, 80.0)  # 不是两端点 42 → 68
    assert s.direction is Direction.UP
    assert s.leg_count == 4
    assert s.extreme(Direction.UP) == 80.0
    assert s.extreme(Direction.DOWN) == 42.0


def test_span_of_rejects_impossible_ranges(ctx):
    bis, legs, _ = ctx
    assert span_of(legs, bis, -1, -1) is None
    assert span_of(legs, bis, 0, 99) is None
    assert span_of(legs, bis, 5, 3) is None


def test_slices_put_b_between_the_two_zhongshus(ctx):
    """33课 p79：「两个中枢与连接两者的一个走势」。b 既不属于 A 也不属于 B。"""
    _, legs, zss = ctx
    assert _slice_enter(zss, 1) == (4, 4)
    assert _slice_leave(zss, 0, len(legs)) == (4, 4)  # A 的 c 就是 B 的 b
    assert _slice_leave(zss, 1, len(legs)) == (10, 13)


def test_first_zhongshu_has_no_entering_span(ctx):
    """a 段在数据之外时取不到 b，判不了——返回 None 而不是瞎猜。"""
    bis, legs, zss = ctx
    assert _slice_enter(zss, 0) == (-1, -1)
    assert detect_at(legs, zss, bis, FADING, 0) is None


# --------------------------------------------------------------------------
# 结构门槛。37课 p96：「没有趋势，没有背驰」
# --------------------------------------------------------------------------


def _spans(ctx, enter=(4, 4), leave=(10, 13)):
    bis, legs, zss = ctx
    return span_of(legs, bis, *enter), span_of(legs, bis, *leave)


def test_all_five_gates_pass_on_the_textbook_shape(ctx):
    _, legs, zss = ctx
    enter, leave = _spans(ctx)
    ok, why = _gate_trend(zss, 1, legs, enter, leave, Direction.UP)
    assert ok, why
    assert any("趋势" in r for r in why)


def test_gate_rejects_a_non_trend_relation(ctx):
    """A、B 关系不是趋势 → 只能算盘整背驰，哪怕 MACD 缩得再厉害。"""
    _, legs, zss = ctx
    enter, leave = _spans(ctx)
    # 把 A 的包络撑大，中心定理二就落到「形成高级别中枢」而不是上涨
    doctored = [replace(zss[0], dd=0.0, gg=100.0), zss[1]]
    assert relation(doctored[0], doctored[1]) != "up"
    ok, why = _gate_trend(doctored, 1, legs, enter, leave, Direction.UP)
    assert not ok
    assert any("不是趋势" in r for r in why)


def test_gate_rejects_zhongshus_that_should_be_read_one_level_up(ctx):
    """33课 p79：顶到延伸上限的中枢该升级别看，那就谈不上 A、B 同级别。"""
    _, legs, zss = ctx
    enter, leave = _spans(ctx)
    ok, why = _gate_trend([zss[0], replace(zss[1], level_up=True)], 1, legs, enter, leave, Direction.UP)
    assert not ok
    assert any("非同级别" in r for r in why)


def test_gate_rejects_a_b_segment_big_enough_to_hold_its_own_zhongshu(ctx):
    """37课 p95：「b 段一定不可以出现 30 分钟的中枢」。"""
    _, legs, zss = ctx
    fat_b, leave = _spans(ctx, enter=(2, 4))
    assert fat_b.leg_count == 3
    ok, why = _gate_trend(zss, 1, legs, fat_b, leave, Direction.UP)
    assert not ok
    assert any("自成同级别中枢" in r for r in why)


def test_gate_requires_c_to_make_a_new_high(ctx):
    """37课 p96：「如果 a+A+b+B+c 是上涨，c 一定要创出新高」。"""
    _, legs, zss = ctx
    enter, leave = _spans(ctx)
    weak = replace(leave, high=49.0)  # B 的包络 GG = 50
    ok, why = _gate_trend(zss, 1, legs, enter, weak, Direction.UP)
    assert not ok
    assert any("创新高" in r for r in why)


def test_gate_requires_a_third_buy_point_inside_c(ctx):
    """37课 p96：「c 至少包含对 B 的一个第三类买卖点」——离开之后还要有回试。"""
    _, legs, zss = ctx
    enter, bare = _spans(ctx, leave=(10, 10))
    assert bare.leg_count == 1  # 只有离开段，还没回试
    ok, why = _gate_trend(zss, 1, legs, enter, bare, Direction.UP)
    assert not ok
    assert any("第三类买卖点" in r for r in why)


def test_third_point_fails_when_the_pullback_falls_back_into_the_zhongshu(ctx):
    """20课 p43：回试「不跌破 ZG」才算三买。跌回中枢里就不是。"""
    from chan.beichi import _third_point_against

    _, legs, zss = ctx
    assert _third_point_against(zss[1], legs, 10, 13, Direction.UP)
    # 把回试段压回 [ZD 41, ZG 48] 之内
    sunk = list(legs)
    sunk[11] = replace(legs[11], low=45.0)
    assert not _third_point_against(zss[1], sunk, 10, 13, Direction.UP)


# --------------------------------------------------------------------------
# 量化测量。24课 p53 面积 + 27课 p66 黄白线
# --------------------------------------------------------------------------

# 黄白线在 b 与 c 之间回抽到 0 轴以下，且 c 的 DIF 高点低于 b 的
DIF_FADES = [(16, 20, 5.0), (25, 30, -1.0), (40, 56, 3.0)]
# 同样回抽过 0 轴，但 c 的 DIF 更高——量价同步
DIF_GROWS = [(16, 20, 5.0), (25, 30, -1.0), (40, 56, 9.0)]
# 全程不回 0 轴：两段还在同一波里
DIF_NEVER_CROSSES = [(16, 20, 5.0), (21, 39, 2.0), (40, 56, 3.0)]

BOTH = mk_macd(hist=[(*B_BARS, 10.0), (*C_BARS, 1.0)], dif=DIF_FADES)
ONLY_DIF = mk_macd(hist=[(*B_BARS, 1.0), (*C_BARS, 10.0)], dif=DIF_FADES)
NEITHER = mk_macd(hist=[(*B_BARS, 10.0), (*C_BARS, 20.0)], dif=DIF_GROWS)


def test_shrinking_area_is_a_backchi(ctx):
    """24课 p53：「其对应的 MACD 红柱子面积明显小于 A 段的」。"""
    bis, legs, zss = ctx
    b = detect_at(legs, zss, bis, FADING, 1)
    assert b.kind == TREND
    assert b.direction is Direction.UP and b.is_top
    assert b.area_enter == pytest.approx(50.0)  # 5 根 × 10
    assert b.area_leave == pytest.approx(17.0)  # 17 根 × 1
    assert b.area_ok and b.hit


def test_growing_area_is_not_a_backchi(ctx):
    bis, legs, zss = ctx
    b = detect_at(legs, zss, bis, NEITHER, 1)
    assert not b.area_ok and not b.dif_ok and not b.hit


def test_dif_criterion_needs_a_pullback_through_the_zero_axis(ctx):
    """27课 p66：先「黄白线回拉 0 轴上下」，才谈得上比高低。"""
    bis, legs, zss = ctx
    stuck = mk_macd(hist=[(*B_BARS, 10.0), (*C_BARS, 20.0)], dif=DIF_NEVER_CROSSES)
    b = detect_at(legs, zss, bis, stuck, 1)
    assert not b.dif_crossed_zero
    assert not b.dif_ok
    assert any("没回抽 0 轴" in r for r in b.reasons)


def test_either_criterion_alone_is_a_signal_both_is_standard(ctx):
    """27课 p66：两个判据满足其一即信号，都满足更标准。"""
    bis, legs, zss = ctx
    only_area = detect_at(legs, zss, bis, FADING, 1)
    only_dif = detect_at(legs, zss, bis, ONLY_DIF, 1)
    both = detect_at(legs, zss, bis, BOTH, 1)

    assert (only_area.area_ok, only_area.dif_ok) == (True, False)
    assert (only_dif.area_ok, only_dif.dif_ok) == (False, True)
    assert all(x.hit for x in (only_area, only_dif, both))
    assert not only_area.standard and not only_dif.standard
    assert both.standard


def test_area_ratio_knob_tightens_the_comparison(ctx):
    """C14：原文只说「明显小于」，没给容差。调小 ratio 等于要求缩量更明显。"""
    bis, legs, zss = ctx
    assert detect_at(legs, zss, bis, FADING, 1, area_ratio=AREA_RATIO).area_ok
    # 17 < 50 * 0.3 = 15 不成立
    assert not detect_at(legs, zss, bis, FADING, 1, area_ratio=0.3).area_ok


def test_reasons_record_the_area_numbers_even_when_it_passes(ctx):
    """理由要能拿去跟人解释，所以通过的门槛也记。"""
    bis, legs, zss = ctx
    b = detect_at(legs, zss, bis, FADING, 1)
    assert any("红柱面积" in r for r in b.reasons)
    assert any("24课 p53" in r for r in b.reasons)


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


def test_breaks_zs_and_new_extreme_flags(ctx):
    bis, legs, zss = ctx
    b = detect_at(legs, zss, bis, FADING, 1)
    assert b.breaks_zs  # c 的高点 80 远在 ZG 48 之上
    assert b.new_extreme  # 也在包络 GG 50 之上
    assert b.zs_index == 1


def test_single_zhongshu_degrades_to_panzheng(ctx):
    """27课 p66：「如果在第一个中枢就出现背驰……只能算是盘整背驰」。

    用 `build_zhongshus(start=5)` 单独取出 B，这时它前面没有可比的同级别中枢。
    """
    bis, legs, _ = ctx
    solo = build_zhongshus(legs, start=5)
    assert len(solo) == 1
    b = detect_at(legs, solo, bis, FADING, 0)
    assert b.kind == PANZHENG
    assert b.hit  # 盘整背驰也是信号，只是级别和含义不同
    assert any("第一个中枢" in r for r in b.reasons)


def test_detect_looks_at_the_last_zhongshu_only(ctx):
    """扫描时用的就是这个：当下这一刻只能对最后一个中枢做判断。"""
    bis, legs, zss = ctx
    assert detect(legs, zss, bis, FADING) == detect_at(legs, zss, bis, FADING, 1)


def test_detect_on_no_zhongshu_is_none(ctx):
    bis, legs, _ = ctx
    assert detect(legs, [], bis, FADING) is None


def test_detect_at_out_of_range_is_none(ctx):
    bis, legs, zss = ctx
    assert detect_at(legs, zss, bis, FADING, -1) is None
    assert detect_at(legs, zss, bis, FADING, 9) is None


def test_detect_all_skips_the_zhongshus_it_cannot_judge(ctx):
    """第 0 个中枢取不到进入段，直接跳过而不是塞个半成品进去。"""
    bis, legs, zss = ctx
    out = detect_all(legs, zss, bis, FADING)
    assert [x.zs_index for x in out] == [1]
