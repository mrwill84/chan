"""JSON 出口。这一层不算缠论，但它是模型唯一看得见的东西。

所以测的不是「数字对不对」（那是 `chan/` 各层自己的事），而是**契约**：
字段在不在、可靠性标记有没有如实传下去、过滤有没有在全量上做、
出错的票会不会把整轮扫描带崩。
"""

from __future__ import annotations

import json
import random

import pytest
from conftest import zigzag

from feeds import Level
from scan.report import MIN_BARS, analyze_one, scan_many

# 幅度随机的锯齿。**种子 29 是挑过的**：它在笔级别上同时划出 10 个中枢和
# 六类买卖点各至少一个，六种标记（quasi/ambiguous/confirmed…）都有样本。
# 规整的等幅锯齿不行——它要么一路单边只剩一根线段，要么中枢一个都凑不出。
SEED = 29


def _bars():
    rng = random.Random(SEED)
    pivots = [100.0]
    for i in range(60):
        step = rng.uniform(5, 30) * rng.uniform(0.5, 1.5)
        pivots.append(max(pivots[-1] + (step if i % 2 == 0 else -step), 10.0))
    return zigzag(pivots)


# 这个夹具只有 241 根K线，线段层上中枢太少，所以报告测试统一跑**笔级别**。
# 测的是出口契约，不是哪一层该建中枢。
ZS = "bi"


@pytest.fixture
def bars():
    return _bars()


@pytest.fixture
def rep(bars):
    return analyze_one(bars, "000001", Level.DAY, zs_from=ZS)


# --------------------------------------------------------------------------
# 契约
# --------------------------------------------------------------------------


def test_report_is_json_serializable(rep):
    """numpy 的 bool_/float64 混进来 `json.dumps` 就炸，而且炸在扩展那边。"""
    json.dumps(rep, ensure_ascii=False)


def test_report_has_the_keys_the_skill_promises(rep):
    assert set(rep) >= {
        "code", "level", "zs_from", "bars", "bi", "seg", "trend",
        "zhongshu_count", "bspoint_count", "zhongshus", "bspoints",
        "truncated", "latest", "warnings",
    }


def test_every_structure_carries_a_timestamp(rep):
    """模型跟用户说话用日期，不用K线下标。空串等于没给。"""
    assert all(z["start_ts"] and z["end_ts"] for z in rep["zhongshus"])
    assert all(p["ts"] for p in rep["bspoints"])


def test_zg_zd_and_gg_dd_are_both_present_and_distinct(rep):
    """三买比 ZG、趋势比 GG——两对都给，模型才可能读对（20课 p43）。"""
    assert rep["zhongshus"]
    for z in rep["zhongshus"]:
        assert z["dd"] <= z["zd"] <= z["zg"] <= z["gg"]


def test_short_history_is_refused_not_guessed():
    r = analyze_one(zigzag([100.0, 110.0, 105.0]), "000001", Level.DAY)
    assert "error" in r and str(MIN_BARS) in r["error"]
    assert "bspoints" not in r  # 不要给半份报告，调用方会当成「没有买卖点」


def test_recent_truncates_and_says_so(bars):
    full = analyze_one(bars, "000001", Level.DAY, zs_from=ZS)
    cut = analyze_one(bars, "000001", Level.DAY, zs_from=ZS, recent=1)
    assert cut["bspoint_count"] == full["bspoint_count"]  # 计数是全量的
    assert len(cut["bspoints"]) == 1
    assert cut["truncated"] is True
    assert full["truncated"] is False
    assert cut["bspoints"][0] == full["bspoints"][-1]  # 留的是**最近**那个


def test_latest_is_the_last_point_even_when_truncated(bars):
    full = analyze_one(bars, "000001", Level.DAY, zs_from=ZS)
    cut = analyze_one(bars, "000001", Level.DAY, zs_from=ZS, recent=1)
    assert cut["latest"] == full["latest"] == full["bspoints"][-1]


def test_warnings_are_keyed_not_prose(rep):
    """按代号挑警告，不靠匹配中文句子——句子改一个字过滤就悄悄失效。"""
    assert isinstance(rep["warnings"], dict)
    assert all(isinstance(k, str) and isinstance(v, str)
               for k, v in rep["warnings"].items())


# --------------------------------------------------------------------------
# 批量扫描
# --------------------------------------------------------------------------


