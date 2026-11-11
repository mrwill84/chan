"""缠论确定性划线引擎。

设计前提：**划线是确定性几何算法，不交给 LLM 做。** 本包只做计算，不碰 IO、
不碰网络。LLM 只负责读本包输出的结构化结果做研判。

所有原文没写死、由我们自己定的规则，都登记在仓库根的 CONVENTIONS.md 里。
"""

from .ambiguity import (
    Grouping,
    Reading,
    Run,
    enumerate_groupings,
    enumerate_readings,
    is_ambiguous,
    last_run_differs,
)
from .beichi import Beichi, detect, detect_all, detect_at
from .bi import build_bi, min_gap_for, reduce_fractals
from .buysell import BSPoint, check_bspoints, find_bspoints, latest
from .fractal import annotate_strength, find_fractals
from .invariants import InvariantError, check_all, check_bis, check_segs
from .macd import Macd, macd
from .merge import merge_bars
from .multilevel import (
    AGREE,
    CONFLICT,
    INCOMPLETE,
    LevelAnalysis,
    Nesting,
    Step,
    analyze_level,
    nest,
)
from .num import EPS
from .pipeline import Analysis, analyze
from .seg import build_segs, confirmed_segs
from .types import (
    Bar,
    Bi,
    Direction,
    Fractal,
    FractalKind,
    Leg,
    MergeConfig,
    MergedBar,
    Seg,
    SeqElem,
    ZhongShu,
)
from .zhongshu import (
    build_zhongshus,
    classify,
    legs_from_bis,
    legs_from_segs,
    relation,
)

__all__ = [
    "AGREE",
    "Analysis",
    "analyze",
    "analyze_level",
    "annotate_strength",
    "Bar",
    "Beichi",
    "Bi",
    "BSPoint",
    "build_bi",
    "build_segs",
    "build_zhongshus",
    "check_all",
    "check_bis",
    "check_bspoints",
    "check_segs",
    "classify",
    "confirmed_segs",
    "CONFLICT",
    "detect",
    "detect_all",
    "detect_at",
    "Direction",
    "enumerate_groupings",
    "enumerate_readings",
    "EPS",
    "find_bspoints",
    "find_fractals",
    "Fractal",
    "FractalKind",
    "Grouping",
    "INCOMPLETE",
    "InvariantError",
    "is_ambiguous",
    "last_run_differs",
    "latest",
    "Leg",
    "legs_from_bis",
    "legs_from_segs",
    "LevelAnalysis",
    "macd",
    "Macd",
    "merge_bars",
    "MergeConfig",
    "MergedBar",
    "min_gap_for",
    "nest",
    "Nesting",
    "Reading",
    "reduce_fractals",
    "relation",
    "Run",
    "Seg",
    "SeqElem",
    "Step",
    "ZhongShu",
]
