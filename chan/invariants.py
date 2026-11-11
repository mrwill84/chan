"""原文里那些「如果违反就一定是划错了」的陈述，全部写成可运行的断言。

这是四层验证策略里最有力的一层：它不依赖任何测试数据，划一次线就自检一次，
真实行情里的任何反例都会当场炸出来。

78课 p197 原文（`.source/book.txt` 第 1588 行）：

  「以向上笔开始的线段一定结束于向上笔，不可能一个线段，开始是向上笔，结束
    于一个向下笔。……一个线段，不可能是从底到底或从顶到顶。」

  「同一线段中，两端的一顶一底，顶肯定要高于底，**如果你划出一个不符合这基本
    要求的线段，那肯定是划错了**。」

  「由于图形不断延续，因此，除非是新股上市后最开始的一段，否则任何一段都是
    破坏前一段的……线段的破坏是可以**逆时间传递**的，也就是说被后线段破坏的
    线段，一定破坏前线段，如果违反这个原则，那线段的划分一定有问题。」

77课 p196：「线段中包含笔的数目，都是单数的。」

关于「逆时间传递」为什么没有单独的检查：67课 p172 明说「本课，就是把前面
『线段破坏的充要条件就是被另一个线段破坏』精确化了」——特征序列判据**就是**
线段被线段破坏的精确形式。所以只要每一段都是靠 `_try_end` 通过两种情况之一
确认的，并且段与段首尾相接、方向交替，逆时间传递就自动成立。原文没有给出
独立于特征序列判据之外的「线段破坏」操作定义，见 CONVENTIONS.md C-destroy。
"""

from __future__ import annotations

from .bi import pair_is_valid
from .num import gt
from .types import Bi, Direction, FractalKind, Leg, Seg, ZhongShu


class InvariantError(AssertionError):
    """划线结果违反了原文明确陈述的性质。这是实现 bug，不是数据问题。"""


def check_bis(bis: list[Bi], min_gap: int) -> None:
    for i, b in enumerate(bis):
        if b.start.kind is b.end.kind:
            raise InvariantError(f"笔[{i}] 两端同性质 {b.start.kind.name}")
        if not pair_is_valid(b.start, b.end, min_gap):
            raise InvariantError(
                f"笔[{i}] 违反间隔或价格约束："
                f"m_idx {b.start.m_idx}->{b.end.m_idx}, "
                f"价格 {b.start.price}->{b.end.price}"
            )
        if i:
            prev = bis[i - 1]
            if prev.end is not b.start:
                raise InvariantError(f"笔[{i}] 与笔[{i - 1}] 不首尾相接")
            if prev.direction is b.direction:
                raise InvariantError(f"笔[{i}] 与笔[{i - 1}] 方向相同")


def check_segs(segs: list[Seg], bis: list[Bi]) -> None:
    for s in segs:
        if not s.confirmed:
            continue  # 未完成线段不受这些性质约束
        _check_one_seg(s, bis)

    confirmed = [s for s in segs if s.confirmed]
    for prev, cur in zip(confirmed, confirmed[1:]):
        if prev.direction is cur.direction:
            raise InvariantError(
                f"线段[{cur.index}] 与线段[{prev.index}] 方向相同 {cur.direction.name}；"
                f"78课 p197：线段只能被反向线段破坏"
            )
        if cur.start_bi != prev.end_bi + 1:
            raise InvariantError(
                f"线段[{cur.index}] 与线段[{prev.index}] 不首尾相接："
                f"{prev.end_bi} -> {cur.start_bi}"
            )


def _check_one_seg(s: Seg, bis: list[Bi]) -> None:
    # 77课 p196：线段至少三笔
    if s.bi_count < 3:
        raise InvariantError(f"线段[{s.index}] 只有 {s.bi_count} 笔，不足三笔")

    # 77课 p196：线段中包含笔的数目都是单数
    if s.bi_count % 2 == 0:
        raise InvariantError(f"线段[{s.index}] 笔数 {s.bi_count} 是偶数")

    # 78课 p197：以向上笔开始的线段一定结束于向上笔
    if bis[s.start_bi].direction is not s.direction:
        raise InvariantError(f"线段[{s.index}] 起始笔方向与线段不符")
    if bis[s.end_bi].direction is not s.direction:
        raise InvariantError(
            f"线段[{s.index}] 以 {s.direction.name} 笔开始却结束于 "
            f"{bis[s.end_bi].direction.name} 笔"
        )

    # 78课 p197：不可能从底到底或从顶到顶
    if s.start.kind is s.end.kind:
        raise InvariantError(f"线段[{s.index}] 从 {s.start.kind.name} 到 {s.end.kind.name}")

    # 向上线段必须底起顶收，向下线段反之
    want_start = (
        FractalKind.BOTTOM if s.direction is Direction.UP else FractalKind.TOP
    )
    if s.start.kind is not want_start:
        raise InvariantError(
            f"线段[{s.index}] 方向 {s.direction.name} 却从 {s.start.kind.name} 开始"
        )

    # 78课 p197：两端的一顶一底，顶肯定要高于底
    top, bottom = (
        (s.end, s.start) if s.direction is Direction.UP else (s.start, s.end)
    )
    if not gt(top.price, bottom.price):
        raise InvariantError(
            f"线段[{s.index}] 顶 {top.price} 不高于底 {bottom.price}；"
            f"78课 p197：这肯定是划错了"
        )


