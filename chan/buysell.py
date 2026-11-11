"""一、二、三类买卖点。20课 p43 + 21课 p45 + 53课 pp.126-127（CONVENTIONS 五）。

三类买点在原文里是**三个完全不同的东西**，不是「强度递减的同一种信号」：

| 类 | 怎么来的 | 位置 | 出处 |
|---|---|---|---|
| 1买 | 下跌**趋势**背驰 | 最后一个中枢**之下** | 21课 p45 第 361 行 |
| 2买 | 1买之后第二段次级别走势的低点 | **任何位置** | 21课 p45 第 362 行 |
| 3买 | 向上离开中枢后第一次回试不跌破 ZG | 中枢**之上** | 20课 p43 第 966 行 |

## 原文逐字

**1买**（第 503 行）：

  「注意，**第一类买点肯定是趋势背驰构成的**，而盘整背驰构成的买点，在小级别
    中是意义不大的，所以以前也没专门当成一种买点，但在大级别里，这也构成一种
    **类似第一类买点的买点**……这个级别，至少应该是周线以上。」

  位置约束（第 361 行）：「一个上涨趋势确定后，**不可能再有第一类与第二类
    买点，只可能有第三类买点**……因此，只有在**下跌确立后的中枢下方**才可能
    出现买点。这就是第一类买点。」

**2买**（第 2203 行，最清楚的一处）：

  「所谓第二类买点，就是**第一类买点的次级别回抽结束后再次探底或回试的那个
    次级别走势的结束点**。例如，一个 5 分钟底背驰后，第一类买点上去的 1 分钟
    走势结束后，回头肯定有一个 1 分钟的向下走势，这走势的结束点，就是第二类
    买点。」

  第 362 行同义：「第一买点出现后的**第二段次级别走势低点**就构成第二类买点」
  ——所以是 1买 之后的 **leg+1 向上、leg+2 向下**，取 leg+2 的终点。

  位置（第 362 行）：「第二类买点，**不必然出现在中枢的上或下，可以在任何
    位置出现**。」

  **2买可以比1买低**（第 2208 行）：「第二类买点跌破第一类买点，也就是第二类
    买点比第一类买点低，**这是完全可以的**，这里一般都构成盘整背驰。」
    ——所以不能拿「不破前低」当成笔定义的一部分，只记成标注。

**3买**（第 966 行，定理原文）：

  「一个次级别走势类型向上离开缠中说禅走势中枢，然后以一个次级别走势类型
    回试，其低点**不跌破 ZG**，则构成第三类买点；一个次级别走势类型向下离开
    走势中枢，然后以一个次级别走势类型回抽，其高点**不升破 ZD**，则构成第三类
    卖点。」

  注意三卖的基准是 **ZD** 不是 ZG——两边都要求回试**完全不重新进入中枢**。

  第 967 行（2007-05-22 答疑）：「**离开与回抽均必须次级别的**。」本模块吃的
  `Leg` 按定义就是次级别走势类型，这条自动满足。

## 重合关系（21课 p45，第 363 行）

  「第一类买点与第二类买点是前后出现的，**不可能产生重合**，而第一类与第三类
    买点，一个在中枢之下、一个在中枢之上，**也不可能产生重合**。只有第二类
    买点与第三类买点是可能产生重合的。」

写成了 `check_bspoints` 里的断言——违反就是实现错了，不是行情特殊。

## MACD 定律（第 250 / 2409 / 2450 行，原文重复三次）

  「**第一类买点都是在 0 轴之下背驰形成的，第二类买点都是第一次上 0 轴后回抽
    确认形成的**；卖点的情况就反过来。」

当**标注**算，不当门槛。它依赖 MACD 参数与复权方式，而买卖点的定义本身是纯
几何的。见 `BSPoint.macd_law_ok`。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .beichi import AREA_RATIO, TREND, Beichi, detect_all
from .invariants import InvariantError
from .macd import Macd, hist_area
from .num import fp, gt, lt
from .types import Bi, Direction, Leg, ZhongShu

# 买卖点的类别。用 int 而不是字符串，方便输出端按「1/2/3 类」排序分组。
FIRST = 1
SECOND = 2
THIRD = 3

# 口径来源。C17：二买有两套互不等价的定义，输出里必须标明走的是哪一套。
SRC_TREND_BEICHI = "17课 p37 / 53课 p126：趋势背驰"
SRC_AFTER_FIRST = "21课 p45 第 362 行：1买后第二段次级别走势"
SRC_WIDE_SECOND = "53课 p127：次级别上、次级别下，不创新低或盘整背驰"
SRC_THIRD_THEOREM = "20课 p43 第 966 行：第三类买卖点定理"


@dataclass(frozen=True, slots=True)
class BSPoint:
    """一个买卖点。

    ``leg`` 是**终点落在这个买卖点上**的那一段走势——买点是向下段的终点，
    卖点是向上段的终点。``bar`` 是它在原始K线上的下标，画图和回测都用它。

    ``quasi`` 表示这是「**类**第一类买点」：由盘整背驰而非趋势背驰构成
    （第 503 行），原文说在周线以上级别才有意义，小级别里意义不大。

    ``ambiguous`` 是 C15 的歧义标记，具体哪一种歧义写在 ``reasons`` 里。
    """

    kind: int  # FIRST | SECOND | THIRD
    is_buy: bool
    leg: int
    bar: int
    price: float
    zs_index: int  # 锚定的那个中枢。买卖点必然对应一个中枢（21课 p45 第 359 行）
    source: str
    confirmed: bool = True
    quasi: bool = False  # 类1买：由盘整背驰构成（第 503 行）
    ambiguous: bool = False  # C15
    macd_law_ok: bool | None = None  # 第 2450 行的 MACD 定律，None = 没算
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        """「1买」「3卖」这样的短标签，给图和 skill 输出用。"""
        return f"{self.kind}{'买' if self.is_buy else '卖'}"

    def __str__(self) -> str:  # pragma: no cover - 只用于人看
        tag = "~" if self.quasi else ""
        return f"{tag}{self.label}@leg{self.leg} bar{self.bar} {fp(self.price)}"


# --------------------------------------------------------------------------
# 定位：把一段走势上的极值落到具体的 leg 和原始K线
# --------------------------------------------------------------------------


def _leg_end_bar(legs: list[Leg], bis: list[Bi], j: int) -> int:
    """第 j 段走势终点在**原始K线**上的下标。

    `Leg.src_end` 是笔下标（线段 leg 也一样，它存的是构成笔的范围），再经
    `Fractal.peak_idx` 落到原始K线——买卖点要标在真实的那根K线上。
    """
    return bis[legs[j].src_end].end.peak_idx


def _extreme_leg(legs: list[Leg], lo: int, hi: int, d: Direction) -> int:
    """``legs[lo..hi]`` 里**极值落在哪一段**。向上趋势取最高，向下取最低。

    不能想当然地取 `hi`。71课 p190：「没有人规定分段的结束位置一定是最高、
    最低的。」背驰段的最后一段完全可能不是创极值的那一段。

    **只在与 `d` 同向的段里找**，理由和 20课 p43 只遍历 Zn 一样：段首尾相接，
    向上段的 `low` 就是前一个向下段的 `high`……反过来也一样，不做方向过滤的话
    最低点会被判给紧随其后的那个向上段，买点就标到反弹的起点上去了。
    """
    rng = [j for j in range(lo, hi + 1) if legs[j].direction is d] or list(range(lo, hi + 1))
    key = (lambda j: legs[j].high) if d is Direction.UP else (lambda j: legs[j].low)
    cmp_ = gt if d is Direction.UP else lt
    best = rng[0]
    for j in rng:
        if cmp_(key(j), key(best)):
            best = j
    return best


def _extreme_bar(legs: list[Leg], bis: list[Bi], j: int, want_low: bool) -> int:
    """极值落在原始K线的哪一根。

    对笔 leg 就是它的终点；对线段 leg 不一定——线段的真实极值可能在内部某一笔
    上（71课 p190）。所以遍历构成笔找出价格相等的那个分型。
    """
    leg = legs[j]
    target = leg.low if want_low else leg.high
    for k in range(leg.src_start, leg.src_end + 1):
        for f in (bis[k].start, bis[k].end):
            if f.price == target:
                return f.peak_idx
    return _leg_end_bar(legs, bis, j)  # 理论上到不了，兜个底


def _macd_law_first(m: Macd | None, bar: int, is_buy: bool) -> bool | None:
    """第 2450 行：「第一类买点都是在 **0 轴之下**背驰形成的」，卖点反过来。"""
    if m is None or not (0 <= bar < len(m.dif)):
        return None
    return bool(m.dif[bar] < 0) if is_buy else bool(m.dif[bar] > 0)


def _macd_law_second(m: Macd | None, lo: int, hi: int, is_buy: bool) -> bool | None:
    """第 2450 行：「第二类买点都是**第一次上 0 轴后回抽**确认形成的」。

    即 1买 到 2买 之间黄白线上过 0 轴。卖点镜像。
    """
    if m is None or not (0 <= lo <= hi < len(m.dif)):
        return None
    seg = m.dif[lo : hi + 1]
    return bool((seg > 0).any()) if is_buy else bool((seg < 0).any())


# --------------------------------------------------------------------------
# 第一类买卖点。「第一类买点肯定是趋势背驰构成的」（第 503 行）
# --------------------------------------------------------------------------


def _first_from(b: Beichi, legs: list[Leg], bis: list[Bi], m: Macd | None) -> BSPoint | None:
    """把一次背驰判定翻成一个 1买/1卖。不成立返回 None。

    ``Beichi.direction`` 是**趋势的方向**：向下趋势衰竭（底背驰）给买点，
    向上趋势衰竭（顶背驰）给卖点。
    """
    if not b.hit:
        return None
    is_buy = b.direction is Direction.DOWN
    j = _extreme_leg(legs, b.leave.leg_start, b.leave.leg_end, b.direction)
    bar = _extreme_bar(legs, bis, j, want_low=is_buy)
    price = legs[j].low if is_buy else legs[j].high

    why = [f"背驰段 legs[{b.leave.leg_start}..{b.leave.leg_end}]，极值在 leg{j}"]
    # 第 503 行：盘整背驰构成的只是「类似第一类买点的买点」，且「这个级别，
    # 至少应该是周线以上」。级别本模块不知道，所以只打标记交给上层判断。
    quasi = b.kind != TREND
    if quasi:
        why.append("由盘整背驰构成 → 类1买，原文说周线以上级别才有意义（第 503 行）")
    why.extend(b.reasons)

    return BSPoint(
        kind=FIRST,
        is_buy=is_buy,
        leg=j,
        bar=bar,
        price=price,
        zs_index=b.zs_index,
        source=SRC_TREND_BEICHI,
        confirmed=legs[j].confirmed,
        quasi=quasi,
        macd_law_ok=_macd_law_first(m, bar, is_buy),
        reasons=tuple(why),
    )


# --------------------------------------------------------------------------
# 第二类买卖点。C17 的两套口径
# --------------------------------------------------------------------------


def _second_after(
    p1: BSPoint, legs: list[Leg], bis: list[Bi], m: Macd | None, *, wide: bool
) -> BSPoint | None:
    """1买 之后第二段次级别走势的终点。第 362 / 2203 行。

    `wide=False` 是 17课 p37 的窄口径，不附加任何额外条件——第 2208 行明说
    「第二类买点跌破第一类买点……**这是完全可以的**」，所以破前低不是否决项。

    `wide=True` 是 53课 p127 的广义口径，要求「**不创新低或盘整背驰**」，见
    CONVENTIONS C17。
    """
    j = p1.leg
    up, down = j + 1, j + 2  # 「第一段次级别走势」向上、「第二段」回落
    if down >= len(legs):
        return None
    d1 = legs[j].direction
    if legs[up].direction is d1 or legs[down].direction is not d1:
        return None  # 方向不交替说明中间断了，这不是原文描述的形态

    bar = _leg_end_bar(legs, bis, down)  # 原文说的是这一段的「**结束点**」
    price = legs[down].end_price
    # 第 2208 行：2买 可以比 1买 低，只记不否决
    breaks = lt(price, p1.price) if p1.is_buy else gt(price, p1.price)

    why = [f"1买在 leg{j}，leg{up} 向上、leg{down} 回落，取其结束点（第 2203 行）"]
    if breaks:
        why.append(f"2买 {fp(price)} {'跌破' if p1.is_buy else '升破'} 1买 {fp(p1.price)}"
                   "——第 2208 行明说这完全可以，一般对应盘整背驰")

    if wide:
        # 「不创新低**或**盘整背驰」。创了新低就得至少在力度上弱于第一段，
        # 这正是 24课 p53 的面积比较，只是拿两段走势直接比（第 496 行：
        # 「这两个走势类型是否一定是趋势，都问题不大」）。
        weaker = _leg_weaker(legs, bis, m, down, j, p1.is_buy)
        if breaks and not weaker:
            return None
        why.append("不创新低" if not breaks else "创了新低但力度更弱（盘整背驰）")

    return BSPoint(
        kind=SECOND,
        is_buy=p1.is_buy,
        leg=down,
        bar=bar,
        price=price,
        zs_index=p1.zs_index,
        source=SRC_WIDE_SECOND if wide else SRC_AFTER_FIRST,
        confirmed=legs[down].confirmed,
        quasi=p1.quasi,  # 锚在类1买上的，自己也只是「类2买」
        macd_law_ok=_macd_law_second(m, p1.bar, bar, p1.is_buy),
        reasons=tuple(why),
    )


def _leg_bars(legs: list[Leg], bis: list[Bi], j: int) -> tuple[int, int]:
    """第 j 段走势覆盖的原始K线闭区间。MACD 面积按这个窗口截。"""
    leg = legs[j]
    return (bis[leg.src_start].start.peak_idx, bis[leg.src_end].end.peak_idx)


def _leg_weaker(
    legs: list[Leg], bis: list[Bi], m: Macd | None, a: int, b: int, is_buy: bool
) -> bool:
    """第 a 段的力度是否小于第 b 段。24课 p53 的面积比较，用在两段走势之间。

    第 496 行允许这么用：「这里是把第一、三段看成两个走势类型之间的比较……
    这两个走势类型**是否一定是趋势，都问题不大**，两个盘整在盘整背驰中也是
    可以比较力度的。」

    没有 MACD 就比不了，返回 False（宁可漏掉，不可凭空给信号）。
    """
    if m is None:
        return False
    positive = not is_buy  # 买点看的是向下段 → 绿柱；卖点看向上段 → 红柱
    return lt(hist_area(m, *_leg_bars(legs, bis, a), positive=positive),
              hist_area(m, *_leg_bars(legs, bis, b), positive=positive))


# --------------------------------------------------------------------------
# 第三类买卖点。20课 p43 定理（第 966 行）
# --------------------------------------------------------------------------


def _third_at(
    zss: list[ZhongShu], k: int, legs: list[Leg], bis: list[Bi], m: Macd | None
) -> BSPoint | None:
    """第 k 个中枢的三买/三卖。

    「一个次级别走势类型向上**离开**走势中枢，然后以一个次级别走势类型**回试**，
      其低点**不跌破 ZG**，则构成第三类买点」——所以需要两段：离开段和紧跟着的
    回试段，且回试完全不重新进入中枢。

    **必须是第一次回抽**（C-bs）：原文的「然后」就是紧接着那一段，不是后面任何
    一次回调。实现上回试段恒等于 `depart_leg + 1`，这条自动满足。
    """
    zs = zss[k]
    dep = zs.depart_leg
    if dep is None or dep + 1 >= len(legs):
        return None  # 还没离开，或者离开了但回试段还没走出来
    pull = dep + 1
    d = legs[dep].direction
    if legs[pull].direction is d:
        return None  # 方向不交替，说明这不是「离开 + 回试」的形态

    is_buy = d is Direction.UP  # 向上离开 → 三买；向下离开 → 三卖
    # 三买比 ZG，三卖比 **ZD**（第 966 行原文如此，两边都要求回试不重进中枢）
    if is_buy and not gt(legs[pull].low, zs.zg):
        return None
    if not is_buy and not lt(legs[pull].high, zs.zd):
        return None

    bar = _extreme_bar(legs, bis, pull, want_low=is_buy)
    price = legs[pull].low if is_buy else legs[pull].high
    base = "ZG" if is_buy else "ZD"
    base_v = zs.zg if is_buy else zs.zd
    why = [
        f"leg{dep} 向{'上' if is_buy else '下'}离开中枢[{fp(zs.zd)},{fp(zs.zg)}]，"
        f"leg{pull} 回试 {fp(price)} 未{'跌破' if is_buy else '升破'} {base}={fp(base_v)}"
        "（20课 p43 第 966 行）",
        "是离开后的**第一次**回抽（C-bs）",
    ]

    # C15 的歧义：回试之后又直接长出一个同级别中枢，而它整个不触及原中枢。
    # 这时候这一段到底算「对原中枢的回试」还是「新趋势的第一段」，原文多处
    # 互相矛盾。按保守口径仍然给出信号，但打标记。
    nxt = zss[k + 1] if k + 1 < len(zss) else None
    ambiguous = nxt is not None and nxt.start_leg == pull and (
        gt(nxt.dd, zs.gg) if is_buy else lt(nxt.gg, zs.dd)
    )
    if ambiguous:
        why.append("回试段同时是下一个中枢的起手，且新中枢完全不触及原中枢 → C15 歧义")

    # C18：段数不够 5 段。61课 p154 明说「一个第三类买卖点，**至少需要有 5 段
    # 次级别的**，前三段构成中枢，第四段离开中枢，第 5 段构成第三类买卖点」。
    #
    # 但 18课 p40 的延伸/离开机制允许中枢的**第三段自己飞走**（见 C-core），
    # 这时离开段就是构成段本身，总共只用掉 4 段。两条都是原文，对不上。
    #
    # 取 18课 的宽口径——它是机制层面的推论，而 61课 那句是在描述一个标准
    # 图例。但 4 段的形态确实更弱：中枢刚成立就被自己的第三段带走了，围绕
    # 中枢的震荡根本没发生过。所以给出信号，打标记，让上层自己决定要不要。
    if pull - zs.start_leg < 4:
        ambiguous = True
        why.append(
            f"只用了 {pull - zs.start_leg + 1} 段（中枢第三段自己就是离开段），"
            f"61课 p154 要求至少 5 段 → C18 歧义"
        )

    return BSPoint(
        kind=THIRD,
        is_buy=is_buy,
        leg=pull,
        bar=bar,
        price=price,
        zs_index=k,
        source=SRC_THIRD_THEOREM,
        confirmed=legs[pull].confirmed,
        ambiguous=ambiguous,
        reasons=tuple(why),
    )


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


def find_bspoints(
    legs: list[Leg],
    zss: list[ZhongShu],
    bis: list[Bi],
    m: Macd | None = None,
    *,
    area_ratio: float = AREA_RATIO,
    include_quasi: bool = True,
    wide_second: bool = True,
) -> list[BSPoint]:
    """划出这一串走势上的全部 1/2/3 类买卖点，按K线先后排序。

    ``m`` 是原始K线上的 MACD。**没有它就只能给 3买**——1买 按定义由背驰构成
    （第 503 行），而背驰的量化口径走的就是 MACD（C13）。3买 是纯几何的，
    不需要。

    ``include_quasi=False`` 丢掉由盘整背驰构成的「类1买」及其后续的 2买。
    原文说这类点在小级别里意义不大（第 503 行），日线以下扫描建议关掉。

    ``wide_second`` 控制要不要同时给 53课 p127 的广义 2买（C17）。
    """
    out: list[BSPoint] = []

    if m is not None:
        for b in detect_all(legs, zss, bis, m, area_ratio=area_ratio):
            p1 = _first_from(b, legs, bis, m)
            if p1 is None or (p1.quasi and not include_quasi):
                continue
            out.append(p1)

            # C17 的两套口径。窄口径（17课 p37）要求前面是**趋势**背驰的 1买；
            # 广义口径（53课 p127）不要求——盘整背驰「无所谓第一类买点」
            # （第 1152 行），正是它要对付的情形。
            narrow = None if p1.quasi else _second_after(p1, legs, bis, m, wide=False)
            wide = _second_after(p1, legs, bis, m, wide=True) if wide_second else None
            if narrow and wide and narrow.leg == wide.leg:
                # 同一个点被两套口径同时认下，合并成一条，来源都记上
                narrow = replace(
                    narrow,
                    source=f"{narrow.source} ＋ {wide.source}",
                    reasons=narrow.reasons + wide.reasons[-1:],
                )
                wide = None
            out.extend(x for x in (narrow, wide) if x is not None)

    for k in range(len(zss)):
        p3 = _third_at(zss, k, legs, bis, m)
        if p3 is not None:
            out.append(p3)

    out.sort(key=lambda p: (p.bar, p.kind))
    return out


def latest(points: list[BSPoint], *, confirmed_only: bool = False) -> BSPoint | None:
    """最近的那个买卖点。扫描输出「当下该怎么办」时用它。

    ``confirmed_only=True`` 滤掉落在未完成笔/段上的点——盘后扫描时末端天然
    未完成（C-prov），当成已确认信号会亏钱。
    """
    pool = [p for p in points if p.confirmed] if confirmed_only else points
    return pool[-1] if pool else None


def check_bspoints(points: list[BSPoint], zss: list[ZhongShu] | None = None) -> None:
    """原文里那些「违反就一定是划错了」的陈述，逐条写成断言。

    **重合关系**，21课 p45 第 363 行：

      「第一类买点与第二类买点是前后出现的，不可能产生重合，而第一类与第三类
        买点，一个在中枢之下、一个在中枢之上，也不可能产生重合。**只有第二类
        买点与第三类买点是可能产生重合的**。」

    **位置约束**，21课 p45 第 361 行（给了 ``zss`` 才查）：

      「因此，只有在**下跌确立后的中枢下方**才可能出现买点。这就是第一类买点。」

    位置约束**只管真 1买**。「类1买」是盘整背驰构成的（C-quasi），那是中枢
    内部的事，原文从没说它得在中枢之外——真实数据上也确实有落在中枢里的
    （7 只票 91 个一类点里 6 个，全部是 quasi）。
    """
    by_bar: dict[tuple[int, bool], set[int]] = {}
    for p in points:
        by_bar.setdefault((p.bar, p.is_buy), set()).add(p.kind)
    for (bar, is_buy), kinds in by_bar.items():
        side = "买" if is_buy else "卖"
        if FIRST in kinds and SECOND in kinds:
            raise InvariantError(f"bar{bar}：1{side} 与 2{side} 重合（21课 p45 第 363 行）")
        if FIRST in kinds and THIRD in kinds:
            raise InvariantError(f"bar{bar}：1{side} 与 3{side} 重合（21课 p45 第 363 行）")

    if zss is None:
        return
    for p in points:
        if not (0 <= p.zs_index < len(zss)):
            raise InvariantError(f"{p} 指向不存在的中枢 {p.zs_index}")
        zs = zss[p.zs_index]
        # 1买 在中枢之下、1卖 在中枢之上（21课 p45 第 361 行）。趋势背驰的 c 段
        # 按定义要创新极值、穿出包络，所以这条本该恒成立——不成立就是上游错了
        if p.kind == FIRST and not p.quasi:
            if lt(p.price, zs.zd) if p.is_buy else gt(p.price, zs.zg):
                continue
            raise InvariantError(
                f"{p} 没落在中枢[{fp(zs.zd)},{fp(zs.zg)}]之"
                f"{'下' if p.is_buy else '上'}（21课 p45 第 361 行）"
            )
        # 3买 不跌破 ZG、3卖 不升破 ZD（20课 p43 第 966 行）
        if p.kind == THIRD and not (gt(p.price, zs.zg) if p.is_buy else lt(p.price, zs.zd)):
            raise InvariantError(
                f"{p} {'跌破 ZG' if p.is_buy else '升破 ZD'}"
                f"[{fp(zs.zd)},{fp(zs.zg)}]（20课 p43 第 966 行）"
            )
