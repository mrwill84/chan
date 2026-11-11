"""把各层串起来：K线 → 包含处理 → 分型 → 笔 → 线段。

每一层都是纯函数，这里只负责按顺序调用并做自检。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import invariants
from .bi import build_bi, min_gap_for
from .fractal import annotate_strength, find_fractals
from .merge import merge_bars
from .seg import build_segs
from .types import Bar, Bi, Fractal, MergeConfig, MergedBar, Seg


@dataclass(slots=True)
class Analysis:
    cfg: MergeConfig
    bars: list[Bar]
    merged: list[MergedBar]
    fractals: list[Fractal]  # 压缩后实际成笔的分型
    raw_fractals: list[Fractal]  # 压缩前的全部分型
    bis: list[Bi]
    segs: list[Seg] = field(default_factory=list)

    @property
    def confirmed_segs(self) -> list[Seg]:
        return [s for s in self.segs if s.confirmed]

    @property
    def last_bi_is_provisional(self) -> bool:
        return bool(self.bis) and not self.bis[-1].confirmed


def analyze(
    bars: list[Bar],
    cfg: MergeConfig | None = None,
    *,
    check: bool = True,
    strength: bool = False,
    sub_points: list[tuple[str, bool]] | None = None,
) -> Analysis:
    """走完划线全流程。

    check=True 时跑 78课/77课 的不变量自检，违反直接抛 ``InvariantError``
    ——那是实现 bug，不是数据问题，不应该被静默吞掉。

    strength=True 时附带 82课 的分型强弱标注。它只写字段、不改划分，
    批量扫描时没必要开。

    sub_points 只在 ``cfg.seg_end == "sublevel_1bs"`` 时有用：调用方在**同一
    时刻**用次级别K线划出来的1类点 ``(时间戳, 是否买点)``，见 `chan.segend`。
    """
    cfg = cfg or MergeConfig()
    merged = merge_bars(bars, cfg)
    raw_fractals = find_fractals(merged, bars)
    if strength:
        annotate_strength(raw_fractals, merged, bars)
    bis, kept = build_bi(raw_fractals, cfg)
    segs = build_segs(bis, cfg)

    # 「当下判断末段已经结束」的可选口径（`chan.segend`）。默认 None，这一支
    # 一行都不执行，划线结果与历史逐字节一致——所有既有实测数字都建在那上面。
    if cfg.seg_end:
        from .macd import macd as _macd
        from .segend import split_tail

        segs = split_tail(segs, bis, _macd([b.close for b in bars]), cfg.seg_end,
                          bars=bars, ma_filter=cfg.seg_end_ma_filter,
                          sub_points=sub_points)

    if check:
        invariants.check_all(bis, segs, min_gap_for(cfg.bi_mode))

    return Analysis(
        cfg=cfg,
        bars=bars,
        merged=merged,
        fractals=kept,
        raw_fractals=raw_fractals,
        bis=bis,
        segs=segs,
    )
