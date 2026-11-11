"""MACD。24课 pp.52-53 用它做背驰的**量化测量**（结构门槛另见 `beichi.py`）。

numpy 手写，不依赖 talib——talib 要编译 C 扩展，在 Python 3.13 上是个随时会炸的
二进制依赖，而 MACD 本身只有十几行。

24课 p52 的参数就是通用默认值 (12, 26, 9)，原文没有改动过它们。

**EMA 的初值原文没写**，这是个真实的口径分歧（见 CONVENTIONS.md C-ema）：
常见实现有「首值取第一个样本」和「首值取前 N 个的 SMA」两派，前 ~3N 根柱子的
数值会不同。本项目取**第一个样本**（递归式 EMA 的自然起点，也是绝大多数行情
软件的做法）。背驰比较的是**相隔很远的两段面积**，开头那点差异到不了比较区。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FAST, SLOW, SIGNAL = 12, 26, 9


def ema(x: np.ndarray, n: int) -> np.ndarray:
    """指数移动平均，alpha = 2/(n+1)，首值取 x[0]。

    用显式循环而不是 `scipy.signal.lfilter`：长度在几千根量级，循环开销无关
    紧要，而可读性和「能照着公式核对」在这个项目里值钱得多。
    """
    if x.size == 0:
        return x.astype(float)
    a = 2.0 / (n + 1.0)
    out = np.empty(x.size, dtype=float)
    out[0] = x[0]
    for i in range(1, x.size):
        out[i] = a * x[i] + (1.0 - a) * out[i - 1]
    return out


@dataclass(frozen=True, slots=True)
class Macd:
    """``dif`` 白线，``dea`` 黄线，``hist`` 柱子。

    柱子口径：``hist = 2 * (dif - dea)``。国内行情软件（通达信/同花顺）画的
    MACD 柱子带这个 2 倍系数，24课 p53 说的「柱子面积」就是看着这类软件说的。
    **系数对背驰判断没有任何影响**——比较的是两段面积的大小关系，同乘一个正数
    不改变不等号。留着 2 倍只为数值能和软件对上，方便人工核对。
    """

    dif: np.ndarray
    dea: np.ndarray
    hist: np.ndarray

    def __len__(self) -> int:
        return self.dif.size


def macd(
    closes: list[float] | np.ndarray,
    fast: int = FAST,
    slow: int = SLOW,
    signal: int = SIGNAL,
) -> Macd:
    c = np.asarray(closes, dtype=float)
    dif = ema(c, fast) - ema(c, slow)
    dea = ema(dif, signal)
    return Macd(dif=dif, dea=dea, hist=2.0 * (dif - dea))


def hist_area(m: Macd, lo: int, hi: int, *, positive: bool) -> float:
    """``[lo, hi]`` 闭区间内**同号**柱子的面积（绝对值之和）。

    24课 p53：「比较两段走势的 MACD 柱子面积」。上涨段比**红柱**（正），下跌段
    比**绿柱**（负），不能把正负柱子相加抵消——那样算出来的是净值不是面积，
    一段剧烈震荡会被抹平成 0。

    返回绝对值，调用方直接比大小即可。
    """
    if lo > hi or m.hist.size == 0:
        return 0.0
    lo = max(lo, 0)
    hi = min(hi, m.hist.size - 1)
    seg = m.hist[lo : hi + 1]
    picked = seg[seg > 0] if positive else seg[seg < 0]
    return float(np.abs(picked).sum())


def dif_extreme(m: Macd, lo: int, hi: int, *, want_max: bool) -> float:
    """``[lo, hi]`` 内 DIF（白线）的极值。

    27课 p66 的第二个判据要看「黄白线回抽 0 轴后不创新高/新低」，用的是 DIF
    的极值而不是面积。两个判据满足其一即算背驰信号，都满足更标准。
    """
    if lo > hi or m.dif.size == 0:
        return float("nan")
    seg = m.dif[max(lo, 0) : min(hi, m.dif.size - 1) + 1]
    if seg.size == 0:
        return float("nan")
    return float(seg.max() if want_max else seg.min())


def crosses_zero(m: Macd, lo: int, hi: int) -> bool:
    """``[lo, hi]`` 内 DIF 是否穿过 0 轴。

    27课 p66 的「回抽 0 轴」。没有回抽过 0 轴的两段，黄白线判据不适用——它们
    还在同一波里，比高低没有意义。
    """
    if lo > hi or m.dif.size == 0:
        return False
    seg = m.dif[max(lo, 0) : min(hi, m.dif.size - 1) + 1]
    return bool(seg.size and seg.max() > 0.0 > seg.min())
