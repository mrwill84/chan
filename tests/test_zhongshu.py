"""中枢。17课 p36 / 18课 pp.39-40 / 20课 p43。

这一层直接吃 `Leg`，所以 fixture 用拐点价格列表手搓，不经过笔和线段——
把中枢的错和划线的错分开，出问题时能立刻定位是哪一层。
"""

from __future__ import annotations

import pytest

from chan.invariants import check_zhongshus
from chan.types import Direction, Leg
from chan.zhongshu import (
    AMBIGUOUS,
    HIGHER_LEVEL,
    MAX_LEGS,
    PANZHENG,
    TREND_DOWN,
    TREND_UP,
    UPGRADE_LEGS,
    build_zhongshus,
    classify,
    legs_from_bis,
    legs_overlap,
    relation,
)


def mk_legs(points: list[float], *, last_unconfirmed: bool = False) -> list[Leg]:
    """用拐点价格序列造一串首尾相接的 Leg。极值即端点。"""
    legs = []
    for i, (a, b) in enumerate(zip(points, points[1:])):
        legs.append(
            Leg(
                index=i,
                direction=Direction.UP if b > a else Direction.DOWN,
                start_price=a,
                end_price=b,
                low=min(a, b),
                high=max(a, b),
                src_start=i,
                src_end=i,
                confirmed=not (last_unconfirmed and i == len(points) - 2),
            )
        )
    return legs


# --------------------------------------------------------------------------
# 基本构成
# --------------------------------------------------------------------------


def test_three_overlapping_legs_form_one_zhongshu():
    """17课 p36：中枢区间 = (max(a2,b2,c2), min(a1,b1,c1))。"""
    legs = mk_legs([10, 20, 12, 18])
    zs = build_zhongshus(legs, require_confirmed=False)
    assert len(zs) == 1
    assert zs[0].zd == 12  # max(10, 12, 12)
    assert zs[0].zg == 18  # min(20, 20, 18)
    assert zs[0].direction is Direction.UP  # 回升形成的


def test_zhongshu_needs_three_legs():
    assert build_zhongshus(mk_legs([10, 20, 12]), require_confirmed=False) == []


def test_non_overlapping_legs_form_nothing():
    """三段无公共重合区间。

    注意首尾相接的三段**几乎总是**有重合：A=[a0,a1] 上、B=[b1,a1] 下、
    C=[b1,c1] 上，交集 = [max(a0,b1), min(a1,c1)]，只有回撤跌穿起点
    （b1 < a0）且 c1 < a0 时才真的空。所以这里必须构造一路下台阶的形态。
    """
    assert build_zhongshus(mk_legs([10, 20, 5, 8]), require_confirmed=False) == []


def test_zg_zd_fixed_by_first_three_legs():
    """17课 p36：「具体的计算以**前三个**连续次级别的重叠为准」。

    后面延伸出更高的高点，ZG/ZD 不许变——变了 3买判定就全错。
    """
    legs = mk_legs([10, 20, 12, 18, 13, 19, 14])
    (zs,) = build_zhongshus(legs, require_confirmed=False)
    assert (zs.zd, zs.zg) == (12, 18)
    assert zs.leg_count > 3  # 确实延伸了


# --------------------------------------------------------------------------
# 延伸与离开（18课 p40）
# --------------------------------------------------------------------------


def test_departure_leg_is_not_part_of_the_zhongshu():
    """20课 p43 第三类买卖点定理：离开段在中枢**之外**。

    这是整层最关键的一条。离开段起点在中枢里，单看它永远「与中枢有重叠」；
    要是把它算进去，它的高点会灌进 GG，趋势就永远判不出来。
    """
    #            中枢 [12,18]       离开↑  回落  中枢2
    legs = mk_legs([10, 20, 12, 18, 13, 50, 40, 48, 42, 46, 30])
    zs = build_zhongshus(legs, require_confirmed=False)
    assert len(zs) == 2
    first = zs[0]
    assert first.end_leg == 3  # leg4（13→50）是离开段，不在中枢内
    assert first.gg == 20  # 而不是 50
    assert first.dd == 10


