"""级别树。17课 p36 走势类型三分 / 33课 p79 延伸上限 / 20课 p43 中心定理二。

和 `test_zhongshu.py` 一样吃手搓的 `Leg`，不经过笔和线段——这一层的 bug
（归并顺序、三等分边界）和划线的 bug 要能分开定位。
"""

from __future__ import annotations

import random

from test_zhongshu import mk_legs

from chan.tree import (
    NO_ZHONGSHU,
    _goes_with,
    build_layer,
    build_tree,
    leg_bars,
    split_extended,
    trend_bars,
    trend_to_leg,
)
from chan.types import Bi, Direction, Fractal, FractalKind, Leg
from chan.zhongshu import (
    MIN_LEGS,
    PANZHENG,
    TREND_DOWN,
    TREND_UP,
    UPGRADE_LEGS,
    build_zhongshus,
    relation,
)

# 在 [12,18] 里层层内嵌震荡 9 段（够 UPGRADE_LEGS），然后砸穿下去
_OSC9 = [10, 20, 12, 18, 12.1, 17.9, 12.2, 17.8, 12.3, 17.7]
# 砸到 [2,4.5] 再做一个中枢。后 GG 5 < 前 DD 10 → 中心定理二判下跌
_DOWN = [1, 5, 2, 4.5, 3]
# 两个中枢，中间**隔着两根谁也不属于的段**：
#   中枢A 段0..3 [12,18] 包络[10,20]，段4（13→30）是离开段
#   段5、段6（30→50→19.5）凑不出中枢——高 50/50/25，低 30/19.5/19.5，没公共区间
#   中枢B 段7..11 [22,24] 包络[19.8,25]
# 两个包络擦着重合（19.8 < 20）→ relation 判 higher_level，不并成趋势，
# 于是真的有两个走势类型，段5、段6 的归属才成为一个问题。
_GAP = [10, 20, 12, 18, 13, 30, 50, 19.5, 25, 22, 24, 19.8, 23]
# 下跌趋势，**末枢的离开段朝上**：
#   枢0 段0..3 [42,48]（4 根核心段），段4 47→10 砸下去
#   枢1 段5..8 [12,18]（又是 4 根），于是末构成段 段8 朝下、离开段 段9 朝上
#   后 GG 20 < 前 DD 40 → 中心定理二判 down
# 段数的奇偶决定离开段的方向，所以这个形态不是偶发——全缓存 123 个已走出的
# 末枢里 92 个（74.8%）是它。
_CDIR = [60, 40, 48, 42, 47, 10, 20, 12, 18, 13, 30, 25, 28]


def mk_bis(n: int, stride: int = 4) -> list[Bi]:
    """`mk_legs` 造的段配一串笔：第 i 根笔盖 K 线 `[i*stride, (i+1)*stride]`。

    `mk_legs` 填的是 `src_start = src_end = i`，也就是「第 i 段 = 第 i 根笔」。
    这里只要让每根笔占一段互不重叠、单调推进的K线区间，`leg_bars` 翻下来的
    结果就该是同样单调的。价格一律填 0——这几条测的是**下标的翻译**，价格
    一个字都用不上，填真值反而会让人以为它参与了计算。
    """
    def f(i: int) -> Fractal:
        return Fractal(kind=FractalKind.TOP, m_idx=i, high=0.0, low=0.0,
                       begin_idx=i, end_idx=i, peak_idx=i)
    return [Bi(index=i, start=f(i * stride), end=f((i + 1) * stride),
               direction=Direction.UP) for i in range(n)]


def _zss(pts):
    legs = mk_legs(pts)
    return legs, build_zhongshus(legs, require_confirmed=False)


