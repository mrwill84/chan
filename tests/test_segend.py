"""当下判断段结束。对照 55课、61课、90课、106课 与 RSI 对照组。

这一套口径**不是原文算法**，是对原文那几段话的工程化读法（见 `chan/segend.py`
的模块文档和 CONVENTIONS C-segend）。所以测试的重点不是「判得准不准」——那要
靠实测统计，不是单元测试能回答的——而是两件事：

1. **默认路径一个字节都没动。** 这是全部历史实测数字的地基。
2. 拆出来的段**结构上合法**、前段进中枢层、新末段未确认、**不撞不变量**。
"""

from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta

import pytest
from conftest import mk_bis

from chan.invariants import check_all
from chan.macd import macd
from chan.pipeline import analyze
from chan.seg import build_segs
from chan.segend import METHODS, _ma_allows, provisional_end, split_tail
from chan.types import Bar, Direction, MergeConfig, Seg


STEP = 12  # 笔跨 12 根K线：MACD 的 slow=26 要有足够的样本才不是噪声

#: 不需要调用方额外喂数据的口径。`sublevel_1bs` 要逐步喂次级别1类点，单独测。
PLAIN = [m for m in METHODS if m != "sublevel_1bs"]


def _closes_for(bis):
    closes = [bis[0].start_price]
    for b in bis:
        a, z = b.start.peak_idx, b.end.peak_idx
        for i in range(a + 1, z + 1):
            closes.append(b.start_price
                          + (b.end_price - b.start_price) * (i - a) / (z - a))
    return closes


def _macd_for(bis):
    """造一条**沿着这些笔走**的收盘价序列，再算它的 MACD。

    不能随便拿一条无关的价格序列顶替：`bi_beichi` 和 `force` 比的就是价格
    走势和 MACD 的背离，喂进去一条和笔毫无关系的曲线，测的就不是它们了
    （第一版这么干，结果这两个方法一次都没触发，等于没测）。
    """
    return macd(_closes_for(bis))


def _bars_for(bis):
    closes = _closes_for(bis)
    start = date(2020, 1, 6)
    return [Bar(i, (start + timedelta(days=i)).isoformat(), c, c, c, c)
            for i, c in enumerate(closes)]


def _bis(prices):
    return mk_bis(prices, m_step=STEP)


# 摆幅**递增**的上涨：力度在加强，背驰类的口径不该出声
LONG_UP = [10.0, 20.0, 15.0, 25.0, 18.0, 30.0, 24.0, 36.0, 30.0, 42.0,
           35.0, 50.0, 44.0, 58.0]

# 摆幅**递减**的上涨：节节新高但一浪比一浪弱，这是背驰的标准形态
DECEL_UP = [10.0, 40.0, 25.0, 52.0, 42.0, 58.0, 52.0, 60.5, 56.0, 61.5,
            58.5, 62.0, 60.5, 62.5]

PATHS = {"递增": LONG_UP, "递减": DECEL_UP}


# --------------------------------------------------------------------------
# 一、回归锁：默认路径没被碰
# --------------------------------------------------------------------------


def test_default_is_byte_identical_to_not_passing_the_option():
    """`seg_end=None` 时划线结果与不传该参数时逐字段相等。

    这条是整个改动的安全带。`MergeConfig.seg_end` 的默认值**不要为了让某个
    方法好看就改**——改了之后所有既有实测数字就失去对照组了。
    """
    bis = _bis(LONG_UP)
    a = build_segs(bis, MergeConfig())
    b = build_segs(bis, MergeConfig(seg_end=None))
    assert a == b
    assert all(s.prov_end == "" for s in a), "正路划出来的段不该带口径标记"


def test_unknown_method_raises():
    bis = _bis(LONG_UP)
    segs = build_segs(bis, MergeConfig())
    with pytest.raises(ValueError, match="未知的段结束口径"):
        provisional_end(bis, _macd_for(bis), segs[-1], "没这个口径")