def _load(bars):
    """固定返回同一串K线的 loader。扫描层不认识数据从哪来，这里就喂常量。"""
    return lambda code, level: bars


def _scan(bars, **kw):
    kw.setdefault("zs_from", ZS)
    kw.setdefault("within", 10**6)  # 默认不卡时效，各条测试只管自己那个维度
    return scan_many(["000001", "000002"], Level.DAY, _load(bars), **kw)


def test_scan_returns_a_summary_not_the_whole_structure(bars):
    """命中行必须小。5400 行摘要能进上下文，5400 份完整结构不能。"""
    r = _scan(bars)
    assert r["hit_count"] == 2
    h = r["hits"][0]
    assert set(h) == {"code", "trend", "last_close", "last_ts", "bars_ago",
                      "point", "warnings"}
    assert "zhongshus" not in h and "bspoints" not in h
    assert "reasons" not in h["point"]  # 理由去 chan_analyze 里拿


def test_within_filters_by_recency(bars):
    assert _scan(bars, within=0)["hit_count"] == 0
    r = _scan(bars)
    assert all(h["bars_ago"] >= 0 for h in r["hits"])


def test_kind_filter_looks_at_every_point_not_just_the_last_few():
    """这条盯的是一个真实踩过的坑：先按 `recent` 截断、再按 `kinds` 过滤。

    当时 `scan_many` 传的是 `recent=3`，于是过滤只看得见最后三个点。

    这里不写死类别——夹具一改就可能「恰好最后一个就是它」，那样截不截都对，
    测试就悄悄失效了。改成自己挑一个**排在末尾三个之外**的类别，挑不出来
    就直接报错，不让它假装通过。
    """
    bars = _bars()
    # 两边都把类买卖点算进来。不是因为批量扫描该这么跑（默认恰恰相反），
    # 而是**只有这样夹具里才有排在末尾三个之外的类别**——口径不一致的话，
    # 拿全量挑出来的点在扫描那边根本看不见，比对就成了自说自话。
    full = analyze_one(bars, "000001", Level.DAY, zs_from=ZS, include_quasi=True)
    pts = full["bspoints"]
    tail = {p["kind"] for p in pts[-3:]}
    older = [p for p in pts if p["kind"] not in tail]
    assert older, "夹具里每一类都出现在末尾三个点中，这条测试验不到截断顺序"
    kind = older[-1]["kind"]
    want = [p for p in pts if p["kind"] == kind][-1]

    r = _scan(bars, kinds={kind}, include_quasi=True)
    assert r["hit_count"] == 2
    assert r["hits"][0]["point"]["ts"] == want["ts"]


def test_side_filter(bars):
    r = _scan(bars, sides={"buy"})
    assert r["hit_count"] and all(h["point"]["side"] == "buy" for h in r["hits"])


def test_quasi_is_off_by_default_in_batch_scans(bars):
    """原文说类买卖点「在小级别中是意义不大的」（第 503 行），全市场扫它是噪音。"""
    off = _scan(bars)
    on = _scan(bars, include_quasi=True)
    assert not any(h["point"]["quasi"] for h in off["hits"])
    assert sum(h["point"]["quasi"] for h in on["hits"]) >= 0  # 开了才可能有


def test_one_bad_symbol_does_not_stop_the_scan(bars):
    def flaky(code, level):
        if code == "000002":
            raise RuntimeError("退市了")
        return bars

    r = scan_many(["000001", "000002", "000003"], Level.DAY, flaky,
                  zs_from=ZS, within=10**6)
    assert r["hit_count"] == 2
    assert r["failed_count"] == 1
    assert r["failed"][0]["code"] == "000002"
    assert "退市了" in r["failed"][0]["error"]


def test_too_short_history_counts_as_failed_not_hit():
    short = zigzag([100.0, 110.0, 105.0])
    r = scan_many(["000001"], Level.DAY, _load(short), zs_from=ZS)
    assert r["hit_count"] == 0 and r["failed_count"] == 1


def test_hits_are_newest_first(bars):
    r = _scan(bars)
    assert [h["point"]["ts"] for h in r["hits"]] == sorted(
        (h["point"]["ts"] for h in r["hits"]), reverse=True
    )