def _osc(n_core: int, tail: list[float]) -> list[float]:
    """在 [12,18] 里层层内嵌震荡**恰好** `n_core` 段，再按 `tail` 收尾。

    和 `test_zhongshu._osc` 同一个构造：层层内嵌保证所有段都有公共交集，于是
    升级/拆分判据里只剩「段数够不够」这一半在起作用。
    """
    pts = [10.0, 20.0]
    while len(pts) - 1 < n_core:
        i = (len(pts) - 2) // 2
        pts += [12.0 + 0.1 * i, 18.0 - 0.1 * i]
    return pts[: n_core + 1] + tail


# --------------------------------------------------------------------------
# 三等分本身（33课 p79）
# --------------------------------------------------------------------------


def test_split_needs_nine_core_legs():
    """3+5=8 段仍是同级别，第 9 段才谈得上拆。"""
    legs, (z,) = _zss(_osc(8, [50.0, 45.0, 60.0]))
    assert z.core_end - z.start_leg + 1 == UPGRADE_LEGS - 1
    assert split_extended(legs, z) is None


def test_split_is_three_equal_parts_not_chunks_of_three():
    """n=9 退化成 3/3/3（原文字面），n 大时三等分、余数从前往后摊。"""
    legs, z = _zss(_OSC9 + _DOWN)
    kids = split_extended(legs, z[0])
    assert [k.leg_count for k in kids] == [3, 3, 3]
    assert [(k.start_leg, k.end_leg) for k in kids] == [(0, 2), (3, 5), (6, 8)]

    # 23 段 → 8/8/7，不是 3×7 还余 2
    legs2, z2 = _zss(_osc(23, _DOWN))
    assert z2[0].core_end - z2[0].start_leg + 1 == 23
    assert [k.leg_count for k in split_extended(legs2, z2[0])] == [8, 8, 7]


def test_split_refuses_when_the_three_parts_do_not_overlap():
    """三份各自成中枢还不够，**还要互相重合**——33课 p79 说的是「3 个 5 分钟
    级别的走势**重合**就形成了」。不重合的三个中枢是 20课 p43 的更大级别
    **趋势**，不是更大级别的中枢，保持原样（一个延伸中枢）才对。

    这里 B 整个贴在上沿、C 整个缩在下沿，两者毫无交集；而延伸只要求每段
    「触及 [ZD, ZG]」，蹭到边就算数，所以这种形态一点都不稀奇（C-ext 实测
    846 个够 9 段的中枢里 132 个是这一类）。
    """
    #        ┌── A [12,18] ──┐┌─ B 贴上沿 ─┐┌─ C 缩下沿 ─┐
    legs, (z,) = _zss([10, 20, 12, 18, 17, 25, 11, 13, 12, 18, 5, 8, 3])
    assert z.core_end - z.start_leg + 1 == UPGRADE_LEGS
    assert not z.level_up            # C-ext 那边早就判了不该升级
    assert split_extended(legs, z) is None


def test_split_children_have_no_connecting_leg_between_them():
    """3 个子中枢是**上级别那一个中枢的三个构成部分**，不是三个相邻的同级别
    中枢，中间不许抠连接段出来（抠到的那段仍然触及中枢区间，是假离开）。"""
    legs, z = _zss(_OSC9 + _DOWN)
    kids = split_extended(legs, z[0])
    for a, b in zip(kids, kids[1:]):
        assert b.start_leg == a.end_leg + 1


# --------------------------------------------------------------------------
# C-order：先归并，后拆
# --------------------------------------------------------------------------


def test_over_extended_zhongshu_that_joins_a_trend_is_kept_whole():
    """定理压约定。并得进趋势就说明本级别已经有对手盘，不退回升级那种读法。

    这条是踩出来的：先拆后合时，父中枢和邻居的 `relation` 根本没机会被评估，
    600009 30分钟一整个 段21..59 的下跌趋势就是这么丢的。见 C-order。
    """
    legs, zss = _zss(_OSC9 + _DOWN)
    assert len(zss) == 2
    big, small = zss
    # 前提都成立：它确实够格拆，而且拆得出来
    assert big.core_end - big.start_leg + 1 >= UPGRADE_LEGS
    assert split_extended(legs, big) is not None
    assert relation(big, small) == TREND_DOWN

    zss2, split_from, trends = build_layer(legs, zss, 0)
    assert split_from == {}, "并进趋势的中枢不该被拆"
    assert [t.kind for t in trends] == [TREND_DOWN]
    assert [z.index for z in trends[0].zhongshus] == [0, 1]
    assert len(zss2) == 2