# --------------------------------------------------------------------------
# 二、结构闸门：78课 p197 + 77课 p196
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", list(PATHS))
@pytest.mark.parametrize("method", PLAIN)
def test_split_is_structurally_legal(method, shape):
    """拆出来的两段：前段完成、末段未确认、首尾相接、方向交替、首段奇数笔。"""
    bis = _bis(PATHS[shape])
    segs = build_segs(bis, MergeConfig())
    out = split_tail(segs, bis, _macd_for(bis), method, bars=_bars_for(bis))
    if out is segs:
        pytest.skip(f"{method} 在「{shape}」上没触发——不触发是结果不是缺陷")

    first, second = out[-2], out[-1]
    assert first.confirmed is True, (
        "前段必须算完成，否则 `zhongshu.usable_count` 会把它从尾部剪掉，"
        "整个口径退化成基线（见 split_tail 文档）"
    )
    assert second.confirmed is False, "新末段仍然是没走完的那一段"
    assert first.prov_end == method and second.prov_end == ""
    assert second.start_bi == first.end_bi + 1, "两段必须首尾相接"
    assert first.direction is segs[-1].direction
    assert second.direction is first.direction.flip()
    assert bis[first.start_bi].direction is first.direction
    assert bis[first.end_bi].direction is first.direction, "78课 p197"
    assert first.bi_count >= 3 and first.bi_count % 2 == 1, "77课 p196"


def test_bi_beichi_tells_strengthening_from_weakening():
    """背驰那一条必须**分得开**两种形态，否则它只是个随机开关。

    两条路径都是节节新高的上涨，区别只在摆幅：递增 = 力度在加强，不该出声；
    递减 = 一浪比一浪弱，这正是 24课 p52 的背驰。

    这里测的是**判据本身**（`_m_bi_beichi`），闸门另测。从前这条直接走
    `provisional_end`，而递减路径的背驰笔恰好是还在走的最后一笔——它能通过
    全靠 `_legalize` 把它挪到上一个更低的峰，测试是被那个 bug 撑绿的。
    """
    from chan.segend import _m_bi_beichi

    up = _bis(LONG_UP)
    down = _bis(DECEL_UP)
    assert _m_bi_beichi(up, _macd_for(up), build_segs(up, MergeConfig())[-1]) \
        is None, "力度在加强却报了背驰"
    assert _m_bi_beichi(down, _macd_for(down), build_segs(down, MergeConfig())[-1]) \
        == len(down) - 1, "标准背驰形态却没出声"


def test_running_last_bi_abstains_instead_of_retreating_to_a_lower_peak():
    """判在还在走的最后一笔上：弃权。等它走完、后面有了反向笔，才放行。

    旧实现退到上一个同向峰，那个峰一定更低（最后一笔创了新高才被判中），
    拆出来的完成段收在非极值上，新高落进了下一根反向段。
    """
    running = _bis(DECEL_UP)
    tail = build_segs(running, MergeConfig())[-1]
    assert provisional_end(running, _macd_for(running), tail, "bi_beichi") is None

    done = _bis(DECEL_UP + [61.0])  # 背驰笔走完，后面接上一根向下笔
    tail = build_segs(done, MergeConfig())[-1]
    j = provisional_end(done, _macd_for(done), tail, "bi_beichi")
    assert j == len(done) - 2, "背驰笔已经走完却没放行"
    assert done[j].high == max(b.high for b in done[tail.start_bi:j + 1]), \
        "放行的终点必须是本段极值"


def _sub_case(extra=(61.0,)):
    bis = _bis(DECEL_UP + list(extra))
    bars = _bars_for(bis)
    tail = build_segs(bis, MergeConfig())[-1]
    return bis, bars, tail


def test_sublevel_needs_the_caller_to_supply_sub_points():
    bis, bars, tail = _sub_case()
    with pytest.raises(ValueError, match="sub_points"):
        provisional_end(bis, _macd_for(bis), tail, "sublevel_1bs", bars)


