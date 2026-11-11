"""把一次划线压成 JSON。

字段名用英文（机器键稳定），标签用中文（本地模型读中文原文术语更准）。
每个结构都带 `ts`——模型拿到的是日期不是K线下标，否则它没法跟用户对话。

**可靠性标记一个都不能省。** 盘后扫描时末端笔/段天然未完成（C-prov），
背驰判定依赖 MACD 参数（C13），三买在某些形态下原文自相矛盾（C15）。
这些全部如实输出，由 skill 告诉模型怎么读——藏起来就成了假精确。
"""

from __future__ import annotations

from collections import Counter

from chan import analyze, macd
from chan.ambiguity import enumerate_groupings, last_run_differs
from chan.beichi import detect
from chan.buysell import BSPoint, check_bspoints, find_bspoints
from chan.forward import Scenario, project, zs_key
from chan.invariants import InvariantError, check_zhongshus
from chan.macd import Macd
from chan.tree import NO_ZHONGSHU, Layer, build_tree, layer_to_bars, trend_bars
from chan.types import Bar, Bi, Direction, Leg, MergeConfig, ZhongShu
from chan.verify import MAX_TRIES, bars_digest, stable
from chan.zhongshu import build_zhongshus, legs_from_bis, legs_from_segs
from chan.zn import forecast_next, zn_series
from feeds import Level

# 低于这个K线数，中枢根本凑不出来（三段次级别走势至少要这么多根），
# 新股和次新股直接跳过并说明原因，不要硬算出一堆噪音。
MIN_BARS = 60

_DIR = {Direction.UP: "up", Direction.DOWN: "down"}


def _ts(bars: list[Bar], i: int) -> str:
    """K线下标 → 时间戳。越界时回空串而不是抛——报告层不该因为取标签崩掉。"""
    return bars[i].ts if 0 <= i < len(bars) else ""


def _leg_span(legs: list[Leg], bis: list[Bi], a: int, b: int) -> tuple[int, int]:
    """第 a..b 段覆盖的原始K线闭区间。`Leg.src_*` 存的都是**笔**下标。"""
    return bis[legs[a].src_start].start.peak_idx, bis[legs[b].src_end].end.peak_idx


def _trigger_json(t) -> dict | None:
    return None if t is None else {"op": t.op, "price": round(t.price, 4),
                                   "what": t.what}


def _scenario_json(s: Scenario) -> dict:
    return {"code": s.code, "name": s.name, "becomes": s.becomes,
            "needs": s.needs, "trigger": _trigger_json(s.trigger),
            "forms": s.forms, "at": None if s.at is None else round(s.at, 4),
            "kills": _trigger_json(s.kills), "in_progress": s.in_progress,
            # 和 `in_progress` 并列而不是合并：前者是位置读数（三支主干里
            # 恰好一支为真），后者是力度条件。见 Scenario.evidence_now。
            "evidence_now": s.evidence_now,
            "refs": s.refs}


def _forward_json(f: dict) -> dict:
    """推演表。`scenarios` 是**穷尽**的，模型只排序、不补充（见 forward.py）。"""
    out = {k: v for k, v in f.items() if k != "scenarios"}
    # `project` 拿到的是原始收盘价。报告里所有价位都是 4 位，这里不跟上的话
    # 同一个数会在同一段人话里以两种精度出现（128.858 和 128.85800532）。
    if out.get("last_close") is not None:
        out["last_close"] = round(out["last_close"], 4)
    out["scenarios"] = [_scenario_json(s) for s in f["scenarios"]]
    return out


def _kind_json(kind: str) -> str:
    """走势类型代号出 JSON 前的关口。现在是恒等——但**别删**。

    `tree.NO_ZHONGSHU` 从前是中文串「无中枢」，这里负责归一；现在它自己就是
    `"no_zhongshu"`，四个代号全是机器键，这一层没事可做了。留着是因为它是
    「机器键全英文」这条规矩（见模块开头）在边界上的唯一落点：下次再有人拿
    中文当枚举值，这里是该拦住它的地方，而不是等渲染层去猜。

    断言而不是静默放行：中文混进机器键会一路流到 `structuredContent`，那时候
    再查就要翻三个仓库了。
    """
    assert kind.isascii(), f"走势类型代号必须是机器键，拿到的是 {kind!r}"
    return kind


