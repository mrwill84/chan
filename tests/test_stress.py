"""随机行情压力测试。

验证策略第一层（不变量断言）的放大器：不预设任何期望值，只喂随机K线，
让 78课/77课 的不变量自己去抓反例。手工 fixture 只能覆盖我想得到的形态，
这里覆盖我想不到的。

随机数种子固定，失败可复现。
"""

from __future__ import annotations

import random

import pytest
from conftest import mk_bars

from chan import analyze
from chan.ambiguity import MAX_READINGS, enumerate_readings
from chan.invariants import InvariantError, check_zhongshus
from chan.types import Direction, MergeConfig
from chan.zhongshu import (
    MAX_LEGS,
    MIN_LEGS,
    PANZHENG,
    TREND_DOWN,
    TREND_UP,
    UPGRADE_LEGS,
    build_zhongshus,
    classify,
    legs_from_bis,
    legs_from_segs,
    legs_overlap,
    usable_count,
)

N_CASES = 60
N_BARS = 240


def random_bars(rng: random.Random, n: int = N_BARS, *, gappy: bool = False):
    """随机游走造K线，带上A股常见的脏形态。

    gappy=True 时按一定概率插入跳空、一字板、平盘K线——这些正是容易把
    包含处理和分型逻辑搞崩的地方。
    """
    px = 100.0
    ranges: list[tuple[float, float]] = []
    for _ in range(n):
        if gappy:
            r = rng.random()
            if r < 0.04:  # 跳空
                px *= 1 + rng.choice((-1, 1)) * rng.uniform(0.03, 0.09)
            elif r < 0.07:  # 一字板：零振幅
                px *= 1 + rng.choice((-1, 1)) * 0.1
                ranges.append((round(px, 2), round(px, 2)))
                continue
            elif r < 0.09:  # 平盘，和上一根完全一样
                ranges.append(ranges[-1] if ranges else (px, px))
                continue
        px *= 1 + rng.gauss(0, 0.015)
        px = max(px, 1.0)
        half = abs(rng.gauss(0, 0.012)) * px
        ranges.append((round(px - half, 2), round(px + half, 2)))
    return mk_bars(ranges)


CONFIGS = [
    MergeConfig(),
    MergeConfig(bi_mode="old"),
    MergeConfig(gap_blocks_merge=True),
    MergeConfig(enable_pending_seg=False),
    MergeConfig(initial_direction="up"),
    MergeConfig(initial_direction="down"),
]


@pytest.mark.parametrize("cfg", CONFIGS, ids=lambda c: f"{c.bi_mode}-{c.initial_direction}")
@pytest.mark.parametrize("gappy", [False, True], ids=["clean", "gappy"])
def test_invariants_never_fire_on_random_data(cfg: MergeConfig, gappy: bool):
    """任何配置、任何随机行情下，原文不变量都不该被违反。

    触发 InvariantError 就是实现 bug —— 原文说「划出不符合这基本要求的线段，
    那肯定是划错了」，而这里的输入全部由我们自己的算法产生。
    """
    for case in range(N_CASES):
        rng = random.Random(case * 7919 + int(gappy))
        bars = random_bars(rng, gappy=gappy)
        try:
            analyze(bars, cfg, check=True)
        except InvariantError as exc:  # pragma: no cover - 只在有 bug 时走到
            pytest.fail(f"case={case} gappy={gappy} cfg={cfg}: {exc}")