def test_sublevel_opposite_1bs_inside_the_tail_ends_it_at_the_matching_bi():
    """向上的末段里，次级别在第 12 笔的顶出现 1卖 → 末段收在第 12 笔。"""
    bis, bars, tail = _sub_case()
    top = bars[bis[12].end.peak_idx].ts
    assert provisional_end(bis, _macd_for(bis), tail, "sublevel_1bs", bars,
                           sub_points=[(top, False)]) == 12


def test_sublevel_ignores_wrong_side_and_points_before_the_tail_started():
    bis, bars, tail = _sub_case()
    top = bars[bis[12].end.peak_idx].ts
    early = bars[0].ts
    m = _macd_for(bis)
    assert provisional_end(bis, m, tail, "sublevel_1bs", bars,
                           sub_points=[(top, True)]) is None, "向上段要的是 1卖"
    assert provisional_end(bis, m, tail, "sublevel_1bs", bars,
                           sub_points=[(early, False)]) is None, "段起点之前的不算"
    assert provisional_end(bis, m, tail, "sublevel_1bs", bars,
                           sub_points=[]) is None


def test_sublevel_on_the_running_last_bi_abstains():
    """次级别顶已经出来、本级别还没有一根反向笔：没有可拆的结构，弃权。"""
    bis, bars, tail = _sub_case(extra=())
    top = bars[bis[-1].end.peak_idx].ts
    assert provisional_end(bis, _macd_for(bis), tail, "sublevel_1bs", bars,
                           sub_points=[(top, False)]) is None


def test_legalize_keeps_the_natural_end_before_an_opposite_last_bi():
    """反向的最后一笔对齐到倒数第二根——那正是段的自然终点，行为不变。"""
    from chan.segend import _legalize

    bis = _bis(DECEL_UP + [61.0])
    tail = build_segs(bis, MergeConfig())[-1]
    assert bis[-1].direction is not tail.direction
    assert _legalize(bis, tail, len(bis) - 1) == len(bis) - 2


@pytest.mark.parametrize("method", PLAIN)
def test_three_bi_tail_never_splits(method):
    """3 笔的末段拆不出东西来：拆了第一段就不足三笔。

    `sub_third` / `sub_complete` 在短段上**必然**返回 None（段内连笔中枢都凑
    不出），这不是缺陷，是覆盖率的真实情况。
    """
    bis = _bis([10.0, 20.0, 15.0, 25.0])
    segs = build_segs(bis, MergeConfig())
    assert segs
    assert split_tail(segs, bis, _macd_for(bis), method, bars=_bars_for(bis)) is segs


@pytest.mark.parametrize("method", PLAIN)
def test_confirmed_tail_is_never_touched(method):
    """末段已确认就什么都不做——本模块不碰任何已确认线段。"""
    bis = _bis(LONG_UP)
    segs = build_segs(bis, MergeConfig())
    segs[-1].confirmed = True
    assert split_tail(segs, bis, _macd_for(bis), method, bars=_bars_for(bis)) is segs


# --------------------------------------------------------------------------
# 三、真实数据：不变量 + 买卖点的确认链
# --------------------------------------------------------------------------


def _cached_codes(n=3):
    from feeds import cache

    root = Path(cache.DEFAULT_ROOT)
    if not root.exists():
        return []
    return sorted(root.rglob("day/*.csv"))[:n]