def _layer_json(L: Layer, layers: list[Layer], bis: list[Bi], bars: list[Bar],
                last_close: float, own: dict | None, m: Macd | None = None,
                ) -> dict:
    """级别树的一层：这一层的走势类型切分 + 这一层自己的推演表。

    ``own`` 是已经算好的本层推演（level 0 复用顶层那份，不重算）。

    **上层现在有背驰读数了。** 从前这里一律喂 `None`，因为 `beichi.span_of`
    写死了 `bis[span[0].src_start]`——它假定 `Leg.src_*` 是**笔**下标，而
    `tree.trend_to_leg` 填的是下一层 legs 的下标。上层 legs 远少于笔，所以
    那样取不会越界、不会报错，只会安静地按错误的K线窗口去积 MACD 面积。
    实测 600009 日线 seg 口径 level 1：正确窗口 (2727,2998,5570,6582)，
    旧路径给的是 (518,543,812,953)——差了整整一个年代。

    现在 `span_of` 收 ``to_bars``，上层传 `tree.layer_to_bars`，窗口就对了。
    ``m`` 是**原始K线**上的 MACD，所有层共用一份：面积是那段K线上的客观量，
    不随从哪一层看它而变。``m`` 为 None（调用方没给）时退回不判，行为和从前
    一致。
    """
    cur = L.trends[-1] if L.trends else None
    bc = None
    if own is None and m is not None and L.zhongshus:
        bc = detect(L.legs, L.zhongshus, bis, m,
                    to_bars=layer_to_bars(layers, bis, L.level))
    out: dict = {
        "level": L.level,
        "trend_count": len(L.trends),
        "zhongshu_count": len(L.zhongshus),
        # 被 33课 p79 三等分拆开的那些。树的中枢数因此会比顶层那张平表多——
        # 不是算重了，是同一段结构的两种读法。
        "split_count": len(L.split_from),
        "current": None,
        "forward": own if own is not None else _forward_json(
            project(L.zhongshus, last_close, beichi=bc,
                    beichi_note=None if m is not None else
                    "调用方没给 MACD，这一层不做背驰检测",
                    trend_zss=cur.zhongshus if cur else None)),
    }
    if cur is not None:
        i0, i1 = trend_bars(layers, bis, cur)
        out["current"] = {
            "kind": _kind_json(cur.kind),
            "run": len(cur.zhongshus),
            # 末枢已经走出去了才算完成（72课：背驰点是分界，分界点就落在
            # 离开段上）。问的是**末枢**而不是「end_ts 是不是今天」——后者
            # 两种情况下都可能为假：走势类型走完了，它后面那几根还没凑出新
            # 中枢的段同样没人认领，`end_leg` 照样停在今天之前。
            "complete": cur.zhongshus[-1].departed if cur.zhongshus else False,
            "start_ts": _ts(bars, i0),
            "end_ts": _ts(bars, i1),
            "low": round(cur.low, 4),
            "high": round(cur.high, 4),
            # 这一段走势类型的**末枢**。单独给，是因为它未必等于顶层那张平表
            # 的最后一个中枢：平表里一个 9 段以上的中枢，在树里被拆成了 3 个
            # 子中枢（33课 p79），树的末枢是其中第三个，区间要窄。两个都摆出来
            # 模型才看得见自己在用哪一个（实测 30 只里 4 只不一样）。
            "zd": round(cur.zhongshus[-1].zd, 4) if cur.zhongshus else None,
            "zg": round(cur.zhongshus[-1].zg, 4) if cur.zhongshus else None,
        }
    return out


