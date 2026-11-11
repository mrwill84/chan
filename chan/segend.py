"""当下判断末段是否已经结束——多种口径的对照实现。

## 为什么要有这个模块

`chan/seg.py` 走的是 67课 特征序列那条正路，而 78课 p197 把话说死了：
「线段必须被线段破坏才能确定其完成」。代价在 `VALIDATION-300308.md` 第二步
量过——`--zs seg` 下 3买/3卖 迟到 **144 / 77 根**（4~9 个月），裸收益为正、
真收益为负，**符号被延迟整个翻了过来**。

但原文并没有说「只能等」。它给了处方，而且不止一条：

- **55课 p954**：「在看 30 分钟的第三买卖点时，由于要涉及**次级别 5 分钟的
  判断问题**，所以那时候就不能光用 30 分钟级别的显微镜，同样要转换成 5 分钟
  的……**当你用一个级别的显微镜时，就等于先把次级别的当成线段了**。」
- **61课 p1171 区间套**：「都可以**先假设是进入背驰段**。而当走势实际走出
  来，一旦力度大于前者，那么就可以断定背驰段不成立……这种方法可以**在当下
  精确地定位走势的转折点**。」
- **趋势平均力度**：「面积除以时间……**因为这个概念是即时的，马上就可以
  判断**。」
- **90课 布林通道**：整课就是「中阴阶段结束时间的辅助判断」，并特别提醒
  「一个 5 分钟的中阴过程，对应的是看 30 分钟的布林通道」。所以这里的
  日线实现看**周线 BOLL**，不是又在本级别叠一个指标。
- **106课 均线系统**：「如果 5 日线都不能碰到，那就不会是笔的反弹了」。
  它只做负向过滤，不单独产生段结束信号。

RSI 是另设的对照组：与 MACD 版共用结构、共用测量，检验「各种技术指标
互相相关」是否意味着换指标买不到新信息。它不是原文推荐的方法。

## 这个模块做什么、不做什么

只回答一个问题：`build_segs` 吐出来的那个**末段**（恒为未确认、恒为
`[s .. len(bis)-1]`），当下是不是其实已经在中途结束了。

- **不碰任何历史已确认线段**，不改 `seg.py` 的任何判定规则。
- 提前判出的前段 `confirmed=True`，否则 `usable_count` 会把实验剪成空操作；
  新末段仍未确认。前段只是「本口径主张它完成」，不保证以后不重画，稳定性
  必须由逐前缀走查报告。完整理由见 `split_tail`。
- 默认关闭（`MergeConfig.seg_end=None`），关闭时本模块一行都不执行。

**这些口径都不是原文算法**，是对原文那几段话的工程化读法，属于我方约定。
哪种（如果有的话）站得住要靠实测，见 `CONVENTIONS.md`。
"""

from __future__ import annotations

from .indicators import Rsi, higher_bollinger, rsi, sma
from .macd import Macd, dif_extreme, hist_area
from .types import Bar, Bi, Direction, Seg

#: 段结束口径的代号，CLI 的 `--seg-end` 用的就是这一串。
METHODS = ("bi_beichi", "sub_third", "sub_depart", "sub_complete", "force",
           "bi_beichi_rsi", "force_rsi", "boll_first", "boll_second",
           "boll_squeeze", "sublevel_1bs")
RSI_METHODS = frozenset({"bi_beichi_rsi", "force_rsi"})
BOLL_METHODS = frozenset({"boll_first", "boll_second", "boll_squeeze"})

#: 段内笔中枢至少要几笔才值得一试。3笔连中枢都凑不出（中枢要三段重叠），
#: 加上离开段和回试段，`sub_third` 实际要 5~7 笔。短段上这些方法**必然不
#: 触发**，这是结果不是缺陷，报表里要有「触发率」这一列。
MIN_SUB_BIS = 5


# --------------------------------------------------------------------------
# 统一的结构闸门：所有方法共用，否则比的就不是方法而是各自的越界程度
# --------------------------------------------------------------------------