def check_zhongshus(zss: list[ZhongShu], legs: list[Leg]) -> None:
    """相邻两个中枢之间**必须隔着至少一段**，那一段谁也不属于。

    33课 p79（`.source/book.txt` 第 630 行）：「整个走势其实就简化为**两个
    中枢与连接两者的一个走势**」——a+A+b+B+c 里的 b 是连接段，既不属于 A
    也不属于 B。

    为什么它非存在不可，有一个纯结构的理由，不依赖「走势必完美」那类大定理：
    下跌走势里的中枢是 **上下上**（以向上段开头），因为 a 是向下段，中枢由
    a 之后的三段重叠构成，而段是方向交替的。那么**把价格从前一个中枢带下来
    的那个向下段，方向上就不可能是后一个中枢的第一段**。上涨走势同理（中枢
    是 下上下，连接段向上）。所以同向的两个中枢之间必然隔着连接段，间隔为 0
    本身就证明这两个框划错了。

    违反这一条的来源只有一个，而且已经修掉了：`zhongshu_at` 原先有个 `capped`
    分支（33课 p79 的段数上限）会在中枢**还触及着区间**的时候把它截断，
    `depart_leg` 留空，于是 `next_start` 退化成 `end_leg + 1`，下一个中枢
    **就从那根本该当连接段的段上起步**。后果是双重的：

    1. 那一段同时是「还触及前一个中枢的段」和「后一个中枢的第一构成段」，
       后者的 ZD/ZG 里因此混进了连接段的端点；
    2. 新中枢的奇偶性是反的——它长成 下上下（上涨中枢的形态），却处在一段
       向下的走势里。

    这不是偶发：正常离开时 `next_start = depart_leg + 1`，新中枢的第一段紧挨
    连接段、必然与它反向，奇偶性自动成立；只有截断这一支必错。全缓存 66 组里
    64 组中招，相邻对约 24%。

    改成「不切断、只置 `level_up`」之后这条性质是**构造上**成立的：循环只有
    两个出口，两段连续不触及时 `depart_leg` 必有值、中间隔着离开段；走到数据
    尽头时 `misses <= 1`，`last_touch` 之后最多剩 1 段，凑不满 `MIN_LEGS`。
    所以这个函数现在是回归哨兵，不是会响的告警。

    判据用 `core_end` 不用 `end_leg`：`depart_leg == end_leg` 是合法的（第三
    段自己就飞走时它两种身份兼任，见 `ZhongShu.depart_leg` 的注释），拿
    `end_leg` 比会把这种正常情形也报出来。
    """
    for prev, cur in zip(zss, zss[1:]):
        if cur.start_leg > prev.core_end + 1:
            continue
        a, b = prev.index, cur.index
        d = legs[cur.start_leg].direction.name if cur.start_leg < len(legs) else "?"
        raise InvariantError(
            f"中枢[{a}] 与中枢[{b}] 之间没有连接段："
            f"中枢[{a}] 围绕震荡到段 {prev.core_end}，中枢[{b}] 就从段 "
            f"{cur.start_leg}（{d}）起步，中间一段都不剩。"
            f"33课 p79：两个中枢之间必须有连接它们的一个走势。"
            f"中枢[{a}] depart_leg={prev.depart_leg} level_up={prev.level_up}"
            + ("（8 段上限截断，这是唯一会造成本错误的分支）"
               if prev.depart_leg is None else "")
        )


def check_all(bis: list[Bi], segs: list[Seg], min_gap: int) -> None:
    check_bis(bis, min_gap)
    check_segs(segs, bis)
