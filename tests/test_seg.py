"""线段。对照 67课 p172（定义）+ 71课 pp.181-182（再分辨）+ 77/78课（性质）。"""

from __future__ import annotations

import pytest
from conftest import mk_bis

from chan.invariants import InvariantError, check_segs
from chan.seg import (
    CASE_ENGULF,
    PENDING,
    _breaker_seg_end,
    _contains,
    _resolve_pending,
    _try_end,
    breaker_never_exceeds,
    breaker_passes_c22,
    build_segs,
    elem_of,
    find_seq_fractal,
    has_seq_gap,
    standardize,
    three_bi_overlap,
)
from chan.types import Direction, MergeConfig, SeqElem


def E(low: float, high: float, *idx: int) -> SeqElem:
    return SeqElem(low=low, high=high, bi_indices=list(idx) or [0])


# --------------------------------------------------------------------------
# 特征序列基础件
# --------------------------------------------------------------------------


def test_seq_gap_is_about_overlap_not_candles():
    """67课 p172：「特征序列两相邻元素间没有重合区间，称为该序列的一个缺口」。

    这和 77课 p194 的K线跳空是同名不同物的两个概念。
    """
    assert has_seq_gap(E(20, 30), E(5, 15))
    assert not has_seq_gap(E(20, 30), E(15, 25))
    # 端点接触按「算重合、不算缺口」处理（CONVENTIONS C-seqtouch）
    assert not has_seq_gap(E(20, 30), E(10, 20))


def test_characteristic_elements_are_opposite_to_the_segment():
    """67课 p172：以向上笔开始的线段，特征序列由**向下笔**构成。"""
    bis = mk_bis([0, 10, 5, 15, 8, 20])
    # 线段从 bis[0]（向上）开始，特征元素是 bis[1], bis[3], bis[5]…
    assert bis[0].direction is Direction.UP
    for k in (1, 3):
        assert bis[k].direction is Direction.DOWN
    assert elem_of(bis, 1).low == 5 and elem_of(bis, 1).high == 10


def test_standardize_merges_inclusion():
    out = standardize([E(8, 15, 0), E(10, 20, 1), E(12, 18, 2)], Direction.UP)
    assert len(out) == 2
    # [10,20] 包含 [12,18]，参照 [8,15] 判定为向上 → [max d, max g]
    assert (out[1].low, out[1].high) == (12, 20)
    assert out[1].bi_indices == [1, 2]


def test_standardize_direction_follows_previous_element():
    up = standardize([E(8, 15), E(10, 20), E(12, 18)], Direction.UP)
    down = standardize([E(22, 30), E(10, 20), E(12, 18)], Direction.UP)
    assert (up[1].low, up[1].high) == (12, 20)
    # 参照元素更高 → 向下合并 → [min d, min g]
    assert (down[1].low, down[1].high) == (10, 18)


def test_find_seq_fractal():
    seq = [E(22, 26), E(18, 23), E(20, 27)]
    assert find_seq_fractal(seq, want_top=False) == 1
    assert find_seq_fractal(seq, want_top=True) is None


def test_three_bi_overlap():
    """77课 p196：「线段开始的那三笔，必须有重合」。"""
    overlapping = mk_bis([0, 10, 5, 12])
    assert three_bi_overlap(overlapping, 0)
    # 第三笔根本回不到第一笔的起点之上，三笔没有公共重合区间：
    # [5,10] ∩ [4,10] ∩ [4,4.5] = ∅
    runaway = mk_bis([5, 10, 4, 4.5])
    assert not three_bi_overlap(runaway, 0)


# --------------------------------------------------------------------------
# 第一种情况
# --------------------------------------------------------------------------

CASE1 = [0, 10, 5, 15, 8, 20, 12, 18, 6, 14, 3, 9, 1]