def _legalize(bis: list[Bi], tail: Seg, j: int) -> int | None:
    """把候选终点笔下标 `j` 退到最近一个**合法**的同向笔；退不到就 None。

    合法的三个条件，全部来自原文而非本模块的发明：

    - `bis[j].direction is tail.direction` —— 78课 p197：以向上笔开始的线段
      一定结束于向上笔。
    - `j >= tail.start_bi + 2` 且笔数为奇数 —— 77课 p196：线段至少三笔。
    - `j < len(bis) - 1` —— 后面一根笔都不剩的话就无所谓「拆分」。
    - **终点必须真的越过起点** —— 78课 p197「两端的一顶一底，顶肯定要高于
      底」。这一条是实测补上的：只卡方向和奇偶的话，一个深回撤的上涨段会被
      判在某根收在起点**下方**的向上笔上，`invariants` 当场抛
      「顶 928.99 不高于底 2222.73」。方向对、笔数对，照样可以是个非法线段。

    往前退而不是直接否决：各方法给出的是「大约在这里转」，用同向笔的栅格去
    对齐是纯结构性的修正，不掺入新判断。

    **唯一的例外：对齐后落在最后一根笔上，直接弃权，不往前退。** 最后一笔
    还在走，后面没有笔可拆。旧实现在这里把 `j` 夹到倒数第二根再对齐，等于
    退到**上一个**同向峰——而 `bi_beichi`/`force`/`boll_first` 判在最后一笔上，
    恰恰是因为它创了本段新极值。退回去的那个峰一定更低，于是拆出来的完成段
    收在一个非极值上，真正的新高落进了下一根反向段里。300308 上实测：
    `bi_beichi` 111 次触发里 17 次被这样挪走，17 次全部挪离了极值；`force`
    139 次里 17 次，`boll_first` 41 次里 6 次。

    反向的最后一笔不受影响：它对齐到倒数第二根，正是段的自然终点。
    """
    from .num import gt, lt

    s = tail.start_bi
    up = tail.direction is Direction.UP
    # 退到同向笔的栅格上。段首笔与段同向，所以合法终点是 s, s+2, s+4...
    j -= (j - s) % 2
    if j > len(bis) - 2:
        return None  # 判在还在走的最后一笔上，见上面「唯一的例外」
    while j >= s + 2:
        b = bis[j]
        if b.direction is tail.direction and (
            gt(b.end.price, tail.start.price) if up
            else lt(b.end.price, tail.start.price)
        ):
            return j
        j -= 2
    return None


def _same_dir_bis(bis: list[Bi], tail: Seg) -> list[int]:
    """段内与段同向的笔下标，从段首起每隔一根。"""
    return list(range(tail.start_bi, min(tail.end_bi, len(bis) - 1) + 1, 2))


def _bi_bars(bi: Bi) -> tuple[int, int]:
    """笔覆盖的原始K线闭区间。"""
    return bi.start.peak_idx, bi.end.peak_idx


# --------------------------------------------------------------------------
# 方法一：bi_beichi —— 61课 p1171 区间套，降到笔这一层看背驰
# --------------------------------------------------------------------------


def _m_bi_beichi(bis: list[Bi], m: Macd | Rsi, tail: Seg) -> int | None:
    """段内最后一个同向笔 vs 前一个同向笔，三个条件全中才算背驰。

    1. **创了本段新极值** —— 没创新高的回调谈不上「这一段的终点」
    2. **MACD 同号面积更小** —— 24课 p52 的力度比较
    3. **DIF 没有同步创新极值** —— 价格新高而指标不跟，是背驰的经典形态

    区间套原文是「先假设背驰，力度超了才否定」。这里实现的是它可操作的那一
    半：等力度真的小下来才宣布。更保守，也更可测——「先假设」那一版每根K线
    都在宣布，提前量 0 代价无穷大。
    """
    k = _same_dir_bis(bis, tail)
    if len(k) < 2:
        return None
    last, prev = k[-1], k[-2]
    up = tail.direction is Direction.UP

    # 1. 创本段新极值
    if up:
        if bis[last].high <= max(b.high for b in bis[tail.start_bi : last]):
            return None
    else:
        if bis[last].low >= min(b.low for b in bis[tail.start_bi : last]):
            return None

    l0, l1 = _bi_bars(bis[last])
    p0, p1 = _bi_bars(bis[prev])

    # 2. 同号面积更小（向上段比正柱面积，向下段比负柱面积）
    a_last = abs(hist_area(m, l0, l1, positive=up))
    a_prev = abs(hist_area(m, p0, p1, positive=up))
    if a_prev <= 0.0 or a_last >= a_prev:
        return None

    # 3. DIF 未同步创新极值
    d_last = dif_extreme(m, l0, l1, want_max=up)
    d_prev = dif_extreme(m, p0, p1, want_max=up)
    if (d_last >= d_prev) if up else (d_last <= d_prev):
        return None

    return last