def test_single_non_touching_leg_does_not_end_extension():
    """18课 p40：**前后两个**次级波动都不触及才算离开，单段甩出去不算。"""
    #            中枢 [12,18]   甩上去  又回来
    legs = mk_legs([10, 20, 12, 18, 13, 25, 15, 17, 16])
    (zs,) = build_zhongshus(legs, require_confirmed=False)
    assert zs.zd == 12 and zs.zg == 18
    assert zs.end_leg >= 6  # 甩出去那两段仍在中枢的延伸里


def test_long_extension_is_flagged_not_cut():
    """33课 p79 的 8 段是**升级提示，不是切断点**。

    原文说延伸到第 9 段「就构成更大级别的中枢了」——那是**一个**更大的中枢。
    这条测试以前断言 `leg_count <= MAX_LEGS`，也就是断言引擎会把它切成两个，
    那是把一个 bug 写成了规格。

    切断的实际后果（全缓存 66 组里 64 组中招，相邻对约 24%）：`capped` 是在
    某一段**仍然触及** [ZD, ZG] 的时候跳出的，`depart_leg` 于是留空，而
    `next_start` 退化成 `end_leg + 1`，下一个中枢就从那一段起步。那一段因此
    同时是「还在本中枢里震荡的段」和「下个中枢的第一构成段」，还把自己的端点
    写进了后者的 ZD/ZG。更明显的破绽是奇偶性：下跌走势里的中枢该是 上下上，
    切出来的却是 下上下。

    33课 p79 同一页还说「整个走势其实就简化为**两个中枢与连接两者的一个走势**」
    ——两个中枢之间必须隔着连接段，所以切成紧挨着的两个在原文里根本不存在。
    这条性质现在由 `invariants.check_zhongshus` 守着。
    """
    pts = [10.0, 20.0]
    for i in range(12):  # 一直在 [12,18] 里来回磨，Zn 层层内嵌必有公共交集
        pts.append(12.0 + 0.1 * i)
        pts.append(18.0 - 0.1 * i)
    pts += [50.0, 45.0, 60.0]  # 飞走：后两段都不触及，中枢才算走完
    legs = mk_legs(pts)
    zs = build_zhongshus(legs, require_confirmed=False)
    assert len(zs) == 1, "一路震荡没离开过，就是一个中枢，不该被切开"
    assert zs[0].leg_count > MAX_LEGS
    assert zs[0].level_up is True  # 该升一个级别去看了
    check_zhongshus(zs, legs)


def test_the_third_founding_leg_can_itself_be_the_departure():
    """前三段里第三段自己就飞走：它同时是构成段和离开段（C-core）。

    17课 p36 要求中枢「以前三个连续次级别的重叠为准」，所以这一段不能从中枢
    里拿掉，`end_leg` 必须留在 2。但它又确实是离开段，不能算进 GG/DD——
    两个身份靠 `end_leg` 与 `core_end` 分开。
    """
    #              ┌── 前三段重叠 [12,20] ──┐  这两段再不回头
    legs = mk_legs([10, 20, 12, 50, 45, 60])
    (zs,) = build_zhongshus(legs, require_confirmed=False)
    assert (zs.zd, zs.zg) == (12, 20)
    assert zs.end_leg == 2 and zs.depart_leg == 2  # 同一段，两个身份
    assert zs.core_end == 1  # 包络只数到它之前
    assert zs.gg == 20  # 不是 50
    assert zs.next_start == 3  # 跨过离开段再找下一个（33课 p79）


def test_departure_is_detected_even_past_the_extension_cap():
    """段数上限只限制中枢能延伸多远，**不限制往后看多远**。

    曾经把 `k - i + 1 <= max_legs` 写进循环条件，于是第 9 段上的「不触及」
    根本没被扫到，`departed` 留在 False，飞走的那一段被当成中枢的一部分、
    把 GG 撑到 60。600519 的 zs1 就是这么错的，见 CONVENTIONS C-core。
    """
    #              [12,18] 里磨 7 段 ─────────┐ 飞走  不回头
    legs = mk_legs([10, 20, 12, 18, 13, 19, 14, 60, 55, 70])
    (zs,) = build_zhongshus(legs, require_confirmed=False)
    assert (zs.zd, zs.zg) == (12, 18)
    assert zs.departed and zs.depart_leg == 6  # 第 7 段飞走，落在上限之内
    assert zs.end_leg == 5 and zs.core_end == 5
    assert zs.gg == 20  # 离开段的 60 没灌进来
    assert not zs.level_up  # 实际只用了 6 段，够不着 33课 p79 的升级条件