@pytest.mark.parametrize("gappy", [False, True], ids=["clean", "gappy"])
def test_structural_properties_on_random_data(gappy: bool):
    """不变量之外的结构性质，这些 invariants.py 没覆盖。"""
    for case in range(N_CASES):
        rng = random.Random(case * 104729 + int(gappy))
        a = analyze(random_bars(rng, gappy=gappy), check=True)

        # 包含处理后不得残留包含关系（gap_blocks_merge 关着时）
        for p, c in zip(a.merged, a.merged[1:]):
            inner = p.low <= c.low and c.high <= p.high
            outer = c.low <= p.low and p.high <= c.high
            assert not (inner or outer), f"case={case} 残留包含 {p} / {c}"

        # 合并后的K线必须有序、无洞地覆盖到最后一根。开头可能被 "drop" 起手
        # 策略丢掉若干根（原文空白，见 CONVENTIONS C01），所以不从 0 起算。
        assert a.merged[-1].end_idx == len(a.bars) - 1
        for p, c in zip(a.merged, a.merged[1:]):
            assert c.begin_idx == p.end_idx + 1

        # 笔首尾相接、方向交替（check_bis 已查，这里查的是与分型序列一致）
        assert len(a.bis) == max(0, len(a.fractals) - 1)
        for i, b in enumerate(a.bis):
            assert b.start is a.fractals[i]
            assert b.end is a.fractals[i + 1]

        # 末端必定未确认
        if a.bis:
            assert a.last_bi_is_provisional
        if a.segs:
            assert not a.segs[-1].confirmed
            # 开头可能有若干笔不属于任何线段（前三笔无重合，77课 p196），
            # 但从第一段起必须连续覆盖到最后一笔、不留空洞
            assert a.segs[-1].end_bi == len(a.bis) - 1
            for p, c in zip(a.segs, a.segs[1:]):
                assert c.start_bi == p.end_bi + 1


def test_degenerate_inputs():
    """退化输入不该抛异常，只该给出空结果。"""
    for ranges in ([], [(10.0, 10.0)], [(10.0, 11.0), (10.5, 10.8)]):
        a = analyze(mk_bars(list(ranges)), check=True)
        assert a.bis == []
        assert a.confirmed_segs == []


def test_flat_market_produces_nothing():
    """全程一个价位（长期停牌后复牌前的极端情况）：合并成一根，无笔无段。"""
    a = analyze(mk_bars([(50.0, 50.0)] * 100), check=True)
    assert len(a.merged) == 1
    assert a.bis == []


def test_monotonic_market_has_no_fractal():
    """单边无回头（连续一字涨停）：不该划出任何笔。20课 p42。"""
    bars = mk_bars([(100 + i, 100 + i) for i in range(60)])
    a = analyze(bars, check=True)
    assert a.raw_fractals == []
    assert a.bis == []


def test_old_mode_is_a_subset_of_new_mode():
    """老笔门槛更严（间隔 >=4 vs >=3），成笔数不该多于新笔。

    这是差分对比（验证策略第四层）的自动化版本：两种定义跑同一批数据，
    用一个必然成立的偏序关系当断言。
    """
    for case in range(20):
        rng = random.Random(case * 31337)
        bars = random_bars(rng, gappy=True)
        new = analyze(bars, MergeConfig(bi_mode="new"), check=True)
        old = analyze(bars, MergeConfig(bi_mode="old"), check=True)
        assert len(old.bis) <= len(new.bis), f"case={case}"


def test_truncated_and_full_runs_are_each_internally_valid():
    """截短版和完整版**各自**都要是一份合法划线。两者不要求一致。

    这里原本断言的是「已确认线段不该被后续K线改写」。那条断言撤掉了，原因不是
    它太严，是它**要错了东西**：划线给的永远是「截止到今天这批K线该怎么分」，
    明天证据变了答案就该跟着变。详见 tests/test_prefix.py 的模块说明。

    顺带记一笔它当初为什么没报警：随机游走造不出趋势行情里那种「第二特征序列
    一路被合并掉、差一个元素」的形态，20 组一次都没撞上。真实数据上 60 只里有
    48 只会改写。所以**前缀这件事必须用真实K线测**，那部分在 test_prefix.py。
    """
    for case in range(20):
        rng = random.Random(case * 65537)
        bars = random_bars(rng, gappy=True)
        # check=True 会跑 invariants，任一份非法都会在这里抛
        analyze(bars, check=True)
        analyze(bars[: len(bars) - 20], check=True)


