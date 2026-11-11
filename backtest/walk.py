"""逐根向前走，每一根都**重新划一遍线**，记录买卖点是什么时候才看得见的。

这个模块只干一件事，但这件事决定了整个回测是真是假：

**买卖点的「发生时间」和「可见时间」不是同一天。**

引擎说某个 1买 在 2026-10-15。那是它事后回看标出来的位置。可在 10-15 当天，
构成这个点的笔还没走完，分型还没确认，引擎当时根本划不出它来——要等到
10-23 的K线出来，这个点才第一次出现在输出里。中间差的这 8 根K线，是你**不
可能**在 10-15 那天知道的信息。

拿点自己的时间戳和价格去算收益，就是拿 10-23 才有的知识在 10-15 下单。
缠论回测做出漂亮曲线，十有八九死在这里。

所以这里的做法是：``for t in range(...)``，每一步只把 ``bars[:t+1]`` 交给
引擎，看它这一步说了什么。一个点第一次被说出来的那个 ``t``，才是它的
**可见时间**；要算收益，入场价只能用那一天的收盘价。

顺带量出来另外两个平时没人报的数：

- **repaint lag**：可见时间 - 点自己的时间。这是你实际要付的延迟。
- **存活率**：某一步说出来的点，有多少在最终那一版划线里还在。被抹掉的那些
  正是 `confirmed=False` 想警告的东西（C-prov），这里给它一个数字。

一次 `analyze_one` 约 4 毫秒，640 根全程重划 2.5 秒。没有任何理由走捷径。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chan.types import Bar
from feeds import Level
from scan.report import MIN_BARS, analyze_one, analyze_one_checked

#: 买卖点的身份。``bar`` 是它在**原始K线序列**里的下标。
#:
#: 用下标而不用时间戳，是因为前缀切片不改变下标：`bars[:t+1]` 里第 b 根
#: 仍然是第 b 根。换成时间戳也行，但下标还能直接拿去取价，少一次查找。
SignalKey = tuple[int, str, int]  # (kind, side, bar)


@dataclass(slots=True)
class Sighting:
    """一个买卖点在走查过程中的全部经历。"""

    kind: int
    side: str
    label: str
    bar: int                 # 点自己在K线上的位置（引擎事后标的）
    ts: str
    price: float

    first_seen: int          # 第一次被划出来时的 t
    last_seen: int           # 最后一次被划出来时的 t
    seen_count: int = 0      # 一共在多少步里出现过
    gone_times: int = 0      # 出现后又消失过几次（消失再回来算一次）
    survived: bool = False   # 最终那一版划线里还在不在

    # 第一次被看见时的标记。**这是你当时能看到的那一版**，不是最终版——
    # 最终版的 confirmed 多半是 True（后面的笔走完了），拿它做判断是作弊。
    quasi_at_first: bool = False
    confirmed_at_first: bool = False
    ambiguous_at_first: bool = False
    trend_at_first: str = ""
    reasons_at_first: list[str] = field(default_factory=list)

    @property
    def lag(self) -> int:
        """可见时间 - 发生时间，单位是K线根数。**实际要付的延迟。**"""
        return self.first_seen - self.bar

    @property
    def stable(self) -> bool:
        """从出现到最后一步都没断过。断过的说明引擎自己反复过。"""
        return self.gone_times == 0 and self.survived


def walk_forward(
    bars: list[Bar],
    code: str,
    level: Level,
    *,
    zs_from: str = "seg",
    include_quasi: bool = True,
    warmup: int = MIN_BARS,
    step: int = 1,
    seg_end: str | None = None,
    seg_end_ma_filter: bool = False,
    verify: int = 1,
    step_kw=None,
    on_progress=None,
) -> list[Sighting]:
    """从第 ``warmup`` 根走到最后一根，每一步重划一次线。

    返回每个买卖点的「经历」，按**第一次被看见的时间**排序。

    ``step > 1`` 是抽样走查（快，但 lag 会被量化到 step 的倍数，只适合估个
    大概）。默认 1，也就是逐根走——4 毫秒一步，没必要省。

    ``include_quasi`` 固定建议开着：类买卖点在回测里恰恰是最该量的一类，
    因为盘后扫描里它们占了近期信号的绝大多数（C-zero）。要筛在后面筛。

    ``seg_end`` 透传给 `chan.segend`，换掉「末段何时算结束」的口径。它正是
    这张表要量的东西：3类点那 77~144 根延迟几乎全来自等线段确认。

    ``verify > 1`` 时每个检查点用多数校验。凑不齐多数的报告不能进场统计，
    直接抛错让外层短进程重试；否则硬件位翻会被平均成一种「策略表现」。

    ``step_kw(t)`` 返回这一步额外传给 `analyze_one` 的参数（目前只有次级别的
    ``sub_points``）。**它只拿到 t**，必须只用第 t 根收盘时已知的东西——次级别
    数据的因果性由调用方负责，见 `backtest.sublevel`。
    """
    tracker = Tracker()
    n = len(bars)
    for t in range(warmup - 1, n, step):
        kw = dict(zs_from=zs_from, include_quasi=include_quasi, recent=0,
                  seg_end=seg_end, seg_end_ma_filter=seg_end_ma_filter)
        if step_kw is not None:
            kw.update(step_kw(t))
        tracker.step(t, report_at(bars, t, code, level, verify=verify, **kw))
        if on_progress is not None:
            on_progress(t, n, len(tracker.seen))
    return tracker.finish()


def report_at(bars: list[Bar], t: int, code: str, level: Level, *,
              verify: int = 1, **kw) -> dict:
    """第 t 步的报告。**整个回测的正确性全系于这一行：引擎只能看到 bars[:t+1]。**"""
    if verify > 1:
        r = analyze_one_checked(bars[: t + 1], code, level, tries=verify, **kw)
        if "unstable" in r.get("warnings", {}):
            raise RuntimeError(f"{code} {level.value} t={t}：多数校验失败，拒绝入样")
        return r
    return analyze_one(bars[: t + 1], code, level, **kw)


class Tracker:
    """逐步记账，把每一步的报告累积成每个买卖点的「经历」。

    从 `walk_forward` 里抽出来，是为了让**拆成多个短进程**的走查（每步结果
    先落盘、最后重放，见 `backtest.sublevel`）和一口气走完的走查用**同一份**
    统计口径——两份各写一遍，迟早有一处的「消失又回来」算法悄悄不一样。
    """

    def __init__(self) -> None:
        self.seen: dict[SignalKey, Sighting] = {}
        self.alive: set[SignalKey] = set()   # 上一步还在的
        self.final: set[SignalKey] = set()

    def step(self, t: int, r: dict) -> None:
        if "error" in r:
            return
        now: set[SignalKey] = set()
        for p in r["bspoints"]:
            key = (p["kind"], p["side"], p["bar"])
            now.add(key)
            s = self.seen.get(key)
            if s is None:
                self.seen[key] = s = Sighting(
                    kind=p["kind"], side=p["side"], label=p["label"],
                    bar=p["bar"], ts=p["ts"], price=p["price"],
                    first_seen=t, last_seen=t,
                    quasi_at_first=p["quasi"],
                    confirmed_at_first=p["confirmed"],
                    ambiguous_at_first=p["ambiguous"],
                    trend_at_first=r["trend"],
                    reasons_at_first=list(p.get("reasons") or []),
                )
            elif key not in self.alive:
                # 消失过又回来了。引擎在这个位置上反复过，说明当时的划法
                # 不稳——这种点事后看着挺好，当时根本不敢下手。
                s.gone_times += 1
            s.last_seen = t
            s.seen_count += 1
        self.alive = now
        self.final = now

    def finish(self) -> list[Sighting]:
        for key, s in self.seen.items():
            s.survived = key in self.final
        return sorted(self.seen.values(), key=lambda s: (s.first_seen, s.bar))