def _osc(n_core: int, tail: list[float]) -> list[Leg]:
    """造一个在 [12,18] 里层层内嵌震荡 `n_core` 段、然后按 `tail` 收尾的序列。

    层层内嵌保证**所有段**都有公共交集（因而重读出来的 3 个次级别中枢必然
    重合），于是升级判据里只剩「段数够不够」这一半在起作用。
    """
    pts = [10.0, 20.0]
    while len(pts) - 1 < n_core:
        i = (len(pts) - 2) // 2
        pts.append(12.0 + 0.1 * i)
        pts.append(18.0 - 0.1 * i)
    return mk_legs(pts[: n_core + 1] + tail)


def test_level_up_boundary_is_the_ninth_leg():
    """33课 p79：3+5=8 段**仍是同级别**，第 9 段才升级。

    引擎原先写的是 `core 段数 >= MAX_LEGS`，在第 8 段就报升级，早了一段；
    `tests/test_stress.py` 那条写的是 `> MAX_LEGS`，两边差一段差了很久没人
    发现——因为在「总段数」这个口径下 8 和 9 都「像」上限，看不出谁对。

    9 这个数有两条独立的来路，互为校验：延伸上限 5 段 + 构成中枢的 3 段 = 8，
    第一个越界的是 9；而「每 3 段构成一个次级别中枢」× 3 个中枢也是 9
    （`UPGRADE_LEGS = MIN_LEGS * 3`）。8 没有这种结构意义。
    """
    eight = _osc(8, [50.0, 45.0, 60.0])
    (z8,) = build_zhongshus(eight, require_confirmed=False)
    assert z8.core_end - z8.start_leg + 1 == MAX_LEGS == UPGRADE_LEGS - 1
    assert z8.confirmed and not z8.level_up, "8 段还是同级别，不该报升级"

    nine = _osc(9, [50.0, 45.0, 60.0])
    (z9,) = build_zhongshus(nine, require_confirmed=False)
    assert z9.core_end - z9.start_leg + 1 == UPGRADE_LEGS
    assert z9.level_up, "第 9 段凑满 3 个次级别中枢，该升一级看了"


def test_level_up_compares_zhongshus_not_legs():
    """33课 p79 的升级论证前提是「**3 个**次级别走势**重合**」。

    **重合的对象是「中枢」，不是「段」。** 走势重合指的是走势类型（盘整/趋势）
    的区间重合；9 段按「每 3 段构成一个中枢」切成 3 个次级别中枢，要那 3 个
    **中枢区间**有公共交集才升级。一路向下、两两不沾边的 3 个中枢是下跌
    **趋势**（20课 p43 中心定理二），不是更大级别的中枢。

    这一条不是自动成立的：延伸只要求每段「触及 [ZD, ZG]」，蹭到边就算数。
    这里 B 整个贴在上沿、C 整个缩在下沿，两者毫无交集。全缓存实测 846 个
    够 9 段的中枢里有 132 个（15.6%）是这一类。

    曾经把判据写成「5 个 Zn 有公共交集」（`D <= G`），那是**对象选错了**——
    Zn 是段不是中枢，而且它拿全部核心段去求交，第 11、13 段的摆动会回头
    推翻第 9 段就该下的结论。全缓存两者差 190 个。
    """
    #              ┌─── A ───┐  ┌──  B  ──┐  ┌──  C ──┐   飞走
    legs = mk_legs([10, 20, 12, 18, 17, 25, 11, 13, 12, 18, 5, 8, 3])
    (zs,) = build_zhongshus(legs, require_confirmed=False)
    assert (zs.zd, zs.zg) == (12, 18)
    core = legs[zs.start_leg : zs.core_end + 1]
    assert len(core) == UPGRADE_LEGS

    # 重读成 3 个次级别中枢，方向自动交替（段隔 3 个就反向）：上 / 下 / 上
    subs = [core[k : k + 3] for k in range(0, UPGRADE_LEGS, 3)]
    assert [s[0].direction for s in subs] == [
        Direction.UP, Direction.DOWN, Direction.UP]
    assert [(max(x.low for x in s), min(x.high for x in s)) for s in subs] == [
        (12, 18), (17, 18), (12, 13)]
    # B=[17,18] 和 C=[12,13] 不沾边 → 3 个中枢没有公共交集
    assert not legs_overlap(core)
    assert zs.leg_count > MAX_LEGS
    assert zs.confirmed and not zs.level_up


