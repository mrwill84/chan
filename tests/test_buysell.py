"""123 类买卖点。20课 p43 + 21课 p45 + 53课 pp.126-127。

沿用 `test_beichi.py` 的那串价格——它是 37课 p96 的标准 `a+A+b+B+c` 上涨趋势，
所以顶背驰、1卖、2卖、3买都能在同一个夹具上验证，省得每条规则各造一套数据。
"""

from __future__ import annotations

import random
from dataclasses import replace

import numpy as np
import pytest
from conftest import mk_bis, zigzag
from test_beichi import FADING, N_BARS, TREND_UP, mk_macd

from chan import analyze, macd
from chan.buysell import (
    FIRST,
    SECOND,
    THIRD,
    BSPoint,
    _extreme_leg,
    _third_at,
    check_bspoints,
    find_bspoints,
    latest,
)
from chan.invariants import InvariantError
from chan.macd import Macd
from chan.types import Bar, Direction
from chan.zhongshu import build_zhongshus, legs_from_bis


@pytest.fixture
def ctx():
    bis = mk_bis([float(x) for x in TREND_UP])
    legs = legs_from_bis(bis)
    return bis, legs, build_zhongshus(legs)


# --------------------------------------------------------------------------
# 定位
# --------------------------------------------------------------------------


def test_extreme_leg_only_looks_at_same_direction_legs(ctx):
    """段首尾相接，不做方向过滤的话最低点会被判给紧随其后的那个向上段。"""
    _, legs, _ = ctx
    # legs 10-13 是 42→70→60→80→68，向上趋势的最高点 80 在 leg12（60→80）
    assert _extreme_leg(legs, 10, 13, Direction.UP) == 12
    assert legs[12].high == 80.0
    # 同一范围里的最低点 42 在 leg10（42→70，向上段的起点）——取向下段时
    # 必须落到 leg11（70→60），而不是起点价相同的 leg10
    assert legs[_extreme_leg(legs, 10, 13, Direction.DOWN)].direction is Direction.DOWN


def test_extreme_leg_falls_back_when_no_leg_matches(ctx):
    """范围里一段同向的都没有时退回全体，不能抛异常。"""
    _, legs, _ = ctx
    assert _extreme_leg(legs, 11, 11, Direction.UP) == 11  # leg11 是向下的


# --------------------------------------------------------------------------
# 第三类买卖点。20课 p43 第 966 行
# --------------------------------------------------------------------------


def test_third_buy_needs_the_pullback_to_hold_above_zg(ctx):
    """「其低点不跌破 ZG，则构成第三类买点」。"""
    bis, legs, zss = ctx
    # 中枢 A = legs 0-3，[ZD 12, ZG 20]；leg4（13→50）离开，leg5（50→40）回试
    p = _third_at(zss, 0, legs, bis, None)
    assert p is not None
    assert (p.kind, p.is_buy) == (THIRD, True)
    assert p.leg == 5 and p.price == 40.0  # 回试低点 40 > ZG 20
    assert any("20课 p43 第 966 行" in r for r in p.reasons)
    assert any("第一次" in r for r in p.reasons)


def test_third_buy_rejected_when_the_pullback_re_enters_the_zhongshu(ctx):
    """回试跌回 [ZD, ZG] 之内就不是三买——定理要的是完全不重进中枢。"""
    bis, legs, zss = ctx
    sunk = list(legs)
    sunk[5] = replace(legs[5], low=15.0, end_price=15.0)  # 跌回 [12, 20]
    assert _third_at(zss, 0, sunk, bis, None) is None


def test_third_sell_compares_against_zd_not_zg():
    """第 966 行：三卖是「其高点**不升破 ZD**」。基准是中枢的**下沿**。"""
    #           中枢 [42,48] ──────────────┐ 向下离开  回抽
    bis = mk_bis([50.0, 40.0, 48.0, 42.0, 47.0, 10.0, 30.0, 8.0])
    legs = legs_from_bis(bis)
    (zs,) = build_zhongshus(legs, require_confirmed=False)
    assert (zs.zd, zs.zg) == (42.0, 48.0)
    assert zs.depart_leg == 4  # leg4（47→10）向下飞走
    p = _third_at([zs], 0, legs, bis, None)
    assert p is not None and not p.is_buy and p.kind == THIRD
    assert p.price == 30.0  # 回抽高点 30 < ZD 42，若拿 ZG 48 比就会放过更高的回抽
    assert "ZD" in p.reasons[0]


