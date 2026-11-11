"""给走查出来的买卖点算分。

三条规矩，每一条都是为了让结果**没法自己骗自己**：

1. **入场价用「第一次看见」那天的收盘价**，不用点自己的价。两者之差就是
   repaint 延迟的真实成本，这里把两种都算出来摆在一起——差额叫
   ``lookahead_premium``，它是「事后看图很准」和「当时能赚到」之间的距离。
2. **必须有基准。** 「1买 之后 10 天上涨的占 55%」这句话单独说毫无意义——
   要是这只票任意 10 天窗口有 58% 上涨，那 55% 是**负**的边际。所以每个口径
   都配一个同区间的无条件基准率，报的是 `edge = 命中率 - 基准率`。
3. **窗口不够的点要单独计数，不能悄悄丢掉。** 最近的信号恰恰是最想看的，
   而它们正是没有未来数据的那些。丢掉不说，样本就被悄悄挑过了。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from chan.types import Bar

from .walk import Sighting

#: 默认考察的持有期（K线根数）。日线上 5≈一周、10≈两周、20≈一个月。
HORIZONS = (5, 10, 20)


def _signed(side: str, ret: float) -> float:
    """买点看涨、卖点看跌。统一成「对信号有利为正」。"""
    return ret if side == "buy" else -ret


@dataclass(slots=True)
class Outcome:
    """一个买卖点的事后表现。"""

    sighting: Sighting
    entry_bar: int
    entry_price: float
    rets: dict[int, float]          # 持有期 -> 有利方向的收益率
    mfe: float                      # 最大有利偏移
    mae: float                      # 最大不利偏移
    naive_rets: dict[int, float]    # 作弊版：从点自己那天进场
    enough_future: bool             # 最长持有期的数据够不够


def evaluate(
    sightings: list[Sighting], bars: list[Bar], *, horizons=HORIZONS,
) -> list[Outcome]:
    """逐个算收益。``bars`` 必须是走查用的**同一串**原始K线。"""
    n = len(bars)
    hmax = max(horizons)
    out: list[Outcome] = []
    for s in sightings:
        e = s.first_seen
        ep = bars[e].close
        rets, naive = {}, {}
        for h in horizons:
            if e + h < n:
                rets[h] = _signed(s.side, bars[e + h].close / ep - 1.0)
            # 作弊版从点自己那根起算。它**看得见未来**，列出来只为对照。
            if s.bar + h < n:
                naive[h] = _signed(
                    s.side, bars[s.bar + h].close / bars[s.bar].close - 1.0)
        window = bars[e + 1: min(e + 1 + hmax, n)]
        if window and s.side == "buy":
            mfe = max(b.high for b in window) / ep - 1.0
            mae = min(b.low for b in window) / ep - 1.0
        elif window:
            mfe = 1.0 - min(b.low for b in window) / ep
            mae = 1.0 - max(b.high for b in window) / ep
        else:
            mfe = mae = 0.0
        out.append(Outcome(s, e, ep, rets, mfe, mae, naive,
                           enough_future=e + hmax < n))
    return out


def base_rate(bars: list[Bar], h: int, side: str = "buy") -> tuple[float, float]:
    """同一串K线上，**任取一天**进场持有 h 根的胜率和平均收益。

    这是所有命中率的分母。没有它，任何「准确率 60%」都不可解读。
    """
    rs = [_signed(side, bars[i + h].close / bars[i].close - 1.0)
          for i in range(len(bars) - h)]
    if not rs:
        return 0.0, 0.0
    return sum(r > 0 for r in rs) / len(rs), statistics.fmean(rs)


def summarize(
    outcomes: list[Outcome], bars: list[Bar], *, horizons=HORIZONS,
    group=lambda o: o.sighting.label,
) -> dict:
    """按 ``group`` 分组汇总。默认按「1买/2买/3买…」分。

    每组每个持有期给四个数：样本量、胜率、平均收益、**相对基准的边际**。
    """
    groups: dict[str, list[Outcome]] = {}
    skipped = 0
    for o in outcomes:
        if not o.enough_future:
            # 最近的信号没有未来数据。它们不是「表现不好」，是**还没有表现**。
            skipped += 1
            continue
        groups.setdefault(group(o), []).append(o)

    res: dict = {"groups": {}, "skipped_no_future": skipped,
                 "base": {}, "horizons": list(horizons)}
    for side in ("buy", "sell"):
        res["base"][side] = {
            h: dict(zip(("win_rate", "mean_ret"), base_rate(bars, h, side)))
            for h in horizons
        }

    for name, os_ in sorted(groups.items()):
        side = os_[0].sighting.side
        row: dict = {"n": len(os_),
                     # 基准是分方向的，读报告时要知道这一组该比哪一个
                     "side": side,
                     "mean_lag": statistics.fmean(o.sighting.lag for o in os_),
                     "survived": sum(o.sighting.survived for o in os_),
                     "stable": sum(o.sighting.stable for o in os_),
                     "mean_mfe": statistics.fmean(o.mfe for o in os_),
                     "mean_mae": statistics.fmean(o.mae for o in os_),
                     "h": {}}
        for h in horizons:
            rs = [o.rets[h] for o in os_ if h in o.rets]
            if not rs:
                continue
            win = sum(r > 0 for r in rs) / len(rs)
            bw, bm = base_rate(bars, h, side)
            nv = [o.naive_rets[h] for o in os_ if h in o.naive_rets]
            row["h"][h] = {
                "n": len(rs),
                "win_rate": win,
                "mean_ret": statistics.fmean(rs),
                "edge_win": win - bw,
                "edge_ret": statistics.fmean(rs) - bm,
                # 作弊版减真实版。这个数**越大，说明「事后看图」骗得越狠**。
                "lookahead_premium": (
                    statistics.fmean(nv) - statistics.fmean(rs) if nv else None),
            }
        res["groups"][name] = row
    return res
