"""本级别 + 真次级别的逐根走查：区间套的「当下」版。

`chan.multilevel.nest` 是回头看的：大级别点已经在完整历史上划出来，再去次级别
里找时间窗口内的点。这里是往前走的：每走一根本级别K线，用**那一刻已收盘**的
次级别K线重新划线，取当下的1类点交给 `chan.segend` 的 `sublevel_1bs`，判本级别
末段是否已经结束——第674行「5 分钟回抽确认了一个第三类买点」。

## 一个登记在案的近似（C-nest）

C-nest 要求每个级别在自己的**完整历史**上划线。次级别逐步全量重划跑不动（5分钟
24000 根划一次 1.3 秒，且随长度超线性增长），所以每一步只取截至那一刻的最近
``SUB_WINDOW`` 根次级别K线（4000 根 5分钟 ≈ 三个多月）。窗口起点随步推进，窗口
开头的划线会抖，但离当下几千根之外的抖动传不到末端的1类点上——这是假设，不是
证明。窗口远长于任何一段本级别回抽。

因果性：次级别只取 ``ts <= 本级别第 t 根的 ts`` 的K线。两边的时间戳都是K线的
**结束**时刻，所以「10:00 那根30分钟」收盘时，09:35~10:00 的5分钟都已收盘。
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import replace

from chan.buysell import find_bspoints
from chan.macd import macd
from chan.pipeline import analyze
from chan.types import Bar
from chan.verify import stable
from chan.zhongshu import build_zhongshus, legs_from_segs

SUB_WINDOW = 4000


def sub_window(sub_bars: list[Bar], sub_ts: list[str], cut_ts: str,
               window: int = SUB_WINDOW) -> list[Bar]:
    """截至 ``cut_ts``（含）已收盘的次级别K线，最近 ``window`` 根，下标从0重排。"""
    i = bisect_right(sub_ts, cut_ts)
    return [replace(b, idx=k) for k, b in enumerate(sub_bars[max(0, i - window):i])]


def sub_points(bars: list[Bar], *, include_quasi: bool = True,
               verify: int = 3) -> list[tuple[str, bool]]:
    """次级别K线上**当下**的1类买卖点 ``(所在K线ts, 是否买点)``，中枢建在线段上。"""
    if len(bars) < 60:
        return []

    def calc():
        a = analyze(bars, check=True)
        if not a.segs:
            return []
        legs = legs_from_segs(a.segs, a.bis)
        pts = find_bspoints(legs, build_zhongshus(legs), a.bis,
                            macd([b.close for b in bars]),
                            include_quasi=include_quasi)
        return sorted({(bars[p.bar].ts, p.is_buy) for p in pts if p.kind == 1})

    if verify <= 1:
        return calc()
    out, chk = stable(calc, tries=verify, label=f"sub n={len(bars)}")
    if not chk.ok:
        raise RuntimeError(f"次级别 {bars[-1].ts}：多数校验失败，拒绝入样")
    return out


def _keep(r: dict) -> dict:
    """落盘只留 `Tracker` 用得到的字段。"""
    if "error" in r:
        return {"error": r["error"]}
    f = ("kind", "side", "label", "bar", "ts", "price", "quasi", "confirmed",
         "ambiguous", "reasons")
    return {"trend": r["trend"], "bspoints": [{k: p.get(k) for k in f}
                                              for p in r["bspoints"]]}


def _read_steps(path) -> dict[int, dict]:
    """读回已落盘的步。进程被杀时最后一行可能只写了一半——丢掉它并把文件截齐。"""
    import json
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return {}
    good, broken = [], False
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            good.append(json.loads(line))
        except json.JSONDecodeError:
            broken = True
    if broken:
        p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in good),
                     encoding="utf-8")
    return {x["t"]: x["r"] for x in good}


def run_steps(path, bars: list[Bar], code: str, level, *, start: int, end: int,
              verify: int = 3, step_kw=None, **kw) -> int:
    """把第 start..end-1 步的报告逐行追加到 ``path``；已经落盘的步跳过。

    C-bitflip：这台机器上进程活得越久越容易被损坏，次级别那趟一口气跑完会
    段错误。所以拆成短进程、一步一落盘，崩了从断点续。返回本次新算的步数。
    """
    import json

    from .walk import report_at

    done = _read_steps(path)
    new = 0
    with open(path, "a", encoding="utf-8") as f:
        for t in range(start, min(end, len(bars))):
            if t in done:
                continue
            k = dict(kw)
            if step_kw is not None:
                k.update(step_kw(t))
            r = report_at(bars, t, code, level, verify=verify, **k)
            f.write(json.dumps({"t": t, "r": _keep(r)}, ensure_ascii=False) + "\n")
            f.flush()
            new += 1
    return new


def replay_steps(path, *, first: int, last: int):
    """把落盘的步按 t 重放成买卖点经历。缺步直接报错，不许带着窟窿出数。"""
    from .walk import Tracker

    steps = _read_steps(path)
    missing = [t for t in range(first, last + 1) if t not in steps]
    if missing:
        raise ValueError(f"{path} 缺 {len(missing)} 步（首个 t={missing[0]}），没走完")
    tr = Tracker()
    for t in range(first, last + 1):
        tr.step(t, steps[t])
    return tr.finish()


# --------------------------------------------------------------------------
# 「当下3买」：20课 原文定义，回试一结束就成立，由5分钟1买判回试结束
# --------------------------------------------------------------------------
#
# 引擎的 `buysell._third_at` 要 `zs.depart_leg` 有值，而「离开」按 18课 p40 要
# **连续两段**不触及中枢——回试段之后还得再走完并确认一段。所以引擎的 3买
# 总要等回试**之后那一段**也走完。20课 p43 第966行的定义本身没有这一步：「一个
# 次级别走势类型向上离开缠中说禅走势中枢，然后以一个次级别走势类型回试，其
# 低点不跌破 ZG，则构成第三类买点」。第674行的实战就是这么用的。


def retest_candidate(a, bars: list[Bar]):
    """当下是否处在「向上离开中枢 → 正在回试、低点仍在 ZG 上方」的形态里。

    要求（全部在本级别、只用已确认的段建中枢）：末段向下且未确认；倒数第二段
    向上、已确认，且就是末中枢最后一段触及中枢的那段（离开段）、高点在 ZG 之上；
    末中枢还没被引擎判「离开」（否则那是引擎自己的 3买 了）；回试至今最低点
    严格高于 ZG。返回 ``(key, zg, 回试最低价, 最低那根K线下标, 回试起点ts)``
    或 None。``key`` 用（中枢起点段, 离开段起点K线）标识这一次离开。
    """
    from chan.zhongshu import build_zhongshus, legs_from_segs
    from chan.num import gt

    if len(a.segs) < 2:
        return None
    tail, leave = a.segs[-1], a.segs[-2]
    if tail.confirmed or not leave.confirmed:
        return None
    if tail.direction.name != "DOWN" or leave.direction.name != "UP":
        return None
    legs = legs_from_segs(a.segs, a.bis)
    zss = build_zhongshus(legs)
    if not zss:
        return None
    zs = zss[-1]
    li = len(legs) - 2
    if zs.depart_leg is not None or zs.end_leg != li or not gt(legs[li].high, zs.zg):
        return None
    lo_i = min(range(tail.start.peak_idx, len(bars)), key=lambda i: bars[i].low)
    if not gt(bars[lo_i].low, zs.zg):
        return None
    key = (zs.start_leg, leave.start.peak_idx)
    return key, zs.zg, bars[lo_i].low, lo_i, bars[tail.start.peak_idx].ts


def sub_confirms_low(sub_pts: list[tuple[str, bool]], low_bar_open_ts: str,
                     low_bar_ts: str, retest_start_ts: str) -> str | None:
    """5分钟当下的1买是否落在回试**当前最低点那根**本级别K线里。

    区间是 ``(low_bar_open_ts, low_bar_ts]``：最低那根的前一根 ts 到它自己的 ts
    （时间戳都是K线结束时刻）。两头都要卡：

    - 更早、更高的低点上的1买不算——价格后来又创了新低，那个底已经被破了；
    - **更晚**的也不算。第一版只卡了左边，于是反弹之后一个更高低点上的1买也被
      算成「回试见底」（那在位置上更接近2买），和预登记的「落在回试当前最低那根
      30分钟K线里」不符：样本外 150 次判出里 99 次是这样来的。

    返回确认用的那个5分钟1买的 ts，否则 None。
    """
    ok = [ts for ts, is_buy in sub_pts
          if is_buy and ts > retest_start_ts and low_bar_open_ts < ts <= low_bar_ts]
    return max(ok) if ok else None


# --------------------------------------------------------------------------
# 「当下3买」第二版：不用背驰，按「第一个中枢」+「走势必完美」+ 5分钟向上笔确认
# --------------------------------------------------------------------------
#
# 第一版的「5分钟1买」样本外 88 次里 86 次是类1买（盘整背驰）。第二版完全不看背驰：
#
# - 只做第一个中枢的 3买：第 1627 行「尽量只介入第一个中枢的第三类买点」、第 2402 行
#   「第三类买点，一定要在第一个中枢后效果最好」。
# - 走势必完美：「C 段还没有形成一个中枢，根据走势必完美，这 C 段肯定没完」——5分钟
#   回拉段里先得有一个5分钟中枢，回拉这段走势才可能完成。
# - 回拉低点之后第一根5分钟向上笔已被确认（后面已经出现反向笔），回拉确实结束了。


def first_zhongshu(a) -> bool:
    """末中枢是不是跌完之后的第一个：它和前一个中枢的关系是「下跌」（中心定理二）。

    窗口里只有一个中枢时判不了「前面是不是跌下来的」，一律不算——宁缺毋滥。
    """
    from chan.zhongshu import TREND_DOWN, build_zhongshus, legs_from_segs, relation

    zss = build_zhongshus(legs_from_segs(a.segs, a.bis))
    return len(zss) >= 2 and relation(zss[-2], zss[-1]) == TREND_DOWN


def pullback_done(bis, bars: list[Bar], zs_spans: list[tuple[int, int]],
                  retest_start_ts: str) -> str | None:
    """5分钟上的回拉是否已经「完美」并且结束。纯函数，输入是已经划好的5分钟结构。

    ``zs_spans`` 是5分钟中枢覆盖的K线下标闭区间 ``[(起, 止), ...]``。
    条件：回拉开始以来的最低点（``retest_start_ts`` 之后最低的那根）之前，回拉里至少
    有一个5分钟中枢（整个落在回拉起点之后、最低点之前）；并且从最低点出发的向上笔
    已经被确认（不是最后一笔）。返回这根向上笔结束那根K线的 ts，否则 None。
    """
    idx = [i for i, b in enumerate(bars) if b.ts > retest_start_ts]
    if not idx:
        return None
    lo = min(idx, key=lambda i: bars[i].low)
    if not any(idx[0] <= s and e <= lo for s, e in zs_spans):
        return None  # 回拉还没形成中枢：走势必完美，这段肯定没完
    tol = 1e-9 * max(1.0, bars[lo].low)
    for k, bi in enumerate(bis[:-1]):          # 最后一笔还在走，不算确认
        if bi.direction.name == "UP" and abs(bi.start.price - bars[lo].low) <= tol \
                and bi.start.peak_idx >= idx[0]:
            return bars[bi.end.peak_idx].ts
    return None


def top5_index(sub_bars: list[Bar], retest_start_ts: str) -> int:
    """离开段顶那根 30 分钟 K 线（``retest_start_ts`` 是它的结束时刻）里，最高的那根 5 分钟。"""
    end = max(i for i, b in enumerate(sub_bars) if b.ts <= retest_start_ts)
    return max(range(max(0, end - 5), end + 1), key=lambda i: sub_bars[i].high)


def latest_intact_point(sub_bars: list[Bar], bar_idxs: list[int]) -> str | None:
    """买点（K线下标）里「之后没有再创新低」的最晚一个的 ts；都被破了返回 None。"""
    ok = [i for i in bar_idxs
          if min((b.low for b in sub_bars[i + 1:]), default=float("inf"))
          >= sub_bars[i].low * (1 - 1e-9)]
    return sub_bars[max(ok)].ts if ok else None


def sub_pullback_buy(sub_bars: list[Bar], retest_start_ts: str,
                     low_window: tuple[str, str] | None = None, *,
                     verify: int = 3) -> str | None:
    """第三版的 5 分钟确认（2026-10-30 用户定）：回拉走完 = 5 分钟上出现买点。

    5 分钟笔中枢**从回拉的顶那一笔之后重新划**（`build_zhongshus(start=)`），不让顶被吸进
    更早的中枢——否则回拉没有自己的 a，引擎永远比不出盘整背驰。然后要求当下出现
    5 分钟 1买或类 1买（盘整背驰）。返回那个点的 ts，否则 None。

    ``low_window=(开, 止)`` 是严格口径：点必须落在回拉当前最低点那根 30 分钟 K 线里
    （同 `sub_confirms_low`，只卡左边会把更晚更高低点上的点算成回试见底）。
    ``None`` 是 2026-10-30 放宽后的口径：点之后到当下没有再创新低（点还没被破），
    取其中最晚的一个。
    """
    from chan.zhongshu import build_zhongshus, legs_from_bis

    if len(sub_bars) < 60:
        return None

    def calc():
        a = analyze(sub_bars, check=True)
        if not a.bis:
            return None
        top = top5_index(sub_bars, retest_start_ts)
        legs = legs_from_bis(a.bis)
        i0 = next((i for i, x in enumerate(legs)
                   if a.bis[x.src_start].start.peak_idx >= top), None)
        if i0 is None:
            return None
        pts = find_bspoints(legs, build_zhongshus(legs, start=i0), a.bis,
                            macd([b.close for b in sub_bars]), include_quasi=True)
        buys = [p for p in pts if p.kind == 1 and p.is_buy and p.bar > top]
        if low_window is not None:
            return sub_confirms_low(sorted({(sub_bars[p.bar].ts, True) for p in buys}),
                                    low_window[0], low_window[1], retest_start_ts)
        return latest_intact_point(sub_bars, [p.bar for p in buys])

    if verify <= 1:
        return calc()
    out, chk = stable(calc, tries=verify, label=f"sub-buy n={len(sub_bars)}")
    if not chk.ok:
        raise RuntimeError(f"次级别 {sub_bars[-1].ts}：多数校验失败，拒绝入样")
    return out


def sub_pullback_done(sub_bars: list[Bar], retest_start_ts: str, *,
                      verify: int = 3) -> str | None:
    """在5分钟窗口上划线，回拉里的中枢用 **5分钟笔中枢**，交给 `pullback_done` 判。

    为什么是笔中枢而不是线段中枢：一段30分钟回拉在5分钟上通常只有 1~2 段线段，
    线段中枢要三段重叠，几乎不可能出现——第一版就这样写，30只票 418 个候选步
    一次都没触发（抽查 91 步：回拉段里完整的5分钟线段只有 1~2 段）。按级别对应，
    30分钟回拉约等于三根30分钟笔，在5分钟上对应的是笔构成的中枢，也就是「5分钟
    笔走势」。改动发生在看任何收益数字之前，只修了一个结构上不可能触发的条件。
    """
    from chan.zhongshu import build_zhongshus, legs_from_bis

    if len(sub_bars) < 60:
        return None

    def calc():
        a = analyze(sub_bars, check=True)
        if not a.bis:
            return None
        legs = legs_from_bis(a.bis)
        spans = [(a.bis[legs[z.start_leg].src_start].start.peak_idx,
                  a.bis[legs[z.core_end].src_end].end.peak_idx)
                 for z in build_zhongshus(legs)]
        return pullback_done(a.bis, sub_bars, spans, retest_start_ts)

    if verify <= 1:
        return calc()
    out, chk = stable(calc, tries=verify, label=f"sub-pullback n={len(sub_bars)}")
    if not chk.ok:
        raise RuntimeError(f"次级别 {sub_bars[-1].ts}：多数校验失败，拒绝入样")
    return out


# --------------------------------------------------------------------------
# 「当下3买」第三版：aAbBc 背驰出 1买 之后，新走势第一个中枢的 3买
# --------------------------------------------------------------------------
#
# 第二版的 `first_zhongshu`（末中枢与前一个是下跌关系）选中的其实是**下跌趋势的
# 最后一个中枢 B**：正式样本 27 笔的中枢全是「上下上…上」，回拉那一段按「下跌中枢
# 是上下上」读就是 B 之后的 c 段（`_plot/fake3b/trend_001333.png`）。而且 B 后面
# 没有 c，就没有 b、c 可比，MACD 判不了背驰——没有 1买，也就谈不上 2买、3买。
#
# 第三版按完整的链条来（全部在30分钟线段上）：
#   A →(中心定理二 下跌)→ B，B 被一段向下段 c 离开；
#   c = B 之后一路跌到最低的那整段，c 对 b 背驰（`c_beichi`）→ 1买 在 c 的最低点，
#   之后不再创新低（2买 不破 1买）；
#   1买 之后：a'（向上）是新走势的进入段，从下一段起找中枢 A'，必是 下上下；
#   A' 不在 B 之下（否则还是下跌延续）；
#   一段向上段离开 A'（A' 最后触及的那段，高点 > A' 的 ZG），当下正在回拉、
#   回拉最低点仍 > A' 的 ZG。
# A' 用 `zhongshu_at(legs, 1买那段+2)` 单独构造，不用 `build_zhongshus` 的贪心读法——
# 后者按奇偶从 c 之后第一段（a'）起中枢，读出来是 上下上，又回到第二版的毛病。


def c_beichi(legs, zss, k: int, bis, m, li: int, need_c3: bool) -> int | None:
    """B=zss[k] 之后那一整段下跌 c 对 b 的背驰。成立返回 1买 那一段的下标，否则 None。

    不用 `find_bspoints` 的 1买：引擎的 c 是 `_slice_leave`，截到下一个中枢起点为止，
    而贪心读法的下一个中枢紧跟着从离开段后一段起步——c 永远只有一段，真正的最低点
    被划进了后面的中枢（原30只 13 个形态里 12 个，1买 之后还有更低的低点）。
    这里 c = 从离开 B 那段起，到 B 之后、离开 A' 之前最低的那一段向下段。
    量法用引擎同一套（`_measure`：MACD 面积或黄白线，满足其一），门槛照 37课 p96：
    ① A→B 下跌 ② A、B 同级别 ③ b 不自成中枢 ④ c 创新低；⑤ c 含对 B 的 3卖，
    ``need_c3=False`` 时免。
    """
    from chan.beichi import AREA_RATIO, _measure, _slice_enter, _third_point_against, span_of
    from chan.num import lt
    from chan.types import Direction
    from chan.zhongshu import MIN_LEGS, TREND_DOWN, relation

    if k < 1:
        return None
    A, B = zss[k - 1], zss[k]
    c0 = B.core_end + 1
    downs = [x for x in range(c0, li) if legs[x].direction is Direction.DOWN]
    if not downs or relation(A, B) != TREND_DOWN or A.level_up or B.level_up:
        return None
    j = min(downs, key=lambda x: legs[x].low)
    enter = span_of(legs, bis, *_slice_enter(zss, k))
    leave = span_of(legs, bis, c0, j)
    floor = B.dd
    C = c_inner_zhongshu(legs, c0, j)
    if C is not None:
        # a+A+b+B+c+C：趋势有三个中枢，背驰比 d（离开 C）对 c（B→C 的连接段）
        if relation(B, C) != TREND_DOWN or C.depart_leg is None or C.depart_leg >= li:
            return None
        j = min((x for x in range(C.depart_leg, li) if legs[x].direction is Direction.DOWN),
                key=lambda x: legs[x].low)
        enter = span_of(legs, bis, c0, C.start_leg - 1)
        leave = span_of(legs, bis, C.depart_leg, j)
        floor = C.dd
    if (enter is None or leave is None or enter.direction is not Direction.DOWN
            or enter.leg_count >= MIN_LEGS or not lt(leave.low, floor)):
        return None
    if need_c3 and not _third_point_against(B, legs, c0, j, Direction.DOWN):
        return None
    _, _, area_ok, dif_ok, *_ = _measure(m, enter, leave, Direction.DOWN, AREA_RATIO)
    return j if (area_ok or dif_ok) else None


def c_inner_zhongshu(legs, c0: int, j: int):
    """c（离开 B 的那段 ``c0`` 起，到最低那段 ``j``）里面自己的笔中枢 C，没有返回 None。

    C 在 1买 那段之前就已成形（三段构成都在 ``j`` 之前），第一构成段向上
    （与离开段 c0 相反，上下上）。用户 2026-10-29 指出 300750 的 c 里有这个中枢而图上没画：
    有 C 时走势是 a+A+b+B+c+C，背驰要比 d 对 c；C 若一路延伸到当下，A' 只是 C 的延伸。
    """
    from chan.types import Direction
    from chan.zhongshu import usable_count, zhongshu_at

    us = usable_count(legs, True)
    for i in range(c0 + 1, j - 1):
        if legs[i].direction is Direction.DOWN:
            continue
        z = zhongshu_at(legs, i, us)
        if z is not None:
            return z
    return None


def _chain_1buy(a, bars: list[Bar], zs_from: str, need_c3: bool):
    """aAbBc 链条到 1买 为止。成立返回 ``(legs, B, j, li, tail, leave)``，否则 None。

    ``j`` 是 1买 那一段的下标；``tail`` / ``leave`` 是当下末笔（回拉）和上一笔（离开 A'）。
    """
    from chan.zhongshu import (TREND_DOWN, build_zhongshus, legs_from_bis, legs_from_segs,
                               relation)

    units = a.segs if zs_from == "seg" else a.bis
    if len(units) < 2:
        return None
    tail, leave = units[-1], units[-2]
    if tail.confirmed or not leave.confirmed:
        return None
    if tail.direction.name != "DOWN" or leave.direction.name != "UP":
        return None
    legs = legs_from_segs(a.segs, a.bis) if zs_from == "seg" else legs_from_bis(a.bis)
    li = len(legs) - 2
    zss = build_zhongshus(legs)
    gone = [k for k, z in enumerate(zss) if z.depart_leg is not None and z.depart_leg < li]
    if not gone or gone[-1] == 0:
        return None
    k = gone[-1]
    B, c = zss[k], zss[k].depart_leg
    if relation(zss[k - 1], B) != TREND_DOWN or legs[c].direction.name != "DOWN":
        return None
    # c 末端的 1买：c 取 B 之后到离开 A' 之前最低的那段，按定义 1买 之后不再创新低
    # （2买 不破 1买）。a' = j+1，A' 从 j+2 起。
    j = c_beichi(legs, zss, k, a.bis, macd([x.close for x in bars]), li, need_c3)
    if j is None:
        return None
    return legs, B, j, li, tail, leave


def one_buy_armed(a, bars: list[Bar], need_c3: bool = False):
    """30 分钟 c 末背驰「当下已具备」（区间套的大级别一层，2026-11-02 用户定，**不等 a' 确认**）。

    第三版同一套链条：笔中枢上 aAbBc（C 分支），c 取到当下最低那段，对 b 的 MACD 背驰（免第⑤道）；
    要求 c 的最低那段就是最后一笔或倒数第二笔（价格现在就在低点附近）。c 可能还没走完，所以只是
    「布防」：真正的入场点由 5 分钟的买点（`one_buy_entry`）给出。
    返回 ``(c 最低那段的起点K线下标, 终点K线下标, 最低价)``，不具备返回 None。
    """
    from chan.zhongshu import TREND_DOWN, build_zhongshus, legs_from_bis, relation

    if len(a.bis) < 4:
        return None
    legs = legs_from_bis(a.bis)
    li = len(legs)
    zss = build_zhongshus(legs)
    gone = [k for k, z in enumerate(zss) if z.depart_leg is not None]
    if not gone or gone[-1] == 0:
        return None
    k = gone[-1]
    B, c = zss[k], zss[k].depart_leg
    if relation(zss[k - 1], B) != TREND_DOWN or legs[c].direction.name != "DOWN":
        return None
    j = c_beichi(legs, zss, k, a.bis, macd([x.close for x in bars]), li, need_c3)
    if j is None or j < li - 2:
        return None
    return a.bis[j].start.peak_idx, a.bis[j].end.peak_idx, legs[j].low


def seg_beichi_now(a, bars: list[Bar], m=None):
    """5 分钟（单一级别）的当下背驰段：线段是本级别、笔是次级别，在同一张图里做区间套（2026-11-03 用户定）。

    依据：61课「先假设是进入背驰段……背驰如果没有创新高，是不存在的」；49课「找出向下离开中枢的当下该段
    走势，看成背驰判断里的背驰段」；24课「C 段还没有形成一个中枢，根据走势必完美，这 C 段肯定没完」。
    「两段次级别不回中枢」（18课）是中枢破坏 / 3买 的条件，1买 不用它——所以 B 还没被确认离开时，B 的最后
    一段若已跌出 ZD，就当作离开段（当下离开），B 用去掉这一段的部分重算。

    条件：① 线段中枢 B 之后的向下段 c（可以没走完）的最低点 < B 的 DD；② 低点在最后两笔里（是当下的低点）；
    ③ c 里在低点那笔之前已有一个笔中枢；④ c（到当下）对 b 的 MACD 面积或黄白线背驰。
    返回 ``{"kind": "trend"|"panzheng", "low_bi", "low_bar", "low", "low_confirmed"}``，不成立返回 None。
    trend = 前面还有一个中枢 A 且 A→B 下跌（中心定理二）；否则是盘整背驰。两种都返回，由调用方分开统计。
    """
    from dataclasses import replace as _rp

    from chan.beichi import AREA_RATIO, _measure, span_of
    from chan.types import Direction
    from chan.zhongshu import TREND_DOWN, build_zhongshus, legs_from_bis, legs_from_segs, relation, zhongshu_at

    if len(a.segs) < 4 or len(a.bis) < 6:
        return None
    legs = legs_from_segs(a.segs, a.bis)
    n = len(legs)
    zss = build_zhongshus(legs)
    if not zss:
        return None
    k = len(zss) - 1
    B = zss[k]
    if B.depart_leg is not None:
        c0 = B.depart_leg
    elif (legs[B.core_end].direction is Direction.DOWN and legs[B.core_end].low < B.zd
          and B.core_end - B.start_leg >= 3):
        c0 = B.core_end
        B = zhongshu_at(legs, B.start_leg, c0, index=B.index)
        if B is None:
            return None
    else:
        c0 = B.core_end + 1
    if c0 >= n or legs[c0].direction is not Direction.DOWN:
        return None
    bis = a.bis
    b0 = legs[c0].src_start
    jlow = min(range(b0, len(bis)), key=lambda i: bis[i].low)
    low = bis[jlow].low
    if not low < B.dd or jlow < len(bis) - 2:
        return None
    bl = legs_from_bis(bis)
    if not any(z.start_leg >= b0 and z.core_end < jlow for z in build_zhongshus(bl, start=b0)):
        return None
    lo, hi = (zss[k - 1].core_end + 1, B.start_leg - 1) if k >= 1 else (B.start_leg - 1, B.start_leg - 1)
    enter = span_of(legs, bis, lo, hi)
    leave = span_of(legs, bis, c0, n - 1)
    if enter is None or leave is None or enter.direction is not Direction.DOWN:
        return None
    leave = _rp(leave, bar_end=len(bars) - 1, low=min(leave.low, low))
    if m is None:
        m = macd([x.close for x in bars])
    _, _, area_ok, dif_ok, *_ = _measure(m, enter, leave, Direction.DOWN, AREA_RATIO)
    if not (area_ok or dif_ok):
        return None
    kind = "trend" if k >= 1 and relation(zss[k - 1], B) == TREND_DOWN else "panzheng"
    return {"kind": kind, "low_bi": jlow, "low_bar": bis[jlow].end.peak_idx, "low": low,
            "low_confirmed": bis[jlow].confirmed,
            "B": (B.start_leg, B.core_end, B.zd, B.zg, B.dd)}


def latest_buy_point(pts, sub_bars: list[Bar], after_bar: int, kinds=(1, 3)):
    """5 分钟买点里时间晚于 ``after_bar``（K线下标）、之后没有再创新低的最晚一个：``(下标, 低点)``。

    ``pts`` 是 `find_bspoints` 的结果；``kinds`` 默认 1、3 类（含类 1买；用户定「都算」，2买不算）。
    """
    ok = [p.bar for p in pts if p.is_buy and p.kind in kinds and p.bar > after_bar
          and min((b.low for b in sub_bars[p.bar + 1:]), default=float("inf"))
          >= sub_bars[p.bar].low * (1 - 1e-9)]
    return (max(ok), sub_bars[max(ok)].low) if ok else None


def one_buy(a, bars: list[Bar], zs_from: str = "bi", need_c3: bool = False):
    """当下链条的 1买：``(1买那根K线下标, 1买价)``，链条不成立返回 None。"""
    ch = _chain_1buy(a, bars, zs_from, need_c3)
    if ch is None:
        return None
    legs, _, j, *_ = ch
    return a.bis[j].end.peak_idx, legs[j].low


def retest_after_1buy(a, bars: list[Bar], zs_from: str = "seg", need_c3: bool = True):
    """第三版回试形态。返回值同 `retest_candidate`：``(key, zg, 回试最低价, 最低那根
    K线下标, 回试起点ts)``，``zg`` 是 A' 的 ZG；不成立返回 None。

    ``zs_from="bi"`` 把整条链（中枢、离开、回拉）都放到笔上：同一张图低一个级别。
    ``need_c3=False`` 免掉 37课 p96 第⑤道「c 至少包含对 B 的一个第三类买卖点」。
    2026-10-28 用户定的第三版口径：笔级别 + 免第⑤道，①~④照旧。
    """
    from chan.num import gt
    from chan.zhongshu import TREND_DOWN, relation, usable_count, zhongshu_at

    ch = _chain_1buy(a, bars, zs_from, need_c3)
    if ch is None:
        return None
    legs, B, j, li, tail, leave = ch
    lo_i = min(range(tail.start.peak_idx, len(bars)), key=lambda i: bars[i].low)
    A2 = zhongshu_at(legs, j + 2, usable_count(legs, True))
    if (A2 is None or A2.direction.name != "DOWN" or A2.depart_leg is not None
            or A2.end_leg != li or relation(B, A2) == TREND_DOWN
            or not gt(legs[li].high, A2.zg) or not gt(bars[lo_i].low, A2.zg)):
        return None
    C = c_inner_zhongshu(legs, B.core_end + 1, j)
    if C is not None and not gt(A2.zd, C.zg):
        return None   # A' 与 C 重叠：还在 C 的震荡里，不是新走势的第一个中枢
    return (A2.start_leg, leave.start.peak_idx), A2.zg, bars[lo_i].low, lo_i, \
        bars[tail.start.peak_idx].ts


# --------------------------------------------------------------------------
# 3买之后的结构化卖法：44课 30分钟操作的全仓处理 + 21课 两种必然情况
# --------------------------------------------------------------------------
#
# 前两版都是固定持有 40 根卖。原文的卖法看结构：
#
# - 21课 第360行：3买之后只有「中枢扩张」（更大级别中枢）和「中枢新生」（上涨趋势）
#   两种情况；中枢**延续**时中枢上不可能有买点。所以收盘跌回 ZG 下面 = 这个 3买
#   判错了，走。
# - 44课 第785行：按30分钟操作，「背驰级别等于当下的走势级别……只要在顶背驰时直接
#   全部卖出就可以」；小级别的回调「如果那向下的5分钟走势跌破构成最后一个30分钟
#   中枢的第三类买点那个5分钟回试的5分钟走势类型的高点，那么，任何的向上回抽都
#   必须先离开」。回试的高点就是回试起点，即离开段的顶。
# - 20课：「一旦不能出现趋势，一定要在盘整的高点出掉」——盘整背驰（类1卖）也算。
#
# 两处登记在案的简化：引擎在30分钟上吐出的**任何**卖点（1/2/3类、含类卖点）都当
# 卖出信号，不只顶背驰；「跌破回试高点之后的向上回抽离开」简化成跌破那根收盘就走，
# 且要求价格先越过回试高点一次（否则买入当下就在它下面，规则立刻触发）。


def exit_facts(a, bars: list[Bar], e: int, zg: float) -> dict:
    """第 t 步（``bars`` 截到 t）卖法要用的结构事实。纯函数。

    ``sells``：引擎当下划出的、落在买入那根之后的卖点 ``[类, K线, 是否类卖点]``。
    ``rels``：3买 那个中枢（按 ZG 认——ZG 诞生即定死）与其后每个中枢的 20课
    中心定理二关系；认不出原中枢时为 None。只作描述（新生/扩张），不参与卖出。
    """
    from chan.num import eq
    from chan.zhongshu import build_zhongshus, legs_from_segs, relation

    if not a.segs:
        return {"sells": [], "rels": None}
    legs = legs_from_segs(a.segs, a.bis)
    zss = build_zhongshus(legs)
    pts = find_bspoints(legs, zss, a.bis, macd([b.close for b in bars]),
                        include_quasi=True)
    sells = sorted([p.kind, p.bar, p.quasi] for p in pts if not p.is_buy and p.bar > e)
    orig = next((z for z in zss if eq(z.zg, zg)), None)
    rels = None if orig is None else [relation(orig, z) for z in zss if z.index > orig.index]
    return {"sells": sells, "rels": rels}


def structural_exit(steps: dict[int, dict], bars: list[Bar], e: int, zg: float,
                    h_ref: float, cap: int) -> tuple[int, str]:
    """按上面的卖法决定第几根收盘卖。``steps[t]`` 是第 t 步的 `exit_facts`。

    从 e+1 起逐根看，先到者卖：收盘 < ZG（``stop``，3买判错）；价格越过回试高点
    ``h_ref`` 之后收盘又跌回它下面（``ref``）；引擎出现新卖点（``sell``）；持有满
    ``cap`` 根（``cap``）。数据走完还没卖，按最后一根收盘了结（``eod``）。
    T+1 和跌停顺延不在这里，交给 `costs.exit_long`。
    """
    from chan.num import gt, lt

    above = False
    last = min(e + cap, len(bars) - 1)
    for t in range(e + 1, last + 1):
        if t not in steps:
            raise ValueError(f"缺第 {t} 步，没走完")
        c = bars[t].close
        if lt(c, zg):
            return t, "stop"
        if above and lt(c, h_ref):
            return t, "ref"
        if steps[t]["sells"]:
            return t, "sell"
        above = above or gt(bars[t].high, h_ref)
        if t - e >= cap:
            return t, "cap"
    return last, "eod"


# --------------------------------------------------------------------------
# 第三版的卖法（2026-10-30，写在任何第三版卖出走查之前）
# --------------------------------------------------------------------------
#
# 第二版那套的「收盘跌回 ZG 就卖」是按 21课 第360行写的（中枢延续时中枢上没有买点），
# 但用户 2026-10-29 纠正：背驰 = 走势终结，之后是盘整或上涨，3买 之后回到中枢是可能的
# （盘整 aBb 背驰）。所以这条不再当止损，也不再叫「假 3买」。
#
# 第三版入场是 1买（aAbBc 的 c 末背驰）之后新走势第一个中枢 A' 的 3买，卖出按结构：
#
#   broken  收盘跌破 1买价：2买 不破 1买（原文），破了说明下跌没结束，前提不成立。
#   sell    从 1买 起重划的 30 分钟**笔**中枢上，引擎划出买入之后的任何卖点（1/2/3 类，
#           含类 1卖 = 盘整背驰）。20课「一旦不能出现趋势，一定要在盘整的高点出掉」；
#           44课 30 分钟操作「顶背驰直接全部卖出」。中枢从 1买 之后那一笔起重划，
#           和 5 分钟确认一样，不让更早的结构把新走势吸进去。
#   cap     持有满 CAP 根。
#   eod     数据走完未卖，按最后一根收盘了结（单独计数）。
#
# 和第二版相比去掉了 ref（跌破回试高点）：它来自 44课 5 分钟回调的说法，在盘整里会被
# 反复触发，用户纠正之后没有依据。简化同第二版：任何卖点都算，不只顶背驰。


def exit_facts_v3(a, bars: list[Bar], e: int, lo1: int) -> dict:
    """第 t 步（``bars`` 截到 t）卖法要用的结构事实。纯函数。

    ``lo1`` 是 1买 那根 K 线下标。``sells``：从 1买 起重划的笔中枢上、落在买入那根
    之后的卖点 ``[类, K线, 是否类卖点]``。
    """
    from chan.zhongshu import build_zhongshus, legs_from_bis

    legs = legs_from_bis(a.bis)
    i0 = next((i for i, x in enumerate(legs) if a.bis[x.src_start].start.peak_idx >= lo1),
              None)
    if i0 is None:
        return {"sells": []}
    pts = find_bspoints(legs, build_zhongshus(legs, start=i0), a.bis,
                        macd([b.close for b in bars]), include_quasi=True)
    return {"sells": sorted([p.kind, p.bar, p.quasi] for p in pts
                            if not p.is_buy and p.bar > e)}


def structural_exit_v3(steps: dict[int, dict], bars: list[Bar], e: int, p1: float,
                       cap: int) -> tuple[int, str]:
    """第三版卖法：第 e 根买入，从 e+1 起逐根看，先到者在那根收盘卖。

    ``broken`` 收盘 < 1买价 ``p1``；``sell`` 该步有卖点；``cap`` 持有满 ``cap`` 根；
    数据走完还没卖按最后一根收盘 ``eod``。T+1 和跌停顺延交给 `costs.exit_long`。
    """
    from chan.num import lt

    last = min(e + cap, len(bars) - 1)
    for t in range(e + 1, last + 1):
        if t not in steps:
            raise ValueError(f"缺第 {t} 步，没走完")
        if lt(bars[t].close, p1):
            return t, "broken"
        if steps[t]["sells"]:
            return t, "sell"
        if t - e >= cap:
            return t, "cap"
    return last, "eod"


# --------------------------------------------------------------------------
# 第四版卖法：从 5 分钟看卖点（2026-10-31，用户定）
# --------------------------------------------------------------------------
#
# 第三版的 30 分钟笔级别卖点（几乎全是类 1卖）比 5 分钟晚 1~5 根 30 分钟，而且常在下跌之后
# 才被认出来（688190 的 5 分钟类 1卖在 86.25，30 分钟的在 81.74）。原文 44课（第 1174 行）：
# 3买之后只有「中枢扩展」或「新中枢上移」，新中枢上移的要求是从 3买 开始的向上段不能出现
# 盘整背驰；卖点由向上段内部逐级的背驰嵌套定位。不管哪种，3买 之后 5 分钟总有向上一段，
# 所以卖点从 5 分钟看。
#
# 口径：入场之后，每根 5 分钟收盘用截至那一刻的最近 4000 根 5 分钟默认划线（笔中枢，
# 不重划），引擎在入场那一刻之后划出的**任何**卖点（1/2/3 类、含类卖点，用户定「任何都算」）
# → 在那根 5 分钟收盘卖；收盘跌破 1买价 → broken；持有满 CAP 根（30 分钟 160 根 = 5 分钟 960 根）
# → cap。T+1、跌停顺延同 `costs.exit_long`（直接在 5 分钟K线上用）。
# 已知：5 分钟上 3买/3卖 很频繁（688190 入场第二天就有一个 3卖），「任何都算」会走得很早；
# 这条是用户明确要求先看有效性的，不是我推荐的。


def exit_facts_v4(a, bars: list[Bar], entry_ts: str) -> dict:
    """第 t 步（``bars`` 截到 t，5 分钟）卖法要用的结构事实。纯函数。

    ``sells``：引擎当下划出的、时间晚于入场那一刻的卖点 ``[类, K线ts, 是否类卖点]``。
    """
    from chan.zhongshu import build_zhongshus, legs_from_bis

    if not a.bis:
        return {"sells": []}
    legs = legs_from_bis(a.bis)
    zss = build_zhongshus(legs)
    if not zss:
        return {"sells": []}
    pts = find_bspoints(legs, zss, a.bis, macd([b.close for b in bars]), include_quasi=True)
    return {"sells": sorted([p.kind, bars[p.bar].ts, p.quasi] for p in pts
                            if not p.is_buy and bars[p.bar].ts > entry_ts)}


def make_step_kw(bars: list[Bar], sub_bars: list[Bar], *,
                 window: int = SUB_WINDOW, verify: int = 3):
    """给 `walk_forward(step_kw=...)` 用：第 t 步只看本级别第 t 根收盘时的次级别。"""
    sub_ts = [b.ts for b in sub_bars]

    def step_kw(t: int) -> dict:
        w = sub_window(sub_bars, sub_ts, bars[t].ts, window)
        return {"sub_points": sub_points(w, verify=verify)}
    return step_kw