def test_over_extended_zhongshu_standing_alone_is_split():
    """落单（自成一个盘整）才退回 33课 p79 的升级读法。"""
    legs, zss = _zss(_OSC9 + [1, 5])   # 砸下去就结束，凑不出第二个中枢
    assert len(zss) == 1
    zss2, split_from, trends = build_layer(legs, zss, 0)
    assert len(zss2) == MIN_LEGS
    assert split_from == {0: 0, 1: 1, 2: 2}
    assert [t.kind for t in trends] == [PANZHENG] * MIN_LEGS
    assert [z.index for z in zss2] == [0, 1, 2]


def test_split_children_never_join_a_trend():
    """守卫的效果靠**顺序**保证，不靠额外判断：子中枢诞生在归并之后。"""
    legs, zss = _zss(_OSC9 + [1, 5])
    _, split_from, trends = build_layer(legs, zss, 0)
    for z_idx, t_idx in split_from.items():
        t = trends[t_idx]
        assert t.kind == PANZHENG and len(t.zhongshus) == 1


# --------------------------------------------------------------------------
# 归并（17课 p36）
# --------------------------------------------------------------------------


def test_single_zhongshu_is_a_panzheng_trend():
    legs, zss = _zss([10, 20, 12, 18, 13])
    _, _, trends = build_layer(legs, zss, 0)
    assert [t.kind for t in trends] == [PANZHENG]


def test_greedy_takes_the_longest_run():
    """三个依次向下不重叠的中枢是**一个**下跌趋势，不是两个。"""
    legs, zss = _zss([50, 40, 48, 42, 47, 10, 20, 12, 18, 14,
                      30, 2, 6, 3, 5, 4])
    assert len(zss) == 3
    assert all(relation(a, b) == TREND_DOWN for a, b in zip(zss, zss[1:]))
    _, _, trends = build_layer(legs, zss, 0)
    assert [t.kind for t in trends] == [TREND_DOWN]
    assert len(trends[0].zhongshus) == 3


def test_direction_change_breaks_the_run():
    """同一个走势类型里不许既有 up 又有 down。"""
    legs, zss = _zss(_OSC9 + _DOWN)
    _, _, trends = build_layer(legs, zss, 0)
    for t in trends:
        rs = {relation(a, b) for a, b in zip(t.zhongshus, t.zhongshus[1:])}
        assert len(rs) <= 1


# --------------------------------------------------------------------------
# 走势类型首尾相接（72课「前后两个走势类型，就以该背驰点为分界」）
# --------------------------------------------------------------------------


def test_trend_includes_the_departure_leg_of_its_last_zhongshu():
    """末枢的离开段是 aAbBc 里的那个 c，归本走势类型。

    早先取的是 `end_leg`（最后一根**延伸**段），离开段在它后面一根，于是每两个
    走势类型之间漏掉一根段。600009 30分钟因此丢了段11 和 段21，8.69 元和
    10.31 元的真实走势在 L1 根本不存在。
    """
    legs, zss = _zss(_GAP)
    assert zss[0].depart_leg == zss[0].end_leg + 1   # 离开段在 end_leg 之外
    _, _, trends = build_layer(legs, zss, 0)
    assert trends[0].end_leg == zss[0].depart_leg