# --------------------------------------------------------------------------
# 方法二~四：55课 p954 换显微镜——把段内的笔当成次级别走势类型
# --------------------------------------------------------------------------
#
# 「当你用一个级别的显微镜时，就等于先把次级别的当成线段了。」所以要在当下
# 判断线段有没有完，就把段内的笔拿去建中枢、找买卖点、长走势类型——完全复用
# 现成的 zhongshu / buysell / tree，一行算法都不重写。
#
# 三种方法共用一个**关键口径**：次级别分析一律 `require_confirmed=False`。
#
#   这是个有代价的选择，必须讲清楚。末段里最后一根笔天生未确认，用
#   `require_confirmed=True` 就等于要求次级别也走完它自己的确认流程——那只是
#   把同一笔延迟搬到小一层，实测会系统性偏晚，整个实验就白做了。放开它换来
#   提前量，代价是次级别结构会抖，抖出来的就是**撤回率**。这正是测量要量的
#   东西，所以不绕过，让它进报表。


def _sub_legs(bis: list[Bi], tail: Seg):
    """段内的笔 → 次级别 legs，以及回到全局笔下标的偏移量。

    `legs_from_bis` 给出的 `src_*` 是**切片内**的下标，所有结果都要加回
    `offset` 才是全局笔下标。这是这三种方法最容易错的地方。
    """
    from .zhongshu import legs_from_bis

    offset = tail.start_bi
    sub_bis = bis[offset:]
    if len(sub_bis) < MIN_SUB_BIS:
        return None, None, offset
    return legs_from_bis(sub_bis), sub_bis, offset


def _m_sub_depart(bis: list[Bi], tail: Seg) -> int | None:
    """段内最后一个笔中枢被**一段**反向走势走出去 → 段已结束。**不等回试**。

    三种次级别方法里最宽的一种。

    **不能用 `ZhongShu.depart_leg` 来实现这一条**，这是实测踩出来的坑：
    `zhongshu.py` 里 `departed = misses >= 2`——18课 p40 的「离开」本身就要求
    **连续两段**不再触及，那第二段正是回试段。所以拿 `depart_leg` 写出来的
    「不要求回试」会和 `sub_third` 变成同一个判据（300308 上实测 279 次宣布
    逐条相同，一次都不差）。这里改成直接找**第一根脱离中枢区间的笔**，才真
    的是少等一笔。
    """
    from .num import ge, le
    from .zhongshu import build_zhongshus

    legs, _sub_bis, offset = _sub_legs(bis, tail)
    if legs is None:
        return None
    zss = build_zhongshus(legs, require_confirmed=False)
    if not zss:
        return None
    zs = zss[-1]
    for k in range(zs.start_leg + 3, len(legs)):
        g = legs[k]
        if le(g.low, zs.zg) and ge(g.high, zs.zd):
            continue  # 还在中枢里晃，不算走掉
        if g.direction is tail.direction:
            return None  # 顺着段方向飞走，那是段在延续，不是段结束
        return offset + k - 1  # 段收在离开段**之前**那一根笔
    return None


