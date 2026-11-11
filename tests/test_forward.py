"""走势推演与打分层。17课 p36 的三分类 + 24课「走势必完美」。

这个文件补的是一片**完全没有测试的地方**：`chan.forward` 的 `project` /
`resolve` / `trailing_trend`，`backtest.agent.parse_ranking`，以及整个
`backtest.forward_walk`。打分层一旦出错，错的方式是「数字照常印出来、只是
不对」——没有异常、没有红字，所以这里的每一条都针对一个**已经踩过或者差点
踩进去的具体坑**，不做泛泛的冒烟。

fixture 一律从拐点价格手搓 `Leg`，不经过K线/笔/线段（和 `test_zhongshu.py`
同一个理由：把推演的错和划线的错分开）。
"""

from __future__ import annotations

import pytest

from chan.forward import project, resolve, zs_key
from chan.types import Direction, Leg, ZhongShu
from chan.zhongshu import TREND_DOWN, TREND_UP, build_zhongshus


def mk_legs(points: list[float]) -> list[Leg]:
    return [
        Leg(index=i, direction=Direction.UP if b > a else Direction.DOWN,
            start_price=a, end_price=b, low=min(a, b), high=max(a, b),
            src_start=i, src_end=i, confirmed=True)
        for i, (a, b) in enumerate(zip(points, points[1:]))
    ]


def mk_zs(zd: float, zg: float, *, legs: int = 3, gg=None, dd=None,
          depart: bool = False) -> ZhongShu:
    """直接造一个中枢壳子，`leg_count` 由 start/end 决定。

    ``depart`` 默认 **False**（`depart_leg is None` = 走势还没离开）。这不是
    随便挑的默认值：`resolve` 现在拿它区分「真的离开了」和「`MAX_LEGS` 把一个
    还在延伸的中枢截成两截」，造 fixture 时必须显式说清是哪一种，不能靠默认
    值蒙混。要测 `break_*`，就得把 `depart=True` 写出来。
    """
    return ZhongShu(index=0, direction=Direction.UP, start_leg=0,
                    end_leg=legs - 1, zg=zg, zd=zd,
                    gg=gg if gg is not None else zg,
                    dd=dd if dd is not None else zd, g=zg, d=zd,
                    depart_leg=legs - 1 if depart else None)


# --------------------------------------------------------------------------
# project：选项表必须是完备且互斥的
# --------------------------------------------------------------------------


@pytest.mark.parametrize("close", [5.0, 9.999, 10.0, 12.0, 15.0, 15.001, 20.0])
def test_project_exactly_one_in_progress(close):
    """24课「走势必完美」：当前这段一定落在某一类里，而且只落一类。

    这是整张选项表的地基。事后打分靠「恰好命中一项」，要是某个价位上两支
    同时 `in_progress`、或者一支都不是，`in_progress` 那一列的含义就塌了，
    而它塌得毫无声息——报表照印。
    """
    zss = build_zhongshus(mk_legs([8, 16, 10, 15, 11, 14]))
    f = project(zss, close)
    core = [s for s in f["scenarios"]
            if s.code in ("extend", "break_up", "break_down")]
    assert len(core) == 3
    assert sum(bool(s.in_progress) for s in core) == 1


def test_project_boundary_belongs_to_extend():
    """边界归 extend：`_extend` 用 ge/le、`_leave` 用 gt/lt。

    钉死这一条，因为两边**都**写了比较符，改任何一边都会悄悄造出一个既不
    延伸也不离开的价位，或者一个两边都算的价位。
    """
    zss = build_zhongshus(mk_legs([8, 16, 10, 15, 11, 14]))
    z = zss[-1]
    for price in (z.zd, z.zg):
        f = project(zss, price)
        prog = [s.code for s in f["scenarios"] if s.in_progress]
        assert prog == ["extend"], f"{price} 上正在走的是 {prog}"