def test_filter_is_echoed_back(bars):
    """模型要能看出这轮结果是哪套条件下的，否则两次扫描拿混了也不知道。"""
    r = _scan(bars, kinds={1, 3}, sides={"buy"}, within=7)
    assert r["filter"] == {"within": 7, "kinds": [1, 3], "sides": ["buy"],
                           "include_quasi": False, "confirmed_only": False}


def test_scan_result_is_json_serializable(bars):
    json.dumps(_scan(bars), ensure_ascii=False)


# --------------------------------------------------------------------------
# 「0 命中」必须自己解释自己
#
# 这几条防的是一次真实的误读：391 只扫出 0 命中，看着像全市场没机会，实际是
# 97 只在 5 根K线内都有买卖点，只是**无一例外是 quasi**，被默认筛条件整批滤掉。
# 一个光秃秃的 `hit_count: 0` 和「这些票上没有买卖点」长得一模一样。
# --------------------------------------------------------------------------


def test_zero_hits_says_which_filter_emptied_it(bars):
    r = _scan(bars, within=0)
    assert r["hit_count"] == 0
    assert sum(r["missed_because"].values()) == r["scanned"]


def test_stale_and_absent_are_different_answers(bars):
    """「有点但旧了」要和「一个点都没有」分开——前者调 `--within` 就有。"""
    r = _scan(bars, within=0)
    assert set(r["missed_because"]) == {"stale"}, r["missed_because"]


def test_the_quasi_filter_owns_the_miss_when_the_fresh_points_are_all_quasi():
    """原因要从**够新的那些点**里倒推，不是从全部点里。

    否则这一批会一律报「只是不够新」——字面没错（滤掉 quasi 之后最近的合格点
    确实在窗口外），但它把人指向「调大 within」，而真正该做的是加 `--quasi`。
    """
    bars = _bars()
    full = analyze_one(bars, "000001", Level.DAY, zs_from=ZS, include_quasi=True)
    pts = full["bspoints"]
    last = full["bars"]["count"] - 1
    # 挑一个窗口：里面**只有** quasi 点。夹具里 quasi 点不少，总能找到。
    window = next(
        (w for w in range(1, 60)
         if (fresh := [p for p in pts if last - p["bar"] <= w])
         and all(p["quasi"] for p in fresh)),
        None,
    )
    assert window is not None, "夹具里找不到只含 quasi 点的窗口，这条测试失效了"

    assert _scan(bars, within=window, include_quasi=False)["missed_because"] == {
        "quasi": 2
    }
    # 同一个窗口，放开 quasi 就命中——说明挡住的确实是这道筛子
    assert _scan(bars, within=window, include_quasi=True)["hit_count"] == 2


def test_an_empty_scan_is_not_reported_as_an_explanation(bars):
    """命中的票不该出现在 `missed_because` 里，不然两个数加起来对不上。"""
    r = _scan(bars)
    assert r["hit_count"] == 2
    assert r["missed_because"] == {}


# --------------------------------------------------------------------------
# 数值格式化。理由字符串是本地模型唯一的判定依据，打错量级比不给还坏
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "x,want",
    [
        (10150.0, "10150"),       # `.4g` 会给 1.015e+04，模型八成看错量级
        (8352.76, "8352.76"),     # 两位小数不能丢——`.4g` 给的是 8353
        (13.5, "13.5"),
        (1.0, "1"),
        (0.0123456, "0.01235"),   # 5分钟级别的小数值，两位小数全成 0
        (0.0, "0"),
    ],
)
def test_price_formatting_never_goes_scientific(x: float, want: str):
    from chan.num import fp

    assert fp(x) == want


def test_reasons_carry_readable_prices(rep):
    """后复权价常是五位数。理由里只要出现一个 e+04，这条就该红。"""
    blob = " ".join(r for p in rep["bspoints"] for r in p["reasons"])
    assert "e+" not in blob and "e-" not in blob


# --------------------------------------------------------------------------
# 自检（chan.verify）
# --------------------------------------------------------------------------


def test_checked_report_equals_the_plain_one(bars):
    """自检不许改结果。它只在旁边记一笔账。

    这是整层的前提：如果包一下就把报告改了，那它报的「两遍不一致」永远分不清
    是硬件翻了位还是这个包装自己的毛病。
    """
    from scan.report import analyze_one_checked

    plain = analyze_one(bars, "000001", Level.DAY, zs_from=ZS)
    checked = analyze_one_checked(bars, "000001", Level.DAY, zs_from=ZS)
    assert checked.pop("verify")["runs"] == 2
    assert checked == plain