def _zs_json(z: ZhongShu, legs: list[Leg], bis: list[Bi], bars: list[Bar]) -> dict:
    """一个中枢。

    `zg`/`zd` 与 `gg`/`dd` 必须分开给——前者是中枢本体、由前三段定死，三买三卖
    拿它做基准；后者是震荡包络、随延伸浮动，趋势判定拿它做基准（20课 p43）。
    模型最容易混的就是这两对，所以键名里直接写清楚。
    """
    lo, hi = _leg_span(legs, bis, z.start_leg, z.end_leg)
    span = z.core_end - z.start_leg + 1
    readings = zn_series(z, legs)
    forecast = forecast_next(z, legs)
    return {
        "i": z.index,
        # **不是中枢所处走势的方向**，是 20课 p43 的 Z 走势段方向（= 第一构成
        # 段的方向），只用来挑算 GG/DD 的那些段。它和走势方向**永远相反**：
        # 下跌走势里的中枢是「上下上」，Zn 向上；上涨走势里是「下上下」，Zn
        # 向下。这个键以前叫 `dir`，谁看都会读成「下跌中枢」，正好读反。
        "zn_dir": _DIR[z.direction],
        "zg": round(z.zg, 4),
        "zd": round(z.zd, 4),
        "gg": round(z.gg, 4),
        "dd": round(z.dd, 4),
        # 20课 p43 的 G=min(gn)、D=max(dn)，即所有 Zn 的公共交集边界。
        # **它和升级判据没有关系**——升级比的是 3 个次级别中枢重不重合，
        # 对象是中枢不是段，见 `zhongshu.zhongshu_at` 的 `level_up`。
        "g": round(z.g, 4),
        "d": round(z.d, 4),
        "start_ts": _ts(bars, lo),
        "end_ts": _ts(bars, hi),
        "legs": z.leg_count,
        # 围绕中枢震荡的段数，不含离开段。够 UPGRADE_LEGS=9 段（= 每 3 段一个
        # 次级别中枢 × 3 个）且那 3 个中枢有公共交集，才挂 `level_up`（33课
        # p79）。够段数但 `level_up` 为假 = 3 个次级别中枢互不重合 = 更大级别
        # 的**趋势**，不是更大级别的中枢。
        "core_legs": span,
        "confirmed": z.confirmed,
        "departed": z.departed,
        "level_up": z.level_up,
        # 92课：越过 A/B 只是变盘提醒，不是3类点；forecast 是几何外推，
        # 不是概率，也不是必然到达价。模型可以拿它填「下一支的观察价位」。
        "zn_monitor": {
            "z": round((z.zd + z.zg) / 2.0, 4),
            "readings": [{"leg": r.leg, "zn": round(r.zn, 4),
                          "strength": r.strength, "position": r.position,
                          "confirmed": r.confirmed} for r in readings],
            "crossed": sum(r.crossed for r in readings),
            "forecast": None if forecast is None else {
                "direction": _DIR[forecast.direction],
                "near": round(forecast.near, 4),
                "far": round(forecast.far, 4),
                "zn": round(forecast.zn, 4),
                "low": round(forecast.low, 4),
                "high": round(forecast.high, 4),
                "basis": forecast.basis,
                "validated": False,
            },
        },
    }


def _bs_json(p: BSPoint, bars: list[Bar]) -> dict:
    """一个买卖点。`reasons` 原样带出——skill 要模型复述理由而不是自己编。"""
    return {
        "kind": p.kind,
        "side": "buy" if p.is_buy else "sell",
        "label": p.label,
        "ts": _ts(bars, p.bar),
        "bar": p.bar,
        "price": round(p.price, 4),
        "zs": p.zs_index,
        "confirmed": p.confirmed,
        "quasi": p.quasi,
        "ambiguous": p.ambiguous,
        "macd_law_ok": p.macd_law_ok,
        "source": p.source,
        "reasons": list(p.reasons),
    }