def test_an_unfinished_last_zhongshu_leaves_its_tail_unclaimed():
    """`depart_leg is None` = 末枢还没走出去，走势类型未完成。

    此时收在 `end_leg`，后面的段如实留着没人认领——不许假装它完成了。
    这里让末段未确认（C-confirm：没确认的段不参与中枢），于是中枢停在 段3，
    段4 真的悬在外面——而不是被延伸吞掉。
    """
    legs = mk_legs([10, 20, 12, 18, 13, 19], last_unconfirmed=True)
    zss = build_zhongshus(legs)
    assert len(zss) == 1 and zss[0].depart_leg is None
    _, _, trends = build_layer(legs, zss, 0)
    assert trends[0].end_leg == zss[0].end_leg
    assert trends[0].end_leg < len(legs) - 1   # 尾巴确实空着


def test_trends_are_contiguous_and_the_upper_polyline_joins_up():
    """分界只是一个**点**，两边都不许留空。

    下一个中枢未必紧接着离开段就成形（`build_zhongshus` 会往前滑着找），中间
    那几根段是下一个走势类型的进入段，不是无主之物。不接上的话，上一层的折线
    就是断的，而 `build_zhongshus` 求三段重叠时默认它首尾相接。
    """
    legs, zss = _zss(_GAP)
    assert len(zss) == 2
    assert zss[1].start_leg > zss[0].depart_leg + 1, "构造失败：中间没有空隙"

    _, _, trends = build_layer(legs, zss, 0)
    assert len(trends) >= 2
    for p, c in zip(trends, trends[1:]):
        assert c.start_leg == p.end_leg + 1, "走势类型之间不许留空"
        assert c.start_price == p.end_price, "上一层的折线必须首尾相接"
    # 升上去那串 Leg 也得连得上
    l1 = [trend_to_leg(t, i) for i, t in enumerate(trends)]
    for p, c in zip(l1, l1[1:]):
        assert p.end_price == c.start_price
        assert c.src_start == p.src_end + 1


def test_first_trend_does_not_swallow_the_legs_before_the_first_zhongshu():
    """第一个中枢之前没有任何结构，那些段无从归类，留着不认领。"""
    legs = mk_legs([30, 10, 18, 12, 17, 2, 11, 4, 10, 5])
    zss = build_zhongshus(legs, require_confirmed=False, start=1)
    _, _, trends = build_layer(legs, zss, 0)
    assert trends[0].start_leg == zss[0].start_leg == 1
    assert trends[0].start_leg > 0


# --------------------------------------------------------------------------
# 两端的方向（aAbBc 里 a 和 c 与趋势同向是**定义的一部分**）
# --------------------------------------------------------------------------


def test_goes_with_only_constrains_trends():
    """上涨趋势 a↑A b↑B c↑，下跌趋势 a↓A b↓B c↓；盘整没有先验方向。"""
    up = Leg(index=0, direction=Direction.UP, start_price=1, end_price=2,
             low=1, high=2, src_start=0, src_end=0, confirmed=True)
    dn = Leg(index=1, direction=Direction.DOWN, start_price=2, end_price=1,
             low=1, high=2, src_start=1, src_end=1, confirmed=True)
    assert _goes_with(TREND_UP, up) and not _goes_with(TREND_UP, dn)
    assert _goes_with(TREND_DOWN, dn) and not _goes_with(TREND_DOWN, up)
    # 盘整的方向就是由这两根段定出来的，没什么可违反的
    assert _goes_with(PANZHENG, up) and _goes_with(PANZHENG, dn)


def test_a_trend_does_not_end_on_a_counter_directional_departure():
    """离开段朝反方向时，它是**下一个**走势类型的 a，不是这一个的 c。

    不查的后果是标签说反话（300308 日线「下跌」309.30 → 799.44）。此时退回
    `core_end` 而**不是** `end_leg`——三段中枢里 `end_leg` 就是离开段，退到
    `end_leg` 等于没退（实测 liar 25 个里有 16 个栽在这上面）。
    """
    legs = mk_legs(_CDIR)
    zss = build_zhongshus(legs, require_confirmed=False)
    assert len(zss) == 2 and relation(*zss) == TREND_DOWN
    last = zss[1]
    assert last.depart_leg == 9
    assert legs[9].direction is Direction.UP, "构造失败：离开段得朝上才有戏"

    _, _, trends = build_layer(legs, zss, 0)
    t = trends[0]
    assert t.kind == TREND_DOWN
    assert t.end_leg == last.core_end == 8, "收在 core_end，不是离开段也不是 end_leg"
    assert t.end_price < t.start_price
    # 用户看见的那个毛病：L1 的一根段里有比它开头更高的点
    assert t.high == t.start_price