def test_checked_report_stays_json_serializable(bars):
    """`verify` 里混进 Check 对象之类，炸的是扩展那边，不是这里。"""
    from scan.report import analyze_one_checked

    r = analyze_one_checked(bars, "000001", Level.DAY, zs_from=ZS)
    json.dumps(r, ensure_ascii=False)


def test_checked_report_notices_bars_changing_under_it(bars):
    """K线在计算途中被改掉——`stable` 的一号盲区，只有前后比摘要才看得见。

    这里用一个「算到一半偷偷改输入」的假 analyze 来模拟。真实情形是损坏落在
    输入上，两遍一起错、投票全票通过，而 `bars_stable` 是唯一会说话的那个。
    """
    import dataclasses

    import scan.report as R

    real = R.analyze_one
    state = {"n": 0}

    def sneaky(bs, *a, **kw):
        state["n"] += 1
        out = real(bs, *a, **kw)
        if state["n"] == 1:
            # `Bar` 是 frozen 的，所以换掉列表里的那一项而不是改它的字段——
            # 改字段会抛 FrozenInstanceError，被 `_once` 当成一票收走，这条
            # 测试就会在什么都没验到的情况下通过。（第一版正是这么错的。）
            bs[0] = dataclasses.replace(bs[0], close=bs[0].close + 1.0)
        return out

    try:
        R.analyze_one = sneaky
        r = R.analyze_one_checked(bars, "000001", Level.DAY, zs_from=ZS)
    finally:
        R.analyze_one = real

    assert r["verify"]["bars_stable"] is False
    assert "bars_mutated" in r["warnings"]


def test_too_short_bars_still_get_a_verify_record(bars):
    """早退路径（K线不够）也要记账，否则汇总表里它和「查过没事」分不开。"""
    from scan.report import analyze_one_checked

    r = analyze_one_checked(bars[:10], "000001", Level.DAY, zs_from=ZS)
    assert "error" in r and r["verify"]["runs"] == 2 and r["verify"]["ok"]


# --------------------------------------------------------------------------
# 走势类型的口径（E5）
# --------------------------------------------------------------------------


def test_trend_is_the_trailing_reading_not_the_whole_history():
    """`trend` 回答「现在是什么走势」，不是「这整段历史是什么走势」。

    这条是回归测试：原先 `trend` 用的是 `zhongshu.classify(zss)`，那是 17课
    p36 对「一个**完成的**走势类型」的定义，套在整段中枢序列上只要有一对相邻
    中枢不同向就判盘整——实测 15 只票 × 日线/30分/5分**全部**返回 panzheng。
    一个恒为常数的字段不是信息，它只是占着模型上下文里的一行。
    """
    from conftest import trending_tail_bars

    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, classify, legs_from_bis

    bars = trending_tail_bars()
    rep = analyze_one(bars, "000001", Level.DAY, zs_from=ZS)

    a = analyze(bars, check=True)
    zss = build_zhongshus(legs_from_bis(a.bis))
    assert classify(zss) == "panzheng", "夹具失效了，它得是两个口径分叉的那种形状"
    assert rep["trend"] == "down"


def test_trend_never_contradicts_the_projection(rep):
    """同一份报告里这两个字段打架过——12 只日线有 5 只方向相反。

    模型两个都读得到，于是「日线盘整」和「下跌趋势的三卖」会同时出现在一次
    回答里。所以不是「让它们尽量一致」，是**取同一个值**：`trend` 直接取
    `project` 算好的结果，物理上没有第二个数可以分叉。
    """
    assert rep["trend"] == rep["forward"]["trend"]


# --------------------------------------------------------------------------
# 级别树：走势类型的切分口径
# --------------------------------------------------------------------------