def test_project_trend_adds_reversal_panzheng_does_not():
    """`reversal` 只在趋势中出现。打分层的折叠依赖这条。

    `fold_reversal` 碰到盘整里的 reversal 会抛——那是对上游串行的保护。
    要是 `project` 真在盘整里给了它，抛的就不是保护而是误伤。
    """
    # 两个不重叠的上升中枢 → 上涨趋势。数值钉死，不写成条件断言：
    # `if 是趋势: assert ...` 在 fixture 退化成盘整时会**静默跳过**，
    # 测试照样全绿，而这条想守的恰恰是「趋势里一定有 reversal」。
    up = build_zhongshus(mk_legs(
        [8, 16, 10, 15, 11, 14, 30, 40, 32, 39, 33, 38]))
    assert len(up) == 2
    f = project(up, 50.0)
    assert f["trend"] == TREND_UP and f["run"] == 2
    assert [s.code for s in f["scenarios"]] == [
        "extend", "break_up", "break_down", "reversal"]

    flat = build_zhongshus(mk_legs([8, 16, 10, 15, 11, 14]))
    f = project(flat, 12.0)
    assert f["trend"] not in (TREND_UP, TREND_DOWN)
    assert "reversal" not in [s.code for s in f["scenarios"]]


def test_reversal_never_claims_in_progress():
    """背驰成立也**不许**置 `in_progress`，证据走 `evidence_now`。

    两者含义不同：`in_progress` 是位置读数（三支主干里恰好一支为真，整个
    打分层的分母靠它），背驰成立是力度判断。而背驰成立的时刻价格正在创新
    低——位置上走的就是 `break_down`。两个都置真，`in_progress_code` 取第
    一个为真的，于是 reversal **被静默丢掉**，一点痕迹都不留。

    这条用 `beichi=None`（「还判不了」）和一个 hit 的假 Beichi 各测一遍，
    守的是「无论背驰判成什么，位置读数那一列都不动」。
    """
    from chan.beichi import TREND, Beichi, Span

    up = build_zhongshus(mk_legs(
        [8, 16, 10, 15, 11, 14, 30, 40, 32, 39, 33, 38]))

    def span(d):
        return Span(leg_start=0, leg_end=0, bar_start=0, bar_end=1,
                    low=1.0, high=2.0, direction=d)

    hit = Beichi(kind=TREND, direction=Direction.UP, zs_index=1,
                 enter=span(Direction.UP), leave=span(Direction.UP),
                 area_enter=100.0, area_leave=40.0, area_ok=True,
                 dif_ok=True, dif_crossed_zero=True, breaks_zs=True,
                 new_extreme=True, hit=True, standard=True)

    for b, want_ev in ((None, False), (hit, True)):
        f = project(up, 50.0, beichi=b)
        rev = next(s for s in f["scenarios"] if s.code == "reversal")
        assert rev.in_progress is False
        assert rev.evidence_now is want_ev
        core = [s for s in f["scenarios"] if s.code != "reversal"]
        assert sum(bool(s.in_progress) for s in core) == 1

    # 实测值必须进 `needs`，而且带单独的「力度：」前缀——提示词按这个前缀
    # 认出该行并在蒙住组里删掉它（BEICHI_MARK）。
    rev = next(s for s in project(up, 50.0, beichi=hit)["scenarios"]
               if s.code == "reversal")
    force = next(n for n in rev.needs if n.startswith("力度："))
    assert "40.0" in force and "100.0" in force


# --------------------------------------------------------------------------
# resolve：四条返回路径
# --------------------------------------------------------------------------


def test_resolve_extend_needs_more_legs():
    """段数变多 = 延伸。"""
    before = mk_zs(10, 15, legs=3)
    after = [mk_zs(10, 15, legs=5)]
    assert resolve(before, after)["code"] == "extend"


def test_resolve_unresolved_when_nothing_changed():
    """段数没变、也没有新中枢 = 还下不了结论。

    这**不是**缺陷，是诚实回答。最近那批检查点必然是这个。
    """
    assert resolve(mk_zs(10, 15, legs=3),
                   [mk_zs(10, 15, legs=3)])["code"] == "unresolved"