def _m_sub_third(bis: list[Bi], tail: Seg) -> int | None:
    """段内出现与**段方向相反**的笔级别 3买/3卖 → 段已结束。

    55课 p954 + p1177：「一个第三类买卖点，至少需要有 5 段次级别的，前三段
    构成中枢，第四段离开中枢，第 5 段构成第三类买卖点。」第674行有现成实战：
    「2760 到 2858 这 30 分钟中枢，03081000 的 **5 分钟回抽确认了一个第三类
    买点**」——30分的3买用5分回抽当下确认，没等 30分 线段走完。

    比 `sub_depart` 严：多要一根回试笔。贵一笔的延迟，换的是回试没破坏结构
    这个确认。两者的差值就是「回试确认值不值这个钱」的实测答案。
    """
    from .buysell import find_bspoints
    from .zhongshu import build_zhongshus

    legs, sub_bis, offset = _sub_legs(bis, tail)
    if legs is None:
        return None
    zss = build_zhongshus(legs, require_confirmed=False)
    if not zss:
        return None
    # m=None：只要 3类点。3买是纯几何的，不需要 MACD（见 find_bspoints 文档）
    pts = find_bspoints(legs, zss, sub_bis, None, include_quasi=False)
    want_buy = tail.direction is Direction.DOWN  # 向下段要靠 3买 来终结
    cand = [p for p in pts if p.kind == 3 and p.is_buy is want_buy]
    if not cand:
        return None
    # p.leg 是回试段（dep+1），所以离开段是 p.leg-1，段收在它之前那一根
    return offset + max(p.leg for p in cand) - 2


def _m_sub_complete(bis: list[Bi], tail: Seg) -> int | None:
    """段内的笔长出了一个**已完成的走势类型** → 段结束。

    走势必完美：一个走势类型完成了就必然转入下一个。`Trend` 没有 `complete`
    字段，判据按 `tree.py:138` 的注释取「末枢已被离开」——那条注释说未完成的
    情形正是「末枢还没走出去时它后面的段」。
    """
    from .tree import build_tree

    legs, _sub_bis, offset = _sub_legs(bis, tail)
    if legs is None:
        return None
    layers = build_tree(legs, require_confirmed=False, max_levels=1)
    if not layers:
        return None
    done = [t for t in layers[0].trends
            if t.zhongshus and t.zhongshus[-1].depart_leg is not None]
    if not done:
        return None
    return offset + done[-1].end_leg


# --------------------------------------------------------------------------
# 方法五：force —— 趋势平均力度，原文唯一自称「即时」的指标
# --------------------------------------------------------------------------


def _kiss_runs(m: Macd | Rsi) -> list[tuple[int, int, int]]:
    """把 DIF/DEA 的关系切成一串「吻」：(符号, 起K线, 止K线)，闭区间。

    「吻」就是 DIF 与 DEA 两次交叉之间的那一段。符号 +1 表示 DIF 在上。
    """
    d = m.dif - m.dea
    if d.size == 0:
        return []
    runs: list[tuple[int, int, int]] = []
    sign = 1 if d[0] >= 0 else -1
    start = 0
    for i in range(1, d.size):
        s = 1 if d[i] >= 0 else -1
        if s != sign:
            runs.append((sign, start, i - 1))
            sign, start = s, i
    runs.append((sign, start, d.size - 1))
    return runs


def _m_force(bis: list[Bi], m: Macd | Rsi, tail: Seg) -> int | None:
    """当前「吻」的平均力度小于前一个同号吻 → 背驰将成，段在极值那一笔结束。

    第221行：「当下与前一『吻』的结束时短线均线与长期均线形成的面积除以
    时间……**因为这个概念是即时的，马上就可以判断**……真正的转折点基本就
    可以完全同时地抓住。」

    面积用 `Σ|DIF−DEA|`、时间用根数，正是原文的「面积除以时间」。和前四种
    方法最大的不同：它**不需要段内有足够多的笔**，所以在 3 笔短段上也能出
    声——代价大概率是误报率，交给测量去判。
    """
    import numpy as np

    runs = _kiss_runs(m)
    want = 1 if tail.direction is Direction.UP else -1
    same = [r for r in runs if r[0] == want]
    if len(same) < 2 or same[-1] is not runs[-1]:
        return None  # 当前吻不是本段该看的那一边，不表态

    d = np.abs(m.dif - m.dea)

    def strength(run: tuple[int, int, int]) -> float:
        _, a, b = run
        return float(d[a : b + 1].sum()) / max(b - a + 1, 1)

    if strength(same[-1]) >= strength(same[-2]):
        return None

    # 力度在衰减，段应当收在目前为止的极值所在的那根同向笔
    k = _same_dir_bis(bis, tail)
    if not k:
        return None
    if tail.direction is Direction.UP:
        return max(k, key=lambda i: bis[i].high)
    return min(k, key=lambda i: bis[i].low)