@pytest.mark.parametrize("method", PLAIN)
def test_real_data_still_passes_invariants(method):
    """真实K线逐前缀重划，开着任一口径都不许撞不变量。

    前段是 `confirmed=True` 进去的，所以 `check_segs` 会**真的检查它**：
    三笔起、奇数笔、同向收尾、与上一根已确认线段首尾相接且方向交替。这条
    测试因此是加强而不是豁免。
    """
    from feeds import Level, cache

    paths = _cached_codes(1)
    if not paths:
        pytest.skip("没有本地缓存")
    p = paths[0]
    bars = cache.load(p.stem, Level("day"), source=p.parent.parent.name)
    cfg = MergeConfig(seg_end=method)
    fired = 0
    # 步长 37：逐根走要 1500 次重划，这台机器跑长循环会位翻（C-bitflip）
    for t in range(300, min(len(bars), 1200), 37):
        a = analyze(bars[: t + 1], cfg, check=True)  # check=True 就是在跑不变量
        if len(a.segs) >= 2 and a.segs[-2].prov_end == method:
            fired += 1
            assert a.segs[-2].confirmed and not a.segs[-1].confirmed
            check_all(a.bis, a.segs, 1)
    assert fired >= 0  # 触发 0 次也合法，这里只保证不炸


def test_ma_filter_vetoes_only_a_short_untested_reversal():
    """106课 是负向过滤：短且不碰线才否决，碰线或够长都必须放行。"""
    bis = _bis(LONG_UP)
    tail = Seg(index=0, start_bi=0, end_bi=5, direction=Direction.UP,
               start=bis[0].start, end=bis[5].end, confirmed=False)
    j = 2
    start = bis[j].end.peak_idx
    # 平顶价 100，反向K线悬在 MA21=100 之上，不足 18 根。
    bars = [Bar(i, f"2024-01-{i % 28 + 1:02d}", 101, 102, 101, 100)
            for i in range(start + 5)]
    assert not _ma_allows(bis, bars, tail, j)
    touched = list(bars)
    b = touched[-1]
    touched[-1] = Bar(b.idx, b.ts, b.open, b.high, 99.0, b.close)
    assert _ma_allows(bis, touched, tail, j)
    long_enough = bars + [Bar(len(bars) + i, f"T{i}", 101, 102, 101, 100)
                          for i in range(20)]
    assert _ma_allows(bis, long_enough, tail, j)


def test_new_indicator_methods_require_bars_instead_of_silently_misfiring():
    bis = _bis(LONG_UP)
    segs = build_segs(bis, MergeConfig())
    for method in ("bi_beichi_rsi", "force_rsi", "boll_first",
                   "boll_second", "boll_squeeze"):
        with pytest.raises(ValueError, match="需要原始K线"):
            provisional_end(bis, _macd_for(bis), segs[-1], method)


def test_split_actually_reaches_the_zhongshu_layer():
    """**这条是整个模块最重要的测试**：提前判出来的段必须真的进到中枢层。

    回归锁，钉死一个已经踩过一次的坑。第一版把拆出来的两段都标成未确认，
    看上去很安全，实则是个可以证明的空操作——`zhongshu.usable_count` 从尾部
    剪掉未确认段，基线剪 1 段、拆开后剪 2 段，**可用段集合完全相同**。当时
    `bi_beichi` 在 300308 上宣布了 185 次，而买卖点表与基线一个数都没差。

    所以这里不测「判得对不对」，只测「它到底有没有作用」：开了口径之后，
    中枢层能用的段数必须真的比基线多。
    """
    from chan.zhongshu import legs_from_segs, usable_count
    from feeds import Level, cache

    paths = _cached_codes(1)
    if not paths:
        pytest.skip("没有本地缓存")
    p = paths[0]
    bars = cache.load(p.stem, Level("day"), source=p.parent.parent.name)

    for t in range(400, min(len(bars), 1200), 29):
        pre = bars[: t + 1]
        a = analyze(pre, MergeConfig(seg_end="force"), check=False)
        if len(a.segs) < 2 or a.segs[-2].prov_end != "force":
            continue
        base = analyze(pre, check=False)
        n_new = usable_count(legs_from_segs(a.segs, a.bis), True)
        n_base = usable_count(legs_from_segs(base.segs, base.bis), True)
        assert n_new == n_base + 1, (
            f"提前判了段却没能多给中枢层一段可用的（{n_base} → {n_new}）"
            "——口径退化成了基线"
        )
        return
    pytest.skip("这只票上 force 一次都没触发")