def test_resolve_leg_count_must_be_restored():
    """踩过的坑：`leg_count` 还原错会**白送 `extend`**。

    `_shell` 若写成 `end_leg=0`，段数恒为 1，而 1 小于任何真实段数，于是
    所有停在末尾中枢上的检查点全部判成「延伸」。600009 30分钟上这一口吃掉
    292 条里的 74 条（25%），`unresolved` 被抹成 0——而最近这批本来就该是
    `unresolved`。这个错不抛异常、不留痕迹，只是让报表变好看。
    """
    real = mk_zs(10, 15, legs=4)
    same = [mk_zs(10, 15, legs=4)]
    assert resolve(real, same)["code"] == "unresolved"

    broken = mk_zs(10, 15, legs=1)          # end_leg=0 的后果
    assert resolve(broken, same)["code"] == "extend"


def test_resolve_not_departed_is_never_a_break():
    """**真踩过，而且很贵**：没离开过的中枢不许判成「离开」。

    18课 p40 的离开是「连续两段都不触及」，记在 `depart_leg` 上。`None` 就
    是没离开。而 33课 p79 的 8 段上限会把一个还在延伸的中枢截断，下一个中枢
    从紧接着那一段起步——于是「后面有中枢」这个条件成立了，但走势压根没出去。

    为什么「间隔 0 段」本身就证明这不是趋势：下跌趋势的中枢是「上下上」、以
    上段开头，把价格从前一个中枢带下来的那个下段不可能属于它，所以**同向的
    两个中枢之间必然隔着至少一段**。再者 `capped` 是在第 9 段触及中枢时退出
    的，新中枢必然含一个触及 [ZD, ZG] 的段，`relation` 的趋势分支成立不了。

    原先 `resolve` 不查这一条，600009 30分钟 H=480 下 443 个 `break_*` 里
    180 个（40.6%）是这么来的，**而且全是 break_down**——「恒猜 break_down」
    那个基准基本上是它撑起来的。标签错了，上面每一层打分都跟着错，而且错得
    不抛异常、只是让多数类看起来更强。
    """
    before = mk_zs(10, 15, legs=3)
    grown = mk_zs(10, 15, legs=8)
    nxt = mk_zs(12, 18, legs=3)     # 与它重叠，正是截断产物的样子

    # 没离开 + 后面有中枢 → extend，不是 break_*
    assert grown.depart_leg is None
    r = resolve(before, [grown, nxt])
    assert r["code"] == "extend", r

    # 对照：真的离开了（depart_leg 有值），才允许判 break_*
    left = ZhongShu(index=0, direction=Direction.UP, start_leg=0, end_leg=7,
                    zg=15, zd=10, gg=15, dd=10, g=15, d=10, depart_leg=7)
    assert left.departed
    assert resolve(before, [left, mk_zs(20, 25)])["code"] == "break_up"


def test_resolve_redrawn_when_zhongshu_gone():
    """原中枢在更长的K线上被重划掉了（C-redraw）。

    这是我方明确接受的代价：它说明**当时那个中枢本身不牢**，不是推演错了。
    所以它不进分母，但必须单独计数。
    """
    r = resolve(mk_zs(10, 15, legs=3), [mk_zs(40, 50, legs=3)])
    assert r["code"] == "redrawn"


def test_resolve_break_direction_follows_new_zhongshu():
    """新中枢在上方 → break_up，在下方 → break_down。

    ``depart=True`` 不能省：没离开过的中枢一律判 `extend`，见
    `test_resolve_not_departed_is_never_a_break`。
    """
    old = mk_zs(10, 15, legs=3)
    gone = mk_zs(10, 15, legs=3, depart=True)
    up = resolve(old, [gone, mk_zs(20, 25, legs=3)])
    down = resolve(old, [gone, mk_zs(2, 5, legs=3)])
    assert up["code"] == "break_up"
    assert down["code"] == "break_down"


def test_zs_key_matches_same_identity():
    """`zs_key` 和 `_same` 必须同一口径，否则会出现『resolve 认同一、分桶
    认两个』这种没人查得出来的分裂。"""
    assert zs_key(mk_zs(10.0, 15.0)) == zs_key(mk_zs(10.0, 15.0))
    assert zs_key({"zg": 15.0, "zd": 10.0}) == zs_key(mk_zs(10.0, 15.0))
    assert zs_key(mk_zs(10.0, 15.0)) != zs_key(mk_zs(10.0, 15.01))


