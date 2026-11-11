"""逐根K线「当下」复盘：从一个点开始，5 分钟一根一根往前走。

每走一根只用**截至那一刻**的K线（5 分钟到这一根为止，30 分钟只取已经收盘的），
回答两件事，并且把理由写出来：

1. **末段线段结束了没有。** 67课 特征序列是正路，但要等后面的线段来破坏，
   天然晚。`chan.segend` 收了原文里几条「当下就能判」的处方（区间套降到笔看
   背驰、55课 换显微镜把段内的笔当次级别、平均力度、次级别1类点），这里逐条
   跑一遍，**结论一律取引擎自己的函数**，本模块只负责把条件和数字写成人话。
2. **有没有背驰。** `chan.beichi.detect`，线段中枢、笔中枢各一套。它本来就
   逐道记理由（37课 p96 五道门槛、24课 面积、27课 黄白线），原样带出。

查看器拿**全部K线**的划线当「事后」答案对账（在 replay.js 里算，回放是一页
一页接着加载的，对账跟着已加载的全部页走）：每个事后的线段终点，当下最早在
第几根被哪个口径判出来、晚了几根、各口径误报了几次。这才回答得了「能不能
当下判断」。

## 下标

5 分钟、30 分钟都是 `scan.dashboard.window` 那个窗口的**前缀**，所以任何一步
的K线下标和查看器、和事后划线是同一套，可以直接比。

## 防位翻

一步约 0.4 秒，几百步就是这台机器会坏内存的那种长循环（见 chan/verify.py）。
所以按块切开、一块一个子进程（`scan.scanner._iterate`），每一步 `stable()`。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right

from chan.beichi import detect, reach_reason
from chan.buysell import find_bspoints
from chan.macd import dif_extreme, hist_area, macd
from chan.num import fp
from chan.pipeline import analyze
from chan.segend import (
    MIN_SUB_BIS, _kiss_runs, _m_bi_beichi, _m_force, _m_sub_complete, _m_sub_depart,
    _m_sub_third, _m_sublevel_1bs, _same_dir_bis, _sub_legs, provisional_end,
)
from chan.types import Direction
from chan.zhongshu import build_zhongshus, legs_from_bis, legs_from_segs

#: 末段结束的几种当下口径。BOLL 要更高一级的K线、RSI 是对照组，这里不放。
METHODS = {
    "bi_beichi": "笔背驰（61课 区间套：段内最后一笔对前一同向笔）",
    "sub_depart": "段内笔中枢被反向一笔离开（55课 换显微镜）",
    "sub_third": "段内出现反向3买/3卖（55课 p954）",
    "sub_complete": "段内笔走势类型已完成（走势必完美）",
    "force": "平均力度衰减（「吻」的面积除以时间，原文自称即时）",
    "sublevel_1bs": "次级别（5 分钟）出现反向1类买卖点（区间套）",
}
METHODS_5 = ("bi_beichi", "sub_depart", "sub_third", "sub_complete", "force")
METHODS_30 = METHODS_5 + ("sublevel_1bs",)

#: 快照里画图要的结构只带最近这么多，更早的由查看器用事后划线补
DRAW_BIS, DRAW_SEGS, DRAW_PTS = 60, 12, 10

UP_S = {Direction.UP: "向上", Direction.DOWN: "向下"}
OK, NO = "✓", "✗"


def _ts(bars, i: int) -> str:
    return bars[min(i, len(bars) - 1)].ts[5:16]


def _bi_at(bars, b) -> str:
    return f"{_ts(bars, b.end.peak_idx)} {fp(b.end.price)}"


# --------------------------------------------------------------------------
# 各口径的理由。结论不在这里算，见 `_methods`
# --------------------------------------------------------------------------


def _why_bi_beichi(bis, m, tail, bars) -> list[str]:
    k = _same_dir_bis(bis, tail)
    if len(k) < 2:
        return [f"段内同向笔只有 {len(k)} 根，没有两笔可比"]
    last, prev = k[-1], k[-2]
    up = tail.direction is Direction.UP
    lb, pb = bis[last], bis[prev]
    seg_ext = (max if up else min)((b.high if up else b.low) for b in bis[tail.start_bi:last])
    ext = lb.high if up else lb.low
    new = ext > seg_ext if up else ext < seg_ext
    out = [f"比第 {prev} 笔（到 {_bi_at(bars, pb)}）和第 {last} 笔（到 {_bi_at(bars, lb)}）",
           f"{OK if new else NO} 第 {last} 笔{'创本段新高' if up else '创本段新低'}：{fp(ext)} 对段内此前 {fp(seg_ext)}"]
    a_l = abs(hist_area(m, lb.start.peak_idx, lb.end.peak_idx, positive=up))
    a_p = abs(hist_area(m, pb.start.peak_idx, pb.end.peak_idx, positive=up))
    out.append(f"{OK if 0 < a_p and a_l < a_p else NO} {'红' if up else '绿'}柱面积 {fp(a_l)} 对前一笔 {fp(a_p)}"
               "（要更小，24课 p52）")
    d_l = dif_extreme(m, lb.start.peak_idx, lb.end.peak_idx, want_max=up)
    d_p = dif_extreme(m, pb.start.peak_idx, pb.end.peak_idx, want_max=up)
    ok = d_l < d_p if up else d_l > d_p
    out.append(f"{OK if ok else NO} DIF {'高点' if up else '低点'} {fp(d_l)} 对前一笔 {fp(d_p)}"
               f"（要{'没跟着创新高' if up else '没跟着创新低'}）")
    return out


def _sub_zs(bis, tail, bars):
    """段内的笔 → 次级别中枢。不够时返回 (None, 理由)。"""
    legs, sub_bis, offset = _sub_legs(bis, tail)
    n = len(bis) - tail.start_bi
    if legs is None:
        return None, [f"段内只有 {n} 笔，不足 {MIN_SUB_BIS} 笔，换显微镜看不出东西"]
    zss = build_zhongshus(legs, require_confirmed=False)
    if not zss:
        return None, [f"段内 {n} 笔还没有三笔重叠，没有笔中枢"]
    z = zss[-1]
    a, b = offset + z.start_leg, offset + z.core_end
    return (legs, sub_bis, offset, zss, z), [
        f"段内最后一个笔中枢 [{fp(z.zd)}, {fp(z.zg)}]，第 {a}~{b} 笔"
        f"（{_ts(bars, bis[a].start.peak_idx)} ~ {_ts(bars, bis[b].end.peak_idx)}）"]


def _why_sub_depart(bis, tail, bars) -> list[str]:
    got, out = _sub_zs(bis, tail, bars)
    if got is None:
        return out
    legs, _, offset, _, z = got
    for k in range(z.start_leg + 3, len(legs)):
        g = legs[k]
        if g.low <= z.zg and g.high >= z.zd:
            continue
        same = g.direction is tail.direction
        out.append(f"{NO if same else OK} 第 {offset + k} 笔第一个脱离中枢，方向{UP_S[g.direction]}"
                   + ("，顺着段的方向走，是段在延续" if same else "，和段反向 → 段收在它前一笔"))
        return out
    out.append(f"{NO} 中枢之后的笔都还在中枢区间里晃")
    return out


def _why_sub_third(bis, tail, bars) -> list[str]:
    got, out = _sub_zs(bis, tail, bars)
    if got is None:
        return out
    legs, sub_bis, offset, zss, _ = got
    pts = [p for p in find_bspoints(legs, zss, sub_bis, None, include_quasi=False) if p.kind == 3]
    want_buy = tail.direction is Direction.DOWN
    rev = [p for p in pts if p.is_buy is want_buy]
    same = len(pts) - len(rev)
    if rev:
        p = rev[-1]
        out.append(f"{OK} 段内出现{p.label}：第 {offset + p.leg} 笔回试 {_bi_at(bars, bis[offset + p.leg])} 不回中枢"
                   f" → 离开段之前那一笔是段的终点")
    else:
        out.append(f"{NO} 段内没有{'3买' if want_buy else '3卖'}"
                   + (f"（同向的3类点 {same} 个，说明段还在顺势走）" if same else ""))
    return out


def _why_sub_complete(bis, tail, bars) -> list[str]:
    from chan.tree import build_tree

    legs, _, offset = _sub_legs(bis, tail)
    if legs is None:
        return [f"段内只有 {len(bis) - tail.start_bi} 笔，不足 {MIN_SUB_BIS} 笔"]
    layers = build_tree(legs, require_confirmed=False, max_levels=1)
    trends = layers[0].trends if layers else []
    done = [t for t in trends if t.zhongshus and t.zhongshus[-1].depart_leg is not None]
    if not done:
        return [f"{NO} 段内笔走势类型 {len(trends)} 个，末枢都还没被离开（没走完）"]
    t = done[-1]
    return [f"{OK} 段内笔走势类型 {len(trends)} 个，最近一个已完成（末枢被离开），"
            f"收在第 {offset + t.end_leg} 笔 {_bi_at(bars, bis[offset + t.end_leg])}"]


def _why_force(m, tail) -> list[str]:
    import numpy as np

    runs = _kiss_runs(m)
    want = 1 if tail.direction is Direction.UP else -1
    same = [r for r in runs if r[0] == want]
    side = "DIF 在 DEA 上方" if want > 0 else "DIF 在 DEA 下方"
    if len(same) < 2:
        return [f"{side}的「吻」不足两个，没得比"]
    if same[-1] is not runs[-1]:
        return [f"{NO} 当下这个「吻」不是{side}的那种，和本段方向不对，不表态"]
    d = np.abs(m.dif - m.dea)

    def st(r):
        return float(d[r[1]:r[2] + 1].sum()) / max(r[2] - r[1] + 1, 1)

    a, b = st(same[-1]), st(same[-2])
    n = same[-1][2] - same[-1][1] + 1
    return [f"{OK if a < b else NO} 当下的吻（{side}，已 {n} 根）平均力度 {fp(a)} 对前一个同号吻 {fp(b)}"
            "（要更小）"]


def _why_sublevel(bars, tail, sub_points) -> list[str]:
    want_buy = tail.direction is Direction.DOWN
    start = bars[tail.start.peak_idx].ts
    c = [ts for ts, b in sub_points if b is want_buy and ts > start]
    if not c:
        return [f"{NO} 末段起点 {start[5:16]} 之后，5 分钟还没有{'1买' if want_buy else '1卖'}"]
    return [f"{OK} 末段起点之后 5 分钟出现{'1买' if want_buy else '1卖'} {len(c)} 次，最近 {max(c)[5:16]}"]


# --------------------------------------------------------------------------
# 结论：一律用引擎自己的函数
# --------------------------------------------------------------------------


def _gate_why(bis, tail, j: int) -> str:
    """`_legalize` 把一个候选终点否掉的原因（复述它的两条，不另判）。"""
    a = j - (j - tail.start_bi) % 2
    if a > len(bis) - 2:
        return "判在还在走的最后一笔上，后面还没有反向笔，没得拆（_legalize）"
    return "往前退不到一根越过段起点的同向笔（78课 p197 顶要高于底）"


def _geo(mid: str, bis, m, tail) -> dict:
    """理由在图上画什么：比的是哪两笔、哪个段内中枢、哪两个「吻」。下标都是K线下标。"""
    seg = {"tail": [tail.start.peak_idx, tail.start.price]}
    if mid == "bi_beichi":
        k = _same_dir_bis(bis, tail)
        if len(k) >= 2:
            up = tail.direction is Direction.UP
            seg["strokes"] = [
                {"x0": b.start.peak_idx, "y0": b.start.price, "x1": b.end.peak_idx, "y1": b.end.price,
                 "area": abs(hist_area(m, b.start.peak_idx, b.end.peak_idx, positive=up)),
                 "name": name}
                for b, name in ((bis[k[-2]], "前一笔"), (bis[k[-1]], "末笔"))]
    elif mid in ("sub_depart", "sub_third", "sub_complete"):
        legs, _, offset = _sub_legs(bis, tail)
        zss = build_zhongshus(legs, require_confirmed=False) if legs else []
        if zss:
            z = zss[-1]
            seg["zs"] = {"x0": bis[offset + z.start_leg].start.peak_idx,
                         "x1": bis[offset + z.core_end].end.peak_idx, "zd": z.zd, "zg": z.zg}
    elif mid == "force":
        want = 1 if tail.direction is Direction.UP else -1
        same = [r for r in _kiss_runs(m) if r[0] == want]
        if len(same) >= 2:
            seg["runs"] = [[r[1], r[2]] for r in same[-2:]]
    return seg


def _methods(a, m, bars, ids, sub_points) -> list[dict]:
    bis, tail = a.bis, a.segs[-1]
    out = []
    for mid in ids:
        if tail.confirmed or tail.end_bi <= tail.start_bi + 1:
            out.append({"id": mid, "ok": False, "why": ["末段不足三笔，谈不上中途结束"]})
            continue
        raw = {
            "bi_beichi": lambda: _m_bi_beichi(bis, m, tail),
            "sub_depart": lambda: _m_sub_depart(bis, tail),
            "sub_third": lambda: _m_sub_third(bis, tail),
            "sub_complete": lambda: _m_sub_complete(bis, tail),
            "force": lambda: _m_force(bis, m, tail),
            "sublevel_1bs": lambda: _m_sublevel_1bs(bis, bars, tail, sub_points),
        }[mid]()
        why = {
            "bi_beichi": lambda: _why_bi_beichi(bis, m, tail, bars),
            "sub_depart": lambda: _why_sub_depart(bis, tail, bars),
            "sub_third": lambda: _why_sub_third(bis, tail, bars),
            "sub_complete": lambda: _why_sub_complete(bis, tail, bars),
            "force": lambda: _why_force(m, tail),
            "sublevel_1bs": lambda: _why_sublevel(bars, tail, sub_points),
        }[mid]()
        j = provisional_end(bis, m, tail, mid, bars, sub_points=sub_points)
        rec = {"id": mid, "ok": j is not None, "why": why, "geo": _geo(mid, bis, m, tail)}
        if j is not None:
            e = bis[j].end
            rec.update(bar=e.peak_idx, price=e.price, bi=j)
            why.append(f"→ 判末段已在第 {j} 笔结束：{_bi_at(bars, bis[j])}")
        elif raw is not None:
            why.append(f"→ 条件满足，但{_gate_why(bis, tail, raw)}")
        out.append(rec)
    return out


REACH = {
    "out_of_range": "还没有中枢",
    "no_enter": "最后一个中枢前面没有进入段，没东西可比",
    "no_leave": "还没有离开最后一个中枢的那一段（c 段没出现）",
    "opposite": "进入段和离开段方向相反，这是折返，不是两次同向推进",
}


def walk_start(legs, zss) -> int | None:
    """当下这个走势的进入段 a 是第几段。None = 引擎默认读法没把两个走势搅在一起。

    默认读法（`build_zhongshus` 从头贪心）只管三段重叠和延伸，不管走势在哪里
    结束：上涨冲顶的那段和顶上下来的那段只要都穿过前一个中枢的 [ZD, ZG]，就
    算成它的延伸——**顶被吸进上涨走势的中枢里**，之后下跌走势的第一个中枢就会
    和它配成 A、B（000651 5 分钟 2026-08：Z12 = 段100~102 含 07-30 的顶 7704，
    和顶下来的 Z13 被判 higher_level，「b 段」其实是下跌走势的进入段 a）。

    按走势必完美，顶就是上涨走势的终点、下跌走势的起点（用户 2026-10-30 的
    做法：从顶重划）。判定只用结构：在「前一个中枢 A 的进入段起」到现在里找
    最高点和最低点，靠后的那个是当下走势的起点（末段还在创新高/新低时，那一头
    不算起点）。起点若就是 A 进入段的起点，A 本来就在当下走势里，默认读法没
    问题；它若落在 A 的里面或之后、**并且高过 A 的 GG**（底则低过 DD），说明
    A 之后先离开创了新高再下来，A 属于前一个走势，从顶下来那段起是新走势。
    这是 33课 p79 多义性里挑「以当下走势起点为准」的那一种读法，不是改中枢定义。
    """
    r = _walk_from(legs, zss)
    return r[0] if r and r[0] > r[1] else None


def _walk_from(legs, zss) -> tuple[int, int] | None:
    """(当下走势起点那一段, A 的进入段)。起点可以就是 A 的进入段（A 在当下走势里）。
    找不到、或起点没离开过 A 的波动区间，返回 None。规则见 `walk_start`。"""
    if not zss or len(legs) < 2:
        return None
    A = zss[-2] if len(zss) >= 2 else zss[-1]
    lo = max(0, A.start_leg - 1)  # A 的进入段
    n = len(legs)
    # 走势方向不能看末段：顶下来之后的反弹段是向上的，但当下仍是从顶开始的下跌。
    # 从 A 的进入段起的最高点、最低点，各是哪一段的起点；落在末段终点上的
    # （还在创新高/新低）不是起点。两个里靠后的那个就是当下走势的起点。
    # 一样高（低）时取最晚那段：向上段的终点和下一根向下段的起点是同一个点
    hi = max(range(lo, n), key=lambda j: (legs[j].high, j))
    lw = max(range(lo, n), key=lambda j: (-legs[j].low, j))
    top = hi if legs[hi].direction is Direction.DOWN else hi + 1
    bot = lw if legs[lw].direction is Direction.UP else lw + 1
    cand = [p for p in (top, bot) if p < n]
    if not cand:
        return None
    ext = max(cand)
    down = legs[ext].direction is Direction.DOWN
    # 还得真的离开过 A：顶要高过 A 的 GG（底要低过 DD）。只在 A 的波动区间里的高点
    # 是中枢里的震荡，不是走势的终点——不加这条，600519 上 80 次里重划了 72 次，
    # 盘整里每个小高点都被当成走势起点，把中枢切碎
    from chan.num import gt, lt
    beyond = gt(legs[ext].high, A.gg) if down else lt(legs[ext].low, A.dd)
    return (ext, lo) if beyond else None


def swallowed(legs, zss=None, depth: int = 3) -> list[int]:
    """从当下往回数 ``depth`` 个走势，起点被默认读法吞进了中枢的那些（升序）。

    「吞进」= 起点那一段是某个中枢第一构成段之后的构成段：进顶那段和出顶那段
    都算在同一个中枢里（000651 的 Z12 = 段100~102）。中枢在顶之前就完成、顶只
    是离开段终点的不算——那之后的回抽是正经的 3买/3卖。

    只看当下这个走势不够：000651 08-18 从 7192 起了个新的上涨，当下走势的起点
    换成那个底，没被吞；07-30 那个被吞的顶在再前一个走势的起点上，不往回数的
    话 Z12 和它上面的 3卖 就又回来了。
    """
    zss = build_zhongshus(legs) if zss is None else zss
    out, end = [], len(legs)
    for _ in range(depth):
        sub = legs[:end]
        r = _walk_from(sub, zss if end == len(legs) else build_zhongshus(sub))
        if r is None:
            break
        s = r[0]
        if any(z.start_leg < s <= z.end_leg for z in zss):
            out.append(s)
        end = s
    return sorted(out)


def split_zss(legs, cuts: list[int]) -> list[tuple[int, list]]:
    """在 ``cuts``（升序）这些走势起点处拆开重划。返回 [(这一块用到第几段为止（不含）, 这一块的中枢)]：
    每一块只用它自己的段，下一块从切点那段之后起划。"""
    out, prev = [], None
    for c in cuts + [len(legs)]:
        out.append((c, build_zhongshus(legs[:c]) if prev is None else build_zhongshus(legs[:c], start=prev + 1)))
        prev = c
    return out


def _beichi(legs, bis, m, bars, unit: str = "段") -> dict:
    """``unit``：legs 是线段（"段"）还是笔（"笔"），只影响文字。"""
    zss = build_zhongshus(legs)
    pre = []
    cuts = swallowed(legs, zss)
    j = walk_start(legs, zss)
    if j is None and cuts:
        # 当下走势已经有两个以上中枢，A 不再是前一个走势的了，但它的起点当初被吞进了
        # 前一个走势的中枢：默认读法里那个中枢还在，会被配进来，照样按起点重划
        j = cuts[-1]
    if j is not None:
        g = legs[j]
        down = g.direction is Direction.DOWN
        start_bar = bis[g.src_start].start.peak_idx
        pre.append(f"当下走势：从 {_ts(bars, start_bar)} 的{'顶' if down else '底'} "
                   f"{fp(g.high if down else g.low)} 开始的{'下跌' if down else '上涨'}，第 {j} {unit}是它的进入段 a")
        z1 = build_zhongshus(legs, start=j + 1)
        if not z1:
            # 新走势还没长出中枢：它自己谈不上背驰，这时有用的是前一个走势到这个顶（底）
            # 为止背驰了没有——那正是 1类买卖点要问的。只用顶（底）之前的段，免得离开段 c
            # 把顶后面那段也吞进去
            pre.append(f"它还没形成自己的中枢，谈不上背驰；下面是前一个"
                       f"{'上涨' if down else '下跌'}走势到这个{'顶' if down else '底'}为止的判断")
            legs = legs[:j]
            s = max((c for c in cuts if c < j), default=None)
            if s is None:
                zss = build_zhongshus(legs)
            else:
                # 前一个走势自己的起点也被吞过：它也从起点重划，不然那个中枢又配成 A、B
                zss = build_zhongshus(legs, start=s + 1)
                pre.append(f"前一个走势从第 {s} {unit}起（默认读法把它的起点也吞进了更早的中枢，同样按起点重划）")
            j = None
        else:
            old = [z for z in zss if z.start_leg <= j <= z.end_leg]
            if old:
                pre.append(f"引擎默认读法把这个{'顶' if down else '底'}算进了前一个走势的中枢 "
                           f"[{fp(old[0].zd)}, {fp(old[0].zg)}]（第 {old[0].start_leg}~{old[0].end_leg} {unit}），"
                           "会和这个走势的中枢配成 A、B——两个走势搅在一起（33课 p79 多义性）。这里按走势起点重划")
            zss = z1
    b = detect(legs, zss, bis, m) if zss else None
    if b is None:
        why = reach_reason(legs, zss, bis, len(zss) - 1) if zss else "out_of_range"
        return {"hit": False, "why": pre + [REACH.get(why, why)]}
    k = b.zs_index
    z = zss[k]
    top = b.direction is Direction.UP
    # 走势里第一个中枢前面那段是进入段 a（aAb，只能盘整背驰）；有前一个中枢才是 b（aAbBc）
    en = "b 段" if k >= 1 else "进入段 a"
    head = [f"{'最后一个中枢 B' if k >= 1 else '这个走势的第一个中枢'} [{fp(z.zd)}, {fp(z.zg)}]，"
            f"{en} {_ts(bars, b.enter.bar_start)}~{_ts(bars, b.enter.bar_end)}，"
            f"离开段 c {_ts(bars, b.leave.bar_start)}~{_ts(bars, b.leave.bar_end)}（{UP_S[b.direction]}）"]
    if b.dif_crossed_zero:
        head.append(f"{OK if b.dif_ok else NO} DIF {'高点' if top else '低点'} c={fp(b.dif_leave)} 对 {'b' if k >= 1 else 'a'}={fp(b.dif_enter)}（27课 p66）")
    tail = [f"→ {'成立' if b.hit else '不成立'}：{'趋势' if b.kind == 'trend' else '盘整'}"
            f"{'顶' if top else '底'}背驰" + ("，面积和黄白线都满足，更标准" if b.standard else "")]

    def box(zz):
        return {"x0": bis[legs[zz.start_leg].src_start].start.peak_idx,
                "x1": bis[legs[zz.core_end].src_end].end.peak_idx, "zd": zz.zd, "zg": zz.zg}

    # 画图用：中枢 B（和 A）、进入段/b 段和 c 段各自的K线区间和面积、当下走势起点
    geo = {"zs": box(z), "zs_name": "中枢 B" if k >= 1 else "第一个中枢",
           "b": [b.enter.bar_start, b.enter.bar_end, abs(b.area_enter)], "b_name": en,
           "c": [b.leave.bar_start, b.leave.bar_end, abs(b.area_leave)]}
    if k >= 1:
        geo["za"] = box(zss[k - 1])
    if j is not None:
        g = legs[j]
        geo["start"] = [bis[g.src_start].start.peak_idx,
                        g.high if g.direction is Direction.DOWN else g.low]
    # 引擎的理由统一写「b」；第一个中枢时比的是进入段 a，改成 a 免得和上面对不上
    reasons = [r.replace(" b=", " a=") if k == 0 else r for r in b.reasons]
    return {"hit": b.hit, "kind": b.kind, "top": top, "standard": b.standard,
            "geo": geo, "why": pre + head + reasons + tail}


def _seg_json(s) -> dict:
    return {"x0": s.start.peak_idx, "y0": s.start.price, "x1": s.end.peak_idx,
            "y1": s.end.price, "dir": s.direction.name, "conf": s.confirmed}


def seg_draw_end(s, bis):
    """画图用的线段终点。

    未完成的线段，引擎把终点记在最新一笔的末端，不管方向对不对（向上段可能
    停在一个低点上），直接连线会把中间的高点抹掉。画到段内同向的极值笔端点，
    也就是「此刻结束的话」它的终点。已完成的原样返回。
    """
    if s.confirmed:
        return s.end
    ends = [b.end for b in bis if s.start.peak_idx < b.end.peak_idx <= s.end.peak_idx]
    if not ends:
        return s.end
    return (max if s.direction is Direction.UP else min)(ends, key=lambda f: f.price)


def _seg_draw(s, bis) -> dict:
    e = seg_draw_end(s, bis)
    return {**_seg_json(s), "x1": e.peak_idx, "y1": e.price}


def _zs_json(zss, legs, bis) -> list[dict]:
    return [{"x0": bis[legs[z.start_leg].src_start].start.peak_idx,
             "x1": bis[legs[z.core_end].src_end].end.peak_idx,
             "zd": z.zd, "zg": z.zg} for z in zss]


def level_snapshot(bars, ids, sub_points=None, *, now=False) -> dict | None:
    """一个级别在这一刻的全部判断。``bars`` 已经是截至当下的前缀。"""
    if len(bars) < 60:
        return None
    a = analyze(bars, check=False)
    if len(a.segs) < 2 or len(a.bis) < 4:
        return None
    m = macd([b.close for b in bars])
    bis, segs, tail = a.bis, a.segs, a.segs[-1]
    sl, bl = legs_from_segs(segs, bis), legs_from_bis(bis)
    conf = [s for s in segs if s.confirmed]
    out = {
        "n": len(bars),
        "tail": {**_seg_json(tail), "nbis": tail.end_bi - tail.start_bi + 1,
                 "start_ts": bars[tail.start.peak_idx].ts},
        "prev": ({**_seg_json(conf[-1]), "ts": bars[conf[-1].end.peak_idx].ts} if conf else None),
        "n_conf": len(conf),
        "methods": _methods(a, m, bars, ids, sub_points),
        "beichi": {"seg": _beichi(sl, bis, m, bars), "bi": _beichi(bl, bis, m, bars, "笔")},
    }
    if now:
        from .sublevel import seg_beichi_now
        r = seg_beichi_now(a, bars, m)
        out["now"] = ({"kind": r["kind"], "bar": r["low_bar"], "low": r["low"], "conf": r["low_confirmed"]}
                      if r else None)
    seg_zs = build_zhongshus(sl)
    pts = sub_pts = find_bspoints(sl, seg_zs, bis, m)
    # 默认读法把走势起点（顶/底）吞进了前一个走势的中枢（进顶那段和出顶那段都是
    # 它的构成段）时，按走势起点拆开重划：前一个走势只用顶之前的段，后一个走势
    # 从顶下来那段之后起划。000651 5 分钟 08-06 的「3卖」就是对这种中枢判的。
    # 只拆吞了顶的（见 `swallowed`），往回数几个走势都拆
    cuts = swallowed(sl, seg_zs)
    if cuts:
        pieces = split_zss(sl, cuts)
        pts = sorted((p for c, zz in pieces if zz for p in find_bspoints(sl[:c], zz, bis, m)),
                     key=lambda p: (p.bar, p.kind))
        seg_zs = [z for _, zz in pieces for z in zz]
    first = max(0, len(bis) - DRAW_BIS)
    cut = bis[first].start.peak_idx
    out["draw"] = {
        "cut": cut,
        "bis": [_seg_json(b) for b in bis[first:]],
        "segs": [_seg_draw(s, bis) for s in segs[-DRAW_SEGS:]],
        "bi_zs": [z for z in _zs_json(build_zhongshus(bl), bl, bis) if z["x1"] >= cut],
        "seg_zs": [z for z in _zs_json(seg_zs, sl, bis) if z["x1"] >= cut],
        "pts": [{"bar": p.bar, "ts": bars[p.bar].ts, "price": p.price, "kind": p.kind, "is_buy": p.is_buy,
                 "label": p.label, "quasi": p.quasi, "conf": p.confirmed, "source": p.source}
                for p in pts[-DRAW_PTS:]],
    }
    # 给上一级 sublevel_1bs 用的1类点，口径同 `backtest.sublevel.sub_points`
    # （线段中枢），所以用默认读法那批点，不跟着上面的重划走
    out["_sub"] = sorted({(bars[p.bar].ts, p.is_buy) for p in sub_pts if p.kind == 1})
    return out


def step(w5, w30, t: int) -> dict:
    """第 t 根 5 分钟收盘这一刻。30 分钟只取已经收盘的（时间戳 <= 当下）。"""
    b5 = w5[:t + 1]
    now_ts = b5[-1].ts
    i30 = bisect_right([b.ts for b in w30], now_ts)
    s5 = level_snapshot(b5, METHODS_5, now=True)
    sub = s5.pop("_sub") if s5 else []
    s30 = level_snapshot(w30[:i30], METHODS_30, sub)
    if s30:
        s30.pop("_sub")
    return {"t": t, "ts": now_ts, "close": b5[-1].close, "n30": i30,
            "5min": s5, "30min": s30}


# --------------------------------------------------------------------------
# 跑一段：切块、子进程、自检
# --------------------------------------------------------------------------

#: 一块几步。约 0.25 秒一步、算两遍，一块 5 秒上下，远短于这台机器出事的 20 秒
CHUNK = 10


def _chunk(cid: str) -> dict:
    """子进程里算一块（``cid`` = "起-止"，闭区间）。异常转成 FAIL 记录。"""
    from chan.verify import stable
    from scan.dashboard import window
    from scan.scanner import _TASK as t, FAIL

    try:
        a, b = map(int, cid.split("-"))
        w30, w5 = window(t["code"], t["n30"], source=t["source"])
        steps, bad, redo = [], [], 0
        for i in range(a, b + 1):
            s, chk = stable(lambda: step(w5, w30, i), tries=t["verify"], label=f"{t['code']} t={i}")
            redo += chk.runs > 2
            if not chk.ok:
                bad.append(i)
            steps.append(s)
        return {"code": cid, "status": "ok", "steps": steps, "bad": bad, "redo": redo}
    except Exception as e:  # noqa: BLE001
        return {"code": cid, "status": FAIL, "error": f"{type(e).__name__}: {e}"}


def _key(p) -> tuple:
    return (p["kind"], p["is_buy"], p["bar"])


def _events(prev: dict | None, cur: dict) -> list[str]:
    """这一步相对上一步，哪些判断变了。查看器的「下一个事件」就跳这些。"""
    out = []
    for lv, tag in (("5min", "5分"), ("30min", "30分")):
        p, c = (prev or {}).get(lv), cur.get(lv)
        if not c or not p:
            continue
        if c["n_conf"] > p["n_conf"] and c["prev"]:
            s = c["prev"]
            out.append(f"{tag} 特征序列确认：{UP_S[Direction[s['dir']]]}段在 {s['ts'][5:16]} {fp(s['y1'])} 结束（67课）")
        pm = {x["id"]: x for x in p["methods"]}
        for x in c["methods"]:
            q = pm.get(x["id"], {})
            if x["ok"] and (not q.get("ok") or q.get("bar") != x["bar"]):
                out.append(f"{tag} {x['id']} 判末段结束于 {fp(x['price'])}")
            elif q.get("ok") and not x["ok"]:
                out.append(f"{tag} {x['id']} 撤回")
        for k, name in (("seg", "线段"), ("bi", "笔")):
            if c["beichi"][k]["hit"] and not p["beichi"][k]["hit"]:
                b = c["beichi"][k]
                out.append(f"{tag} {name}{'顶' if b['top'] else '底'}背驰成立（{'趋势' if b['kind'] == 'trend' else '盘整'}）")
        if lv == "5min" and c.get("now") and not p.get("now"):
            out.append(f"{tag} seg_beichi_now 成立（{c['now']['kind']}）")
        pk = {_key(x): x for x in p["draw"]["pts"]}
        ck = {_key(x): x for x in c["draw"]["pts"]}
        # 快照只带最近 DRAW_PTS 个点，只比两边都覆盖到的那一段：撤回一个新点时，
        # 一个很老的点会滑进窗口，不能当成「新出现」
        lo = min((k[2] for k in ck), default=0)
        plo = min((k[2] for k in pk), default=0)
        for k, x in ck.items():
            if k not in pk and k[2] >= plo:
                # 点落在很早的K线上 = 结构变了才「补认」出来的，当下并不能在那时看到
                ago = c["n"] - 1 - x["bar"]
                late = f"（补认，那根在 {ago} 根之前）" if ago > 3 else ""
                out.append(f"{tag} 新出现 {'类' if x['quasi'] else ''}{x['label']} "
                           f"{x['ts'][5:16]} {fp(x['price'])}{late}")
        for k, x in pk.items():
            if k not in ck and k[2] >= lo:
                out.append(f"{tag} 撤回 {'类' if x['quasi'] else ''}{x['label']} {fp(x['price'])}")
    return out


#: 回放一页走几根。查看器（viewer.html 的 RP_PAGE）按这个名字一页一页往后要，两边要一致
PAGE = 120


def run(code: str, t0_ts: str, n: int, out, *, n30: int | None = None, jobs: int = 1,
        verify: int | None = None, source: str | None = None) -> dict:
    """从 ``t0_ts`` 那根 5 分钟起走 ``n`` 根（一页），写出 `window.REPLAY = {...}` 的 .js。

    回放不限长度：查看器走到一页快完时，从这一页最后一根的下一根再要一页
    （返回值里的 ``next``）。事后对账在查看器里按已加载的全部页算，这里不算。
    """
    import json
    import os
    from pathlib import Path

    from chan.verify import MAX_TRIES
    from feeds.cache import DEFAULT_SOURCE
    from scan.dashboard import N30, window
    from scan.scanner import _iterate

    n30, verify, source = n30 or N30, verify or MAX_TRIES, source or DEFAULT_SOURCE
    w30, w5 = window(code, n30, source=source)
    ts5 = [b.ts for b in w5]
    t0 = max(60, min(bisect_left(ts5, t0_ts), len(w5) - 1))  # 第一根 >= t0_ts 的
    t1 = min(t0 + n - 1, len(w5) - 1)
    # 多算起点前一步，只用来和第一步比出事件：一页接一页时，页首那一步的变化才不会丢
    tp = t0 - 1 if t0 > 60 else t0
    cids = [f"{a}-{min(a + CHUNK - 1, t1)}" for a in range(tp, t1 + 1, CHUNK)]

    task = {"code": code, "n30": n30, "source": source, "verify": verify}
    steps, failed, bad, redo = [], [], [], 0
    for rec in _iterate(cids, task, jobs, fn=_chunk):
        if rec.get("status") != "ok":
            failed.append(rec)
            continue
        steps += rec["steps"]
        bad += rec["bad"]
        redo += rec["redo"]
    steps.sort(key=lambda s: s["t"])
    for i, s in enumerate(steps):
        # 前一步缺了（那块没算出来）就不比，免得把整页的点都报成「新出现」
        prev = steps[i - 1] if i and steps[i - 1]["t"] == s["t"] - 1 else None
        s["events"] = _events(prev, s)
    steps = [s for s in steps if s["t"] >= t0]
    bad = [i for i in bad if i >= t0]

    data = {"code": code, "t0": t0, "t1": t1, "ts0": ts5[t0], "ts1": ts5[t1],
            "next": ts5[t1 + 1] if t1 + 1 < len(w5) else None,
            "methods": METHODS, "steps": steps,
            "health": {"verify": verify, "unstable": bad, "recomputed": redo,
                       "failed": [f.get("code") for f in failed]}}
    out = Path(out)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("window.REPLAY = ")
        json.dump(data, f, ensure_ascii=False)
        f.write(";\n")
    os.replace(tmp, out)
    return {"out": str(out), "steps": len(steps), "from": ts5[t0], "to": ts5[t1], "next": data["next"],
            "failed_chunks": len(failed), "unstable": bad, "recomputed": redo}
