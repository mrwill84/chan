"""走查回测。

这里只有一类测试值得写：**证明引擎在第 t 步没有偷看第 t 根之后的K线。**

其余的（收益算得对不对、分组汇总对不对）都是算术，错了一眼能看出来。
而偷看未来这件事不会报错、不会让任何断言变红，只会让回测结果变好看——
正是因为这样，缠论回测的漂亮曲线才几乎全是假的。

主力手段是**投毒**：把第 t 根之后的K线换成垃圾，再走一遍。如果前 t 步的
结论有任何一个字不一样，就说明那些步里用到了后面的数据。
"""

from __future__ import annotations

import pytest
from conftest import zigzag

from backtest import base_rate, evaluate, summarize, walk_forward
from chan.types import Bar
from feeds import Level

LV = Level.DAY


def _bars(n: int = 40, seed: int = 29):
    """和报告层同一套锯齿夹具，保证能划出各类买卖点。"""
    import random

    rng = random.Random(seed)
    pivots = [100.0]
    for i in range(n):
        step = rng.uniform(5, 30) * rng.uniform(0.5, 1.5)
        pivots.append(max(pivots[-1] + (step if i % 2 == 0 else -step), 10.0))
    return zigzag(pivots)


@pytest.fixture(scope="module")
def bars():
    return _bars()


@pytest.fixture(scope="module")
def sightings(bars):
    return walk_forward(bars, "000001", LV, zs_from="bi")


def _poison(bars: list[Bar], t: int) -> list[Bar]:
    """保留前 t+1 根，之后全换成一根离谱的平盘K线。

    换成「离谱」而不是「随机」：如果哪一步真的看到了后面，划出来的线会被
    这个数量级直接带飞，不可能碰巧还和原来一样。
    """
    out = list(bars[: t + 1])
    for i in range(t + 1, len(bars)):
        b = bars[i]
        out.append(Bar(idx=b.idx, ts=b.ts, open=1e6, high=1e6,
                       low=1e6, close=1e6, volume=0.0))
    return out


# --------------------------------------------------------------------------
# 不许偷看未来
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cut", [70, 90, 120])
def test_poisoning_the_future_changes_nothing_in_the_past(bars, sightings, cut):
    """第 t 步的结论，必须只由前 t 根K线决定。

    这条红了就意味着整个回测作废——不是「偏乐观」，是**结果没有意义**。
    """
    if cut >= len(bars) - 1:
        pytest.skip("夹具太短")
    poisoned = walk_forward(_poison(bars, cut), "000001", LV, zs_from="bi")

    def before(ss):
        return {(s.kind, s.side, s.bar, s.first_seen)
                for s in ss if s.first_seen <= cut}

    assert before(poisoned) == before(sightings)


def test_verify_really_uses_the_checked_report(bars, monkeypatch):
    """CLI 的 --verify 不能只是印在命令行里（旧接线就漏了它）。"""
    import backtest.walk as mod

    calls = []

    def fake(b, *a, **kw):
        calls.append(kw)
        return {"error": "测试：跳过"}

    monkeypatch.setattr(mod, "analyze_one_checked", fake)
    monkeypatch.setattr(mod, "analyze_one",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("verify=3 不该走未校验报告")))
    assert walk_forward(bars, "000001", LV, zs_from="bi", verify=3) == []
    assert calls and all(x["tries"] == 3 for x in calls)


def test_step_kw_is_called_with_the_cursor_and_reaches_the_report(bars, monkeypatch):
    import backtest.walk as mod

    seen = []

    def fake(b, *a, **kw):
        seen.append((len(b), kw.get("sub_points")))
        return {"error": "测试：跳过"}

    monkeypatch.setattr(mod, "analyze_one", fake)
    walk_forward(bars, "000001", LV, zs_from="bi",
                 step_kw=lambda t: {"sub_points": [(f"t{t}", True)]})
    assert seen and all(sp == [(f"t{n - 1}", True)] for n, sp in seen)