def test_no_third_point_before_the_zhongshu_is_left():
    """还没走掉的中枢谈不上三买三卖——离开段都没有。"""
    bis = mk_bis([10.0, 20.0, 12.0, 18.0, 13.0])
    legs = legs_from_bis(bis)
    (zs,) = build_zhongshus(legs, require_confirmed=False)
    assert zs.depart_leg is None
    assert _third_at([zs], 0, legs, bis, None) is None


def test_third_buy_also_fires_on_the_second_zhongshu(ctx):
    """夹具里 B 也走掉了（leg10 离开、leg11 回试 60 > ZG 48）。"""
    bis, legs, zss = ctx
    assert zss[1].depart_leg == 10
    p = _third_at(zss, 1, legs, bis, None)
    assert p is not None and p.is_buy and p.leg == 11 and p.price == 60.0


# --------------------------------------------------------------------------
# 第一、二类买卖点
# --------------------------------------------------------------------------


def test_first_sell_sits_on_the_top_backchi(ctx):
    """「第一类买点肯定是趋势背驰构成的」（第 503 行）。这里是镜像的 1卖。"""
    bis, legs, zss = ctx
    pts = find_bspoints(legs, zss, bis, FADING)
    sells = [p for p in pts if p.kind == FIRST]
    assert len(sells) == 1
    p = sells[0]
    assert not p.is_buy and not p.quasi  # 趋势背驰，不是「类1卖」
    assert p.leg == 12 and p.price == 80.0  # c 段最高点
    assert p.zs_index == 1


def test_first_point_from_panzheng_is_marked_quasi(ctx):
    """盘整背驰只给「类第一类买点」（第 503 行），必须打标记区分。"""
    bis, legs, _ = ctx
    solo = build_zhongshus(legs, start=5)  # 只剩一个中枢 → 盘整背驰
    pts = find_bspoints(legs, solo, bis, FADING)
    firsts = [p for p in pts if p.kind == FIRST]
    assert firsts and all(p.quasi for p in firsts)
    assert any("类1买" in r for p in firsts for r in p.reasons)


def test_quasi_points_can_be_filtered_out(ctx):
    bis, legs, _ = ctx
    solo = build_zhongshus(legs, start=5)
    assert not [p for p in find_bspoints(legs, solo, bis, FADING, include_quasi=False)
                if p.kind in (FIRST, SECOND)]


# 1卖之后还要再走两段，`TREND_UP` 的 c 段顶就在末尾，续不出来——**而且也
# 续不得**：末尾那一笔一补上，legs 11-13 立刻自成一个中枢，zs1 的 c 段被截成
# 一段，趋势背驰的第五道门槛（c 里要有三买）当场失守，顶背驰就退化成盘整背驰。
#
# 所以这里另造一串。形状和 `TREND_UP` 同源，只是把顶背驰挪到序列中段：
#
#   legs 0-3     中枢 A  [12,18]   leg4 (13→50) 离开
#   legs 5-7     中枢 B  [41,48]   leg8 (41→70) 离开
#   legs 8-10    c 段    离开 + 回试 60 > ZG 48（三买，门槛五）
#   legs 11-14   中枢 C  [78,80]   后面还有两段，2卖 才有地方落
#
# 下一个中枢只能从**向下段**起步：这是一段上涨走势，中枢是「下上下」，而
# `build_zhongshus` 从离开段之后按方向奇偶往后滑（见那里的 docstring）。
# legs 9-11 不重叠（leg11 只回落到 75，高于 leg9 的顶 70），所以滑到 leg11。
# 末两个点 88/80 是为此补的——没有它们 leg13 还没确认，中枢 C 根本凑不满三段。
#
# 早先这串只到 78.0，中枢 C 被认在 legs 10-12，起步段 leg10 是**向上**的：
# 那把 60→80 这根上涨趋势段吞成了中枢首段，和 600009 30分钟上段51
# （74.55→60.77）被吞进中枢是同一个毛病。
SECOND_PTS = [10.0, 20.0, 12.0, 18.0, 13.0, 50.0, 40.0, 48.0, 41.0, 70.0, 60.0, 80.0,
              75.0, 85.0, 78.0, 88.0, 80.0]