def _zs_invariants(zss, usable, case, legs=None):
    """中枢层的硬不变量，全部来自 17课 p36 / 20课 p43 的定义本身。"""
    for z in zss:
        # 由 legs_overlap 保证；不成立说明重叠区间算错了
        assert z.zd <= z.zg + 1e-9, f"case={case} ZD>ZG: {z}"
        # DD<=ZD<=ZG<=GG 是定义的直接推论：ZD=max(d1,d2)>=min(dn)=DD，
        # ZG=min(g1,g2)<=max(gn)=GG
        assert z.dd <= z.zd + 1e-9, f"case={case} DD>ZD: {z}"
        assert z.zg <= z.gg + 1e-9, f"case={case} ZG>GG: {z}"
        assert z.dd <= z.d + 1e-9 and z.g <= z.gg + 1e-9, f"case={case} 包络反了: {z}"
        # 17课 p36 至少三段。**上限不是硬约束**：33课 p79 的 8 段说的是
        # 「就构成更大级别的中枢了」，那是一个更大的中枢，不是把它切成两个。
        # 超了只要求把 `level_up` 挂起来提醒升级看。曾经在这里断言
        # `leg_count <= MAX_LEGS`，而那条断言编码的恰恰是「到 8 段就切断」
        # 这个错误行为——切断会让下一个中枢从本该当连接段的那一段起步。
        assert MIN_LEGS <= z.leg_count, f"case={case} 段数不足: {z}"
        assert 0 <= z.start_leg <= z.end_leg < usable

        # 升级标志的两个必要条件，33课 p79 那句话的两半（「**3 个**次级别
        # 中枢」+「**重合**」）。这里只查单向蕴含，不查反向——反向等于把
        # `zhongshu_at` 的表达式抄一遍，抓不到任何东西。
        #
        # 这里曾经断言的是反向：`leg_count > MAX_LEGS → level_up`。它在只有
        # 段数这一个条件时成立，加上「重合」之后就不成立了，而且随机数据一跑
        # 就炸：段数够但 3 个次级别中枢互不重合的形态很常见（全缓存 846 个够
        # 9 段的中枢里 132 个），那是更大级别的**趋势**不是更大级别的中枢。
        if z.level_up:
            span = z.core_end - z.start_leg + 1
            assert span >= UPGRADE_LEGS, (
                f"case={case} 挂了 level_up 却只有 {span} 段: {z}")
            if legs is not None:
                # 3 个次级别中枢的公共交集 = 这 9 段全体的公共交集（分组不影响
                # 结果），所以直接查全体。锚定**头 9 段**：升级是第 9 段那一刻
                # 发生的事，后面再长也不回头翻案。
                head = legs[z.start_leg : z.start_leg + UPGRADE_LEGS]
                assert legs_overlap(head), (
                    f"case={case} 3 个次级别中枢无公共交集却挂了 level_up: {z}")
    for a, b in zip(zss, zss[1:]):
        # 中枢不共段，且连接段（33课 p79 的 b）不属于任何一个
        assert a.end_leg < b.start_leg, f"case={case} 中枢共段: {a} / {b}"
        if a.departed:
            # 离开段永远不能被下一个中枢吞掉。注意它**可能就是** a.end_leg
            # 本身（17课 p36 的前三段里第三段自己飞走），所以基准是
            # depart_leg 而不是 end_leg，见 CONVENTIONS C-core。
            assert b.start_leg > a.depart_leg, f"case={case} 连接段被吞了: {a} / {b}"
            assert a.core_end < a.depart_leg <= a.end_leg + 1
    # 相邻中枢之间必有连接段——`a.departed` 为假时上面那一支根本不查，而那
    # 正是出事的那一支。放在这里跑一遍，错误信息也比裸 assert 说得清。
    if legs is not None:
        check_zhongshus(zss, legs)


@pytest.mark.parametrize("gappy", [False, True], ids=["clean", "gappy"])
def test_zhongshu_invariants_on_random_data(gappy: bool):
    """随机行情下中枢层的不变量。笔中枢和线段中枢都跑。"""
    for case in range(N_CASES):
        rng = random.Random(case * 15485863 + int(gappy))
        a = analyze(random_bars(rng, gappy=gappy), check=True)
        for legs in (legs_from_bis(a.bis), legs_from_segs(a.segs, a.bis)):
            usable = usable_count(legs, True)
            zss = build_zhongshus(legs)
            _zs_invariants(zss, usable, case, legs)
            assert classify(zss) in (TREND_UP, TREND_DOWN, PANZHENG)


