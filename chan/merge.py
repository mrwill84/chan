"""包含关系处理。65课 p168。

原文要点（逐条对应下面的实现）：

1. 用区间记法 [di, gi] 描述第 i 根K线的低点与高点。
2. 包含 = 一根的区间完全覆盖另一根（谁包含谁都算）。
3. 向上合并取 [max d, max g]，向下合并取 [min d, min g]。
4. **包含关系不满足传递律**，必须顺序处理：先合 1-2 得到新K线，再拿这根
   新K线去比第 3 根。不能预先算出所有两两包含关系再统一合并。
5. 方向判定：第 n 根与 n+1 包含、且 n 与 n-1 不包含时，gn >= gn-1 为向上，
   dn <= dn-1 为向下。

原文空白（见 CONVENTIONS.md）：
- 起手方向：第一根K线没有 n-1，方向无定义。
- 跳空是否阻断合并：原文**一个字都没写**。
"""

from __future__ import annotations

from .num import ge, gt, le
from .types import Bar, Direction, MergeConfig, MergedBar


def contains(a_low: float, a_high: float, b_low: float, b_high: float) -> bool:
    """区间 a 是否包含区间 b（a 完全覆盖 b，含端点相等）。"""
    return le(a_low, b_low) and ge(a_high, b_high)


def has_inclusion(a_low: float, a_high: float, b_low: float, b_high: float) -> bool:
    """两区间是否存在包含关系（任一方向）。"""
    return contains(a_low, a_high, b_low, b_high) or contains(b_low, b_high, a_low, a_high)


def has_gap(prev: Bar, cur: Bar) -> bool:
    """两根**原始**K线之间是否存在跳空缺口。77课 p194：无成交的价格区间。

    注意这和 78课 p199 的「特征序列缺口」是同名不同物的两个概念，
    后者在 seg.py 里叫 SeqGap，切勿混用。
    """
    return gt(cur.low, prev.high) or gt(prev.low, cur.high)


def _initial_start(bars: list[Bar], cfg: MergeConfig) -> int:
    """决定从第几根原始K线开始建立合并序列。

    "drop"：丢弃开头互相包含的K线，从第一对非包含的相邻K线起算。这样
    merged[1] 一定存在且与 merged[0] 非包含，后续任何一次合并都能从
    merged[-2] 取到合法的方向参照，不需要任何凭空假设。
    """
    if cfg.initial_direction != "drop":
        return 0
    n = len(bars)
    for i in range(n - 1):
        a, b = bars[i], bars[i + 1]
        if not has_inclusion(a.low, a.high, b.low, b.high):
            return i
    return max(n - 1, 0)


def _forced_direction(cfg: MergeConfig) -> Direction:
    return Direction.DOWN if cfg.initial_direction == "down" else Direction.UP


def merge_bars(bars: list[Bar], cfg: MergeConfig | None = None) -> list[MergedBar]:
    """对原始K线序列做包含处理，返回合并后的序列。

    保证：返回序列中任意相邻两根都**不存在**包含关系。这是分型判定的前提。
    """
    cfg = cfg or MergeConfig()
    if not bars:
        return []

    start = _initial_start(bars, cfg)
    first = bars[start]
    merged: list[MergedBar] = [
        MergedBar(
            seq=0,
            low=first.low,
            high=first.high,
            direction=_forced_direction(cfg),
            begin_idx=first.idx,
            end_idx=first.idx,
            merged_count=1,
        )
    ]
    last_raw = first

    for b in bars[start + 1 :]:
        cur = merged[-1]
        blocked = cfg.gap_blocks_merge and has_gap(last_raw, b)

        if not blocked and has_inclusion(cur.low, cur.high, b.low, b.high):
            # 方向取自 merged[-2]，而不是取自被合并的两根本身。
            if len(merged) >= 2:
                prev = merged[-2]
                d = Direction.UP if gt(cur.high, prev.high) else Direction.DOWN
            else:
                d = _forced_direction(cfg)

            if d is Direction.UP:
                cur.low = max(cur.low, b.low)
                cur.high = max(cur.high, b.high)
            else:
                cur.low = min(cur.low, b.low)
                cur.high = min(cur.high, b.high)
            cur.direction = d
            cur.end_idx = b.idx
            cur.merged_count += 1
        else:
            merged.append(
                MergedBar(
                    seq=len(merged),
                    low=b.low,
                    high=b.high,
                    direction=Direction.UP if gt(b.high, cur.high) else Direction.DOWN,
                    begin_idx=b.idx,
                    end_idx=b.idx,
                    merged_count=1,
                )
            )
        last_raw = b

    return merged


def assert_no_inclusion(merged: list[MergedBar]) -> None:
    """自检：合并后序列不允许残留任何相邻包含关系。

    这条违反了说明合并实现有 bug，而不是数据问题，所以直接抛。
    """
    for i in range(len(merged) - 1):
        a, b = merged[i], merged[i + 1]
        if has_inclusion(a.low, a.high, b.low, b.high):
            raise AssertionError(
                f"合并后仍存在包含关系 seq={a.seq}[{a.low},{a.high}] "
                f"seq={b.seq}[{b.low},{b.high}]"
            )