def test_second_point_is_the_end_of_the_second_sub_leg():
    """第 2203 行：1卖下去一段，回头那一段的**结束点**就是 2卖。"""
    bis = mk_bis(SECOND_PTS)
    legs = legs_from_bis(bis)
    zss = build_zhongshus(legs)
    assert [(z.start_leg, z.end_leg) for z in zss] == [(0, 3), (5, 7), (11, 14)]
    m = mk_macd(hist=[(16, 20, 10.0), (32, 40, 1.0)])
    pts = find_bspoints(legs, zss, bis, m)

    (p1,) = [p for p in pts if p.kind == FIRST]
    (p2,) = [p for p in pts if p.kind == SECOND]
    # c 段是 legs 8-10（41→70→60→80），顶背驰落在它的结束点
    assert p1.leg == 10 and p1.price == 80.0 and not p1.is_buy  # 顶背驰 → 1卖
    assert not p1.quasi  # 趋势背驰，不是类1卖
    assert p2.leg == p1.leg + 2  # leg11 回落、leg12 反抽，取 leg12 的结束点
    assert p2.price == 85.0 and not p2.is_buy
    assert p2.bar > p1.bar
    assert any("第 2203 行" in r for r in p2.reasons)


def test_both_second_point_readings_merge_into_one():
    """同一个点被窄、广两套口径同时认下时合成一条，来源都记上（C17）。"""
    bis = mk_bis(SECOND_PTS)
    legs = legs_from_bis(bis)
    m = mk_macd(hist=[(16, 20, 10.0), (32, 40, 1.0)])
    (p2,) = [p for p in find_bspoints(legs, build_zhongshus(legs), bis, m)
             if p.kind == SECOND]
    assert "第 362 行" in p2.source and "53课 p127" in p2.source
    # 2卖 升破了 1卖，广义口径靠「力度更弱」放行，两条理由都要在
    assert any("第 2208 行" in r for r in p2.reasons)
    assert any("力度更弱" in r for r in p2.reasons)


def test_second_point_may_break_the_first(ctx):
    """第 2208 行：「第二类买点比第一类买点低，**这是完全可以的**」。

    窄口径不能因为破了前低就把 2买 扔掉——只记进 reasons。
    """
    from chan.buysell import _second_after

    bis, legs, _ = ctx
    p1 = BSPoint(kind=FIRST, is_buy=True, leg=10, bar=40, price=42.0,
                 zs_index=1, source="test")
    # legs 11(70→60) 向下、12(60→80) 向上……方向不对，换一个起点
    p1 = BSPoint(kind=FIRST, is_buy=True, leg=11, bar=44, price=60.0,
                 zs_index=1, source="test")
    p2 = _second_after(p1, legs, bis, None, wide=False)
    assert p2 is not None and p2.leg == 13  # leg12 向上、leg13 向下
    assert p2.price == 68.0


def test_second_point_requires_alternating_directions(ctx):
    """方向不交替说明中间断了，这不是原文描述的形态。"""
    from chan.buysell import _second_after

    bis, legs, _ = ctx
    p1 = BSPoint(kind=FIRST, is_buy=False, leg=12, bar=52, price=80.0,
                 zs_index=1, source="test")
    # leg13 向下、leg14 不存在
    assert _second_after(p1, legs, bis, None, wide=False) is None


# --------------------------------------------------------------------------
# 口径与开关
# --------------------------------------------------------------------------


def test_macd_is_optional_and_only_third_points_survive_without_it(ctx):
    """3买 是纯几何的；1买 按定义要背驰，没 MACD 就给不了（C13）。"""
    bis, legs, zss = ctx
    pts = find_bspoints(legs, zss, bis, None)
    assert pts and all(p.kind == THIRD for p in pts)