# --------------------------------------------------------------------------
# 打分层：折叠、基准、分桶
# --------------------------------------------------------------------------


def test_fold_reversal_direction_must_not_be_backwards():
    """下跌趋势的 reversal 是**向上**转折 → 并进 break_up。

    方向搞反不会报错，只会让报表上的多空整个镜像——而且镜像得很像真的。
    """
    from backtest.forward_walk import fold_reversal

    p = {"extend": .1, "break_up": .2, "break_down": .3, "reversal": .4}
    assert fold_reversal(p, TREND_DOWN)["break_up"] == pytest.approx(.6)
    assert fold_reversal(p, TREND_UP)["break_down"] == pytest.approx(.7)


def test_fold_reversal_refuses_to_guess_in_panzheng():
    """盘整里出现 reversal 说明上游串了行，此时「往哪折」没有答案。

    静默猜一个方向正是这个函数要防的事。
    """
    from backtest.forward_walk import fold_reversal

    with pytest.raises(ValueError):
        fold_reversal({"extend": .5, "reversal": .5}, "panzheng")


def test_fold_reversal_does_not_renormalize():
    """折叠保留零和，不重新归一。

    归一会让 `0.5 reversal + 0.5 break_up` 和 `1.0 break_up` 得同分，
    把 reversal 变成可以无成本倾倒概率的避险桶。
    """
    from backtest.forward_walk import fold_reversal

    out = fold_reversal({"break_up": .5, "reversal": .3}, TREND_DOWN)
    assert sum(out.values()) == pytest.approx(.8)   # 不是 1.0


def test_uniform_baseline_is_folded_too():
    """D1-2：均匀基准必须走同一条折叠路，否则它会**凭空看多**。

    提示词说「拿不准就把概率摊平」，模型摊的是 4 项；折叠后那个分布是
    extend .25 / 反向 .50 / 同向 .25。基准若直接在 3 类上取 1/3，一个完全
    不知所措的模型就会得到一截「看多」的分，而那截分来自折叠规则本身。
    """
    from backtest.forward_walk import brier, uniform_ref

    row = {"codes_raw": ["extend", "break_up", "break_down", "reversal"],
           "codes": ["extend", "break_up", "break_down"], "trend": TREND_DOWN}
    u = uniform_ref(row)
    assert u == {"extend": .25, "break_up": .5, "break_down": .25}

    # 关键断言：这个「不知所措」的分布在真值是**延续**时要被罚，
    # 不能比平摊 1/3 还轻松。
    flat = {c: 1 / 3 for c in row["codes"]}
    assert brier(u, "break_down", row["codes"]) > brier(flat, "break_down",
                                                        row["codes"])


def test_episode_bucketing_tolerance():
    """1e-9 的差要折成一个 episode，1e-3 的差不折。"""
    from backtest.forward_walk import episodes

    def rec(i, zg, zd):
        return {"i": i, "ts": f"T{i}", "zs": {"zg": zg, "zd": zd},
                "trend": "panzheng", "scenarios": []}

    same = episodes([rec(0, 15.0, 10.0), rec(8, 15.0 + 1e-9, 10.0)])
    diff = episodes([rec(0, 15.0, 10.0), rec(8, 15.001, 10.0)])
    assert len(same) == 1 and same[0]["n"] == 2
    assert len(diff) == 2


def test_parse_ranking_not_ok_when_model_ranked_nothing():
    """模型一支都没排时 `ok` 必须是 False。

    `probs` 这时被漏排补 0 填满、字典非空，用 `bool(probs)` 判就会 ok=True，
    而 `top` 按 -0 稳定排序返回选项表里的第一个 code——把「模型什么都没说」
    伪装成「模型选了 break_down」，而且伪装得毫无痕迹。
    """
    from backtest.agent import parse_ranking

    codes = ["extend", "break_up", "break_down"]
    r = parse_ranking('{"ranking": []}', codes)
    assert r.ok is False
    assert r.missing == codes

    ok = parse_ranking('{"ranking": [{"code": "extend", "p": 0.7}]}', codes)
    assert ok.ok is True and ok.top == "extend"