@pytest.mark.parametrize("gappy", [False, True], ids=["clean", "gappy"])
def test_readings_on_random_data(gappy: bool):
    """多义性枚举：每条读法本身都得是合法的，而且第一条等于贪心读法。

    36课 p88「多义性不是含糊性」——枚举出来的每一条都必须严格合规，
    只是视角不同。有一条不合规就不是多义性而是 bug。
    """
    for case in range(N_CASES):
        rng = random.Random(case * 32452843 + int(gappy))
        a = analyze(random_bars(rng, gappy=gappy), check=True)
        legs = legs_from_segs(a.segs, a.bis)
        usable = usable_count(legs, True)
        readings = enumerate_readings(legs)
        assert readings, f"case={case} 连一条读法都没有"
        assert len(readings) <= MAX_READINGS
        for r in readings:
            _zs_invariants(r.zhongshus, usable, case, legs)
        # 贪心（每层都取最早起手）必须是枚举出来的读法之一。它**不一定**排第一
        # ——36课 p87 的「避繁就简」完全可能更偏好一个起手更晚、但不出现延伸的
        # 组合，这正是多义性的意义所在。
        greedy = tuple((z.start_leg, z.end_leg) for z in build_zhongshus(legs))
        assert greedy in {r.key for r in readings}, f"case={case} 贪心读法没被枚举到"
        # 分数单调不减，第一条就是最简
        assert [r.score for r in readings] == sorted(r.score for r in readings)


def test_the_limit_argument_is_not_a_decoration():
    """`enumerate_readings(limit=N)` 要真的能回多于 `MAX_READINGS` 条。

    踩过的样子：截断写在 `_walk` 里、读的是模块全局 `MAX_READINGS`，于是
    `limit` 只在最后切一刀——给 100 也只回 12，而且**不报错**。姐妹函数
    `enumerate_groupings` 一直是把 limit 传进递归的，两边不一致没人发现。

    这里不断言「一定回满 N 条」：有的随机行情合法读法本来就不足 N 条，那是
    对的。断言的是「卡在 12 上」这个特征消失——存在至少一个案例超过 12。

    用**笔** legs：随机行情上线段只划得出 1~5 根，读法恒为 1 条，测不出截断。
    真实日线上段 legs 的读法中位有 8262 条，是随机行情太短，不是口径问题。
    """
    over = 0
    for case in range(N_CASES):
        rng = random.Random(case * 32452843)
        a = analyze(random_bars(rng, gappy=True), check=True)
        legs = legs_from_bis(a.bis)
        many = enumerate_readings(legs, limit=100)
        assert len(many) <= 100
        # 要多少回多少的前提是：预算放大后，前 MAX_READINGS 条仍然是合法读法
        usable = usable_count(legs, True)
        for r in many[:MAX_READINGS]:
            _zs_invariants(r.zhongshus, usable, case, legs)
        assert [r.score for r in many] == sorted(r.score for r in many)
        over += len(many) > MAX_READINGS
    assert over, f"{N_CASES} 个案例里没有一个超过 {MAX_READINGS} 条，limit 没生效"


def test_zhongshu_prefix_stability():
    """已确认的中枢不该因为后面多来几根K线而改写。

    和线段那条同样的理由：会跳变的话历史信号全部不可信。
    """
    for case in range(20):
        rng = random.Random(case * 49979687)
        bars = random_bars(rng, gappy=True)
        full = build_zhongshus(legs_from_bis(analyze(bars, check=True).bis))
        short = build_zhongshus(
            legs_from_bis(analyze(bars[: len(bars) - 20], check=True).bis)
        )
        done = [z for z in short if z.confirmed]
        for i, z in enumerate(done[:-1]):  # 末尾那个可能因延伸而长大，放过
            assert i < len(full), f"case={case} 中枢[{i}] 消失了"
            assert (z.start_leg, z.end_leg, z.zd, z.zg) == (
                full[i].start_leg,
                full[i].end_leg,
                full[i].zd,
                full[i].zg,
            ), f"case={case} 中枢[{i}] 被后续K线改写"


def test_direction_alternation_across_random_data():
    for case in range(30):
        rng = random.Random(case * 2654435761)
        a = analyze(random_bars(rng, gappy=True), check=True)
        for p, c in zip(a.bis, a.bis[1:]):
            assert p.direction is not c.direction
        done = a.confirmed_segs
        for p, c in zip(done, done[1:]):
            assert p.direction is not c.direction
        for s in done:
            assert s.direction in (Direction.UP, Direction.DOWN)