# --------------------------------------------------------------------------
# GG/DD 只遍历 Zn（20课 p43）
# --------------------------------------------------------------------------


def test_envelope_only_covers_same_direction_legs():
    legs = mk_legs([10, 20, 12, 18])  # 向上形成，Zn = leg0[10,20], leg2[12,18]
    (zs,) = build_zhongshus(legs, require_confirmed=False)
    assert (zs.gg, zs.dd, zs.g, zs.d) == (20, 10, 18, 12)


def test_down_formed_zhongshu_direction():
    """回调形成的中枢，Z 段向下。"""
    legs = mk_legs([20, 10, 18, 12])
    (zs,) = build_zhongshus(legs, require_confirmed=False)
    assert zs.direction is Direction.DOWN
    assert (zs.zd, zs.zg) == (12, 18)


# --------------------------------------------------------------------------
# C-enter：进入段的终点不进 GG/DD
# --------------------------------------------------------------------------


def test_entry_terminal_does_not_enter_the_envelope():
    """首构成段只贡献远端极值，近端那个是进入段砸/拉出来的。

    18课 p39 要的是「围绕走势中枢产生的任何瞬间波动」——**围绕中枢产生的**。
    两段首尾相接，首构成段的近端在数值上就是上一段的终点，它是上一段的产物。
    这是 C-core 的另一半：那里把离开段整段挡在包络外，这里挡进入段漏进来的
    那一个端点。
    """
    #            中枢 A ───────┐ b 段拉到 60 ┌─── 中枢 B ───────┐
    legs = mk_legs([10, 20, 12, 18, 13, 60, 40, 55, 42, 50, 44])
    zss = build_zhongshus(legs, require_confirmed=False)
    assert len(zss) == 2
    a, b = zss

    # A 起于 leg0，前面没有进入段，GG/DD 原样取全部 Zn
    assert (a.start_leg, a.direction) == (0, Direction.UP)
    assert (a.dd, a.gg) == (10, 20)

    # B 的首 Zn 是 leg5（60→40），它的高点 60 正是 b 段（leg4: 13→60）的终点
    assert (b.start_leg, b.direction) == (5, Direction.DOWN)
    assert legs[4].end_price == legs[5].high == 60
    assert b.gg == 55  # leg7（55→42）的高点；不剔的话是 60
    assert b.dd == 40  # 低点一侧是远端，照常参与

    # 恒等式不受影响
    assert b.dd <= b.zd <= b.zg <= b.gg


def test_entry_exclusion_never_shrinks_a_trend_into_a_higher_level():
    """这条规则的偏向是**单向**的：只会把 higher_level 放成趋势，不会反过来。

    下跌走势里 a 砸出来的低点会把前中枢的 DD 往下拽，而判下跌要求
    「后 GG < 前 DD」——DD 被拽低就更难满足。上涨对称。全缓存 33 条序列实测
    433 对相邻关系变了 11 对（6 对转 up、5 对转 down），全部是
    higher_level → 趋势，反方向一对都没有。
    """
    #        a 段 ┐ ┌─ A [12,17] ─┐ b 段 ┐ ┌─ B [4,10] ─┐
    legs = mk_legs([30, 10, 18, 12, 17, 2, 11, 4, 10, 5])
    # start=1 把 leg0（30→10）摆成进入段 a——600009 那处争议就是这个形状
    zss = build_zhongshus(legs, require_confirmed=False, start=1)
    assert len(zss) == 2
    a, b = zss
    assert (a.start_leg, a.core_end, a.depart_leg) == (1, 3, 4)
    assert (b.start_leg, b.core_end) == (5, 8)

    assert a.dd == 12   # 不是 10：leg1 的低点 10 就是 a 段砸出来的终点
    assert b.gg == 11
    assert relation(a, b) == TREND_DOWN  # 后 GG 11 < 前 DD 12

    # 旧口径（把进入段终点算进去）下 前DD 会是 10，后GG 11 反而更高，
    # 一段明摆着的下跌就被判成「形成高级别中枢」
    assert min(legs[i].low for i in (1, 3)) == 10
    assert b.gg > 10