def _divergent_bars():
    """树和 `trailing_trend` 读不一样的那种形状。种子 48 是扫出来的。

    它的中枢序列尾巴长这样（笔级别，11 个中枢）：

        … 枢8 --down--> 枢9 --up--> 枢10

    `tree._greedy_groups` 贪心取最长同向串，`枢8+枢9` 已经构成一个**走完的**
    下跌趋势，枢10 只好自己单独成一个盘整。而 `trailing_trend` 从枢10 往回捋，
    看见 `relation(枢9, 枢10) == up` 就把枢9 借回来，判成「上涨趋势，2 个中枢」
    ——同一个枢9 同时属于两个走势类型。

    实测 30 只缓存日线票里 4 只多认领（都恰好多 1 个），其中 3 只因此把 kind
    判得不一样，而且全是 trailing=up vs 树=panzheng，和这个夹具一个形状。
    """
    rng = random.Random(48)
    pivots = [100.0]
    for i in range(60):
        step = rng.uniform(5, 30) * rng.uniform(0.5, 1.5)
        pivots.append(max(pivots[-1] + (step if i % 2 == 0 else -step), 10.0))
    return zigzag(pivots)


def test_the_tree_partition_wins_and_the_other_reading_is_kept():
    """主口径是树的切分，其余合法切法降为 `alt_readings`。

    不是「另一种错了」——33课 p79 的走势多义性说得清楚，同一段走势在不同
    读法下各有一个合法答案。所以不丢，摆出来交给模型（C-amb：程序枚举，模型
    选择）。钉死的是**两件事都发生**：`trend` 跟树走，另一个读法还在。

    这个夹具上，从前硬挂的那一条（`trailing_trend` 给的 up/2枢）必须仍然在
    列表里——换成完整枚举之后它不该消失，只是不再是唯一的那条。
    """
    rep = analyze_one(_divergent_bars(), "000001", Level.DAY, zs_from=ZS)
    alts = rep["alt_readings"]
    assert rep["trend"] == "panzheng" and rep["forward"]["run"] == 1
    assert any(a["trend"] == "up" and a["run"] == 2 for a in alts), alts
    # 多认领的那个中枢就是分歧的全部来源，`run` 的差额必须说得出来
    assert all(a["run"] > rep["forward"]["run"] for a in alts)
    assert rep["trend"] == rep["forward"]["trend"]   # C-trendnow 第 1 条不破


def test_alt_readings_are_deduped_by_the_run_we_are_standing_in():
    """同一个末段不许报两遍。

    枚举出来的切法大多只在**靠前**的位置分叉，末段一模一样。这个夹具上原本
    有三条备选，末段全是「up，中枢 9..10」——印三遍只是把同一句话说三次，
    而模型会把重复当成「这个读法更可信」。
    """
    alts = analyze_one(_divergent_bars(), "000001", Level.DAY,
                       zs_from=ZS)["alt_readings"]
    keys = [(a["trend"], tuple(a["zhongshu_range"])) for a in alts]
    assert len(keys) == len(set(keys)), keys


def test_no_alt_readings_when_the_current_run_is_unambiguous(rep):
    """末段只有一种读法时**不许**挂这个键。

    一个人人都有的字段等于没有字段：模型会学会跳过它，真正分歧的那些就跟着
    一起被跳过。门槛特意不是「有没有别的切法」——那个实测 58/60 都为真
    （见 `ambiguity.enumerate_groupings` 的文档串），而是「**当下所在**的那
    一段读法不同」，20/60。
    """
    assert "alt_readings" not in rep


def test_every_tree_layer_carries_timestamps_and_its_own_projection(rep):
    """每一层都要能跟用户说话——没有日期的结构等于没给（和中枢、买卖点同规矩）。"""
    assert rep["tree"] and rep["tree"][0]["level"] == 0
    assert [t["level"] for t in rep["tree"]] == list(range(len(rep["tree"])))
    for t in rep["tree"]:
        assert t["current"]["start_ts"] and t["current"]["end_ts"]
        assert t["current"]["start_ts"] <= t["current"]["end_ts"]
        assert t["forward"]["scenarios"], f"L{t['level']} 没有推演表"


def test_level_zero_reuses_the_top_level_projection(rep):
    """`tree[0].forward` 和顶层 `forward` 是同一次计算，不是算两遍比对。

    算两遍就又多了一个会分家的数——而且这两个数会一起印在同一段人话里。
    """
    assert rep["tree"][0]["forward"] == rep["forward"]