def test_macd_law_is_recorded_not_enforced(ctx):
    """第 2450 行的 MACD 定律当标注算，不当门槛。"""
    bis, legs, zss = ctx
    # 顶背驰但 DIF 在 0 轴**之下**——按定律不该是 1卖，但信号照给、只标 False
    m = Macd(dif=np.full(N_BARS, -1.0), dea=np.zeros(N_BARS), hist=FADING.hist)
    (p,) = [x for x in find_bspoints(legs, zss, bis, m) if x.kind == FIRST]
    assert p.macd_law_ok is False
    m2 = Macd(dif=np.full(N_BARS, 1.0), dea=np.zeros(N_BARS), hist=FADING.hist)
    (p2,) = [x for x in find_bspoints(legs, zss, bis, m2) if x.kind == FIRST]
    assert p2.macd_law_ok is True


def test_macd_law_is_none_without_macd(ctx):
    bis, legs, zss = ctx
    assert all(p.macd_law_ok is None for p in find_bspoints(legs, zss, bis, None))


def test_wide_second_can_be_turned_off(ctx):
    bis, legs, zss = ctx
    on = find_bspoints(legs, zss, bis, FADING, wide_second=True)
    off = find_bspoints(legs, zss, bis, FADING, wide_second=False)
    assert len(off) <= len(on)


# --------------------------------------------------------------------------
# 输出契约
# --------------------------------------------------------------------------


def test_points_come_back_sorted_by_bar(ctx):
    bis, legs, zss = ctx
    pts = find_bspoints(legs, zss, bis, FADING)
    assert [p.bar for p in pts] == sorted(p.bar for p in pts)


def test_label_reads_like_the_book(ctx):
    bis, legs, zss = ctx
    labels = {p.label for p in find_bspoints(legs, zss, bis, FADING)}
    assert labels <= {"1买", "2买", "3买", "1卖", "2卖", "3卖"}


def test_latest_can_skip_provisional_points(ctx):
    bis, legs, zss = ctx
    pts = find_bspoints(legs, zss, bis, FADING)
    assert latest([]) is None
    assert latest(pts) is pts[-1]
    assert latest(pts, confirmed_only=True) in (None, *pts)


# --------------------------------------------------------------------------
# 重合不变量。21课 p45 第 363 行
# --------------------------------------------------------------------------


def _pt(kind: int, bar: int, is_buy: bool = True) -> BSPoint:
    return BSPoint(kind=kind, is_buy=is_buy, leg=0, bar=bar, price=1.0,
                   zs_index=0, source="test")


def test_second_and_third_may_coincide():
    """「只有第二类买点与第三类买点是可能产生重合的」——不该报错。"""
    check_bspoints([_pt(SECOND, 10), _pt(THIRD, 10)])


@pytest.mark.parametrize("other", [SECOND, THIRD])
def test_first_can_never_coincide_with_second_or_third(other: int):
    with pytest.raises(InvariantError):
        check_bspoints([_pt(FIRST, 10), _pt(other, 10)])


def test_same_bar_opposite_sides_is_not_a_coincidence():
    """一买一卖落在同一根K线上不算重合——它们是两侧的事。"""
    check_bspoints([_pt(FIRST, 10, True), _pt(THIRD, 10, False)])


def test_real_fixture_satisfies_the_coincidence_invariant(ctx):
    bis, legs, zss = ctx
    check_bspoints(find_bspoints(legs, zss, bis, FADING), zss)


# --------------------------------------------------------------------------
# 位置不变量。21课 p45 第 361 行（给了 zss 才查）
# --------------------------------------------------------------------------


def test_first_point_must_sit_outside_its_zhongshu(ctx):
    """「只有在**下跌确立后的中枢下方**才可能出现买点。这就是第一类买点。」"""
    _, _, zss = ctx
    zs = zss[1]  # [41, 48]
    inside = BSPoint(kind=FIRST, is_buy=True, leg=0, bar=0, price=45.0,
                     zs_index=1, source="test")
    with pytest.raises(InvariantError):
        check_bspoints([inside], zss)
    below = replace(inside, price=30.0)
    check_bspoints([below], zss)
    assert below.price < zs.zd


