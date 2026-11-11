"""笔。77课 p195。

原文（逐字，line 1557 / 1565-1567 of .source/book.txt）：

  「笔，必须是一顶一底，而且顶和底之间至少有一个 K 线不属于顶分型与底分型。
    当然，还有一个最显然的，就是在同一笔中，顶分型中最高那 K 线的区间至少要
    有一部分高于底分型中最低那 K 线的区间，如果这条都不满足，也就是顶都在低
    的范围内或顶比底还低，这显然是不可接受的。」

  划分步骤：
    一、确定所有符合标准的分型。
    二、如果前后两分型是同一性质的，对于顶，前面的低于后面的，只保留后面的；
        对于底，前面的高于后面的，只保留后面的。不满足上面情况的，例如相等
        的，都可以先保留。
    三、……在连续的顶后，必须会出现新的底，把这连续的顶中**最先一个**，和这
        新出现的底连在一起，就是新的一笔，而中间的那些顶，都 X 掉。

步骤二 + 步骤三合起来的净效果：一串同性质分型里取极值，**相等取最早**。

两个约束的数值化（推导写在这里是为了可审计）：

  间隔约束：顶在合并序列下标 i，占据 i-1, i, i+1；底在下标 j，占据 j-1, j, j+1。
    「不属于两个分型」的独立K线是 i+2 .. j-2，共 j-i-3 根。
    原文要求 >= 1 根  →  j - i >= 4   （bi_mode="old"）
    社区「新笔」只要求两分型不共用K线，允许 0 根独立K线  →  j - i >= 3
    「新笔」「老笔」「独立K线」这些词在全书 267 页里一次都没出现过，是 2008 年
    之后的社区约定。见 CONVENTIONS.md。

  价格约束：「顶分型中最高那K线的区间至少要有一部分高于底分型中最低那K线的
    区间」→ g_顶 > g_底。注意是两个**高点**比，不是 g_顶 > d_底。

为什么不能单向流式扫描：步骤三的约束失败会反过来推翻步骤二已经做出的取舍，
所以必须迭代到不动点。原文的唯一性证明本身就是靠「一个底不经过一个顶后就有
一个更低的底，这是最典型的笔没完成的情况」这种回溯论证走通的。
"""

from __future__ import annotations

from .num import gt, lt
from .types import Bi, Direction, Fractal, FractalKind, MergeConfig

#: 分型中心在合并序列上的最小间隔。见模块文档里的推导。
MIN_GAP = {"old": 4, "new": 3}


def min_gap_for(bi_mode: str) -> int:
    try:
        return MIN_GAP[bi_mode]
    except KeyError:
        raise ValueError(f"未知 bi_mode={bi_mode!r}，可选 {sorted(MIN_GAP)}") from None


def _more_extreme(a: Fractal, b: Fractal) -> bool:
    """b 相对 a 是否更极端（顶更高 / 底更低）。相等返回 False —— 相等保留最早。"""
    if a.kind is FractalKind.TOP:
        return gt(b.high, a.high)
    return lt(b.low, a.low)


def reduce_fractals(fractals: list[Fractal]) -> list[Fractal]:
    """步骤二 + 步骤三：把每一段连续同性质分型压成一个（取极值，相等取最早）。"""
    out: list[Fractal] = []
    for f in fractals:
        if out and out[-1].kind is f.kind:
            if _more_extreme(out[-1], f):
                out[-1] = f
            # 否则（更不极端、或相等）丢弃 f，保留已有的那个 —— 相等取最早
        else:
            out.append(f)
    return out


def pair_is_valid(a: Fractal, b: Fractal, min_gap: int) -> bool:
    """相邻一顶一底能否成笔：间隔约束 + 价格约束。"""
    if a.kind is b.kind:
        return False
    if b.m_idx - a.m_idx < min_gap:
        return False
    top, bottom = (a, b) if a.kind is FractalKind.TOP else (b, a)
    return gt(top.high, bottom.high)


def build_bi(
    fractals: list[Fractal], cfg: MergeConfig | None = None
) -> tuple[list[Bi], list[Fractal]]:
    """划笔。返回 (笔列表, 最终保留的分型列表)。

    迭代到不动点：压缩同性质 -> 检查相邻配对 -> 配对失败则丢弃**较晚**的那个
    分型 -> 回到压缩。丢弃较晚的一个是我们的约定（CONVENTIONS.md）：原文没有
    直接规定，但它和原文唯一性证明的从左到右推进方向一致——较早确认的结构在
    没有被「笔没完成」推翻之前保持有效。

    每轮至少删掉一个分型，所以一定终止。
    """
    cfg = cfg or MergeConfig()
    min_gap = min_gap_for(cfg.bi_mode)

    kept = list(fractals)
    while True:
        kept = reduce_fractals(kept)
        drop_at = None
        for i in range(len(kept) - 1):
            if not pair_is_valid(kept[i], kept[i + 1], min_gap):
                drop_at = i + 1
                break
        if drop_at is None:
            break
        kept.pop(drop_at)

    bis: list[Bi] = []
    for i in range(len(kept) - 1):
        a, b = kept[i], kept[i + 1]
        bis.append(
            Bi(
                index=i,
                start=a,
                end=b,
                direction=Direction.UP if a.kind is FractalKind.BOTTOM else Direction.DOWN,
                confirmed=True,
            )
        )

    # 末端笔天然未完成：它的终点分型还可能被后续更极端的同性质分型取代。
    # 77课 p195「笔没完成」。盘后扫描绝不能把它当成已确认信号。
    if bis:
        bis[-1].confirmed = False

    return bis, kept


def assert_bi_invariants(bis: list[Bi], min_gap: int) -> None:
    """划完笔的自检。违反说明实现有 bug。"""
    for i, b in enumerate(bis):
        if b.start.kind is b.end.kind:
            raise AssertionError(f"笔[{i}] 两端同性质 {b.start.kind}")
        if not pair_is_valid(b.start, b.end, min_gap):
            raise AssertionError(
                f"笔[{i}] 不满足间隔或价格约束: "
                f"m_idx {b.start.m_idx}->{b.end.m_idx}, "
                f"price {b.start.price}->{b.end.price}"
            )
        if i > 0:
            prev = bis[i - 1]
            if prev.end is not b.start:
                raise AssertionError(f"笔[{i}] 与笔[{i - 1}] 不首尾相接")
            if prev.direction is b.direction:
                raise AssertionError(f"笔[{i}] 与笔[{i - 1}] 方向相同 {b.direction}")
