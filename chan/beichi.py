"""背驰。37课 p96 做**结构门槛** + 24课 p53 做**量化测量**（CONVENTIONS C13）。

## 两套记号不要搞混

24课 p53（第 422 行）的例子用 A段/B段/C段，其中 **B段是中枢**、A段C段是走势；
37课 p96 的 `a+A+b+B+c` 里 **A、B 是中枢**、a/b/c 是连接走势。对照两处原文：

  24课：「15 日 10:35 到 16 日 10:25 构成 **A 段**。16 日 10:25 到 17 日 10:10，
        一个标准的三段构成新的中枢，也相应构成 B 段……其后就是 **C 段**的上涨，
        其对应的 MACD 红柱子面积明显小于 A 段的。」

映射关系：**24课的「A段」就是 37课的 b，「C段」就是 c**。37课 p96（第 633 行）
把话挑明了：

  「对 a+A+b+B+c，背驰的大概意思就是 **c 段的力度比 b 的小了**。」

所以比的是 **c vs b**（进入最后一个中枢的那一段 vs 离开它的那一段），
不是 c vs 中枢A。本模块统一用 37课 的记号，字段名叫 `enter`(=b) / `leave`(=c)。

## 结构门槛（37课 p96，第 685-690 行）

  「**没有趋势，没有背驰**，不是任何 a+A+b+B+c 形式的都有背驰的。当说
    a+A+b+B+c 中有背驰时，首先要 a+A+b+B+c 是一个趋势。而一个趋势，就意味着
    **A、B 是同级别的中枢**，否则，就只能看成是其中较大中枢的一个震荡。」

  「其次，**c 必然是次级别的**，也就是说，c 至少包含对 B 的一个第三类买卖点，
    否则，就可以看成是 B 中枢的小级别波动，完全可以用盘整背驰来处理。而
    **b 是有可能小于次级别的**。」

  「还有，如果 a+A+b+B+c 是上涨，**c 一定要创出新高**；a+A+b+B+c 是下跌，
    c 一定要创出新低。」

37课 p95（第 607 行）补了 b 的上限：「**b 段一定不可以出现 30 分钟的中枢**，
也就是只能最多是 5 分钟级别的。」——即 b 里不能有同级别中枢。

不满足门槛的，退化成**盘整背驰**（24课 p53 第 423 行、27课 p66 第 495 行：
「如果在第一个中枢就出现背驰，那不会是真正意义上的背驰，只能算是盘整背驰」）。

## 为什么门槛不能省

27课 p66（第 444 行）：「日线上在上涨的中段刚开始的时候……这时候 5 分钟即使
出现背驰，其下跌力度显然有限。」同一个 MACD 面积缩小，在趋势末端和在中枢内部
含义完全不同。门槛就是用来区分这两者的，省掉它等于把所有缩量都当成顶部。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .macd import Macd, crosses_zero, dif_extreme, hist_area
from .num import fp, gt, lt
from .types import Bi, Direction, Leg, ZhongShu
from .zhongshu import MIN_LEGS, TREND_DOWN, TREND_UP, relation

TREND = "trend"  # 趋势背驰，37课 p96 的标准形态
PANZHENG = "panzheng"  # 盘整背驰，24课 p53 / 27课 p66

# C14：面积比较的容差，原文没给。1.0 就是原文字面的「小于」。调大等于放宽，
# 调小等于要求缩量更明显。只在这里出现一次，便于整体调参。
AREA_RATIO = 1.0


@dataclass(frozen=True, slots=True)
class Span:
    """一段走势：在 leg 序列里的范围、在原始K线上的窗口、以及真实极值。

    ``bar_start`` / ``bar_end`` 是**原始K线下标**，MACD 是在原始K线上算的，
    面积要按这个窗口去截。极值取构成笔的真实高低点，不是两端点（71课 p190）。
    """

    leg_start: int
    leg_end: int
    bar_start: int
    bar_end: int
    low: float
    high: float
    direction: Direction

    @property
    def leg_count(self) -> int:
        return self.leg_end - self.leg_start + 1

    def extreme(self, d: Direction) -> float:
        return self.high if d is Direction.UP else self.low


@dataclass(frozen=True, slots=True)
class Beichi:
    """一次背驰判定的完整结果，连同每一道门槛的结论。

    ``reasons`` 把结构门槛逐条记下来，**不满足也要记**——Pi skill 要拿它跟
    用户解释「为什么这里不算背驰」，而不是只给一个 True/False。
    """

    kind: str  # TREND | PANZHENG
    direction: Direction  # 背驰的方向：UP = 顶背驰，DOWN = 底背驰
    zs_index: int  # 最后那个中枢 B 在中枢序列里的下标
    enter: Span  # b 段（趋势）／A 段（盘整）
    leave: Span  # c 段
    area_enter: float
    area_leave: float
    area_ok: bool  # 24课 p53：c 段面积 < b 段面积
    dif_ok: bool  # 27课 p66：黄白线回抽 0 轴后不创新高/新低
    dif_crossed_zero: bool
    breaks_zs: bool  # c 段是否突破 [ZD, ZG]。24课 p53 盘整背驰分两种情况
    new_extreme: bool  # 37课 p96：c 是否创出新高/新低
    hit: bool  # 结论：面积与黄白线两个判据满足其一即成立
    standard: bool  # 两个都满足，27课 p66 称为更标准
    #: b 段和 c 段各自的 DIF 极值。`dif_ok` 只说「背没背」，这两个数说
    #: 「差多少」——推演的选项表要印的是后者。放在末尾是因为它们有默认
    #: 值，dataclass 不允许有默认值的字段排在无默认值的字段前面。
    dif_enter: float = 0.0
    dif_leave: float = 0.0
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_top(self) -> bool:
        """顶背驰（上涨衰竭）。下跌衰竭是底背驰。"""
        return self.direction is Direction.UP


#: 把一根 leg 的下标映射成它覆盖的K线下标闭区间。`span_of` 用它把 leg 区间
#: 落到 MACD 要的原始K线窗口上。
ToBars = Callable[[int], tuple[int, int]]


def bi_to_bars(legs: list[Leg], bis: list[Bi]) -> ToBars:
    """level 0 的映射：``Leg.src_*`` 就是笔下标，查一次分型的 `peak_idx`。

    只对**本级别**的 leg 成立（`legs_from_bis` / `legs_from_segs` 产的那些）。
    级别树上层的 leg 其 ``src_*`` 指向下一层 legs，要用
    `chan.tree.layer_to_bars`。
    """
    def f(i: int) -> tuple[int, int]:
        return (bis[legs[i].src_start].start.peak_idx,
                bis[legs[i].src_end].end.peak_idx)
    return f


def span_of(legs: list[Leg], bis: list[Bi], lo: int, hi: int, *,
            to_bars: ToBars | None = None) -> Span | None:
    """把 ``legs[lo..hi]`` 折成一个 Span。范围非法返回 None。

    MACD 的窗口必须是**原始K线**下标，而 leg 只知道自己在来源序列里的位置，
    所以要一个映射把 leg 下标翻成K线下标。``to_bars`` 不给就按 level 0 处理
    （`bi_to_bars`，即 ``Leg.src_*`` 是笔下标）。

    **这个参数就是上层背驰过去做不了的原因。** 从前这里写死
    ``bis[span[0].src_start]``，而级别树上层 leg 的 ``src_*`` 存的是下一层
    legs 的下标。上层 legs 远少于笔，所以那样取**不会越界、不会报错**，只会
    安静地读错一个分型、按错误的窗口去积 MACD 面积——比崩掉难发现得多。
    现在上层要传 `chan.tree.layer_to_bars(layers, bis, level)`。

    两端**分别**翻：起点取 lo 那根的起点、终点取 hi 那根的终点。拿同一个下标
    取两端会把区间塌成一根 leg（`chan.tree.leg_bars` 的 docstring 记着同一个坑）。
    """
    if lo < 0 or hi >= len(legs) or lo > hi:
        return None
    span = legs[lo : hi + 1]
    f = to_bars or bi_to_bars(legs, bis)
    return Span(
        leg_start=lo,
        leg_end=hi,
        bar_start=f(lo)[0],
        bar_end=f(hi)[1],
        low=min(x.low for x in span),
        high=max(x.high for x in span),
        direction=span[0].direction,
    )


# --------------------------------------------------------------------------
# 结构门槛。37课 p96
# --------------------------------------------------------------------------


def _slice_enter(zss: list[ZhongShu], k: int) -> tuple[int, int]:
    """b 段在 leg 序列里的范围：从前一个中枢**震荡完**到本中枢起步之间。

    起点用 `core_end + 1` 而不是 `end_leg + 1`：离开段可能就是前一个中枢的
    最后一段（见 C-core），而离开段正是 b 的第一推。拿 end_leg 起算会把 b 错
    读成离开之后的那次回抽——方向都是反的。

    k == 0 时没有前一个中枢，退回「本中枢之前紧邻的那一段」——这就是 24课 p53
    盘整背驰例子里的 A 段。
    """
    if k == 0:
        return (zss[0].start_leg - 1, zss[0].start_leg - 1)
    return (zss[k - 1].core_end + 1, zss[k].start_leg - 1)


def _slice_leave(zss: list[ZhongShu], k: int, n_legs: int) -> tuple[int, int]:
    """c 段：从本中枢**震荡完**到下一个中枢起步之间；没有下一个就一直到末尾。

    对非最后一个中枢，它的「c」正是通往下一个中枢的那一段——也就是下一轮的 b。
    这个对称性让历史回溯和当下判断走同一段代码。

    起点同样是 `core_end + 1`：离开段是 c 的第一推，它必须在 c 里，否则
    「c 创新高」和「c 里有三买」两道门槛都无从谈起。

    两个中枢紧挨着（前一个没走掉、下一个从 `end_leg + 1` 起）时会返回一个空
    区间，`span_of` 给 None，判定就此放弃。这种相邻通常是 `max_legs` 把一个
    大中枢截成了两截（33课 p79），本来就该升一级看，不判是对的。
    """
    lo = zss[k].core_end + 1
    hi = (zss[k + 1].start_leg - 1) if k + 1 < len(zss) else (n_legs - 1)
    return (lo, hi)


def _third_point_against(zs: ZhongShu, legs: list[Leg], lo: int, hi: int, d: Direction) -> bool:
    """c 段里有没有对 B 的一个第三类买卖点。37课 p96 对 c 的硬要求。

    20课 p43 第三类买卖点定理：「一个次级别走势类型向上**离开**走势中枢，然后
    以一个次级别走势类型回试，其低点**不跌破 ZG**，则构成第三类买点」。

    所以 c 至少要有两段（离开 + 回试），且回试那段不能退回 [ZD, ZG]。只有一段
    的 c 还没走完，谈不上三买三卖——37课 p96：「否则，就可以看成是 B 中枢的
    小级别波动，完全可以用盘整背驰来处理。」
    """
    if hi - lo + 1 < 2:
        return False
    pull = legs[lo + 1]  # 离开段之后的第一次回试
    return gt(pull.low, zs.zg) if d is Direction.UP else lt(pull.high, zs.zd)


def _gate_trend(
    zss: list[ZhongShu], k: int, legs: list[Leg], enter: Span, leave: Span, d: Direction
) -> tuple[bool, list[str]]:
    """37课 p96 的五道门槛。返回 (是否够得上趋势背驰, 逐条理由)。

    **五道，不是四道**（原先这里写的是四，漏数了「二、A、B 同级别」那道，
    它没有独立的 `if` 分支标号所以容易看漏）。逐道列出来：

    ① A、B 构成同向趋势        ② A、B 同级别（都没顶到延伸上限）
    ③ b 段不自成同级别中枢     ④ c 创新高/新低
    ⑤ c 含对 B 的第三类买卖点

    **这五道是合取，实测极难同时满足。** 分母是「量得出来」的中枢（k>=1 且
    `_reach` 不返回 None），逐道失败率（笔口径）：

        票       级别   分母   ①      ②      ③      ④      ⑤     趋势背驰
        600009   day     44   59.1%  47.7%  18.2%  15.9%  88.6%   0  ( 0.0%)
        600009   30min   24   70.8%  41.7%  16.7%  12.5%  79.2%   0  ( 0.0%)
        600519   day     37   40.5%  43.2%  29.7%   5.4%  64.9%   1  ( 2.7%)
        600519   30min   73   46.6%  37.0%  19.2%   8.2%  75.3%   5  ( 6.8%)
        600519   5min   512   46.5%  32.2%  14.5%  11.3%  85.4%  20  ( 3.9%)

    三件事从这张表里读得出来，都影响「要不要改判定口径」：

    **一、五道门几乎互相独立，所以通过率是乘出来的。** 把五个通过率直接相乘
    （当独立处理）：5min 行给 4.0%，实测 3.9%；30min 行给 6.2%，实测 6.8%。
    没有哪一道是「元凶」——去掉任何一道，剩下四道的乘积仍在 10%~25% 量级。

    **二、⑤ 是单道里最重的一记（65%~89%）。** 它要求 c 至少两段、且回试不退回
    [ZD, ZG]（见 `_third_point_against`）。对**最后一个**中枢——也就是 `detect`
    唯一会看的那个——c 往往只有一段，于是必然失败。含义是：趋势背驰在原文定义
    下基本是一个**事后标签**，当下实时判出来的那次多半只能是盘整背驰。这不是
    实现的缺陷，37课 p96 原文就是这么划的（「否则……完全可以用盘整背驰来处理」）。

    **三、失败率是级别不变量，换细级别不降低稀有度。** 600519 从 day 到 5min，
    K线多了 12.9 倍、可判中枢多了 13.8 倍、趋势背驰从 1 条变成 20 条——**绝对
    条数够用了，但比例没动**（2.7% → 6.8% → 3.9%，不单调）。而且 ⑤ 还**随级别
    变差**（64.9% → 75.3% → 85.4%）：级别越细，c 的回试越容易蹭回中枢区间。
    所以「拿更多K线」解决的是样本量，不解决稀有度。

    要改判定口径之前先看这组数，别把「没见过」当成「算错了」。
    """
    why: list[str] = []
    ok = True

    # 一、「没有趋势，没有背驰」。前一个中枢与本中枢必须构成同向趋势关系
    rel = relation(zss[k - 1], zss[k])
    want = TREND_UP if d is Direction.UP else TREND_DOWN
    if rel == want:
        why.append(f"A、B 构成{want}趋势（37课 p96）")
    else:
        ok = False
        why.append(f"A、B 的关系是 {rel}，不是趋势 → 只能算盘整背驰（37课 p96）")

    # 二、「A、B 是同级别的中枢」。level_up 说明该中枢已经顶到同级别延伸上限，
    #     应当升一级去看（33课 p79），那就不是同级别的两个中枢了
    if zss[k - 1].level_up or zss[k].level_up:
        ok = False
        why.append("A 或 B 已顶到延伸上限，应升级别看，A、B 非同级别（33课 p79）")

    # 三、37课 p95：「b 段一定不可以出现 30 分钟的中枢」，即 b 里不能有同级别中枢
    if enter.leg_count >= MIN_LEGS:
        ok = False
        why.append(f"b 段有 {enter.leg_count} 段，可能自成同级别中枢（37课 p95）")

    # 四、「如果 a+A+b+B+c 是上涨，c 一定要创出新高」。拿中枢 B 的包络做基准
    base = zss[k].gg if d is Direction.UP else zss[k].dd
    cmp_ = gt if d is Direction.UP else lt
    if not cmp_(leave.extreme(d), base):
        ok = False
        why.append(
            f"c 段极值 {fp(leave.extreme(d))} 没{'创新高' if d is Direction.UP else '创新低'}"
            f"（B 的包络 {fp(base)}）（37课 p96）"
        )

    # 五、「c 至少包含对 B 的一个第三类买卖点」
    if not _third_point_against(zss[k], legs, leave.leg_start, leave.leg_end, d):
        ok = False
        why.append("c 段里找不到对 B 的第三类买卖点 → 退回盘整背驰（37课 p96）")

    return ok, why


# --------------------------------------------------------------------------
# 量化测量。24课 p53 + 27课 p66
# --------------------------------------------------------------------------


def _measure(
    m: Macd, enter: Span, leave: Span, d: Direction, area_ratio: float
) -> tuple[float, float, bool, bool, bool]:
    """返回 (b面积, c面积, 面积判据, 黄白线判据, 是否回抽过0轴)。

    24课 p53：「其对应的 MACD 红柱子面积明显小于 A 段的」——上涨段数红柱、
    下跌段数绿柱，只累加同号（见 CONVENTIONS C-area）。

    27课 p66 的第二判据要看黄白线，原文（第 505 行）说的是「**黄白线回拉 0 轴
    上下**，而后一柱子面积小于前一柱子的」。所以回抽 0 轴是**前提**：两段之间
    黄白线没回过 0 轴，说明它们还在同一波里，比高低没有意义。
    """
    up = d is Direction.UP
    a_enter = hist_area(m, enter.bar_start, enter.bar_end, positive=up)
    a_leave = hist_area(m, leave.bar_start, leave.bar_end, positive=up)
    area_ok = lt(a_leave, a_enter * area_ratio)

    # 中枢那一段（b 走完到 c 起步之间）有没有把黄白线拉回 0 轴
    crossed = crosses_zero(m, enter.bar_end, leave.bar_start)
    e_enter = dif_extreme(m, enter.bar_start, enter.bar_end, want_max=up)
    e_leave = dif_extreme(m, leave.bar_start, leave.bar_end, want_max=up)
    # 价格创了新高，DIF 却没有 → 黄白线背驰
    dif_ok = crossed and (lt(e_leave, e_enter) if up else gt(e_leave, e_enter))
    # 两个 DIF 极值要**原样带出去**，不能只留布尔。`dif_ok` 回答「背没背」，
    # 推演的选项表要回答「差多少」——「DIF 0.42 → 0.31，未同步创新低」和
    # 「0.42 → 0.419」是同一个布尔、完全不同的两件事。
    return a_enter, a_leave, area_ok, dif_ok, crossed, e_enter, e_leave


#: `detect_at` 够不着时的原因代号。**必须分开计数**：前三个是「这里没有可比
#: 的两段」，`OPPOSITE` 是「两段都在，但方向相反」——后者不是盲区，它是一个
#: 有内容的读数（当下处在震荡折返点，该升一级看）。
OUT_OF_RANGE = "out_of_range"
NO_ENTER = "no_enter"
NO_LEAVE = "no_leave"
OPPOSITE = "opposite"


def _reach(
    legs: list[Leg], zss: list[ZhongShu], bis: list[Bi], k: int,
    to_bars: ToBars | None = None,
) -> tuple[Span | None, Span | None, str | None]:
    """取第 k 个中枢的进入段与离开段，并说明够不着时是哪一种。

    `detect_at` 和 `reach_reason` 共用这一份，否则诊断用的归因迟早和判定
    本身分叉——`_annotate_beichi` 的报表就是靠它说「判不了」是哪一类。
    """
    if not (0 <= k < len(zss)):
        return None, None, OUT_OF_RANGE
    enter = span_of(legs, bis, *_slice_enter(zss, k), to_bars=to_bars)
    leave = span_of(legs, bis, *_slice_leave(zss, k, len(legs)),
                    to_bars=to_bars)
    if enter is None:
        return None, None, NO_ENTER
    if leave is None:
        return enter, None, NO_LEAVE
    # 进入段与离开段必须同向——背驰按定义是**比较同向的两次推进**（24课 p53
    # 的盘整背驰比 a 与 b，37课 p96 的趋势背驰比 b 与 c）。方向相反说明这个
    # 中枢是一次折返的拐点，这里根本没有「两次同向推进」可比。
    #
    # 注意这是 **aAb 层面**的事，和够不够得上 a+A+b+B+c 无关：后者是
    # `_gate_trend` 的活，它只决定这次背驰叫趋势背驰还是盘整背驰。
    if enter.direction is not leave.direction:
        return enter, leave, OPPOSITE
    return enter, leave, None


def reach_reason(
    legs: list[Leg], zss: list[ZhongShu], bis: list[Bi], k: int,
    to_bars: ToBars | None = None,
) -> str | None:
    """`detect_at` 为什么返回 None。够得着时返回 None。"""
    return _reach(legs, zss, bis, k, to_bars)[2]


def detect_at(
    legs: list[Leg],
    zss: list[ZhongShu],
    bis: list[Bi],
    m: Macd,
    k: int,
    *,
    area_ratio: float = AREA_RATIO,
    to_bars: ToBars | None = None,
) -> Beichi | None:
    """判定**离开第 k 个中枢**的那一段有没有背驰。够不着就返回 None。

    对最后一个中枢，c 段是「当下正在走的那一段」；对中间的中枢，c 段就是通往
    下一个中枢的连接段。两者走同一段代码，所以历史回溯和当下判断口径一致。

    返回 None 的具体原因用 `reach_reason` 问，两者共用 `_reach`。

    ``to_bars`` 见 `span_of`：级别树上层必须传
    `chan.tree.layer_to_bars(layers, bis, level)`，否则 MACD 的窗口是错的。
    ``m`` 始终是**原始K线**上的 MACD，不论判的是哪一层——24课 p53 比的是
    那两段走势覆盖的那段K线上的柱子面积，面积不随你从哪一层看它而变。
    """
    enter, leave, bad = _reach(legs, zss, bis, k, to_bars)
    if bad is not None or enter is None or leave is None:
        return None
    d = leave.direction

    ok, why = (
        _gate_trend(zss, k, legs, enter, leave, d)
        if k >= 1
        else (False, ["这是第一个中枢 → 不是真正意义的背驰，只能算盘整背驰（27课 p66）"])
    )
    kind = TREND if ok else PANZHENG

    (a_in, a_out, area_ok, dif_ok, crossed,
     dif_in, dif_out) = _measure(m, enter, leave, d, area_ratio)
    why.append(
        f"{'红' if d is Direction.UP else '绿'}柱面积 c={fp(a_out)} "
        f"{'<' if area_ok else '>='} b={fp(a_in)}（24课 p53）"
    )
    if not crossed:
        why.append("b 与 c 之间黄白线没回抽 0 轴，黄白线判据不适用（27课 p66）")

    zs = zss[k]
    breaks = gt(leave.high, zs.zg) if d is Direction.UP else lt(leave.low, zs.zd)
    base = zs.gg if d is Direction.UP else zs.dd
    new_ext = gt(leave.extreme(d), base) if d is Direction.UP else lt(leave.extreme(d), base)

    return Beichi(
        kind=kind,
        direction=d,
        zs_index=k,
        enter=enter,
        leave=leave,
        area_enter=a_in,
        area_leave=a_out,
        area_ok=area_ok,
        dif_ok=dif_ok,
        dif_crossed_zero=crossed,
        dif_enter=dif_in,
        dif_leave=dif_out,
        breaks_zs=breaks,
        new_extreme=new_ext,
        # 27课 p66：两个判据满足其一即信号，都满足更标准
        hit=area_ok or dif_ok,
        standard=area_ok and dif_ok,
        reasons=tuple(why),
    )


def detect(
    legs: list[Leg],
    zss: list[ZhongShu],
    bis: list[Bi],
    m: Macd,
    *,
    area_ratio: float = AREA_RATIO,
    to_bars: ToBars | None = None,
) -> Beichi | None:
    """当下这一刻的背驰判定——只看**最后一个**中枢。扫描用的就是这个。"""
    if not zss:
        return None
    return detect_at(legs, zss, bis, m, len(zss) - 1,
                     area_ratio=area_ratio, to_bars=to_bars)


def detect_all(
    legs: list[Leg],
    zss: list[ZhongShu],
    bis: list[Bi],
    m: Macd,
    *,
    area_ratio: float = AREA_RATIO,
    to_bars: ToBars | None = None,
) -> list[Beichi]:
    """历史上每一个中枢的离开段各判一次，供回测与人工核对。

    **注意这不是「当时能看到的判断」**：中间那些中枢的 c 段是事后才知道走到哪
    为止的。要模拟当下，得把数据截断后重新跑 `detect`。
    """
    out = [detect_at(legs, zss, bis, m, k, area_ratio=area_ratio,
                     to_bars=to_bars) for k in range(len(zss))]
    return [x for x in out if x is not None]