def test_case_one_segment():
    """第一元素与第二元素间无缺口 → 第一种情况，顶分型一成立线段就结束。

    序列在 20 见顶：第一元素 bis[3]=[8,15]，第二元素 bis[5]=[12,20]，两者
    重合（12 < 15）所以是第一种情况；(e1, [12,20], [6,18]) 构成顶分型。
    """
    bis = mk_bis(CASE1)
    segs = build_segs(bis)
    done = [s for s in segs if s.confirmed]
    assert len(done) == 1
    s = done[0]
    assert s.case == 1
    assert (s.start_bi, s.end_bi) == (0, 4)
    assert s.direction is Direction.UP
    assert s.bi_count == 5
    assert (s.start_price, s.end_price) == (0, 20)
    check_segs(segs, bis)


def test_trailing_segment_is_provisional():
    """78课 p197：线段必须被线段破坏才算完成。末端那段永远未确认。"""
    segs = build_segs(mk_bis(CASE1))
    assert not segs[-1].confirmed
    assert segs[-1].case == 0


# --------------------------------------------------------------------------
# 第二种情况
# --------------------------------------------------------------------------

CASE2 = [0, 10, 5, 15, 8, 30, 22, 26, 18, 23, 20, 27, 14]


def test_case_two_segment():
    """第一元素与第二元素间**有缺口** → 第二种情况。

    8→30 这一笔拉出缺口：第一元素 bis[3]=[8,15]，第二元素 bis[5]=[22,30]，
    15 < 22 无重合。此时必须等「从该分型最高点开始的向下一笔开始的序列的
    特征序列出现底分型」——即 bis[6]/bis[8]/bis[10] 这三个向上笔构成底分型
    ——线段才在 30 处结束。
    """
    bis = mk_bis(CASE2)
    assert has_seq_gap(elem_of(bis, 3), elem_of(bis, 5))
    segs = build_segs(bis)
    done = [s for s in segs if s.confirmed]
    assert done[0].case == 2
    assert (done[0].start_bi, done[0].end_bi) == (0, 4)
    assert done[0].end_price == 30
    check_segs(segs, bis)


def test_case_two_then_case_one():
    """第二段在 30→18 处按第一种情况结束，两段方向交替、首尾相接。"""
    bis = mk_bis(CASE2)
    done = [s for s in build_segs(bis) if s.confirmed]
    assert len(done) == 2
    assert done[1].direction is Direction.DOWN
    assert done[1].case == 1
    assert (done[1].start_bi, done[1].end_bi) == (5, 7)
    assert (done[1].start_price, done[1].end_price) == (30, 18)


def test_case_two_needs_three_elements():
    """71课 p181：「如果第二特征序列没有三个元素，就根本不存在出现分段中
    第二种情况的可能」。把数据截短到第二特征序列只剩两个元素就不该成段。"""
    short = build_segs(mk_bis(CASE2[:11]))
    assert [s for s in short if s.confirmed] == []


# --------------------------------------------------------------------------
# 71课 p181 的待定状态
# --------------------------------------------------------------------------

PENDING_BREAKS_END = [0, 10, 5, 15, 8, 20, 10, 18, 12, 16, 9]
PENDING_BREAKS_START = [0, 10, 5, 15, 8, 20, 10, 18, 12, 16, 11, 25]


def test_pending_resolved_by_breaking_the_first_bi_end():
    """71课 p181：第三笔完全在第一笔范围内 → 三笔分不出方向，特征序列定义
    不了。出口一：「最终还是先破了第一笔的结束位置，这时候，新的线段显然
    成立，旧线段还是被破坏了」。

    转折点 20 下来的第一笔是 20→10，第三笔 18→12 完全落在 [10,20] 内。
    后面先跌破 10（到 9）→ 旧线段在 20 处结束。
    """
    bis = mk_bis(PENDING_BREAKS_END)
    done = [s for s in build_segs(bis) if s.confirmed]
    assert len(done) == 1
    assert done[0].end_bi == 4
    assert done[0].end_price == 20