# --------------------------------------------------------------------------
# 有界视野：视野之外的K线不许影响已判定的记录
# --------------------------------------------------------------------------


def _toy_bars():
    """一串在窄带里来回的锯齿，够长出笔中枢。

    这里用 ``zs_from="bi"``（全项目默认是 ``"seg"``，C-zslevel）——段中枢
    要三笔成段、三段成枢，手搓到那个长度得上百根K线，而这几条测的是**打分
    层**，不是中枢层本身，笔中枢足以把同一条代码路径走完。
    """
    from tests.conftest import zigzag

    return zigzag([10, 16, 11, 15, 12, 14.5, 11.5, 15.5, 12.5, 14.8,
                   11.8, 15.2, 12.2, 16.5, 13, 15.8, 12.8, 16.2],
                  leg_bars=6)


def test_skipped_no_future_is_not_silently_shortened():
    """``i+1+H`` 越过末尾 → `skipped_no_future`，**不进分母**。

    不要图省事写成 `min(i+1+H, len(bars))`：那会把末尾那批检查点的视野悄悄
    缩短，让它们看起来「判出来了」，而它们恰恰是最该说「还不知道」的那批。
    这是 C-nofuture 在分类任务上的对应物。
    """
    from feeds import Level

    from backtest.forward_walk import (
        score_projections,
        summarize_projections,
        walk_projections,
    )

    bars = _toy_bars()
    recs = walk_projections(bars, "T", Level("30min"), step=6, zs_from="bi")
    score_projections(recs, bars, horizons=(24,), zs_from="bi",
                      unbounded=False)

    for r in recs:
        code = r["outcomes"]["24"]["code"]
        past_end = r["i"] + 1 + 24 > len(bars)
        assert past_end == (code == "skipped_no_future"), \
            f"检查点 {r['i']}：越界={past_end} 但判成 {code}"

    s = summarize_projections(recs, horizons=(24,), unbounded=False)
    ep = s["h"]["24"]["episode"]
    assert ep["n_scored"] <= ep["n_total"]


def test_poisoning_bars_outside_the_horizon_changes_nothing():
    """投毒：把视野之外的K线全改成极值，已判定的记录必须一字不变。

    这条守的是「有界」两个字真的有界。`score_projections` 若在任何地方碰到
    了 `bars[i+1+H:]`，这里立刻红——而它要是没红，有界视野就只是个说法。
    """
    from dataclasses import replace

    from feeds import Level

    from backtest.forward_walk import score_projections, walk_projections

    H, CUT = 24, 84
    bars = _toy_bars()
    poisoned = [b if b.idx < CUT else
                replace(b, open=900.0, high=999.0, low=899.0, close=998.0)
                for b in bars]

    def run(judge_on):
        recs = walk_projections(bars, "T", Level("30min"), step=6,
                                zs_from="bi")
        score_projections(recs, judge_on, horizons=(H,), zs_from="bi")
        return {r["i"]: (r["outcomes"][str(H)]["code"],
                         r["outcomes"]["inf"]["code"]) for r in recs}

    before, after = run(bars), run(poisoned)

    # 有界列：视野完全落在投毒线之前的记录，一字不许变。
    safe = [i for i in before if i + 1 + H <= CUT]
    assert safe, "fixture 里没有一条记录的视野完全落在投毒线之前"
    for i in safe:
        assert before[i][0] == after[i][0], \
            f"检查点 {i} 的视野 [{i + 1}, {i + 1 + H}) 在投毒线 {CUT} 之前，" \
            f"却从 {before[i][0]} 变成了 {after[i][0]}"

    # 无界列：**必须**有记录被毒到。没有的话上面那组断言只是在说「这份毒
    # 本来就没劲」，测试会永远绿而什么也不守。同一份毒、两种视野、一个中招
    # 一个免疫——这才是「有界」两个字的证据。
    assert any(before[i][1] != after[i][1] for i in before), \
        "投毒连无界判定都没动，这份 fixture 证明不了有界视野的隔离性"


