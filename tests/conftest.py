"""测试公共夹具。手工构造K线，不碰网络。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chan.types import Bar, Bi, Direction, Fractal, FractalKind  # noqa: E402


def mk_bars(ranges: list[tuple[float, float]], ohlc: list[tuple[float, float]] | None = None):
    """从 (low, high) 列表造K线。

    ohlc 可选，给 (open, close)；不给时默认 open=low、close=high，
    这样默认全是阳线——只有 82课 强弱标注会看开收盘，划线逻辑完全不看。
    """
    out = []
    for i, (low, high) in enumerate(ranges):
        o, c = ohlc[i] if ohlc else (low, high)
        out.append(Bar(idx=i, ts=f"T{i:03d}", open=o, high=high, low=low, close=c))
    return out


def zigzag(pivots: list[float], leg_bars: int = 4, width: float = 0.2):
    """按给定的转折价序列造一串**无包含关系**的锯齿K线。

    每条腿均分成 leg_bars 根，中心价单调推进，宽度恒定，所以相邻K线的
    低点和高点同向移动，天然不存在包含关系。转折点正好落在下标
    0, leg_bars, 2*leg_bars, ... 上，于是分型中心的间隔恰好等于 leg_bars
    ——这让笔的间隔约束（old 要 >=4，new 要 >=3）可以被精确地逐根测试。
    """
    centers = [pivots[0]]
    for a, b in zip(pivots, pivots[1:]):
        for t in range(1, leg_bars + 1):
            centers.append(a + (b - a) * t / leg_bars)
    return mk_bars([(c - width / 2, c + width / 2) for c in centers])


def mk_bis(prices: list[float], m_step: int = 4) -> list[Bi]:
    """直接从一串交替的转折价造笔，跳过K线与分型层。

    线段的测试只关心笔的区间和方向，从价格直接造笔可以精确控制每一个
    特征序列元素的位置，比先造K线再划笔可控得多。分型做成高低点重合的
    一个点，这不影响线段算法——它只读 Bi 的 low/high/direction。
    """
    fs = []
    for k, p in enumerate(prices):
        if k == 0:
            kind = FractalKind.BOTTOM if prices[1] > p else FractalKind.TOP
        else:
            kind = FractalKind.TOP if p > prices[k - 1] else FractalKind.BOTTOM
        fs.append(
            Fractal(
                kind=kind,
                m_idx=k * m_step,
                high=p,
                low=p,
                begin_idx=k * m_step - 1,
                end_idx=k * m_step + 1,
                peak_idx=k * m_step,
                ts=f"T{k:03d}",
            )
        )
    bis = [
        Bi(
            index=i,
            start=fs[i],
            end=fs[i + 1],
            direction=Direction.UP if fs[i].kind is FractalKind.BOTTOM else Direction.DOWN,
        )
        for i in range(len(fs) - 1)
    ]
    if bis:
        bis[-1].confirmed = False
    return bis


def trending_tail_bars():
    """造一段「早期向上、之后一路向下」的K线：`classify` 和 `trailing_trend` 必然分叉。

    5 个中枢，相邻关系依次是 up / down / down / down。按 17课 p36 的「完成的
    走势类型」口径（`zhongshu.classify`），只要有一对相邻中枢不同向，整体就
    判**盘整**；而末端那四个中枢依次下移，当下走势是**下跌**。

    真实行情上这不是特例是常态——实测 15 只票 × 日线/30分/5分，`classify`
    全部返回 panzheng。这个夹具只是把那件事压进 100 多根K线里，好让测试跑得动。
    """
    piv = []
    for c, w, n in [(100, 8, 3), (118, 8, 3), (70, 8, 3), (40, 8, 3), (14, 6, 3)]:
        piv += [float(c - w), float(c + w)] * n
    return zigzag(piv)