def test_pending_resolved_by_breaking_the_first_bi_start():
    """出口二：「最终，先破第一笔的开始位置，这样，旧线段只被一笔破坏，
    接着就延续原来的方向，那么，显然旧线段依然延续，新线段没有出现」。

    同样的形态，但后面先升破 20（到 25）→ 向上线段继续，不在 20 处分段。
    """
    bis = mk_bis(PENDING_BREAKS_START)
    done = [s for s in build_segs(bis) if s.confirmed]
    assert all(s.end_bi != 4 for s in done)


def test_pending_switch_changes_behaviour():
    """关掉待定处理后，这种形态会退化成普通的特征序列包含处理。"""
    bis = mk_bis(PENDING_BREAKS_START)
    off = build_segs(bis, MergeConfig(enable_pending_seg=False))
    on = build_segs(bis, MergeConfig(enable_pending_seg=True))
    assert [s.end_bi for s in off if s.confirmed] != [
        s.end_bi for s in on if s.confirmed
    ]


# --------------------------------------------------------------------------
# 78课 p197 的不变量
# --------------------------------------------------------------------------


def test_invariants_hold_on_fixtures():
    for prices in (CASE1, CASE2, PENDING_BREAKS_END, PENDING_BREAKS_START):
        bis = mk_bis(prices)
        check_segs(build_segs(bis), bis)


def test_invariant_catches_even_bi_count():
    bis = mk_bis(CASE1)
    segs = build_segs(bis)
    segs[0].end_bi += 1  # 人为破坏：笔数变成偶数
    with pytest.raises(InvariantError, match="偶数"):
        check_segs(segs, bis)


def test_invariant_catches_top_below_bottom():
    """78课 p197：「同一线段中，两端的一顶一底，顶肯定要高于底，如果你划出
    一个不符合这基本要求的线段，那肯定是划错了」。"""
    bis = mk_bis(CASE1)
    segs = build_segs(bis)
    s = segs[0]
    s.start, s.end = s.end, s.start  # 人为倒置两端
    with pytest.raises(InvariantError):
        check_segs(segs, bis)


# --------------------------------------------------------------------------
# 真实数据抓出来的两个 bug（600519 后复权日线）
# --------------------------------------------------------------------------


def test_breaker_must_itself_satisfy_top_above_bottom():
    """C-win：破坏方也是线段，它**自己的终点**要过 78课 p197 的顶高于底。"""
    # 向上段在 pivot=50 处假设转折，破坏方是向下段，终点须低于 50
    assert breaker_passes_c22(E(40, 48), 50, Direction.UP)
    assert not breaker_passes_c22(E(78, 93), 50, Direction.UP)  # 终点反而更高
    # 向下段镜像
    assert breaker_passes_c22(E(55, 62), 50, Direction.DOWN)
    assert not breaker_passes_c22(E(30, 45), 50, Direction.DOWN)


def test_breaker_c22_constrains_the_endpoint_not_every_bar_along_the_way():
    """约束的是破坏段的终点，不是沿途每一根。

    一条 50→40 的向下线段成立，哪怕价格随后涨到 55——那个 55 属于**再下一条**
    线段，不能回头否定它。早先这里写成「沿途一旦越回 pivot 就作废」，600519
    从 2003-09 到 2004-10 整整 13 个月的上涨会因此被当成一条线段切不开。
    """
    assert breaker_passes_c22(E(40, 50), 50, Direction.UP)


