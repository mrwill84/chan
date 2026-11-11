"""第90/106课用的指标，以及 RSI 对照组。纯函数，零 IO。

- BOLL(20, 2)：90课「中阴阶段结束时间的辅助判断」。日线段看**周线**，
  对应当下这一根还没走完的周线只使用已经出现的日线，绝不补未来数据。
- MA(21)：106课的最少延伸过滤。参数的解释在 `chan.segend`，不在这里。
- RSI(14)：Wilder 原始递推，只作 MACD 的同结构对照，不是原文推荐。

原文没有写死 BOLL 的周期、标准差倍数和「收口」的数值门槛。20/2 是软件
通用默认；收口定义为**带宽较上一高一级周期收窄，且创近 20 个高一级周期
最低**，属于我可检验约定，不能冒充原文的「接近 100%」。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .macd import ema
from .types import Bar


def sma(x: list[float] | np.ndarray, n: int) -> np.ndarray:
    """简单移动平均；不足 n 根的位置为 NaN，不拿短窗口冒充成熟指标。"""
    a = np.asarray(x, dtype=float)
    out = np.full(a.size, np.nan)
    if n <= 0:
        raise ValueError("均线周期必须为正")
    if a.size >= n:
        cs = np.concatenate(([0.0], np.cumsum(a)))
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


@dataclass(frozen=True, slots=True)
class Rsi:
    """与 Macd 同形状，便于复用背驰算法；字段含义在这里写死。

    ``dif = RSI-50``：以 50 为强弱零轴；``dea`` 是它的 9 周期 EMA。
    ``hist = RSI-50`` 而不是 DIF-DEA，因为面积比较要量的是 RSI 在强/弱区
    的行程。``force`` 仍用 DIF/DEA 两线的吻，与 MACD 版保持同构。
    前 14 根为中性 50，不提前制造背离。
    """
    dif: np.ndarray
    dea: np.ndarray
    hist: np.ndarray
    rsi: np.ndarray


def rsi(closes: list[float] | np.ndarray, n: int = 14) -> Rsi:
    c = np.asarray(closes, dtype=float)
    if n <= 0:
        raise ValueError("RSI 周期必须为正")
    out = np.full(c.size, 50.0)
    if c.size > n:
        delta = np.diff(c)
        gain = np.maximum(delta, 0.0)
        loss = np.maximum(-delta, 0.0)
        ag = float(gain[:n].mean())
        al = float(loss[:n].mean())

        def value(g: float, l: float) -> float:
            if l == 0.0:
                return 50.0 if g == 0.0 else 100.0
            return 100.0 - 100.0 / (1.0 + g / l)

        out[n] = value(ag, al)
        for i in range(n + 1, c.size):
            ag = (ag * (n - 1) + gain[i - 1]) / n
            al = (al * (n - 1) + loss[i - 1]) / n
            out[i] = value(ag, al)
    dif = out - 50.0
    return Rsi(dif=dif, dea=ema(dif, 9), hist=dif.copy(), rsi=out)


@dataclass(frozen=True, slots=True)
class Bollinger:
    """映射回原始K线下标的 BOLL；``squeeze`` 是 90课「收口」的可操作口径。"""
    mid: np.ndarray
    upper: np.ndarray
    lower: np.ndarray
    bandwidth: np.ndarray
    squeeze: np.ndarray


def _higher_keys(bars: list[Bar]) -> list[object]:
    """5分→30分（6根）、30分→日线、日线→周线。

    90课 的例子是「5分钟中阴看30分钟 BOLL」，不是任何级别都机械地看周线。
    用相邻时间戳的中位间隔识别输入级别，午休/隔夜不影响中位数。

    5分→30分**按时钟分组，不按下标每 6 根一组**。时间戳是K线的结束时刻，
    所以 09:35~10:00 归 10:00 那根30分，桶号取 ``(分钟数-1)//30``。旧实现用
    ``i // 6``，全序列从 09:35 起、每天正好 48 根时碰巧对齐；从盘中切一段
    （600519 最近1000根，起点 10:15）167 组里 166 组跨了30分钟边界，停牌
    半天也会让之后全部错位。
    """
    try:
        ts = [datetime.fromisoformat(b.ts) for b in bars]
    except ValueError as e:
        raise ValueError("高一级 BOLL 需要 ISO 时间戳") from e
    if len(ts) < 2:
        return [0] * len(bars)
    gaps = sorted((b - a).total_seconds() / 60.0 for a, b in zip(ts, ts[1:]))
    minutes = gaps[len(gaps) // 2]
    if minutes <= 10:
        return [(t.date(), (t.hour * 60 + t.minute - 1) // 30) for t in ts]
    if minutes < 60:
        return [t.date() for t in ts]
    return [t.date().isocalendar()[:2] for t in ts]


def higher_bollinger(bars: list[Bar], n: int = 20, k: float = 2.0,
                     lookback: int = 20) -> Bollinger:
    """由当前输入聚合一级的 BOLL，再映回每根原始K线。

    级别映射是 5分→30分、30分→日线、日线→周线（90课 的显微镜例子）。
    同一高一级周期内，每根原始K线只用当下最新收盘更新轨道；已结束周期的
    历史值不再改变。标准差用总体标准差 ``ddof=0``。
    """
    size = len(bars)
    empty = np.full(size, np.nan)
    if not bars:
        return Bollinger(empty, empty.copy(), empty.copy(), empty.copy(),
                         np.zeros(0, dtype=bool))
    keys = _higher_keys(bars)
    starts = [0]
    for i in range(1, size):
        if keys[i] != keys[i - 1]:
            starts.append(i)
    ends = starts[1:] + [size]
    closes = [bars[e - 1].close for e in ends]
    final_bw = np.full(len(starts), np.nan)
    for w in range(n - 1, len(starts)):
        sd = float(np.std(closes[w - n + 1:w + 1]))
        final_bw[w] = 2.0 * k * sd / float(np.mean(closes[w - n + 1:w + 1]))

    mid = np.full(size, np.nan)
    upper = np.full(size, np.nan)
    lower = np.full(size, np.nan)
    bandwidth = np.full(size, np.nan)
    squeeze = np.zeros(size, dtype=bool)
    for w, (a, z) in enumerate(zip(starts, ends)):
        if w + 1 < n:
            continue
        past = closes[max(0, w - n + 1):w]
        for t in range(a, z):
            window = np.asarray([*past, bars[t].close], dtype=float)
            mu = float(window.mean())
            sd = float(window.std())
            mid[t], upper[t], lower[t] = mu, mu + k * sd, mu - k * sd
            bandwidth[t] = (upper[t] - lower[t]) / mu
            old = final_bw[max(0, w - lookback + 1):w]
            old = old[np.isfinite(old)]
            squeeze[t] = bool(old.size == lookback - 1 and w > n - 1
                              and bandwidth[t] < final_bw[w - 1]
                              and bandwidth[t] <= old.min())
    return Bollinger(mid, upper, lower, bandwidth, squeeze)