# --------------------------------------------------------------------------
# 中心定理二与走势分类
# --------------------------------------------------------------------------


def test_uptrend_two_non_overlapping_zhongshus():
    legs = mk_legs([10, 20, 12, 18, 13, 50, 40, 48, 42, 46, 30])
    zs = build_zhongshus(legs, require_confirmed=False)
    assert len(zs) == 2
    assert relation(zs[0], zs[1]) == TREND_UP  # 后 DD > 前 GG
    assert classify(zs) == TREND_UP


def test_downtrend_mirror():
    legs = mk_legs([50, 40, 48, 42, 47, 10, 20, 12, 18, 14, 30])
    zs = build_zhongshus(legs, require_confirmed=False)
    assert len(zs) == 2
    assert relation(zs[0], zs[1]) == TREND_DOWN
    assert classify(zs) == TREND_DOWN


def test_single_zhongshu_is_panzheng():
    legs = mk_legs([10, 20, 12, 18, 13])
    assert classify(build_zhongshus(legs, require_confirmed=False)) == PANZHENG


def test_relation_higher_level():
    """后 ZD > 前 ZG 但 后 DD <= 前 GG —— 够不上趋势，构成更大级别中枢。"""
    legs = mk_legs([10, 20, 12, 18, 13, 26, 19, 25, 20, 24, 15])
    zs = build_zhongshus(legs, require_confirmed=False)
    assert len(zs) == 2
    assert relation(zs[0], zs[1]) in (HIGHER_LEVEL, AMBIGUOUS)
    assert classify(zs) == PANZHENG  # 不是趋势


# --------------------------------------------------------------------------
# 未完成的段不参与（17课 p36）
# --------------------------------------------------------------------------


def test_unconfirmed_tail_leg_excluded():
    """「次级别的前三个走势类型都是**完成的**才构成该级别的走势中枢」。"""
    legs = mk_legs([10, 20, 12, 18], last_unconfirmed=True)
    assert build_zhongshus(legs, require_confirmed=True) == []
    assert len(build_zhongshus(legs, require_confirmed=False)) == 1


def test_zhongshu_confirmed_flag():
    """后面还有段走出来，中枢才谈得上已经走完。"""
    legs = mk_legs([10, 20, 12, 18])
    (zs,) = build_zhongshus(legs, require_confirmed=False)
    assert zs.confirmed is False  # 最后一段就是中枢的最后一段
    legs2 = mk_legs([10, 20, 12, 18, 13, 50, 40, 48, 42, 46, 30])
    zs2 = build_zhongshus(legs2, require_confirmed=False)
    assert zs2[0].confirmed is True


# --------------------------------------------------------------------------
# 不重叠地依次划出
# --------------------------------------------------------------------------


def test_zhongshus_do_not_share_legs():
    legs = mk_legs([10, 20, 12, 18, 13, 50, 40, 48, 42, 46, 30])
    zs = build_zhongshus(legs, require_confirmed=False)
    for a, b in zip(zs, zs[1:]):
        assert a.end_leg < b.start_leg


def test_contains():
    (zs,) = build_zhongshus(mk_legs([10, 20, 12, 18]), require_confirmed=False)
    assert zs.contains(15)
    assert zs.contains(12) and zs.contains(18)  # 含边界
    assert not zs.contains(11.9)


# --------------------------------------------------------------------------
# 和上游对接
# --------------------------------------------------------------------------


def test_legs_from_bis_roundtrip():
    from conftest import mk_bars

    from chan import analyze

    a = analyze(mk_bars([(10 + (i % 7), 12 + (i % 7)) for i in range(80)]), check=True)
    legs = legs_from_bis(a.bis)
    assert len(legs) == len(a.bis)
    for leg, bi in zip(legs, a.bis):
        assert (leg.low, leg.high, leg.direction) == (bi.low, bi.high, bi.direction)
    build_zhongshus(legs)  # 不该抛


@pytest.mark.parametrize("pts", [[], [10], [10, 20], [10, 20, 12]])
def test_degenerate(pts):
    assert build_zhongshus(mk_legs(pts), require_confirmed=False) == []