def test_sub_window_only_contains_bars_closed_by_the_cut():
    from backtest.sublevel import sub_window

    sub = [Bar(i, f"2024-01-02 {9 + (35 + 5 * i) // 60:02d}:{(35 + 5 * i) % 60:02d}:00",
               1, 1, 1, 1) for i in range(20)]
    ts = [b.ts for b in sub]
    w = sub_window(sub, ts, "2024-01-02 10:00:00", window=4)
    assert [b.ts[11:16] for b in w] == ["09:45", "09:50", "09:55", "10:00"]
    assert [b.idx for b in w] == [0, 1, 2, 3], "次级别窗口必须从0重排下标"


def test_chunked_steps_replay_to_exactly_the_same_sightings(bars, sightings, tmp_path):
    """拆成几段短进程、中途还有一行写坏，重放结果必须和一口气走完的逐字段相同。"""
    from dataclasses import asdict

    from backtest.sublevel import replay_steps, run_steps

    p = tmp_path / "steps.jsonl"
    first, last = 59, len(bars) - 1
    run_steps(p, bars, "000001", LV, start=first, end=90, verify=1, zs_from="bi")
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"t": 90, "r": {"tre')            # 模拟进程被杀在半行
    run_steps(p, bars, "000001", LV, start=first, end=last + 1, verify=1, zs_from="bi")
    got = replay_steps(p, first=first, last=last)
    assert [asdict(s) for s in got] == [asdict(s) for s in sightings]


def test_replay_refuses_to_report_with_missing_steps(bars, tmp_path):
    from backtest.sublevel import replay_steps, run_steps

    p = tmp_path / "steps.jsonl"
    run_steps(p, bars, "000001", LV, start=59, end=80, verify=1, zs_from="bi")
    with pytest.raises(ValueError, match="缺"):
        replay_steps(p, first=59, last=100)


def test_sub_confirms_only_a_1buy_at_the_current_retest_low():
    from backtest.sublevel import sub_confirms_low

    start, low_open, low_ts = ("2025-07-24 14:30:00", "2025-07-31 14:30:00",
                               "2025-07-31 15:00:00")
    early = ("2025-07-31 10:50:00", True)      # 更高的早先低点，后来被跌破
    at_low = ("2025-07-31 14:55:00", True)     # 当前最低那根30分钟里
    later = ("2025-08-05 10:45:00", True)      # 反弹后更高低点上的1买（位置像2买）
    sell = ("2025-07-31 14:55:00", False)
    before = ("2025-07-20 10:00:00", True)     # 回试开始之前
    assert sub_confirms_low([early], low_open, low_ts, start) is None
    assert sub_confirms_low([sell, before], low_open, low_ts, start) is None
    assert sub_confirms_low([later], low_open, low_ts, start) is None, \
        "最低那根之后的1买不算（预登记口径）"
    assert sub_confirms_low([early, at_low, later], low_open, low_ts, start) == at_low[0]


def _pb_case():
    """回拉：起点 t=2 之后，K线 3..8 有个5分钟中枢，最低点在第 10 根，之后向上笔。"""
    from chan.types import Bi, Direction, Fractal, FractalKind

    lows = [10, 9, 8, 7.5, 7.8, 7.4, 7.7, 7.5, 7.6, 7.0, 6.5, 7.2, 7.8, 7.4, 7.6]
    bars_ = [Bar(i, f"T{i:03d}", x, x + 0.1, x, x) for i, x in enumerate(lows)]

    def fr(i, kind, price):
        return Fractal(kind, i, price, price, i - 1, i + 1, i, f"T{i:03d}")

    down = Bi(0, fr(2, FractalKind.TOP, 8.1), fr(10, FractalKind.BOTTOM, 6.5), Direction.DOWN)
    up = Bi(1, fr(10, FractalKind.BOTTOM, 6.5), fr(12, FractalKind.TOP, 7.9), Direction.UP)
    nxt = Bi(2, fr(12, FractalKind.TOP, 7.9), fr(13, FractalKind.BOTTOM, 7.4), Direction.DOWN,
             confirmed=False)
    return bars_, down, up, nxt


def test_pullback_done_needs_a_zhongshu_and_a_confirmed_up_bi():
    from backtest.sublevel import pullback_done

    bars_, down, up, nxt = _pb_case()
    zs = [(3, 8)]
    assert pullback_done([down, up, nxt], bars_, zs, "T002") == "T012"
    # 向上笔还是最后一笔（没被确认）→ 不算
    assert pullback_done([down, up], bars_, zs, "T002") is None
    # 走势必完美：回拉里没有中枢 → 这段肯定没完
    assert pullback_done([down, up, nxt], bars_, [], "T002") is None
    # 中枢在回拉开始之前 / 在最低点之后 → 都不算回拉自己的中枢
    assert pullback_done([down, up, nxt], bars_, [(0, 4)], "T002") is None
    assert pullback_done([down, up, nxt], bars_, [(9, 12)], "T002") is None


def _ex_case(highs_closes):
    """买在第 0 根；ZG=10、回试高点 12。``highs_closes`` 从第 1 根起。"""
    bars_ = [Bar(0, "T000", 11, 11.2, 10.8, 11)]
    for i, (h, c) in enumerate(highs_closes, 1):
        bars_.append(Bar(i, f"T{i:03d}", c, h, min(c, h) - 0.1, c))
    return bars_


def test_structural_exit_stop_ref_sell_cap_eod():
    from backtest.sublevel import structural_exit

    quiet = {t: {"sells": [], "rels": None} for t in range(1, 20)}
    # 收盘跌回 ZG 下 → 3买判错
    b = _ex_case([(11.5, 11.2), (11.0, 9.9), (13, 13)])
    assert structural_exit(quiet, b, 0, 10.0, 12.0, 160) == (2, "stop")
    # 收在回试高点下面但从没越过它 → 不算；越过之后再跌回 → 走
    b = _ex_case([(11.9, 11.5), (11.8, 11.4), (12.5, 12.3), (12.4, 11.9)])
    assert structural_exit(quiet, b, 0, 10.0, 12.0, 160) == (4, "ref")
    # 同一根冲过又收回：要等后面的K线
    b = _ex_case([(12.5, 11.8), (12.0, 11.7)])
    assert structural_exit(quiet, b, 0, 10.0, 12.0, 160) == (2, "ref")
    # 引擎出卖点
    b = _ex_case([(11.5, 11.2), (11.6, 11.3), (11.7, 11.4)])
    sells = {**quiet, 2: {"sells": [[1, 2, True]], "rels": None}}
    assert structural_exit(sells, b, 0, 10.0, 12.0, 160) == (2, "sell")
    # 持有期上限 / 数据走完
    assert structural_exit(quiet, b, 0, 10.0, 12.0, 2) == (2, "cap")
    assert structural_exit(quiet, b, 0, 10.0, 12.0, 160) == (3, "eod")
    # 缺步不许出数
    with pytest.raises(ValueError):
        structural_exit({1: quiet[1]}, b, 0, 10.0, 12.0, 160)


def test_structural_exit_v3_broken_sell_cap_eod():
    from backtest.sublevel import structural_exit_v3

    quiet = {t: {"sells": []} for t in range(1, 20)}
    # 收盘跌破 1买价 → 前提不成立
    b = _ex_case([(11.5, 11.2), (11.0, 9.9), (13, 13)])
    assert structural_exit_v3(quiet, b, 0, 10.0, 160) == (2, "broken")
    # 跌回「原中枢 ZG」不再是卖出理由：只要没破 1买价就继续拿
    b = _ex_case([(11.5, 11.2), (10.9, 10.2), (13, 13)])
    assert structural_exit_v3(quiet, b, 0, 10.0, 160) == (3, "eod")
    # 先破 1买价、同步有卖点：算 broken（价格前提优先）
    sells = {**quiet, 2: {"sells": [[1, 2, True]]}}
    b = _ex_case([(11.5, 11.2), (11.0, 9.9), (13, 13)])
    assert structural_exit_v3(sells, b, 0, 10.0, 160) == (2, "broken")
    # 卖点
    b = _ex_case([(11.5, 11.2), (11.6, 11.3), (11.7, 11.4)])
    assert structural_exit_v3(sells, b, 0, 10.0, 160) == (2, "sell")
    # 持有期上限 / 缺步不许出数
    assert structural_exit_v3(quiet, b, 0, 10.0, 2) == (2, "cap")
    with pytest.raises(ValueError):
        structural_exit_v3({1: quiet[1]}, b, 0, 10.0, 160)


def _abbc_legs():
    """A(上下上 ~[21,23]) → b → B(上下上 ~[16,17.5]) → c（中途反弹，最低在第 10 段）
    → a' → A'(下上下) → 离开 → 回拉。"""
    from chan.types import Direction, Leg

    path = [30, 20, 24, 21, 23, 15, 18, 16, 17.5, 12, 14, 10, 13, 11, 12.5, 11.5, 14, 13]
    legs = []
    for i, (p0, p1) in enumerate(zip(path, path[1:])):
        d = Direction.UP if p1 > p0 else Direction.DOWN
        legs.append(Leg(i, d, p0, p1, min(p0, p1), max(p0, p1), i, i))
    return legs


def test_c_beichi_takes_c_down_to_the_lowest_leg(monkeypatch):
    from types import SimpleNamespace as P

    import chan.beichi as bc
    from backtest.sublevel import c_beichi
    from chan.zhongshu import build_zhongshus

    legs = _abbc_legs()
    bis = [P(start=P(peak_idx=10 * i), end=P(peak_idx=10 * i + 10)) for i in range(len(legs))]
    zss = build_zhongshus(legs)
    k = next(i for i, z in enumerate(zss) if z.start_leg == 5)   # B
    seen = {}

    def fake(m, enter, leave, d, r):
        seen.update(enter=(enter.leg_start, enter.leg_end), leave=(leave.leg_start, leave.leg_end))
        return 1.0, 0.5, True, False, False, 0.0, 0.0

    monkeypatch.setattr(bc, "_measure", fake)
    li = len(legs) - 2
    # c 从离开 B 那段（8）一直到最低那段（10），不是只到第一段
    assert c_beichi(legs, zss, k, bis, None, li, need_c3=True) == 10
    assert seen == {"enter": (4, 4), "leave": (8, 10)}
    # 量出来不背驰就没有 1买
    monkeypatch.setattr(bc, "_measure", lambda *a: (1.0, 2.0, False, False, False, 0.0, 0.0))
    assert c_beichi(legs, zss, k, bis, None, li, need_c3=False) is None


def _legs_with_c_zhongshu(tail):
    """A → b → B(上下上 [16,17.5]) → c（17.5→12）→ C(上下上 [12,13.5]) → d → ``tail``。"""
    from chan.types import Direction, Leg

    path = [30, 20, 24, 21, 23, 15, 18, 16, 17.5, 12, 14, 11, 13.5] + tail
    legs = []
    for i, (p0, p1) in enumerate(zip(path, path[1:])):
        d = Direction.UP if p1 > p0 else Direction.DOWN
        legs.append(Leg(i, d, p0, p1, min(p0, p1), max(p0, p1), i, i))
    return legs


def test_c_beichi_with_inner_zhongshu_compares_d_against_c(monkeypatch):
    from types import SimpleNamespace as P

    import chan.beichi as bc
    from backtest.sublevel import c_beichi
    from chan.zhongshu import build_zhongshus

    seen = {}

    def fake(m, enter, leave, d, r):
        seen.update(enter=(enter.leg_start, enter.leg_end), leave=(leave.leg_start, leave.leg_end))
        return 1.0, 0.5, True, False, False, 0.0, 0.0

    monkeypatch.setattr(bc, "_measure", fake)

    def run(tail):
        legs = _legs_with_c_zhongshu(tail)
        bis = [P(start=P(peak_idx=10 * i), end=P(peak_idx=10 * i + 10)) for i in range(len(legs))]
        zss = build_zhongshus(legs)
        k = next(i for i, z in enumerate(zss) if z.start_leg == 5)
        return c_beichi(legs, zss, k, bis, None, len(legs) - 2, need_c3=False)

    # C 在 d（leg12，13.5→8）处走掉：背驰比 d 对 c(leg8)，1买 在 d 的最低点
    assert run([8, 10, 9, 11]) == 12
    assert seen == {"enter": (8, 8), "leave": (12, 12)}
    # 1买 之后的反弹回进 C：C 一路延伸到当下，没有 d，A' 只是 C 的延伸 → 没有 1买
    assert run([8, 12.5, 9, 11]) is None


def test_c_inner_zhongshu_absent_without_three_overlapping_legs():
    from backtest.sublevel import c_inner_zhongshu

    legs = _abbc_legs()
    assert c_inner_zhongshu(legs, 8, 8) is None
    legs = _legs_with_c_zhongshu([8, 10, 9, 11])
    C = c_inner_zhongshu(legs, 8, 12)
    assert C is not None and (C.zd, C.zg) == (12, 13.5)


def test_top5_index_picks_highest_five_minute_bar_in_the_top_half_hour():
    from backtest.sublevel import top5_index

    hs = [9, 10, 12, 11, 10, 9, 20, 11, 11, 11, 11, 11, 11]   # 20 在上一根 30 分钟里
    bars_ = [Bar(i, f"T{i:03d}", h, h, h - 1, h) for i, h in enumerate(hs)]
    # 30 分钟顶那根的结束时刻 = 第 5 根 5 分钟；它包含第 0~5 根
    assert top5_index(bars_, "T005") == 2
    # 结束在第 11 根：包含 6~11，20 在其中
    assert top5_index(bars_, "T011") == 6


def test_latest_buy_point_filters_kind_time_and_broken_lows():
    from types import SimpleNamespace as P

    from backtest.sublevel import latest_buy_point

    lows = [10, 8, 9, 8, 9, 10, 7, 9]
    bars_ = [Bar(i, f"T{i}", l, l + 1, l, l) for i, l in enumerate(lows)]
    pts = [P(is_buy=True, kind=1, bar=1), P(is_buy=True, kind=3, bar=3), P(is_buy=True, kind=2, bar=4),
           P(is_buy=False, kind=1, bar=5), P(is_buy=True, kind=1, bar=6)]
    # 第 6 根创了新低 7：之前的买点（1、3）都被破；第 6 根自己还在；2买不算；卖点不算
    assert latest_buy_point(pts, bars_, 0) == (6, 7)
    assert latest_buy_point(pts[:3], bars_, 0) is None
    assert latest_buy_point(pts, bars_, 6) is None          # 必须晚于 after_bar
    assert latest_buy_point([P(is_buy=True, kind=2, bar=6)], bars_, 0, kinds=(1, 2, 3)) == (6, 7)


def test_latest_intact_point_drops_points_broken_by_a_new_low():
    from backtest.sublevel import latest_intact_point

    lows = [10, 8, 9, 8, 9, 10]          # 第 1、3 根同价（双底）
    bars_ = [Bar(i, f"T{i}", l, l + 1, l, l) for i, l in enumerate(lows)]
    assert latest_intact_point(bars_, [1]) == "T1"        # 之后只有同价、没创新低 → 还在
    assert latest_intact_point(bars_, [1, 3]) == "T3"     # 取最晚的
    lows2 = [10, 8, 9, 7, 9, 10]
    bars2 = [Bar(i, f"T{i}", l, l + 1, l, l) for i, l in enumerate(lows2)]
    assert latest_intact_point(bars2, [1]) is None        # 第 3 根创了新低 → 被破
    assert latest_intact_point(bars2, [3, 5]) == "T5"     # 最后一根自己也算（之后没有K线）


def test_unstable_verified_report_is_rejected(bars, monkeypatch):
    import backtest.walk as mod

    monkeypatch.setattr(mod, "analyze_one_checked",
                        lambda *a, **kw: {"warnings": {"unstable": "不可信"}})
    with pytest.raises(RuntimeError, match="多数校验失败"):
        walk_forward(bars, "000001", LV, zs_from="bi", verify=3)


def test_the_engine_never_receives_a_bar_past_the_cursor(bars, monkeypatch):
    """直接盯住喂给引擎的那串K线有多长。

    上面那条投毒测试是黑盒的，万一某条路径把未来数据藏进了别处（缓存、
    模块级状态），它未必抓得到。这条是白盒的，两条一起才覆盖得住。
    """
    import backtest.walk as mod

    real = mod.analyze_one
    saw: list[tuple[int, str]] = []

    def spy(b, *a, **kw):
        saw.append((len(b), b[-1].ts))
        return real(b, *a, **kw)

    monkeypatch.setattr(mod, "analyze_one", spy)
    walk_forward(bars, "000001", LV, zs_from="bi")
    for n, last_ts in saw:
        assert last_ts == bars[n - 1].ts, "喂进去的末根和长度对不上"
    assert [n for n, _ in saw] == sorted(n for n, _ in saw), "游标必须单调向前"


def test_a_sighting_is_never_seen_before_it_happens(sightings):
    """`first_seen < bar` 意味着引擎在这个点发生之前就标出了它。

    几何上不可能，出现就是走查写错了下标。
    """
    assert all(s.first_seen >= s.bar for s in sightings)


def test_the_lag_is_not_silently_zero(sightings):
    """如果所有 lag 都是 0，多半是走查退化成了「拿最终那版划线」。

    缠论的点天然要等后面的笔走完才确认得了，lag 全零反而说明有问题。
    """
    assert sightings, "夹具里一个买卖点都没走出来，这套测试失效了"
    assert max(s.lag for s in sightings) > 0


# --------------------------------------------------------------------------
# 入场价：必须是看得见的那一天的
# --------------------------------------------------------------------------


def test_entry_price_is_the_close_of_the_day_it_became_visible(bars, sightings):
    for o in evaluate(sightings, bars):
        assert o.entry_bar == o.sighting.first_seen
        assert o.entry_price == bars[o.sighting.first_seen].close


def test_the_naive_version_is_kept_separately_for_contrast(bars, sightings):
    """作弊版必须单独放。混进 `rets` 里，报告就在撒谎而且无从发现。"""
    laggy = [s for s in sightings if s.lag > 0]
    assert laggy, "没有带延迟的点，这条对照测不到东西"
    for o in evaluate(laggy, bars):
        for h, r in o.rets.items():
            if h in o.naive_rets and o.naive_rets[h] != r:
                return  # 至少有一个点上两者确实不同，对照是有效的
    pytest.fail("作弊版和真实版处处相等，说明入场点没真的挪过")


# --------------------------------------------------------------------------
# 基准与样本
# --------------------------------------------------------------------------


def test_buy_and_sell_base_rates_are_mirror_images(bars):
    """同一串K线，买的胜率和卖的胜率应当互补（平盘那部分除外）。"""
    bw, bm = base_rate(bars, 5, "buy")
    sw, sm = base_rate(bars, 5, "sell")
    assert bm == pytest.approx(-sm)
    assert bw + sw == pytest.approx(1.0, abs=0.05)


def test_signals_without_a_future_are_counted_not_dropped(bars, sightings):
    """最近的信号没有未来数据。悄悄丢掉就等于只统计了「老到能看出结果」的那些。"""
    s = summarize(evaluate(sightings, bars), bars)
    counted = sum(g["n"] for g in s["groups"].values())
    assert counted + s["skipped_no_future"] == len(sightings)


def test_every_group_reports_its_edge_against_the_base(bars, sightings):
    """只报胜率不报基准，等于没报——而且要比对**本方向**的基准。

    买点拿卖点的基准去减，符号一反，亏的会显示成赚的。
    """
    s = summarize(evaluate(sightings, bars), bars)
    assert s["groups"], "夹具没分出组"
    for g in s["groups"].values():
        base = s["base"][g["side"]]
        for h, row in g["h"].items():
            assert row["edge_win"] == pytest.approx(
                row["win_rate"] - base[h]["win_rate"])
            assert row["edge_ret"] == pytest.approx(
                row["mean_ret"] - base[h]["mean_ret"])


def test_a_group_knows_which_side_it_is(bars, sightings):
    s = summarize(evaluate(sightings, bars), bars)
    for name, g in s["groups"].items():
        assert g["side"] == ("buy" if "买" in name else "sell"), name