def test_position_rule_does_not_apply_to_quasi_points(ctx):
    """类1买是盘整背驰构成的，本来就是中枢内部的事，不受位置约束。

    真实数据上确有其例：7 只票 91 个一类点里 6 个落在中枢区间内，**全部是
    quasi**。所以这条不能一刀切，否则扫描会在正常行情上崩掉。
    """
    _, _, zss = ctx
    inside = BSPoint(kind=FIRST, is_buy=True, leg=0, bar=0, price=45.0,
                     zs_index=1, source="test", quasi=True)
    check_bspoints([inside], zss)


def test_third_point_position_is_checked_too(ctx):
    """3买不跌破 ZG、3卖不升破 ZD（20课 p43 第 966 行）。"""
    _, _, zss = ctx
    with pytest.raises(InvariantError):
        check_bspoints([BSPoint(kind=THIRD, is_buy=True, leg=0, bar=0, price=45.0,
                                zs_index=1, source="test")], zss)
    with pytest.raises(InvariantError):
        check_bspoints([BSPoint(kind=THIRD, is_buy=False, leg=0, bar=0, price=45.0,
                                zs_index=1, source="test")], zss)


def test_dangling_zhongshu_index_is_an_error(ctx):
    _, _, zss = ctx
    with pytest.raises(InvariantError):
        check_bspoints([_pt(THIRD, 0)], zss[:0])


def test_position_check_is_skipped_without_zhongshus():
    """不给 zss 就只查重合关系——老调用点不受影响。"""
    check_bspoints([BSPoint(kind=FIRST, is_buy=True, leg=0, bar=0, price=45.0,
                            zs_index=99, source="test")])


# --------------------------------------------------------------------------
# 多空对称。整套定义里没有一条是只对买点成立的
# --------------------------------------------------------------------------


def _mirror(bars: list[Bar]) -> list[Bar]:
    """把行情上下翻转：`p → C - p`，高低点互换。C 取全程最高加最低。

    MACD 也跟着整体取反（EMA 对 `C - c` 是 `C - ema(c)`，相减后常数抵消），
    所以翻转后的「底背驰」逐项对应原来的「顶背驰」，不需要另外喂指标。
    """
    c = max(b.high for b in bars) + min(b.low for b in bars)
    return [
        Bar(idx=b.idx, ts=b.ts, open=c - b.open, high=c - b.low, low=c - b.high,
            close=c - b.close, volume=b.volume)
        for b in bars
    ]


def _points_of(bars: list[Bar]) -> list[tuple]:
    a = analyze(bars)
    legs = legs_from_bis(a.bis)
    pts = find_bspoints(legs, build_zhongshus(legs), a.bis, macd([b.close for b in bars]))
    return [(p.kind, p.is_buy, p.leg, p.bar, p.quasi) for p in pts]


@pytest.mark.parametrize("seed", [7, 11, 29])
def test_buy_and_sell_sides_are_exact_mirror_images(seed: int):
    """把行情上下翻转，每个买点都该变成同一位置的卖点，一个不多一个不少。

    **这条抓的是「只写对了一半」的比较。** 买卖点全套定义里到处是
    `gt`/`lt` 成对出现——三买比 ZG、三卖比 ZD（第 966 行），极值段按方向过滤，
    面积按红绿柱分开累加——任何一处把方向写反或漏掉镜像分支，翻转后的结果
    就对不上。

    真实数据上也核对过：600519 后复权日线翻转前 `trend_UP` 命中 2 次、3买 49 /
    3卖 23，翻转后是 `trend_DOWN` 2 次、3卖 52 / 3买 25（笔数 616→618 的微小
    差异来自包含处理的起手方向，见 C01，不是买卖点层的事）。顺带解释了一个
    看着可疑的现象：7 只票跑下来**一个趋势底背驰都没有**，不是代码偏心，是
    这几只票的下跌段确实没走出 37课 p96 那个形态。
    """
    rng = random.Random(seed)
    pivots = [100.0]
    for i in range(60):
        step = rng.uniform(5, 30) * rng.uniform(0.5, 1.5)
        pivots.append(max(pivots[-1] + (step if i % 2 == 0 else -step), 10.0))
    bars = zigzag(pivots)

    straight = _points_of(bars)
    flipped = _points_of(_mirror(bars))
    assert straight, "夹具没跑出任何买卖点，这条测试就什么都没验到"
    assert [(k, not buy, leg, bar, q) for k, buy, leg, bar, q in straight] == flipped