def test_no_trend_runs_against_its_own_direction_at_either_end():
    """随机扫一遍：**走势类型两端都落在朝自己方向走的段上**。

    这条用性质而不是定点来测，因为两头的毛病长得不一样（c 那头是奇偶，a 那头
    是进入段跑反），定点测例盖不全。`start_price`/`end_price` 因此各是该方向上
    的一个局部极值，而不是半路上的随便一点。

    唯一豁免：第一个走势类型的 `start_leg` 等于首枢的第一构成段——它的 a 在
    本级别起点之前，无从追溯，那不是进入段。
    """
    rng = random.Random(20251019)
    seen = 0
    for _ in range(600):
        # 必须**严格锯齿**：真实的段一定交替，而两端方向这件事整个建立在交替
        # 带来的奇偶性上。随手撒点会造出同向相邻的「段」，那是现实里不存在的
        # 输入，拿它当反例只会把人引到错的地方。
        pts, up = [round(rng.uniform(5, 60), 1)], rng.random() < .5
        for _ in range(rng.randint(5, 15)):
            step = rng.uniform(.5, 14.0)
            pts.append(round(pts[-1] + (step if up else -step), 1))
            up = not up
        legs = mk_legs(pts)
        zss = build_zhongshus(legs, require_confirmed=False)
        if not zss:
            continue
        _, _, trends = build_layer(legs, zss, 0)
        for t in trends:
            if not t.zhongshus:          # NO_ZHONGSHU 没有方向可言
                continue
            seen += 1
            assert _goes_with(t.kind, legs[t.end_leg]), f"c 跑反了: {t} / {pts}"
            if t.start_leg < t.zhongshus[0].start_leg:   # 真的是进入段
                assert _goes_with(t.kind, legs[t.start_leg]), f"a 跑反了: {t} / {pts}"
    assert seen > 300, f"构造的样本太少，这条测试没在测什么（{seen}）"


# --------------------------------------------------------------------------
# NO_ZHONGSHU：本级别看不出中枢的那一程
# --------------------------------------------------------------------------


def test_legs_that_form_no_zhongshu_get_their_own_trend():
    """凑不出「三段重叠」的那几根段既不是盘整也不是趋势，单独标出来。

    丢掉会在上一层的折线里戳个洞（C-join）；硬塞给邻居，标签就说反话。标成
    `NO_ZHONGSHU` 对操作本身有用：**这里要降一个级别看**。
    """
    legs, zss = _zss(_GAP)
    _, _, trends = build_layer(legs, zss, 0)
    gaps = [t for t in trends if t.kind == NO_ZHONGSHU]
    assert len(gaps) == 1
    g = gaps[0]
    assert g.zhongshus == [], "盘整和趋势都至少要一个中枢，这个一个都没有"
    assert (g.start_leg, g.end_leg) == (5, 5)
    # 它照样接在折线上，两头都不留空
    assert trends[0].end_price == g.start_price == legs[5].start_price
    assert g.end_price == trends[2].start_price


def test_the_entry_leg_is_exactly_one_leg():
    """20课 p43 的 a 是**一根**段，不是一串；多出来的另立门户。

    并进来的后果是标签直接说反话（600519 日线 走势1「down」25.68 → 453.84，
    7 根进入段把 25.68 一路涨到 658 的那一程全吞了）。
    """
    legs, zss = _zss(_GAP)
    _, _, trends = build_layer(legs, zss, 0)
    for t in trends[1:]:
        if t.zhongshus:
            assert t.zhongshus[0].start_leg - t.start_leg <= 1


