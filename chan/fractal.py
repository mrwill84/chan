"""分型。62课 p161 定义，82课 pp.211-212 强弱分级。

62课 p161 的定义只看高低点，**与阴阳、开盘收盘价完全无关**：
  顶分型 = 中间那根合并K线的高点是三者最高，且低点也是三者最高。
  底分型 = 镜像。

82课 pp.211-212 的强弱分级是另一回事，它**依赖开盘收盘价**，所以只能作为
标注输出，绝不能反过来当成分型的过滤条件——那会违反 62课 的定义。
"""

from __future__ import annotations

from dataclasses import dataclass

from .num import gt, lt
from .types import Bar, Fractal, FractalKind, MergedBar


def find_fractals(merged: list[MergedBar], bars: list[Bar] | None = None) -> list[Fractal]:
    """在合并K线序列上找出全部分型。

    同时检查高点与低点两个条件。对正常的合并序列（相邻非包含）两者等价，
    但在 gap_blocks_merge=True 时序列里可能残留包含关系，这时两个条件就
    不再等价，必须都查——漏查会造出 62课 定义之外的假分型。
    """
    out: list[Fractal] = []
    ts_of = _ts_lookup(bars)

    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]

        if gt(b.high, a.high) and gt(b.high, c.high) and gt(b.low, a.low) and gt(b.low, c.low):
            kind = FractalKind.TOP
        elif lt(b.low, a.low) and lt(b.low, c.low) and lt(b.high, a.high) and lt(b.high, c.high):
            kind = FractalKind.BOTTOM
        else:
            continue

        out.append(
            Fractal(
                kind=kind,
                m_idx=i,
                high=b.high,
                low=b.low,
                begin_idx=a.begin_idx,
                end_idx=c.end_idx,
                peak_idx=b.begin_idx if kind is FractalKind.TOP else b.end_idx,
                ts=ts_of(b.begin_idx),
            )
        )
    return out


def _ts_lookup(bars: list[Bar] | None):
    if not bars:
        return lambda _idx: ""
    by_idx = {b.idx: b.ts for b in bars}
    return lambda idx: by_idx.get(idx, "")


# --------------------------------------------------------------------------
# 82课 强弱标注。仅标注，不过滤。
# --------------------------------------------------------------------------


@dataclass(slots=True)
class StrengthConfig:
    """82课 的「长阳」「小阴小阳」「长上影」原文没给数值门槛，这里的默认值
    是我们自定的，已登记在 CONVENTIONS.md。改动它们不影响任何划线结果。
    """

    long_body_ratio: float = 0.6  # 实体 / 全幅 >= 此值算「长阳/长阴」
    small_body_ratio: float = 0.3  # 实体 / 全幅 <= 此值算「小阴小阳」
    long_shadow_ratio: float = 0.5  # 上/下影 / 全幅 >= 此值算「长上影/长下影」


def _body_ratio(bar: Bar) -> float:
    rng = bar.high - bar.low
    return abs(bar.close - bar.open) / rng if rng > 0 else 0.0


def _upper_shadow_ratio(bar: Bar) -> float:
    rng = bar.high - bar.low
    return (bar.high - max(bar.open, bar.close)) / rng if rng > 0 else 0.0


def _lower_shadow_ratio(bar: Bar) -> float:
    rng = bar.high - bar.low
    return (min(bar.open, bar.close) - bar.low) / rng if rng > 0 else 0.0