def test_zs_from_mismatch_makes_everything_redrawn():
    """判定侧和检查点侧的 `zs_from` 不一致 → **全部判成 `redrawn`**。

    真踩过：`score_projections` 漏传 `zs_from`，它默认 `"seg"`，而检查点是
    用 `"bi"` 划的，于是两边根本不是同一批中枢，`resolve` 一个都认不出来。
    报表照印、不抛异常，只是 `redrawn` 那一列变成 100%——而 `redrawn` 本身
    是个合法结果（C-redraw），所以这个错看起来完全像「这只票的中枢不牢」。

    这条测试的作用是让下一个看见「怎么全是 redrawn」的人一秒钟定位到原因。
    """
    from feeds import Level

    from backtest.forward_walk import score_projections, walk_projections

    bars = _toy_bars()
    recs = walk_projections(bars, "T", Level("30min"), step=6, zs_from="bi")

    score_projections(recs, bars, horizons=(24,), zs_from="bi")
    good = [r["outcomes"]["24"]["code"] for r in recs]

    score_projections(recs, bars, horizons=(24,), zs_from="seg")
    bad = [r["outcomes"]["24"]["code"] for r in recs]

    assert "redrawn" not in good
    assert set(bad) <= {"redrawn", "skipped_no_future"}
    assert "redrawn" in bad


# --------------------------------------------------------------------------
# 「判不了」要说清是哪一种
#
# 这几条防的是一次真实的误读：报表里「判不了」后面跟着一句**写死**的
# 「绝大多数是进入段与离开段方向相反，根本不是 a+A+b+B+c 的形态」。两处都
# 错：数字是一次旧运行留下的，归因也把 **aAb 层面**的方向不符说成了
# a+A+b+B+c 的形态问题——而 a+A+b+B+c 是 `_gate_trend` 的事，它只决定这次
# 背驰叫趋势背驰还是盘整背驰，和够不够得着两段毫无关系。
# --------------------------------------------------------------------------


def test_the_unknown_bucket_is_split_by_a_real_per_run_count():
    """``why`` 的各项必须加起来正好等于 ``unknown``，一个不多一个不少。

    加不上就说明有一条路径忘了记原因，那一类会悄悄消失在报表里——而这正是
    上一版的毛病（压根没记，只能印死话）。
    """
    from feeds import Level

    from backtest.forward_walk import (
        score_projections,
        summarize_projections,
        walk_projections,
    )

    bars = _toy_bars()
    recs = walk_projections(bars, "T", Level("30min"), step=6, zs_from="bi")
    score_projections(recs, bars, horizons=(24,), zs_from="bi",
                      unbounded=False, beichi=True)

    s = summarize_projections(recs, horizons=(24,), unbounded=False)
    b = s["h"]["24"]["checkpoint"]["with_beichi"]
    assert sum(b["why"].values()) == b["unknown"]
    # 没记原因的那一桶必须是空的：它只为「旧版本跑出来的 JSONL」留着
    assert "unrecorded" not in b["why"]


def test_every_reason_code_has_a_human_sentence():
    """报表直接印 `WHY_CN[code]`，漏一个就印出原始代号，没人看得懂。"""
    from chan.beichi import NO_ENTER, NO_LEAVE, OPPOSITE, OUT_OF_RANGE

    from backtest.forward_walk import WHY_CN, ZS_GONE

    for code in (NO_ENTER, NO_LEAVE, OPPOSITE, OUT_OF_RANGE, ZS_GONE):
        assert WHY_CN.get(code), code


def test_reach_reason_agrees_with_detect_at():
    """归因和判定必须同源。分叉了，报表就会解释一个没发生过的 None。

    这是把 `_reach` 抽出来的全部理由：以前 `detect_at` 自己判够不够得着，
    而诊断得另写一份一样的切片逻辑，两份迟早不一样。
    """
    from chan import analyze, macd
    from chan.beichi import detect_at, reach_reason
    from chan.zhongshu import build_zhongshus, legs_from_bis

    bars = _toy_bars()
    a = analyze(bars, check=False)
    legs = legs_from_bis(a.bis)
    zss = build_zhongshus(legs)
    m = macd([x.close for x in bars])

    seen = 0
    for k in range(-1, len(zss) + 1):
        got = detect_at(legs, zss, a.bis, m, k)
        why = reach_reason(legs, zss, a.bis, k)
        assert (got is None) == (why is not None), (k, got, why)
        seen += 1
    assert seen > 2, "夹具没划出中枢，这条什么也没验到"