# --------------------------------------------------------------------------
# BOLL 三种口径：90课 高一级——日线段的中阴看周线，而非本级别
# --------------------------------------------------------------------------


def _strong_then_reenter(bars: list[Bar], boll, bis: list[Bi],
                         prev: int, last: int, up: bool) -> bool:
    """前一同向笔在超强区，随后回轨内；本次同向笔不能有效回到超强区。

    「有效」用收盘价越轨，避免盘中一根长影线反复改写状态。NaN 的未成熟
    BOLL 一律不表态。
    """
    import numpy as np

    p0, p1 = _bi_bars(bis[prev])
    l0, l1 = _bi_bars(bis[last])
    band = boll.upper if up else boll.lower
    close = np.asarray([b.close for b in bars])
    outside = close > band if up else close < band
    inside = close <= band if up else close >= band
    if not bool(np.any(outside[p0:p1 + 1])):
        return False
    if not bool(np.any(inside[p1:l0 + 1])):
        return False
    return not bool(np.any(outside[l0:l1 + 1]))


def _m_boll_first(bis: list[Bi], bars: list[Bar], tail: Seg) -> int | None:
    """90课 的1类点辅助：脱离超强区后，再创新高/低却回不到超强区。"""
    k = _same_dir_bis(bis, tail)
    if len(k) < 2:
        return None
    last, prev = k[-1], k[-2]
    up = tail.direction is Direction.UP
    if up:
        if bis[last].high <= max(b.high for b in bis[tail.start_bi:last]):
            return None
    elif bis[last].low >= min(b.low for b in bis[tail.start_bi:last]):
        return None
    boll = higher_bollinger(bars)
    return last if _strong_then_reenter(bars, boll, bis, prev, last, up) else None


def _m_boll_second(bis: list[Bi], bars: list[Bar], tail: Seg) -> int | None:
    """90课 的2类点辅助：轨道转向后，反弹/回跌在轨道下/上完成。

    判出的段端是**前一个**同向笔（第一类位置），不是当前这次回试的终点。
    它天然比 `boll_first` 多等一根同向笔，是否值得由同一张延迟表回答。
    """
    k = _same_dir_bis(bis, tail)
    if len(k) < 2:
        return None
    last, prev = k[-1], k[-2]
    up = tail.direction is Direction.UP
    boll = higher_bollinger(bars)
    if not _strong_then_reenter(bars, boll, bis, prev, last, up):
        return None
    p1 = bis[prev].end.peak_idx
    l1 = bis[last].end.peak_idx
    band = boll.upper if up else boll.lower
    turned = band[l1] < band[p1] if up else band[l1] > band[p1]
    held = bis[last].end_price <= band[l1] if up else bis[last].end_price >= band[l1]
    return prev if turned and held else None


def _m_boll_squeeze(bis: list[Bi], bars: list[Bar], tail: Seg) -> int | None:
    """90课「收口提示中阴结束」：当前周线收口，且价格已回到轨内。

    收口只提示变盘时间，不指定方向；所以只定位本段已有极值，**不据此预测
    下一支一定反转**。方向仍由后面的结构去决。
    """
    import numpy as np

    boll = higher_bollinger(bars)
    t = len(bars) - 1
    if not boll.squeeze[t]:
        return None
    up = tail.direction is Direction.UP
    close = np.asarray([b.close for b in bars])
    start = bis[tail.start_bi].start.peak_idx
    band = boll.upper if up else boll.lower
    outside = close > band if up else close < band
    inside = close <= band if up else close >= band
    if not np.any(outside[start:t + 1]) or not inside[t]:
        return None
    k = _same_dir_bis(bis, tail)
    if not k:
        return None
    j = max(k, key=lambda i: bis[i].high) if up else min(k, key=lambda i: bis[i].low)
    return j if j < tail.end_bi else None  # 极值之后至少已有一根反向笔


# --------------------------------------------------------------------------
# 真·次级别：用次级别K线自己的1类买卖点判本级别末段结束（区间套）
# --------------------------------------------------------------------------
#
# 第674行：「2760 到 2858 这 30 分钟中枢，03081000 的 **5 分钟回抽确认了一个
# 第三类买点**」。上面的 `sub_*` 是拿段内的**笔**冒充次级别；这里用的是次级别
# **真实K线**在它自己的历史上划出来的1类点，由调用方逐步算好传进来（`chan/`
# 不碰数据源，也不知道「5分钟」是什么）。


