"""统一的浮点比较。

64课 p166 要求「精度一旦预设，就一路保持」，81课 p210 作者自己因为 0.08 的
差值划错过线段。整个引擎里**所有**价格的 > / < / == 都必须走这里，不允许
裸比较，否则不同模块的容差不一致会产生自相矛盾的划分。

默认 EPS = 1e-6，远小于 A 股最小报价单位 0.01，因此对齐到 tick 的价格之间
是精确比较；EPS 只用来吸收前复权换算引入的浮点尾差。
"""

from __future__ import annotations

EPS = 1e-6


def gt(a: float, b: float, eps: float = EPS) -> bool:
    """a 严格大于 b。"""
    return a - b > eps


def lt(a: float, b: float, eps: float = EPS) -> bool:
    """a 严格小于 b。"""
    return b - a > eps


def eq(a: float, b: float, eps: float = EPS) -> bool:
    """a 与 b 在容差内相等。"""
    return abs(a - b) <= eps


def ge(a: float, b: float, eps: float = EPS) -> bool:
    """a 大于或在容差内等于 b。"""
    return a - b > -eps


def le(a: float, b: float, eps: float = EPS) -> bool:
    """a 小于或在容差内等于 b。"""
    return b - a > -eps


def overlap(lo1: float, hi1: float, lo2: float, hi2: float, eps: float = EPS) -> bool:
    """两个闭区间是否有重叠（含仅一点接触）。

    中枢判定要用到。18课 p39 / 20课 p42 强调趋势中前后中枢「绝对不能有任何
    重叠，包括瞬间波动」，所以接触也算重叠，这里用 ge/le 而不是 gt/lt。
    """
    return ge(hi1, lo2, eps) and ge(hi2, lo1, eps)


def fp(x: float) -> str:
    """数值格式化，给**人**和**本地模型**读的理由字符串用（价格、MACD 面积）。

    不能用 `{:.4g}`：后复权的 A 股价格常是五位数，`.4g` 会打成 `1.015e+04`，
    而模型读到科学计数法就很容易把量级看错——600519 的后复权价在一万上下，
    「1.015e+04」和「10150.00」在一段中文理由里的可读性差得很远。

    也不能一律 `{:.2f}`：5 分钟级别上会出现 0.001 量级的比较，两位小数全成 0。
    所以按量级切：一块钱以上给两位小数，以下保四位有效数字。
    """
    if abs(x) >= 1:
        return f"{x:.2f}".rstrip("0").rstrip(".")
    return f"{x:.4g}"