def test_segment_not_confirmed_by_a_fractal_after_price_ran_away():
    """C-win 的行情版：价格早就站回转折点之上，后面再出分型也不算。

    原型是 600519 的 2003-11-05：29.68 被当成向上线段终点，而「确认」它的
    第二特征序列底分型出现在 2005-12 的 78~93 元，价格两笔之后就已经站上
    29.68 了。缺了窗口，一轮 23 年的上涨会被切成「一段涨 + 一段永远走不完
    的跌」。
    """
    # 上涨 → 在 100 假装转折 → 只回撤到 95 → 之后一路单边上行，
    # 途中必然出现若干次回调，随便哪一次都能凑出「底分型」
    pts = [0.0, 50.0, 30.0, 100.0, 95.0]
    p = 100.0
    for _ in range(12):  # 单边上行，每轮一进一退
        p *= 1.4
        pts.append(p)
        pts.append(p * 0.93)
    segs = build_segs(mk_bis(pts))
    pivots = [s.end_bi for s in segs if s.confirmed]
    # 下标 3 就是那个 100 的假转折点，绝不能被确认成线段终点
    assert 3 not in pivots
    assert all(s.direction is Direction.UP for s in segs)


def test_engulfing_element_ends_the_segment_600519_2003_bottom():
    """C-engulf：转折点两侧出现包含 → 段**已破坏**，不是「没分型所以继续」。

    数据是 600519 后复权日线 2003-04-14 ~ 2004-02 的真实笔序列。向下线段
    在 2003-09-23 的 25.88 见底，但转折点后的反弹笔 [25.88, 29.68] 吞掉了
    转折点前的反弹笔 [26.23, 28.05]，底分型在结构上永远凑不出来
    （_is_bottom 要求 after[0].high < e1.high，而 after[0] ⊇ e1）。

    不特判的话这条向下线段一路挂到 2026 年、吞掉 576 笔。
    """
    pts = [30.74, 26.56, 28.43, 27.72, 29.18, 27.83, 30.47, 28.17,
           28.77, 26.23, 28.05, 25.88, 29.68, 28.18, 29.75, 28.89, 36.33]
    segs = build_segs(mk_bis(pts))
    first = segs[0]
    assert first.direction is Direction.DOWN
    assert first.confirmed
    assert first.end_bi == 10          # 终点就是 25.88 那根笔
    assert first.case == CASE_ENGULF
    # 吞没关系确实成立，且标准分型确实不可能通过
    bis = mk_bis(pts)
    e1, e2 = elem_of(bis, 9), elem_of(bis, 11)
    assert e2.low <= e1.low and e1.high <= e2.high


def test_engulf_is_undecided_until_the_breaking_bi_survives_600519_2008():
    """C-engulf 的出口二：吞没只是「待定」，破坏笔自己先被破掉就不算。

    600519 后复权日线 2007-12 ~ 2008-10 的真实笔序列。向下线段从 876.08 起跌，
    在 2008-02-27 的 699.03 处，反弹笔 [699.03, 871.55] 吞掉了前一次反抽
    [754.90, 799.82]——四天弹 25%，看着力度十足；可再下一笔直接砸到 617.10，
    破掉 699.03，随后是整轮崩盘。

    71课 p181 出口二：「先破第一笔的开始位置，这样，旧线段只被一笔破坏，接着
    就延续原来的方向，那么，显然旧线段依然延续，新线段没有出现。」
    """
    pts = [876.08, 794.92, 877.78, 754.90, 799.82, 699.03, 871.55, 617.10,
           717.60, 493.79, 633.44, 485.07, 523.99, 342.82, 367.70, 329.95,
           461.70, 404.39, 466.61]
    bis = mk_bis(pts)
    # 吞没关系确实成立，不加出口二这里就会误切
    assert _contains_elem(elem_of(bis, 5), elem_of(bis, 3))
    first = [s for s in build_segs(bis) if s.confirmed][0]
    assert first.direction is Direction.DOWN
    assert first.start_bi == 0
    assert first.end_bi > 4, "699.03 不是线段终点，2008 崩盘是一条向下线段"
    assert first.end_price == 329.95


def _contains_elem(outer, inner) -> bool:
    return outer.low <= inner.low and inner.high <= outer.high


def test_engulf_needs_full_containment_not_just_a_new_extreme():
    """只创新极值、没把前一次反抽吃掉，不算力度特别大，走常规分型检验。"""
    # 向下段，转折点后的反弹只回到 e1 区间内部，没吞掉它
    bis = mk_bis([30.0, 26.0, 29.0, 25.0, 27.0, 24.0, 26.5])
    e1, e2 = elem_of(bis, 3), elem_of(bis, 5)
    assert not (e2.low <= e1.low and e1.high <= e2.high)