# --------------------------------------------------------------------------
# 跨票汇总（walk_pooled）。单票的独立中枢只有个位数，验收线要 9~10 胜——
# 这一层的全部意义就是分母，所以测的全是「样本有没有被悄悄弄丢或弄假」。
# --------------------------------------------------------------------------


def _pool_loader():
    """两只票，**同一串K线**。这是故意的：它把跨票撞键这件事逼到极端。

    真实数据里两只票的 ZG/ZD 撞到 1e-6 以内不常见但也不稀奇（后复权之后更
    是），而这个夹具让每一个中枢都撞。不撞的夹具测不出 `episodes` 到底有没有
    把票号算进键——它会一路绿，然后在真数据上悄悄把两只票折成一只。
    """
    bars = _toy_bars()
    return bars, (lambda code, level: list(bars))


def test_pooling_two_stocks_doubles_the_denominator():
    """两只票 = 两倍检查点、两倍 episode。**一个都不许被折掉。**"""
    from feeds import Level

    from backtest.forward_walk import (
        episodes,
        score_projections,
        summarize_projections,
        walk_pooled,
        walk_projections,
    )

    bars, load = _pool_loader()
    one = walk_projections(bars, "AAA", Level("30min"), step=6, zs_from="bi")
    score_projections(one, bars, horizons=(24,), zs_from="bi",
                      unbounded=False, beichi=False)

    p = walk_pooled(["AAA", "BBB"], Level("30min"), load, step=6, zs_from="bi",
                    horizons=(24,), unbounded=False, beichi=False)
    assert p["n_ok"] == 2 and p["n_failed"] == 0
    assert len(p["recs"]) == 2 * len(one)
    assert len(episodes(p["recs"])) == 2 * len(episodes(one))

    s = summarize_projections(p["recs"], horizons=(24,), unbounded=False)
    assert s["n_episodes"] == 2 * len(episodes(one))
    # 严格口径和宽松口径都不许跨票合并
    assert s["recluster"]["strict"] == s["recluster"]["loose"]


def test_every_record_carries_its_code():
    """没有票号的记录在汇总层就是无主的，折叠只能靠价位猜。"""
    from feeds import Level

    from backtest.forward_walk import walk_pooled

    _, load = _pool_loader()
    p = walk_pooled(["AAA", "BBB"], Level("30min"), load, step=6,
                    zs_from="bi", horizons=(24,), unbounded=False,
                    beichi=False)
    assert {r["code"] for r in p["recs"]} == {"AAA", "BBB"}
    assert all(r.get("code") for r in p["recs"])


def test_a_failing_stock_does_not_stop_the_pool():
    """一只票崩了，剩下的照跑；但它**必须出现在 failed 里**。

    五千只里悄悄掉三百只，剩下那些的百分比看着和跑全了一模一样。
    """
    from feeds import Level

    from backtest.forward_walk import walk_pooled

    bars, _ = _pool_loader()

    def flaky(code, level):
        if code == "BAD":
            raise RuntimeError("退市了")
        if code == "SHORT":
            return list(bars[:5])
        return list(bars)

    p = walk_pooled(["AAA", "BAD", "SHORT", "CCC"], Level("30min"), flaky,
                    step=6, zs_from="bi", horizons=(24,), unbounded=False,
                    beichi=False)
    assert p["n_ok"] == 2 and p["n_failed"] == 2
    assert {x["code"] for x in p["failed"]} == {"BAD", "SHORT"}
    assert any("退市" in x["error"] for x in p["failed"])