def test_upper_layers_say_unknown_rather_than_no_divergence():
    """上层**判不出来**时要说「还判不了」，不许印成「没背驰」。

    这条从前的理由是「上层根本不测」（`span_of` 写死笔下标，测了是错的）。
    现在上层测了——`span_of` 收 `to_bars`，`_layer_json` 传
    `tree.layer_to_bars`——但判不出来的情形依然存在（末枢取不到进入段或离开
    段）。那时候仍然必须是未知，不是否定：两者差着一个未知和一个否定。

    夹具另造（种子 0、80 段）而不是复用 `rep`：背驰那一支只在走势类型是
    **趋势**时才出现，而 `rep` 那只票每一层都是盘整，拿它测这条等于空转。
    """
    rng = random.Random(0)
    pivots = [100.0]
    for i in range(80):
        step = rng.uniform(5, 30) * rng.uniform(0.5, 1.5)
        pivots.append(max(pivots[-1] + (step if i % 2 == 0 else -step), 10.0))
    rep = analyze_one(zigzag(pivots), "000001", Level.DAY, zs_from=ZS)

    seen = 0
    for t in rep["tree"][1:]:
        for s in t["forward"]["scenarios"]:
            if s["code"] != "reversal":
                continue
            seen += 1
            force = [n for n in s["needs"] if n.startswith("力度")]
            assert force, s["needs"]
            # 测出来了就该报数值，没测出来就必须说「判不了」。禁止的是第三种
            # 说法：拿「没测」当「测了，没背驰」。
            assert ("还判不了" in force[0]) or ("面积" in force[0]), force[0]
    assert seen, "夹具失效了：上层一个趋势都没有，背驰那一支压根没生成"


def test_upper_layer_divergence_measures_the_right_bar_window():
    """上层背驰的 MACD 窗口必须落在那一段走势真正覆盖的K线上。

    这是 `span_of` 从前写死笔下标的那个 bug 的回归测试。它**不会崩**：上层
    legs 远少于笔，错误的下标照样落在 `bis` 范围内，只是指向完全无关的一段
    历史。实测 600009 日线 seg 口径 level 1，正确窗口 (2727,2998,5570,6582)、
    旧路径 (518,543,812,953)——差一个年代，一声不吭。

    所以这里不比「有没有抛异常」，比的是**窗口有没有落在该落的地方**，而且
    直接比两种取法——不依赖「恰好有一个上层背驰判得出来」这种夹具运气
    （合成锯齿上层压根判不出背驰，真要靠它这条测试就是空转）。
    """
    from chan import analyze
    from chan.beichi import span_of
    from chan.tree import build_tree, layer_to_bars, leg_bars
    from chan.zhongshu import legs_from_segs

    rng = random.Random(7)
    pivots = [100.0]
    for i in range(200):
        step = rng.uniform(5, 30) * rng.uniform(0.5, 1.5)
        pivots.append(max(pivots[-1] + (step if i % 2 == 0 else -step), 10.0))
    bars = zigzag(pivots)
    a = analyze(bars, check=False)
    layers = build_tree(legs_from_segs(a.segs, a.bis))
    assert len(layers) > 1, "夹具失效了：只有一层，没有上层可测"

    wrong = 0
    for L in layers[1:]:
        tb = layer_to_bars(layers, a.bis, L.level)
        for i in range(len(L.legs)):
            # 正确取法：逐层翻下去。
            want = leg_bars(layers, a.bis, L.level, i)
            assert tb(i) == want
            sp = span_of(L.legs, a.bis, i, i, to_bars=tb)
            assert (sp.bar_start, sp.bar_end) == want
            assert 0 <= sp.bar_start <= sp.bar_end < len(bars)

            # 旧取法：把上层 leg 的 src_* 当笔下标。**不越界、不报错**，
            # 只是指向别处——这正是它能躲过测试这么久的原因。
            naive = span_of(L.legs, a.bis, i, i)
            if (naive.bar_start, naive.bar_end) != want:
                wrong += 1
    assert wrong, ("夹具失效了：这棵树上两种取法恰好处处相同，"
                   "测不出 to_bars 到底起没起作用")


def test_a_report_with_a_tree_still_serializes(rep):
    json.dumps(rep, ensure_ascii=False)


def test_scan_hits_do_not_carry_the_tree(bars):
    """5400 行摘要进得了上下文，5400 棵树进不了（见 `_match` 的文档串）。"""
    r = _scan(bars)
    assert r["hits"]
    for h in r["hits"]:
        assert "tree" not in h and "forward" not in h and "alt_readings" not in h
