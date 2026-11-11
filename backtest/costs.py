"""交易摩擦：佣金、过户费、印花税、滑点，以及涨跌停封板时成交不了。

`score.py` 报的收益全是毛的，买卖点的 edge 在 +0.3%~+0.4% 量级，一笔来回的
成本（滑点 0.1%/边时约 0.3%~0.35%）就可能把它吃光。这一层回答的正是这个问题。

## 费率（A股，按成交日期）

- 佣金：每边万 2.5，**不计最低 5 元**（小额单实际更贵，这里偏乐观）
- 过户费：每边 0.001%
- 印花税：**只在卖出收**，2023-08-28 起由 0.1% 减半为 0.05%
- 滑点：每边按成交价的比例，默认 0.1%

## 成交约束

后复权价保留了逐日涨跌幅，所以可以用 `收盘/前收−1` 判涨跌停。
收盘价同时等于最高价（涨停）或最低价（跌停）、且涨跌幅达到板块限制减
0.5 个百分点的容差，就认为**收盘时封板**：

- 买入日收盘封涨停 → 收盘价买不进，顺延到下一个没封板的收盘，最多顺延
  `MAX_ENTRY_DEFER` 根，还买不进就放弃这一笔（单独计数，不悄悄丢）
- 卖出日收盘封跌停 → 卖不出，一直顺延到能卖；数据走完还卖不出就记为
  未了结，不进统计

涨跌停幅度：创业板（300/301）2020-08-24 起 20%、之前 10%；科创板（688）
20%；其余 10%。**ST 的 5% 识别不了**（行情数据里没有名称），会把 ST 票的
封板漏判成可成交——偏乐观，登记在案。
"""
from __future__ import annotations

from dataclasses import dataclass

from chan.types import Bar

STAMP_CUT = "2023-08-28"
CHINEXT_20PCT = "2020-08-24"
MAX_ENTRY_DEFER = 5
LIMIT_TOL = 0.005


@dataclass(frozen=True, slots=True)
class Costs:
    commission: float = 0.00025
    transfer: float = 0.00001
    slippage: float = 0.001
    stamp_before: float = 0.001
    stamp_after: float = 0.0005

    def stamp(self, ts: str) -> float:
        return self.stamp_before if ts[:10] < STAMP_CUT else self.stamp_after


def limit_pct(code: str, ts: str) -> float:
    """当日的涨跌停幅度。"""
    if code.startswith("688"):
        return 0.20
    if code.startswith(("300", "301")):
        return 0.20 if ts[:10] >= CHINEXT_20PCT else 0.10
    return 0.10


def prev_day_close(bars: list[Bar]) -> list[float | None]:
    """分钟线每一根对应的**前一交易日**收盘价（第一天为 None）。

    日内K线的涨跌停要拿当前价对比前一交易日收盘，不能对比上一根K线。
    """
    out: list[float | None] = []
    ref = last = None
    day = None
    for b in bars:
        d = b.ts[:10]
        if d != day:
            ref, day = last, d
        out.append(ref)
        last = b.close
    return out


def _day_ret(bars: list[Bar], i: int, ref: list | None = None) -> float | None:
    if ref is not None:
        return None if ref[i] is None else bars[i].close / ref[i] - 1.0
    return None if i <= 0 else bars[i].close / bars[i - 1].close - 1.0


def locked_up(bars: list[Bar], i: int, code: str, ref: list | None = None) -> bool:
    """收盘封涨停：收盘即最高，且涨幅到板。``ref`` 给了就按日内口径。"""
    r = _day_ret(bars, i, ref)
    return (r is not None and bars[i].close >= bars[i].high
            and r >= limit_pct(code, bars[i].ts) - LIMIT_TOL)


def locked_down(bars: list[Bar], i: int, code: str, ref: list | None = None) -> bool:
    """收盘封跌停：收盘即最低，且跌幅到板。``ref`` 给了就按日内口径。"""
    r = _day_ret(bars, i, ref)
    return (r is not None and bars[i].close <= bars[i].low
            and r <= -limit_pct(code, bars[i].ts) + LIMIT_TOL)


def net_long(entry: float, exit_: float, exit_ts: str, c: Costs) -> float:
    """一笔做多的净收益：买入付滑点+佣金+过户，卖出再加印花税。"""
    buy = entry * (1.0 + c.slippage)
    paid = buy * (1.0 + c.commission + c.transfer)
    sell = exit_ * (1.0 - c.slippage)
    got = sell * (1.0 - c.commission - c.transfer - c.stamp(exit_ts))
    return got / paid - 1.0


@dataclass(frozen=True, slots=True)
class Trade:
    signal_bar: int     # 信号第一次可见的那根（原计划买入日）
    entry_bar: int      # 实际买入（涨停顺延后）
    exit_bar: int       # 实际卖出（跌停顺延后）
    gross: float        # 收盘到收盘的毛收益，同 score.py 口径（按实际成交日）
    net: float


def simulate_long(bars: list[Bar], signal_bar: int, h: int, code: str,
                  c: Costs, *, intraday: bool = False) -> Trade | str:
    """信号那根收盘买、持有 h 根后收盘卖。成交不了返回原因字符串。

    ``intraday=True``（分钟线）：涨跌停对比前一交易日收盘；并执行 **T+1**——
    卖出日必须晚于买入日，不够就顺延到下一个交易日。
    """
    ref = prev_day_close(bars) if intraday else None
    e = enter_long(bars, signal_bar, code, ref)
    if isinstance(e, str):
        return e
    return exit_long(bars, signal_bar, e, e + h, code, c, ref=ref, intraday=intraday)


def enter_long(bars: list[Bar], signal_bar: int, code: str,
               ref: list | None = None) -> int | str:
    """实际买入那根：收盘封涨停就顺延，最多 `MAX_ENTRY_DEFER` 根。"""
    e = signal_bar
    while e < len(bars) and locked_up(bars, e, code, ref):
        e += 1
        if e - signal_bar > MAX_ENTRY_DEFER:
            return "entry_locked"
    if e >= len(bars):
        return "no_future"
    return e


def exit_long(bars: list[Bar], signal_bar: int, e: int, x: int, code: str, c: Costs,
              *, ref: list | None = None, intraday: bool = False) -> Trade | str:
    """第 e 根买入、计划第 x 根收盘卖：先过 T+1，再过跌停顺延。

    卖出时刻由调用方决定（固定持有期、或结构化卖法），成交约束统一在这里。
    """
    if intraday:
        while x < len(bars) and bars[x].ts[:10] <= bars[e].ts[:10]:
            x += 1
    if x >= len(bars):
        return "no_future"
    while x < len(bars) and locked_down(bars, x, code, ref):
        x += 1
    if x >= len(bars):
        return "exit_locked"
    gross = bars[x].close / bars[e].close - 1.0
    return Trade(signal_bar, e, x, gross,
                 net_long(bars[e].close, bars[x].close, bars[x].ts, c))