# --------------------------------------------------------------------------
# 破坏段不许越过被破坏点（78课 p197 的另一半）
# --------------------------------------------------------------------------


def test_the_breaker_may_not_rise_above_the_top_it_claims_to_break():
    """300308 日线 2026-05-15 的实盘错划，缩成一串笔。

    向上段在 6961.6 被判结束，走的是情况二：e1=[5164.6,5819.6] 与
    e2=[6289.0,6961.6] 无重合，确是特征序列缺口；第二特征序列也确实凑出了
    底分型。缺的是一句常识——那条「向下线段」的第一笔就从 6289 直冲 8963，
    比它要破坏的那个顶还高 28.8%。

    顶被后面的笔越过，它就不是顶，6961.6 也就不是转折点。正确答案是段一直
    走到 8963（那里情况一的顶分型 bi60/bi62/bi64 干净成立）。

    不加这一条，640 根日线只划出 10 段，建在线段上的中枢退化到 1 个。
    """
    # 后面那两个价位（5434.6 / 6173.2）不能省：少了它们，第二特征序列标准化
    # 之后只剩两个元素，连 `len(second) < 3` 这一关都过不去，情况二根本不会
    # 触发——于是这条测试在**没有**修复的代码上也是绿的，等于什么都没测。
    bis = mk_bis([3206.0, 4083.5, 3515.3, 5819.6, 5164.6, 6961.6, 6289.0,
                  8963.0, 6712.0, 7644.1, 5024.4, 6545.0, 5289.4, 6425.0,
                  5257.8, 5917.5, 5434.6, 6173.2])
    # 缺口是真的，情况二的入口条件确实满足——错不在这里
    assert has_seq_gap(elem_of(bis, 3), elem_of(bis, 5))

    first = [s for s in build_segs(bis) if s.confirmed][0]
    assert first.direction is Direction.UP
    assert first.start_bi == 0
    assert first.end_price == 8963.0, "段该走到新高，不该停在被越过的 6961.6"


def test_what_happens_after_the_breaker_is_done_does_not_count():
    """窗口只到确认分型为止，分型之后再涨回去是**下一条**线段的事。

    和上面那条是一对：约束加过头的话，一条 50→40 的向下线段会因为之后涨到
    55 而被作废，而那个 55 根本不属于它（见 breaker_passes_c22 的说明）。
    """
    bis = mk_bis([50.0, 40.0, 48.0, 38.0, 46.0, 36.0])
    # 窗口内没有越过 50 的笔
    assert breaker_never_exceeds(bis, 0, 4, 50.0, Direction.UP)
    # 把窗口放宽到一根高过 50 的笔上，才该否掉
    bis2 = mk_bis([50.0, 40.0, 55.0, 38.0, 46.0, 36.0])
    assert not breaker_never_exceeds(bis2, 0, 4, 50.0, Direction.UP)


