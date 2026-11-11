"""单只股票的走查回测。

和 `scan/` 的区别：`scan/` 问「**今天**有没有信号」，这里问「这个信号**当时**
看得见吗、看见之后发生了什么」。后者要把历史重走一遍，每一步只许看那一步
之前的K线。

用法::

    python -m backtest 300308 --level day --zs bi
"""

from .score import HORIZONS, Outcome, base_rate, evaluate, summarize
from .walk import Sighting, walk_forward

__all__ = ["HORIZONS", "Outcome", "Sighting", "base_rate", "evaluate",
           "summarize", "walk_forward"]