def analyze_one(
    bars: list[Bar],
    code: str,
    level: Level,
    *,
    zs_from: str = "seg",
    recent: int | None = None,
    include_quasi: bool = True,
    seg_end: str | None = None,
    seg_end_ma_filter: bool = False,
    sub_points: list[tuple[str, bool]] | None = None,
) -> dict:
    """对一只股票的一个级别划线，返回 JSON 可序列化的字典。

    **纯函数，不碰网络**——K线从外面喂进来。这样同一份报告逻辑既能跑缓存、
    也能跑实时推送，测试里还能喂手搓 fixture。

    ``zs_from="seg"`` 把中枢建在线段上（默认，对应日线级别的「次级别走势类型」
    就是线段）；``"bi"`` 建在笔上，看小级别结构时用。

    ``recent`` 只保留最近 N 个中枢和买卖点。全市场扫描时必须给——600519 日线
    一只就有 97 个买卖点，5400 只全塞进上下文谁也读不完。

    ``seg_end`` 给了的话，末段会按 `chan.segend` 的对应口径**当下判定是否已经
    结束**并拆开（默认 None = 只认 67课 特征序列，也就是历史基线）。提前判出的
    前段会进入中枢层，但可能被后续K线推翻，必须用走查的稳定/存活列约束。
    ``seg_end_ma_filter`` 再叠加 106课 的最低延伸否决，不单独产生信号。
    """
    out: dict = {"code": code, "level": level.value, "zs_from": zs_from}
    # 用 {代号: 人话} 而不是一串字符串：批量扫描要按代号挑出「不是每只票都有」
    # 的那几条，靠前缀匹配中文句子迟早出事。
    warnings: dict[str, str] = {}

    if len(bars) < MIN_BARS:
        out["error"] = f"K线只有 {len(bars)} 根，不足 {MIN_BARS} 根，划不出中枢"
        return out

    a = analyze(bars, MergeConfig(seg_end=seg_end,
                                  seg_end_ma_filter=seg_end_ma_filter), check=True,
                sub_points=sub_points)
    bis, segs = a.bis, a.segs
    if not bis or not segs:
        out["error"] = "没划出笔或线段"
        return out

    legs = legs_from_segs(segs, bis) if zs_from == "seg" else legs_from_bis(bis)
    zss = build_zhongshus(legs)
    m = macd([b.close for b in bars])
    pts = find_bspoints(legs, zss, bis, m, include_quasi=include_quasi)

    # 不变量违反是**实现 bug**，但报告层不该把整次扫描带崩——记成警告，
    # 让扫描跑完，之后按 code 回查。
    try:
        check_zhongshus(zss, legs)
        check_bspoints(pts, zss)
    except InvariantError as e:
        warnings["invariant"] = f"不变量违反（这是划线 bug，不是行情特殊）：{e}"

    last = bars[-1]
    out["bars"] = {
        "count": len(bars),
        "first_ts": bars[0].ts,
        "last_ts": last.ts,
        "last_close": round(last.close, 4),
    }
    out["bi"] = {"count": len(bis), "provisional_tail": not bis[-1].confirmed}
    out["seg"] = {
        "count": len(segs),
        "confirmed": sum(s.confirmed for s in segs),
        "pending": sum(s.pending for s in segs),
        "provisional_tail": not segs[-1].confirmed,
    }
    # 级别树。走势类型的切分以它为准——走势类型是时间轴的**划分**，首尾相接、
    # 不重叠（72课：分界是一个点），`tree._greedy_groups` 遵守这一点。
    layers = build_tree(legs)
    cur = layers[0].trends[-1] if layers and layers[0].trends else None

    # 推演：当前走势类型 + 完备的后续选项表。
    #
    # 背驰另测一次再喂进去：`find_bspoints` 内部也判背驰，但它判的是「这里
    # 有没有买卖点」，吐出来的是点，不是**最后一个中枢离开段的力度读数**。
    # `detect` 返回 None 是常态（最后一个中枢还没走掉），`project` 会如实
    # 印成「还判不了」。
    f = project(zss, last.close, beichi=detect(legs, zss, bis, m),
                trend_zss=cur.zhongshus if cur else None)

    # `trend` 取**当下**口径，而且直接取 `project` 算好的那个值，不另算一遍
    # ——这个字段和 `forward.trend` 必须永远是同一个数。
    #
    # 这里原先用的是 `zhongshu.classify(zss)`，错在喂给它的东西：那是 17课
    # p36 对「一个**完成的**走势类型」的定义，套在整段中枢序列上只会有一个
    # 答案——只要历史上有一对相邻中枢不同向就判盘整，而几千根K线上必然有。
    # 实测 15 只票 × 日线/30分/5分**全部**返回 `panzheng`，它是个常数。
    # 现在 `classify` 还在用，但喂的是**一个**走势类型（树切出来的），口径对了。
    out["trend"] = f["trend"]
    out["forward"] = _forward_json(f)

    # 每一层各一份走势类型 + 各自的推演。level 0 复用上面算好的那份。
    out["tree"] = [_layer_json(L, layers, bis, bars, last.close,
                               out["forward"] if L.level == 0 else None, m)
                   for L in layers]

    # 本级别的末枢在两张表里不是同一个：上面 `zhongshus` 那张平表没拆，树按
    # 33课 p79 把 9 段以上的中枢三等分了。实测 30 只日线票里 4 只如此。
    # 推演的触发价用的是**平表**那个（和 `zhongshus`、买卖点锚定口径一致），
    # 所以要明说，否则模型会拿树里那个窄区间去对这边的价位。
    if layers and layers[0].zhongshus and zss and (
            zs_key(zss[-1]) != zs_key(layers[0].zhongshus[-1])):
        warnings["tree_split"] = (
            "本级别最后一个中枢段数过多，级别树按 33课 p79 把它三等分了："
            "`tree[0].current` 的 zd/zg 是第三个子中枢（窄），而 `forward` "
            "的触发价和 `zhongshus` 列表用的是未拆的那个（宽）。两种读法都"
            "合法，说价位时讲清是哪一个。"
        )

    zs_all = [_zs_json(z, legs, bis, bars) for z in zss]
    bs_all = [_bs_json(p, bars) for p in pts]
    out["zhongshu_count"] = len(zs_all)
    out["bspoint_count"] = len(bs_all)
    out["zhongshus"] = zs_all[-recent:] if recent else zs_all
    out["bspoints"] = bs_all[-recent:] if recent else bs_all
    out["truncated"] = bool(recent) and (len(zs_all) > len(out["zhongshus"])
                                         or len(bs_all) > len(out["bspoints"]))
    out["latest"] = bs_all[-1] if bs_all else None

    # 走势类型分组的多义性（33课 p79，C-amb：程序枚举，模型选择）。
    #
    # 这里原先只挂**一条**备选，来源是 `forward.trailing_trend`——从末枢往回捋。
    # 那不是枚举，只是另一个实现碰巧给的另一个答案：17课 p36 没规定一串同向
    # 中枢必须全并成一个趋势，三个同向中枢有四种合法切法，中间两种一个都没
    # 露过面。现在换成 `ambiguity.enumerate_groupings` 的完整枚举，首选恒等于
    # 树的切分（实测 30 只票 × bi/seg 共 60 条序列，**60/60 一致**）。
    #
    # **出场条件是「末段读法不同」，不是「有备选」。** 后者 58/60 都为真，
    # 人人都有的字段模型会学会跳过；前者 20/60，而且实测这 20 条里末段的
    # **kind** 也全都不同——真会改变读数的那些。
    #
    # **按末段去重。** 枚举出来的切法大多只在靠前的位置分叉，末段一模一样
    # ——那个夹具上三条备选的末段全是 `up 中枢9..10`，印三遍只是把同一句话
    # 说三次。留每个不同末段里最简的那一条（`gs` 已按 score 排好序）。
    gs = enumerate_groupings(zss)
    if cur is not None and last_run_differs(gs):
        seen: set[tuple[str, int, int]] = {
            (gs[0].last.kind, gs[0].last.start, gs[0].last.end)}
        alts = []
        for g in gs[1:]:
            k = (g.last.kind, g.last.start, g.last.end)
            if k in seen:
                continue
            seen.add(k)
            alts.append({
                "trend": g.last.kind,
                "run": g.last.count,
                "zhongshu_range": [g.last.start, g.last.end],
                "start_ts": zs_all[g.last.start]["start_ts"],
                "end_ts": zs_all[g.last.end]["end_ts"],
                "runs": len(g.runs),
                "basis": "grouping",
                "notes": g.notes,
            })
        out["alt_readings"] = alts

    if any(z["zn_monitor"]["forecast"] for z in zs_all):
        warnings["zn_forecast"] = (
            "92课 Zn 的下一震荡价是线性几何外推，不是概率或目标价；"
            "300308 实测未稳定优于『下一个 Zn 不变』，validated=false。"
        )
    if not bis[-1].confirmed or not segs[-1].confirmed:
        warnings["provisional_tail"] = (
            "末端笔/段未完成（C-prov）。落在其上的买卖点 confirmed=false，"
            "后面的K线可能把它改掉，不能当已确认信号用。"
        )
    if any(z["level_up"] for z in zs_all):
        warnings["level_up"] = "有中枢延伸到 9 段上限（33课 p79），这一段该升一个级别去看。"
    if any(p["ambiguous"] for p in bs_all):
        warnings["ambiguous"] = (
            "有三买/三卖落在原文自相矛盾的形态上（C15 或 C18），"
            "具体是哪一种见该点 reasons。"
        )
    out["warnings"] = warnings
    return out