def test_an_unresolved_pending_point_does_not_freeze_the_rest():
    """待定 ≠ 到此为止。71课 p182：「如果不满足，那就不是，原来的线段依然延续。」

    一个始终没了结的待定点，两种情况一个都没满足，所以它不是分界点，该往后
    接着试——而不是把后面所有的笔都扣在一条走不完的线段里。

    形态取自 baostock/600006 的实盘：向下线段从 100 跌到 40（这里 j=2），
    紧接着一笔 40→95 的暴力反弹，差一点点没碰到起点 100。此后价格再没升破
    95、也再没跌破 40，`_resolve_pending` 两个出口都够不着，永远 None。

    早先这里是 `break`，于是 600006 真就把 134 笔、四年半全挂在一条未确认的
    线段上；428 只里 143 只(33%)末段卡在待定，冻住全样本 6% 的笔。
    """
    bis = mk_bis([100.0, 50.0, 70.0, 45.0, 68.0, 55.0, 66.0, 50.0, 64.0,
                  47.0, 62.0, 58.0, 67.0])
    # 确认走的是 p181 那个待定（第三笔落在第一笔内），不是 C-engulf：
    # 两者都叫待定，出口却不一样，走错分支这条测试就测不到想测的东西
    assert not _contains(elem_of(bis, 3), elem_of(bis, 1)), "不该是吞没"
    assert _contains(elem_of(bis, 3), elem_of(bis, 5)), "第三笔要落在第一笔内"
    # 待定是真的：j=2 触发，且到数据末尾都没了结
    assert _try_end(bis, 0, 2, Direction.DOWN, MergeConfig()) == PENDING
    assert _resolve_pending(bis, 2, Direction.DOWN) is None

    segs = build_segs(bis)
    done = [s for s in segs if s.confirmed]
    assert len(done) == 1, "跳过待定点后，后面那个干净的分界点应该成段"
    assert (done[0].start_bi, done[0].end_bi) == (0, 8)
    assert done[0].case == 1
    # 跳过的待定点要留痕，事后才查得出「终点为什么不是最低点」
    assert done[0].skipped_pending == 2
    check_segs(segs, bis)


def test_pending_still_freezes_when_nothing_later_confirms():
    """跳过待定不等于放宽确认。后面没有任何一点过得了检验时，照旧挂起。"""
    bis = mk_bis([100.0, 50.0, 70.0, 45.0, 68.0, 55.0, 66.0, 52.0, 65.0,
                  50.0, 64.0, 48.0, 63.0])
    segs = build_segs(bis)
    assert [s for s in segs if s.confirmed] == []
    assert segs[-1].pending and not segs[-1].confirmed


# --------------------------------------------------------------------------
# C-engulf 的待定窗口：封到破坏段自己走完为止
# --------------------------------------------------------------------------

# 600519 后复权日线 2024-05-07 ~ 2025-02-06 的真实笔序列，尾部接了一笔
# 2026-06-25 的低点 8800（中间那 30 多笔和本条判定无关，略去）。
S600519_2024 = [12390.0, 10058.8, 10763.1, 10031.0, 10883.8, 9683.9, 10450.0,
                9781.3, 10095.7, 8862.4, 13587.1, 10520.9, 11859.3, 10591.4,
                11237.7, 10746.7, 11162.5, 10273.9, 10763.6, 10115.0,
                10600.0, 8800.0]


def test_a_later_dip_cannot_veto_a_turn_the_breaker_already_sealed():
    """C-engulf 的待定窗口封到**破坏段自己走完**为止，不是封到数据末尾。

    600519 的 2024-09-19 低点 8862.4：破坏笔三周暴涨 53% 到 13587，吞掉了前
    一次反抽，是个标准的 C-engulf。它的出口要等「先破 13587 还是先破 8862.4」，
    可两边都要等很久——直到 **2026-06-25**，21 个月后、隔着 41 笔，一个 8827.4
    的低点先到了，于是整个 2024 年的 V 型被判成「不算转折」，12390→10115 划成
    一条向下线段，当中那根 53% 的反弹被整个吞掉。

    78课 p197 反过来读就能堵住：破坏段一旦自己走完了（这里是 2024-11-08 那条
    向上段），再往后的行情归它**后面那条**线段管，回头否定不了它。
    """
    bis = mk_bis(S600519_2024)
    j, d = 8, Direction.DOWN
    assert bis[j].end_price == 8862.4
    assert _contains(elem_of(bis, j + 1), elem_of(bis, j - 1)), "该是 C-engulf"

    # 机制本身：无界扫描会被尾部那一笔否掉，按破坏段的终点封窗就不会
    m = _breaker_seg_end(bis, j, d, MergeConfig())
    assert m == 11, "破坏段自己在 bi11（2024-11-08）就走完了"
    assert _resolve_pending(bis, j, d) is False, "无界扫描会被 21 个月后那一笔否掉"
    assert _resolve_pending(bis, j, d, last=m) is None

    done = [s for s in build_segs(bis) if s.confirmed]
    assert (done[0].start_bi, done[0].end_bi) == (0, 8)
    assert done[0].case == CASE_ENGULF
    assert done[0].end_price == 8862.4, "2024 年的底要成为分界点"
    assert done[1].direction is Direction.UP
    assert done[1].end_price == 11859.3, "那根 53% 的反弹要自成一段"