def annotate_strength(
    fractals: list[Fractal],
    merged: list[MergedBar],
    bars: list[Bar],
    cfg: StrengthConfig | None = None,
) -> None:
    """就地给分型打上 82课 的强弱标签。

    标签取值（**沿用原文用词，注意别望文生义**）：

    - ``"continuation"`` 中继型：顶分型里第一根是长阳、第二三根都是小阴小阳。
      原文 p212：「成为真正顶的可能性很小，绝大多数都是中继的」。
    - ``"strong"``：第二根是长上影甚至直接长阴，且第三根不能以阳线收在第二根
      区间的一半之上。原文 p212：「该顶分型的力度就比较大，最终要延续成笔的
      可能性就极大」。
    - ``"weakest"``：第三根跌破第一根的底，且不能高收到第一根区间的一半之上。
      原文 p212 原话是「属于**最弱**的一种，也就是说这顶分型有着较强的杀伤力」
      ——「最弱」说的是市场最弱，对顶分型而言这是**最强**的转折信号。标签直接
      沿用原文的「最弱」措辞，避免二次翻译走样。
    - ``"inclusive"``：三根里存在合并过的K线。原文 p212：「包含关系……意味着
      一种犹豫」。而且 ``"weakest"`` 那条原文明确限定「非包含关系处理后的」，
      所以一旦有合并就不判这档。
    - ``None``：以上都不符合。

    三档之间按 weakest > strong > continuation 的优先级取一个。
    """
    cfg = cfg or StrengthConfig()
    by_idx = {b.idx: b for b in bars}

    for f in fractals:
        trio = [merged[f.m_idx - 1], merged[f.m_idx], merged[f.m_idx + 1]]
        if any(m.is_merged for m in trio):
            f.strength = "inclusive"
            continue
        raw = [by_idx.get(m.begin_idx) for m in trio]
        if any(r is None for r in raw):
            f.strength = None
            continue
        k1, k2, k3 = raw  # type: ignore[misc]
        f.strength = (
            _top_strength(k1, k2, k3, cfg)
            if f.kind is FractalKind.TOP
            else _bottom_strength(k1, k2, k3, cfg)
        )


def _top_strength(k1: Bar, k2: Bar, k3: Bar, cfg: StrengthConfig) -> str | None:
    # 「第三根K线跌破第一根K线的底，而且不能高收到第一根K线区间的一半之上」
    k1_mid = (k1.high + k1.low) / 2
    if lt(k3.low, k1.low) and not gt(k3.close, k1_mid):
        return "weakest"

    # 「第二根是长上影甚至就是直接的长阴，而第三根不能以阳线收在第二根区间的一半之上」
    k2_mid = (k2.high + k2.low) / 2
    k2_heavy = _upper_shadow_ratio(k2) >= cfg.long_shadow_ratio or (
        k2.close < k2.open and _body_ratio(k2) >= cfg.long_body_ratio
    )
    if k2_heavy and not (k3.close > k3.open and gt(k3.close, k2_mid)):
        return "strong"

    # 「第一K线是一长阳线，而第二、三都是小阴、小阳」
    if (
        k1.close > k1.open
        and _body_ratio(k1) >= cfg.long_body_ratio
        and _body_ratio(k2) <= cfg.small_body_ratio
        and _body_ratio(k3) <= cfg.small_body_ratio
    ):
        return "continuation"
    return None


def _bottom_strength(k1: Bar, k2: Bar, k3: Bar, cfg: StrengthConfig) -> str | None:
    """底分型，82课 p211「而底分型的情况，反过来就是」。"""
    k1_mid = (k1.high + k1.low) / 2
    if gt(k3.high, k1.high) and not lt(k3.close, k1_mid):
        return "weakest"

    k2_mid = (k2.high + k2.low) / 2
    k2_heavy = _lower_shadow_ratio(k2) >= cfg.long_shadow_ratio or (
        k2.close > k2.open and _body_ratio(k2) >= cfg.long_body_ratio
    )
    if k2_heavy and not (k3.close < k3.open and lt(k3.close, k2_mid)):
        return "strong"

    if (
        k1.close < k1.open
        and _body_ratio(k1) >= cfg.long_body_ratio
        and _body_ratio(k2) <= cfg.small_body_ratio
        and _body_ratio(k3) <= cfg.small_body_ratio
    ):
        return "continuation"
    return None