def analyze_one_checked(
    bars: list[Bar],
    code: str,
    level: Level,
    *,
    tries: int = MAX_TRIES,
    **kw,
) -> dict:
    """`analyze_one` 算两遍比摘要，一致才信。额外开销 = 一倍 CPU。

    **这不是防 bug，是防硬件**（见 `chan.verify` 的模块文档）。划线层是纯的，
    同一份K线必须给同一份报告；给不出来就只剩内存损坏一种解释。盘后扫全 A 股
    要跑几小时，正是这类损坏最容易安静发生、又最没人看得出来的地方——一个翻在
    价格浮点上的位不会抛异常，只会让某只票的中枢边界错几分钱。

    比 `analyze_one` 多做一件它做不了的事：**K线自己在算之前就坏了**是
    `stable` 的盲区（两遍读同一个列表，一起错），所以这里在前后各取一次
    `bars_digest`。对不上说明要么输入在计算途中被改了（`analyze_one` 不该
    碰输入，那会是 bug），要么就是损坏落在了输入上。两种都要立刻知道。

    不抛异常：``verify.ok=False`` 和 ``warnings`` 是给调用方的信号，由批量
    扫描决定是丢掉这只票还是照登并标红。一只票不该带崩一整晚。
    """
    before = bars_digest(bars)
    out, chk = stable(lambda: analyze_one(bars, code, level, **kw),
                      tries=tries, label=f"{code} {level.value}")
    after = bars_digest(bars)

    out["verify"] = {
        "runs": chk.runs,
        "ok": chk.ok,
        "digest": chk.digests[-1] if chk.ok else None,
        "digests": chk.digests if not chk.ok else None,
        "bars_digest": before,
        "bars_stable": before == after,
    }
    w = out.setdefault("warnings", {})
    if not chk.ok:
        w["unstable"] = (
            f"算了 {chk.runs} 遍、{len(set(chk.digests))} 个不同结果，凑不齐多数。"
            "**这份报告不可信**，机器可能在损坏内存，见 chan/verify.py。"
        )
    elif chk.retried:
        w["recomputed"] = (
            f"第一遍和第二遍不一致，算到第 {chk.runs} 遍才取到多数。"
            "结果本身可信，但这台机器刚刚翻了一个位。"
        )
    if before != after:
        w["bars_mutated"] = (
            "划线前后K线的摘要对不上。要么 analyze_one 改了输入（那是 bug），"
            "要么损坏落在输入上——后者 analyze_one 算几遍都发现不了。"
        )
    return out