# 600023 后复权日线 2014-11-14 ~ 2015-08-27（从 bi22 起），
# 600025 后复权日线 2025-12-04 ~ 2026-09-14（从 bi164 起）。
S600023_2015 = [7.7829, 9.3126, 8.7625, 10.1178, 8.2794, 9.5005, 8.2928,
                9.0845, 8.588, 11.9427, 10.8021, 12.6808, 10.3459, 11.3255,
                10.735, 20.6381, 8.9444, 13.5746, 11.0328, 14.7425, 8.9719]
S600025_2026 = [11.962, 11.112, 11.5308, 10.9149, 13.1323, 11.5062, 12.233,
                11.5924, 12.2577, 11.3337, 12.6273, 10.9026, 13.0777,
                12.3596, 12.9139, 12.0068, 12.6493]


@pytest.mark.parametrize(
    "name, pts, d, pivot, m",
    [("600023", S600023_2015, Direction.UP, 10.1178, 15),
     ("600025", S600025_2026, Direction.DOWN, 10.9149, 11)],
    ids=["600023-2015", "600025-2026"],
)
def test_a_sealed_breaker_does_not_override_exit_two(name, pts, d, pivot, m):
    """出口二照样管用，**哪怕破坏段自己已经走完**。试过反过来，不行。

    上一条（``..._breaker_already_sealed``）把待定窗口封在破坏段的终点上。那之后
    出口二仍可能在窗口**内部**触发，看着自相矛盾：既然 bi[j+1..m] 自己是一条成立
    的线段，窗口里那些跌回枢轴之下的笔就是它的段内行情，按 71课 p190「没有人规定
    分段的结束位置一定是最高、最低的」似乎不该有否决权。

    所以试过加一条「走完了就让出口二失效」。这两只票说明不能这么干——它们的
    结构**完全一样**（C-engulf 成立、破坏段走完、窗口内出口二触发），差别只有
    越过枢轴的幅度：

      600025  bi174 跌到 10.9026，比枢轴 **低 0.11%**，五个月后。看着像噪声。
      600023  bi30  涨到 11.9427，比枢轴 **高 18%**，之后一路到 20.6381（+104%，
              那是 2015 年的真顶）。

    让「走完」压过出口二，600025 确实好看了（原本是一条横穿十个月的平线），
    代价是 600023 把整轮 2015 牛市埋进一条 10.1178→8.9444 的「向下线段」。十只票
    实测：越起点异常 21 条 (2.72%) → **60 条 (7.61%)**，最深 19.3% → **104%**。

    要分开这两例只能拿**幅度**当判据，而 C08（64课 p166「精度一旦预设，就一路
    保持」）不允许。那就按字面来：**0.11% 的破也是破。** 600025 那条 17 笔的
    横线是合规结果，不是 bug。
    """
    bis = mk_bis(pts)
    j, cfg = 2, MergeConfig()
    assert bis[j].end_price == pivot
    assert _contains(elem_of(bis, j + 1), elem_of(bis, j - 1)), "该是 C-engulf"
    assert _breaker_seg_end(bis, j, d, cfg) == m, "破坏段自己走完了"
    assert _resolve_pending(bis, j, d, last=m) is False, "可窗口内出口二先触发"
    assert _try_end(bis, 0, j, d, cfg) is None, "出口二说了算，这里不是分界点"