def _m_sublevel_1bs(bis: list[Bi], bars: list[Bar], tail: Seg,
                    sub_points: list[tuple[str, bool]]) -> int | None:
    """末段是回抽（向下）时，次级别出现了 1买 → 回抽在那里结束；向上镜像。

    ``sub_points`` 是**当下**次级别划线里的1类点 ``(所在K线时间戳, 是否买点)``。
    只认末段起点之后的、方向相反于末段的那种（向下段要 1买 来终结），取最新的
    一个，落到本级别里**结束时刻离它最近**的同向笔上。时间戳都是K线结束时刻，
    次级别那根所在的本级别K线是第一根 ``ts >= 次级别ts`` 的。

    判在还在走的最后一笔上照样由 `_legalize` 弃权：次级别底已经出来、本级别
    却还没有一根反向笔时，本级别没有可拆的结构。
    """
    from bisect import bisect_left

    want_buy = tail.direction is Direction.DOWN
    start_ts = bars[tail.start.peak_idx].ts
    cand = [ts for ts, is_buy in sub_points if is_buy is want_buy and ts > start_ts]
    k = _same_dir_bis(bis, tail)
    if not cand or not k:
        return None
    pidx = bisect_left([b.ts for b in bars], max(cand))
    return min(k, key=lambda i: (abs(bis[i].end.peak_idx - pidx), -i))


# --------------------------------------------------------------------------
# 106课 负向过滤：太弱、太短，连线段级别的最低考验都没完成
# --------------------------------------------------------------------------

BI_MIN_BARS = 6                 # 106课：一笔至少 6 个基本K线单位
SEG_MIN_BARS = 3 * BI_MIN_BARS  # 77课：线段至少三笔；18根是我方推导
SEG_CHALLENGE_MA = 21           # 18根对应最近的斐波那契均线，我方约定


def _ma_allows(bis: list[Bi], bars: list[Bar], tail: Seg, j: int) -> bool:
    """反向不足 18 根且始终没碰到 MA21，才否决；其余一律放行。

    这是个**不充分条件的排除器**，不是「碰线即反转」的买入信号。均线还没
    成熟时弃权，不能拿 NaN 把早期历史全部过滤掉。
    """
    import numpy as np

    start = bis[j].end.peak_idx
    if len(bars) - 1 - start >= SEG_MIN_BARS:
        return True
    ma = sma([b.close for b in bars], SEG_CHALLENGE_MA)
    lo = start + 1  # 极值所在K线不算反向挑战
    if lo >= len(bars):
        return False
    valid = np.isfinite(ma[lo:])
    if not np.any(valid):
        return True
    if tail.direction is Direction.UP:
        touched = [b.low <= x for b, x in zip(bars[lo:], ma[lo:]) if np.isfinite(x)]
    else:
        touched = [b.high >= x for b, x in zip(bars[lo:], ma[lo:]) if np.isfinite(x)]
    return any(touched)


# --------------------------------------------------------------------------
# 对外的两个函数
# --------------------------------------------------------------------------