def scan_many(
    codes: list[str],
    level: Level,
    load,
    *,
    zs_from: str = "seg",
    within: int = 5,
    kinds: set[int] | None = None,
    sides: set[str] | None = None,
    include_quasi: bool = False,
    confirmed_only: bool = False,
    verify: int = MAX_TRIES,
    on_progress=None,
) -> dict:
    """批量扫描，只回**最近出过买卖点**的那些票。

    ``load(code, level) -> list[Bar]`` 由调用方给，扫描层不决定数据从哪来。
    一只票出错不中断整轮——记进 ``failed``，剩下的照跑。全市场 5400 只，
    为一只停牌退市的票白跑半小时不值得。

    ``within`` 是「最近」的尺度：最后一个买卖点距今不超过这么多根K线。日线上
    5 就是一周。调大它就从「今天能不能买」变成「最近谁走出过结构」。

    ``include_quasi`` 这里默认**关**，和 `analyze_one` 相反。原文说类买卖点
    「在小级别中是意义不大的」（第 503 行），全市场扫描时它们是主要的噪音来源；
    单只深看时才值得翻出来。

    ``confirmed_only`` 滤掉落在未完成笔/段上的点（C-prov）。盘后扫描天然会有
    一批这样的点，它们是**线索**不是信号。

    ``verify`` 是每只票算几遍（默认开，见 `analyze_one_checked`）。**这里是
    最该开的地方**：全市场跑几小时，正是硬件损坏安静发生的那种负载。代价是
    一倍 CPU；`verify=1` 关掉。返回的 ``health`` 不只是日志——「今天 5400 只
    里有几只需要重算」本身就是这台机器的健康读数，趋势比绝对值更有意义。
    """
    hits: list[dict] = []
    failed: list[dict] = []
    missed: Counter[str] = Counter()
    # 重算过的和不可信的都**按代号记下来**，不只计数：事后要能回查到具体是
    # 哪几只，一个总数回查不了。
    recomputed: list[str] = []
    unstable: list[str] = []
    for n, code in enumerate(codes):
        try:
            bars = load(code, level)
            # 这里**不能**截断。`_match` 要在全量点上过滤——`kinds={1}` 时
            # 最近三个点可能全是三买，而符合条件的那个 1买 就在它们前面、
            # 仍在 `within` 之内。截完再滤就把它漏了。摘要本来也只取一个点，
            # 多留的那些字典随手就丢，不值得为省它冒这个险。
            # 划线时 quasi **一律算进来**，由 `_match` 再滤。不这么做的话
            # 「被 quasi 滤掉的」和「压根没有点」在输出里长得一模一样。
            r = analyze_one_checked(bars, code, level, tries=verify,
                                    zs_from=zs_from, include_quasi=True)
            v = r.get("verify", {})
            if v.get("runs", 1) > 2:
                recomputed.append(code)
            if not v.get("ok", True) or not v.get("bars_stable", True):
                # 不扔掉：一只票算不稳定不代表它没有信号，而**盖掉它**会让
                # 这台机器的问题从报告里消失。照常参与匹配，另记一笔。
                unstable.append(code)
            if "error" in r:
                failed.append({"code": code, "error": r["error"]})
            else:
                hit, why = _match(r, within=within, kinds=kinds, sides=sides,
                                  confirmed_only=confirmed_only,
                                  include_quasi=include_quasi)
                if hit is not None:
                    # 只在不正常时挂字段：每一行都印 `"verify": "ok"` 等于
                    # 没印，而这个标记要的是在一屏命中里**跳出来**。
                    if code in unstable:
                        hit["unstable"] = True
                    hits.append(hit)
                else:
                    missed[why["miss_reason"]] += 1
        except Exception as e:  # noqa: BLE001 - 一只票的任何问题都不该停住全市场
            failed.append({"code": code, "error": f"{type(e).__name__}: {e}"})
        if on_progress is not None:
            on_progress(n + 1, len(codes), code)

    hits.sort(key=lambda h: (h["point"]["ts"], h["code"]), reverse=True)
    return {
        "level": level.value,
        "zs_from": zs_from,
        "scanned": len(codes),
        "hit_count": len(hits),
        "failed_count": len(failed),
        "missed_because": dict(missed),
        # 机器健康读数（见 chan/verify.py）。`recomputed` 非空就意味着这一轮
        # 里真的发生过位翻；`unstable` 非空意味着**有票的结果不可信**，那一轮
        # 的输出整体都该打问号，不是只打那几只。
        "health": {
            "tries": verify,
            "recomputed_count": len(recomputed),
            "unstable_count": len(unstable),
            "recomputed": recomputed,
            "unstable": unstable,
        },
        "filter": {
            "within": within,
            "kinds": sorted(kinds) if kinds else None,
            "sides": sorted(sides) if sides else None,
            "include_quasi": include_quasi,
            "confirmed_only": confirmed_only,
        },
        "hits": hits,
        "failed": failed,
    }


