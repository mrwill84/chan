"""多级别联立：27课 p67 的「精确大转折点寻找程序」。

原文给的是一个自上而下的收缩过程：大级别先圈出转折区间，次级别在这个区间里
找自己的买卖点，再次级别继续收缩，直到最小级别给出精确的那一点。这就是区间套。

## 这里做了一个关键选择：收缩的是**答案**，不是**输入**

朴素的实现是把次级别K线按大级别区间切一刀，只对切片划线。**那是错的。**
包含处理要从序列开头顺序推（65课 p168，不满足传递律），笔要看分型间隔，线段
要看特征序列——这些全部依赖区间之前的历史。从中间切一刀再划，划出来的笔和段
跟完整历史上划出来的根本不是同一批，而且切点越靠近越失真。

所以每个级别都在**自己的完整历史**上划线，区间套只用来**挑点**：次级别的买卖
点里，时间落在大级别窗口内、方向又一致的，才算这一层的收缩结果。见 C-nest。

## 方向冲突怎么办

27课 p67 的程序是自上而下的，所以**以大级别为准，小级别只决定时机**。次级别
给出反向信号时不覆盖大级别结论，只标 `conflict` 并如实输出两边——这种地方正是
该让人（或模型）看一眼的，不该由程序替它选。

本模块不认识「日线」「30分钟」这些具体级别，只认识调用方给的**有序**级别名
（大 → 小）。级别在缠论里本来就是相对的，而且 `chan/` 不依赖 `feeds/`。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .buysell import BSPoint, find_bspoints
from .forward import trailing_trend
from .macd import macd
from .pipeline import analyze
from .types import Bar, Bi, Leg, ZhongShu
from .zhongshu import build_zhongshus, legs_from_bis, legs_from_segs


@dataclass(frozen=True, slots=True)
class LevelAnalysis:
    """一个级别在自己完整历史上的划线结果。

    只存 `ts` 不存 `Bar`：买卖点和中枢回头都要换算成时间戳才能跨级别比对，
    而完整K线在这里没有别的用处，几千只票联立时白占内存。
    """

    name: str
    ts: tuple[str, ...]
    #: **当下**的走势类型（`forward.trailing_trend`），不是 `zhongshu.classify`。
    #: 区别见 `analyze_level` 的说明——这里曾经用的是后者，而它在真实长度的
    #: 历史上恒等于 `panzheng`，整个字段等于没有。
    trend: str
    bis: tuple[Bi, ...] = ()
    legs: tuple[Leg, ...] = ()
    zhongshus: tuple[ZhongShu, ...] = ()
    points: tuple[BSPoint, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def ts_of(self, bar: int) -> str:
        """K线下标 → 时间戳。越界回空串，跨级别比对不该因为取标签崩掉。"""
        return self.ts[bar] if 0 <= bar < len(self.ts) else ""

    def leg_start_ts(self, p: BSPoint) -> str:
        """买卖点所在那一段走势的**起点**时间。区间套窗口的左端就用它。"""
        if not (0 <= p.leg < len(self.legs)):
            return self.ts_of(p.bar)
        leg = self.legs[p.leg]
        if not (0 <= leg.src_start < len(self.bis)):
            return self.ts_of(p.bar)
        return self.ts_of(self.bis[leg.src_start].start.peak_idx)


def analyze_level(
    name: str,
    bars: list[Bar],
    *,
    zs_from: str = "seg",
    include_quasi: bool = True,
    min_bars: int = 60,
) -> LevelAnalysis:
    """对一个级别划完整一套线。纯函数，不碰网络。

    出错不抛：多级别联立里次级别数据缺失是常态（C-min——东财的 30 分钟线只给
    得出 ~252 根），为一个级别没数据把整次联立炸掉没有道理。错误记进 `error`，
    由调用方决定这一层算不算数。

    `trend` 用 `forward.trailing_trend` 而不是 `zhongshu.classify`。后者是
    17课 p36 对「一个**完成的**走势类型」的定义，没错，但把它套在一只票的
    整段中枢序列上就只剩一个答案：只要历史上有一对相邻中枢不同向，整体就是
    盘整——而几千根K线上必然有。实测 15 只票 × 日线/30分/5分，`classify`
    **全部**返回 `panzheng`。多级别联立要回答的是「日线现在什么走势、30分
    现在什么走势」，那是末端那一段的事。
    """
    ts = tuple(b.ts for b in bars)
    if len(bars) < min_bars:
        return LevelAnalysis(name, ts, "none",
                             error=f"K线只有 {len(bars)} 根，不足 {min_bars} 根，划不出中枢")
    try:
        a = analyze(bars, check=True)
    except Exception as e:  # noqa: BLE001 - 划线异常要归到这一层，不能带崩整次联立
        return LevelAnalysis(name, ts, "none", error=f"{type(e).__name__}: {e}")
    if not a.bis or not a.segs:
        return LevelAnalysis(name, ts, "none", error="没划出笔或线段")

    legs = legs_from_segs(a.segs, a.bis) if zs_from == "seg" else legs_from_bis(a.bis)
    zss = build_zhongshus(legs)
    pts = find_bspoints(legs, zss, a.bis, macd([b.close for b in bars]),
                        include_quasi=include_quasi)
    return LevelAnalysis(
        name=name,
        ts=ts,
        # 没有中枢时 `trailing_trend` 回 panzheng，和原先的 `classify` 一致。
        # 「划出线了但没中枢」确实不等于「盘整」（`tree.NO_ZHONGSHU` 就是为
        # 它留的），但那是另一件事：这里的 "none" 现在表示**这一层没算出来**，
        # 再塞一个状态进去会把两者混掉。
        trend=trailing_trend(zss)[0],
        bis=tuple(a.bis),
        legs=tuple(legs),
        zhongshus=tuple(zss),
        points=tuple(pts),
    )


# --------------------------------------------------------------------------
# 区间套
# --------------------------------------------------------------------------

OK = "ok"
PARTIAL = "partial"
NONE = "none"

AGREE = "agree"
CONFLICT = "conflict"
INCOMPLETE = "incomplete"


def _within(ts: str, lo: str, hi: str) -> bool:
    """`ts` 落在 `[lo, hi]` 内吗——按**父级别的时间粒度**比。

    日线的时间戳是 `2026-10-16`，30 分钟的是 `2026-10-16 14:30`。直接比字符串
    的话，同一天的任何分钟线都 > 那天的日线戳，于是整个窗口的最后一天全被排除
    掉——而区间套要找的转折点，十有八九就在那一天里。

    所以两端都只比到父级别戳的长度为止：右端 `ts[:len(hi)] <= hi` 把 hi 当天
    整天算进来，左端同理。父子同粒度时这个切片是恒等的，退化成普通字符串比较。
    """
    return ts[: len(lo)] >= lo and ts[: len(hi)] <= hi


@dataclass(frozen=True, slots=True)
class Step:
    """区间套的一层。

    `point` 是这一层收缩到的买卖点；`None` 表示这一层没能收缩——原因看
    `coverage` 和 `note`，**不要**当成「这里没有买卖点」。
    """

    level: str
    window: tuple[str, str]
    coverage: str = NONE
    point: BSPoint | None = None
    ts: str = ""
    against: tuple[BSPoint, ...] = ()  # 窗口内的反向点，有就是 conflict
    note: str = ""


@dataclass(frozen=True, slots=True)
class Nesting:
    """一次自上而下的区间套。

    `steps[0]` 是大级别的出发点，最后一个有 `point` 的 step 就是收缩到的
    精确转折点（27课 p67）。
    """

    steps: tuple[Step, ...] = ()
    agreement: str = INCOMPLETE
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def anchor(self) -> Step | None:
        """大级别那一层。方向和结论以它为准。"""
        return self.steps[0] if self.steps else None

    @property
    def precise(self) -> Step | None:
        """收缩到的最小级别那一层。只决定时机，不决定方向。"""
        for s in reversed(self.steps):
            if s.point is not None:
                return s
        return None


def _pick(a: LevelAnalysis, *, side: bool | None, kinds: set[int] | None) -> BSPoint | None:
    """大级别的出发点：满足条件的**最后**一个买卖点。"""
    pool = [p for p in a.points
            if (side is None or p.is_buy == side) and (not kinds or p.kind in kinds)]
    return pool[-1] if pool else None


def nest(
    levels: list[LevelAnalysis],
    *,
    side: bool | None = None,
    kinds: set[int] | None = None,
    anchor: BSPoint | None = None,
) -> Nesting:
    """自上而下跑一遍区间套。`levels` 必须**从大到小**排好。

    每一层的窗口 = 上一层那个买卖点所在走势段的 `[起点, 终点]`。次级别在这个
    窗口里找**同向**的买卖点，取最后一个作为收缩结果，再把窗口交给下一级。

    方向不一致时不覆盖大级别结论（27课 p67 的程序是自上而下的），只把反向点
    记进 `Step.against` 并把整体标成 `conflict`。
    """
    if not levels or not levels[0].ok:
        why = levels[0].error if levels else "没有给任何级别"
        return Nesting(agreement=INCOMPLETE, notes=(f"大级别不可用：{why}",))

    top = levels[0]
    a0 = anchor or _pick(top, side=side, kinds=kinds)
    if a0 is None:
        return Nesting(agreement=INCOMPLETE,
                       notes=(f"{top.name}上没有符合条件的买卖点，区间套没有出发点。",))

    lo, hi = top.leg_start_ts(a0), top.ts_of(a0.bar)
    steps = [Step(level=top.name, window=(lo, hi), coverage=OK, point=a0, ts=hi,
                  note=f"出发点：{top.name} {a0.label}")]
    notes: list[str] = []
    agreement = AGREE

    for child in levels[1:]:
        st = _refine(child, lo, hi, want_buy=a0.is_buy)
        steps.append(st)
        if st.against:
            agreement = CONFLICT
        if st.point is None:
            # 收缩断在这里。再往下的级别窗口无从谈起——继续用上一层的窗口只是
            # 假装还在收缩，实际是换个级别重新找点，不是区间套。
            if agreement != CONFLICT:
                agreement = INCOMPLETE
            notes.append(f"{child.name}没能继续收缩：{st.note}")
            break
        lo, hi = child.leg_start_ts(st.point), st.ts
    return Nesting(steps=tuple(steps), agreement=agreement, notes=tuple(notes))


def _refine(a: LevelAnalysis, lo: str, hi: str, *, want_buy: bool) -> Step:
    """在 `[lo, hi]` 窗口里找这个级别的同向买卖点。"""
    win = (lo, hi)
    if not a.ok:
        return Step(level=a.name, window=win, note=a.error or "不可用")
    if not a.ts:
        return Step(level=a.name, window=win, note="没有K线")

    inside = [t for t in a.ts if _within(t, lo, hi)]
    if not inside:
        # 最常见的就是 C-min：东财的分钟线只给 ~31 个交易日，大级别窗口在几个月
        # 甚至几年前，次级别根本没有那段数据。这和「那段时间没有买卖点」是
        # 完全不同的两件事，必须分开说。
        reach = f"该级别数据只到 {a.ts[0]}~{a.ts[-1]}"
        return Step(level=a.name, window=win, coverage=NONE,
                    note=f"窗口内没有K线（{reach}，见 C-min）")

    cover = OK if a.ts[0][: len(lo)] <= lo else PARTIAL
    pool = [p for p in a.points if _within(a.ts_of(p.bar), lo, hi)]
    same = [p for p in pool if p.is_buy == want_buy]
    against = tuple(p for p in pool if p.is_buy != want_buy)

    if not same:
        why = ("窗口内只有反向买卖点" if against else "窗口内没有买卖点")
        if cover == PARTIAL:
            why += f"，且数据只覆盖到 {a.ts[0]}，窗口左段缺失"
        return Step(level=a.name, window=win, coverage=cover, against=against, note=why)

    p = same[-1]
    note = f"收缩到 {p.label}"
    if against:
        note += f"；窗口内另有 {len(against)} 个反向点，以大级别为准（27课 p67）"
    if cover == PARTIAL:
        note += f"；数据只覆盖到 {a.ts[0]}，窗口左段缺失"
    return Step(level=a.name, window=win, coverage=cover, point=p,
                ts=a.ts_of(p.bar), against=against, note=note)