def provisional_end(
    bis: list[Bi], m: Macd, tail: Seg, method: str,
    bars: list[Bar] | None = None, *, ma_filter: bool = False,
    sub_points: list[tuple[str, bool]] | None = None,
) -> int | None:
    """末段 `tail` 当下是否已经结束？返回真正结束所在的**笔下标**，None=判不了。

    ``sub_points`` 只给 `sublevel_1bs` 用：当下次级别划线里的1类点。

    `build_segs` 的末段恒为 `[s .. len(bis)-1]` 且 `confirmed=False`。本函数
    只回答「它其实在 j 就完了」，**不碰任何已确认线段**。

    出口处所有方法共走 `_legalize` 这一道闸门，否则比的就不是方法本身而是
    各自的越界程度。
    """
    if method not in METHODS:
        raise ValueError(f"未知的段结束口径 {method!r}，可选：{', '.join(METHODS)}")
    if tail.confirmed or tail.end_bi <= tail.start_bi + 1:
        return None
    if bars is None and (method in RSI_METHODS or method in BOLL_METHODS or ma_filter):
        raise ValueError(f"{method} 需要原始K线 bars（RSI/BOLL/均线过滤都建立在价格上）")
    if method == "sublevel_1bs" and (bars is None or sub_points is None):
        raise ValueError("sublevel_1bs 需要本级别 bars 和次级别 sub_points")

    osc = rsi([b.close for b in bars]) if method in RSI_METHODS else m
    if method in ("bi_beichi", "bi_beichi_rsi"):
        j = _m_bi_beichi(bis, osc, tail)
    elif method == "sub_third":
        j = _m_sub_third(bis, tail)
    elif method == "sub_depart":
        j = _m_sub_depart(bis, tail)
    elif method == "sub_complete":
        j = _m_sub_complete(bis, tail)
    elif method in ("force", "force_rsi"):
        j = _m_force(bis, osc, tail)
    elif method == "boll_first":
        j = _m_boll_first(bis, bars, tail)
    elif method == "boll_second":
        j = _m_boll_second(bis, bars, tail)
    elif method == "boll_squeeze":
        j = _m_boll_squeeze(bis, bars, tail)
    else:
        j = _m_sublevel_1bs(bis, bars, tail, sub_points)

    if j is None:
        return None
    j = _legalize(bis, tail, j)
    if j is None:
        return None
    if ma_filter and not _ma_allows(bis, bars, tail, j):
        return None
    return j


def split_tail(segs: list[Seg], bis: list[Bi], m: Macd, method: str,
               bars: list[Bar] | None = None, *, ma_filter: bool = False,
               sub_points: list[tuple[str, bool]] | None = None) -> list[Seg]:
    """末段若被判为已在中途结束，就拆成两段：**前段算完成，后段仍未完成**。

    ## 为什么前段必须 `confirmed=True`（实测逼出来的，不是偷懒）

    原计划是两段都 `confirmed=False`——理由是「未确认」语义正确，而且
    `invariants.check_segs` 跳过未确认线段，白捡一个安全。跑完才发现那样
    **什么都测不到，而且是可以证明的**：

        `zhongshu.usable_count`（zhongshu.py:187）按 17课 p36「只有完成的
        走势类型才算数」，从尾部往回**剪掉所有未确认的段**。

    基线末段本来就未确认，剪 1 段；拆成两段都未确认，剪 2 段——可用段集合
    **逐字节相同**。300308 上实测证实了这一点：`bi_beichi` 宣布了 185 次，
    买卖点表与基线一个数都没变。

    所以要让这个实验有内容，前段就得真的进中枢层。而这在语义上本来就是对的：
    我们主张的正是「这一段**已经完成**」，只不过凭据是区间套/次级别而不是
    67课 特征序列。凭据记在 `prov_end` 里，不在 `confirmed` 里。

    ## 那安全性靠什么

    不再靠「不变量会跳过它」，而是靠前段**本来就合法**：`_legalize` 已经保证
    了 77课 p196 的三笔起、奇数笔，和 78课 p197 的同向收尾；起点沿用原末段
    起点，所以和上一根已确认线段天然首尾相接、方向交替。`check_all` 会真的
    检查它——这是加强不是放松。

    代价是诚实的：挂在前段上的买卖点会标成已确认，**它们可能被后续K线推翻**。
    这正是回测表里「存活 / 稳定 / 撤回率」三列要量的东西，不该用一个恒为
    False 的标志把它藏起来。
    """
    if not segs or segs[-1].confirmed:
        return segs
    tail = segs[-1]
    j = provisional_end(bis, m, tail, method, bars, ma_filter=ma_filter,
                        sub_points=sub_points)
    if j is None:
        return segs

    first = Seg(
        index=tail.index, start_bi=tail.start_bi, end_bi=j,
        direction=tail.direction, start=tail.start, end=bis[j].end,
        case=0, confirmed=True, pending=tail.pending,
        skipped_pending=tail.skipped_pending, prov_end=method,
    )
    second = Seg(
        index=tail.index + 1, start_bi=j + 1, end_bi=tail.end_bi,
        direction=bis[j + 1].direction, start=bis[j + 1].start,
        end=bis[tail.end_bi].end, case=0, confirmed=False,
    )
    return segs[:-1] + [first, second]