def _match(
    r: dict, *, within: int, kinds: set[int] | None, sides: set[str] | None,
    confirmed_only: bool, include_quasi: bool = True,
) -> tuple[dict | None, dict]:
    """这只票算不算命中。回 ``(命中摘要 or None, 没中的原因)``。

    摘要只带**决定要不要点开**所需的东西：最近那个点、它多久以前、当下走势、
    以及几个可靠性标记。完整结构让模型再调一次 `chan_analyze` 去拿——
    5400 行摘要塞得进上下文，5400 份完整结构塞不进。

    **为什么没中也要说清楚。** 「0 命中」有两种完全不同的含义：这些票上
    *没有*买卖点，和这些票上的买卖点*被筛掉了*。实测过一次代价：391 只扫出
    0 命中，看起来像全市场没机会，实际是 97 只在 5 根内都有点，只是**无一例外
    是 quasi 且 confirmed=false**，被默认筛条件整批滤掉了。

    所以原因是**从够新的那些点里**倒推的，不是从全部点里。先按 `within` 切出
    够新的一批，再逐道筛，记下第一个把它清空的那道。顺序反过来的话上面那 391
    只会一律报「只是不够新」——字面上没错（滤掉 quasi 之后最近的合格点确实在
    5 根之外），但把人引向「调大 within」，而真正该做的是加 `--quasi`。
    """
    pool = r["bspoints"]
    total = len(pool)
    last = r["bars"]["count"] - 1

    def ago(p: dict) -> int:
        return last - p["bar"]

    def miss(reason: str, n: int | None = None) -> tuple[None, dict]:
        return None, {"bspoint_count": total, "miss_reason": reason,
                      "nearest_ago": n}

    if not pool:
        return miss("none")

    fresh = [p for p in pool if ago(p) <= within]
    if not fresh:
        # 有点，只是都不够新。这个 `nearest_ago` 直接告诉人 within 要调到多大。
        return miss("stale", ago(pool[-1]))

    for name, keep in (
        ("quasi", lambda p: include_quasi or not p["quasi"]),
        ("confirmed", lambda p: not confirmed_only or p["confirmed"]),
        ("kind", lambda p: not kinds or p["kind"] in kinds),
        ("side", lambda p: not sides or p["side"] in sides),
    ):
        fresh = [p for p in fresh if keep(p)]
        if not fresh:
            return miss(name, ago(pool[-1]))

    p = fresh[-1]
    return {
        "code": r["code"],
        "trend": r["trend"],
        "last_close": r["bars"]["last_close"],
        "last_ts": r["bars"]["last_ts"],
        "bars_ago": ago(p),
        "point": {k: p[k] for k in ("kind", "side", "label", "ts", "price",
                                    "confirmed", "quasi", "ambiguous", "macd_law_ok")},
        # `provisional_tail` 和 `ambiguous` 从 `point` 自己的字段就能看出来，
        # 摘要里再说一遍就是 5400 行重复的套话。只留**点上看不出来**的。
        "warnings": {k: v for k, v in r["warnings"].items()
                     if k in ("invariant", "level_up")},
    }, {}