# --------------------------------------------------------------------------
# C-sub：并进趋势的中枢不拆，但三等分的结果留着
# --------------------------------------------------------------------------


def test_substructure_records_the_split_reading_without_splitting():
    """走势类型一根不动（方向保住），次级别读法另挂一行。

    让位的是**结构**，不是**信息**。600009 30分钟的走势3 占 21 个月，方向对、
    aAbBc 对，可是一个标签管这么久中间无处下手；硬拆开又会把「这是一个下跌
    趋势」整个读没。33课 p79 的走势多义性不是二选一。
    """
    legs, zss = _zss(_OSC9 + _DOWN)
    zss2, split_from, trends = build_layer(legs, zss, 0)
    assert split_from == {} and len(zss2) == 2, "并进趋势的中枢没被拆"
    (t,) = trends
    assert t.kind == TREND_DOWN

    sub = t.substructure
    assert [k.index for k in sub] == [0, 1, 2]
    assert [(k.start_leg, k.end_leg) for k in sub] == [(0, 2), (3, 5), (6, 8)]
    # 和真拆一遍给出的是同一个东西，只是没动本层中枢序列
    assert [(k.zd, k.zg) for k in sub] == [
        (k.zd, k.zg) for k in split_extended(legs, zss[0])]
    # 子结构盖的是父中枢的核心段，不越界
    assert sub[0].start_leg == zss[0].start_leg
    assert sub[-1].end_leg == zss[0].core_end


def test_substructure_is_empty_when_nothing_is_over_extended():
    legs, zss = _zss(_GAP)
    _, _, trends = build_layer(legs, zss, 0)
    assert all(t.substructure == [] for t in trends)


def test_substructure_does_not_touch_the_layer_above():
    """纯附加：本层中枢序列、relation、上一层都不受影响。"""
    legs = mk_legs(_OSC9 + _DOWN)
    layers = build_tree(legs, require_confirmed=False)
    for L in layers:
        for t in L.trends:
            assert all(k.start_leg >= t.start_leg and k.end_leg <= t.end_leg
                       for k in t.substructure)
            # 子结构不在本层中枢序列里
            assert all(k is not z for k in t.substructure for z in L.zhongshus)


# --------------------------------------------------------------------------
# 走势类型 → 上一层的 Leg
# --------------------------------------------------------------------------


def test_trend_to_leg_uses_true_extremes_not_the_zhongshu_range():
    """71课 p190：端点未必是极值。17课 p36 的中枢公式要的是真实高低点。"""
    legs, zss = _zss(_OSC9 + _DOWN)
    _, _, trends = build_layer(legs, zss, 0)
    t = trends[0]
    leg = trend_to_leg(t, 0)
    assert leg.high == max(x.high for x in legs[t.start_leg : t.end_leg + 1])
    assert leg.low == min(x.low for x in legs[t.start_leg : t.end_leg + 1])
    assert leg.high > max(z.zg for z in t.zhongshus)
    assert (leg.src_start, leg.src_end) == (t.start_leg, t.end_leg)
    assert leg.direction is Direction.DOWN


# --------------------------------------------------------------------------
# 建树
# --------------------------------------------------------------------------


def test_build_tree_level_zero_always_exists():
    """level 0 没中枢也要留一层空的，调用方才有地方读 legs。"""
    layers = build_tree(mk_legs([10, 20, 5, 8]), require_confirmed=False)
    assert len(layers) == 1
    assert layers[0].level == 0 and layers[0].zhongshus == []


def test_build_tree_stops_when_a_layer_has_under_three_trends():
    legs = mk_legs(_OSC9 + _DOWN)
    layers = build_tree(legs, require_confirmed=False)
    assert len(layers) == 1          # 只有一个下跌趋势，上一层凑不出三段
    assert [t.kind for t in layers[0].trends] == [TREND_DOWN]