def test_checkpoint_round_trips_and_is_reused(tmp_path):
    """断点：第二趟一条检查点都不许重算，结果要和第一趟逐字相同。"""
    from feeds import Level

    from backtest.forward_walk import walk_pooled

    _, load = _pool_loader()
    kw = dict(step=6, zs_from="bi", horizons=(24,), unbounded=False,
              beichi=False, checkpoint=tmp_path)

    a = walk_pooled(["AAA", "BBB"], Level("30min"), load, **kw)
    assert a["n_cached"] == 0
    assert (tmp_path / "AAA.json").exists()

    b = walk_pooled(["AAA", "BBB"], Level("30min"), load, **kw)
    assert b["n_cached"] == 2
    # 判定侧一次划分都没做——断点命中就不该再碰 `analyze`
    assert b["score_health"]["n_divides"] == 0
    assert a["recs"] == b["recs"]


def test_checkpoint_is_invalidated_when_the_bars_change(tmp_path):
    """`pull --update` 补了一根K线，旧断点必须作废。

    这是断点续跑最容易出的那种错：文件还在，参数那一行也没变，读进来一声
    不响——于是新一轮的结论建立在昨天那批K线上。
    """
    from feeds import Level

    from backtest.forward_walk import walk_pooled

    bars, _ = _pool_loader()
    kw = dict(step=6, zs_from="bi", horizons=(24,), unbounded=False,
              beichi=False, checkpoint=tmp_path)

    walk_pooled(["AAA"], Level("30min"), lambda c, l: list(bars), **kw)
    # 同样根数、只改一个价（digest 不看根数就漏）
    from dataclasses import replace
    tweaked = [replace(b, close=b.close + 0.5) if b.idx == 3 else b
               for b in bars]
    p = walk_pooled(["AAA"], Level("30min"), lambda c, l: list(tweaked), **kw)
    assert p["n_cached"] == 0, "K线变了还在复用断点"


def test_checkpoint_is_invalidated_when_the_params_change(tmp_path):
    """换了视野/步长就不是同一趟，旧断点不许冒充。"""
    from feeds import Level

    from backtest.forward_walk import walk_pooled

    _, load = _pool_loader()
    base = dict(zs_from="bi", unbounded=False, beichi=False,
                checkpoint=tmp_path)

    walk_pooled(["AAA"], Level("30min"), load, step=6, horizons=(24,), **base)
    for changed in (dict(step=8, horizons=(24,)),
                    dict(step=6, horizons=(48,))):
        p = walk_pooled(["AAA"], Level("30min"), load, **changed, **base)
        assert p["n_cached"] == 0, f"{changed} 变了还在复用断点"


def test_a_truncated_checkpoint_file_is_ignored_not_half_read(tmp_path):
    """写了一半的断点文件要当成没有。读成「这只票跑完了」就少了一截分母。"""
    from feeds import Level

    from backtest.forward_walk import walk_pooled

    _, load = _pool_loader()
    kw = dict(step=6, zs_from="bi", horizons=(24,), unbounded=False,
              beichi=False, checkpoint=tmp_path)
    good = walk_pooled(["AAA"], Level("30min"), load, **kw)

    f = tmp_path / "AAA.json"
    f.write_text(f.read_text(encoding="utf-8")[:200], encoding="utf-8")
    again = walk_pooled(["AAA"], Level("30min"), load, **kw)
    assert again["n_cached"] == 0
    assert again["recs"] == good["recs"]


def test_score_rankings_refuses_a_pooled_summary():
    """跨票的 summary 不能进排序打分：`answers` 按 `i` 索引，两只票会串。

    拒绝而不是容忍——串了之后分数照样算得出来，看不出任何异样。
    """
    import pytest

    from feeds import Level

    from backtest.forward_walk import (
        score_rankings,
        summarize_projections,
        walk_pooled,
    )

    _, load = _pool_loader()
    p = walk_pooled(["AAA", "BBB"], Level("30min"), load, step=6,
                    zs_from="bi", horizons=(24,), unbounded=False,
                    beichi=False)
    s = summarize_projections(p["recs"], horizons=(24,), unbounded=False)
    with pytest.raises(ValueError, match="只能评单票"):
        score_rankings(s, {}, key="24")