def test_start_only_shifts_level_zero():
    """33课 p79 的起点多义性只在本级别有自由度，上面各层由切分唯一决定。"""
    legs = mk_legs([30, 10, 18, 12, 17, 2, 11, 4, 10, 5])
    a = build_tree(legs, require_confirmed=False, start=0)
    b = build_tree(legs, require_confirmed=False, start=1)
    assert a[0].zhongshus[0].start_leg != b[0].zhongshus[0].start_leg


# --------------------------------------------------------------------------
# 落回K线下标
# --------------------------------------------------------------------------


def test_leg_bars_climbs_down_every_layer():
    """上层 leg 的 `src_*` 指的是**下一层 legs**，不是笔，所以得逐层翻。

    翻错一层不会抛异常——上层 legs 的下标空间是下层的子集，拿它去索引下层
    照样取得到东西，只是取到的是别人的段。所以这里钉的是「区间单调、落在
    K线范围内」：翻错层的话两端会塌到一起或者整体乱序。
    """
    rng = random.Random(20251020)
    layered = 0
    for _ in range(200):
        pts, up = [round(rng.uniform(5, 60), 1)], rng.random() < .5
        for _ in range(rng.randint(40, 90)):
            step = rng.uniform(.5, 14.0)
            pts.append(round(pts[-1] + (step if up else -step), 1))
            up = not up
        legs = mk_legs(pts)
        bis = mk_bis(len(legs))
        n_bars = len(bis) * 4 + 1
        layers = build_tree(legs, require_confirmed=False)
        layered += len(layers) > 1
        for L in layers:
            prev = -1
            for i in range(len(L.legs)):
                lo, hi = leg_bars(layers, bis, L.level, i)
                assert 0 <= lo <= hi < n_bars, f"L{L.level} 段{i} 区间越界"
                # 同一层的段首尾相接，所以下一根的起点就是上一根的终点
                assert lo >= prev, f"L{L.level} 段{i} 的起点往回跳了"
                prev = lo
    assert layered > 50, f"构造出的多层树太少，递归那一段没测到（{layered}）"


def test_adjacent_trends_join_up_on_the_bar_axis():
    """走势类型是时间轴的**划分**：前后相接、不重叠。

    72课：「前后两个走势类型，就以该背驰点为分界」——分界是一个**点**，不是
    一段空白，也不是一段共有的重叠区。换掉 `trailing_trend` 口径的全部理由
    就是这条性质（它从末枢往回捋，会把已经归属上一个走势类型的中枢再认领
    一遍），所以在K线轴上钉死。

    相邻两段共用那一个分界点，于是后一段的起点 == 前一段的终点；段与段之间
    在K线轴上**不允许**有缺口，`NO_ZHONGSHU` 那种没人认领的一程也已经被
    `_mk_gap` 补成了一个走势类型。
    """
    rng = random.Random(20251021)
    seen = 0
    for _ in range(200):
        pts, up = [round(rng.uniform(5, 60), 1)], rng.random() < .5
        for _ in range(rng.randint(30, 80)):
            step = rng.uniform(.5, 14.0)
            pts.append(round(pts[-1] + (step if up else -step), 1))
            up = not up
        legs = mk_legs(pts)
        bis = mk_bis(len(legs))
        layers = build_tree(legs, require_confirmed=False)
        for L in layers:
            spans = [trend_bars(layers, bis, t) for t in L.trends]
            for t, (lo, hi) in zip(L.trends, spans):
                assert lo <= hi, f"L{L.level} 走势{t.index} 区间是空的"
            for (_, hi), (lo, _) in zip(spans, spans[1:]):
                seen += 1
                assert lo == hi, (f"L{L.level} 两个走势类型没接上："
                                  f"前一个收在 {hi}，后一个从 {lo} 起")
    assert seen > 300, f"相邻对太少，这条测试没在测什么（{seen}）"
