"""走势多义性。33课 p79、36课 pp.86-88。

这一层回答一个设计问题：**哪些事情该由程序定死，哪些该交给模型判断。**

原文把界限划得很清楚。

一、多义性只出现在中枢这一层（33课 p79，`.source/book.txt` 第 625 行）：

  「所有走势的多义性，都与中枢有关。」

  笔和线段没有多义性——77课 p195 给了笔的唯一性证明，67课 p172 给了线段的
  精确定义。所以 `bi.py` / `seg.py` 是确定性的，不该有任何「读法」之说。

二、多义性不是含糊性（36课 p88，第 670 行）：

  「多义性不是含糊性，含糊性是因为不精确、不严格造成的，而多义性，是站在
    一个严格、精确的理论基础上，用同一理论的**不同视角**对同一现象进行分析。」

  所以合法读法的**集合是有限的、可枚举的**，程序完全算得出来。模型要做的是
  在这个集合里**选**，而不是自由发挥。这是本模块存在的理由。

三、其中一类多义性原文直接给了消除规则（33课 p79，第 626 行）：

  「只要对中枢延伸的数量进行限制，就可以消除多义性，一般来说，中枢的延伸
    不能超过 5 段，也就是一旦出现 6 段的延伸，加上形成中枢本身那三段，就
    构成更大级别的中枢了。」

  已编码为 `zhongshu.MAX_LEGS` 与 `ZhongShu.level_up`。

四、剩下的那类，原文说要靠人（36课 p87，第 673、678 行）：

  「具体的组合有很多可能，如何根据当下的走势选择一种最有利指导操作的，
    就是**考功夫的事情**。」
  「组合的一个要点在于，**尽量避繁就简**，因为中枢扩展比较复杂，如果有组合
    使得不出现扩展，当然就采取该种组合更有意义。」

  「避繁就简」是可以打分的，所以 `Reading.score` 给出排序建议；但原文说的是
  「最有利指导操作」，这依赖当下持仓和意图，所以**只排序、不替人决定**。

五、选定之后必须一以贯之（36课 p87，第 674 行）：

  「当下采取什么组合，就要按该种组合的具体图形意义来判断、操作。」

  工程含义：买卖点必须跟着被选中的那一种读法算，不能 A 读法算中枢、B 读法
  算买点。`Reading` 把中枢和由它推出的结论绑在同一个对象里就是为了这个。

六、这种重新组合**不涉及任何预测**（36课 p87，第 678 行）。枚举出来的每一种
   读法都只是对**已走出来**的走势的不同切分，不是对未来的猜测。

原文的实例（36课 p87，第 677-678 行）：同一段走势，中枢可以读成
2911-2892（从 03071330 起），也可以读成 2947-2905（从 03091030 起），
**两者给出的第三类买点位置不同**。这正是本模块要摊开给模型看的东西。

---

## 多义性有两层，这个模块两层都要枚举

**第一层：中枢本身怎么划**（`Reading` / `enumerate_readings`）。起点往后挪
0~2 段，就是上面 36课 p87 那个实例。

**第二层：划好的中枢串怎么归成走势类型**（`Grouping` / `enumerate_groupings`）。
17课 p36 只说「一个中枢是盘整、两个以上依次同向不重叠的中枢是趋势」——它
**没有规定遇到一串同向中枢时必须全并成一个趋势**。三个依次同向的中枢
z0 z1 z2，下面四种切法在原文定义下全部合法：

    [z0 z1 z2]    [z0 z1][z2]    [z0][z1 z2]    [z0][z1][z2]

这一层从前是漏的，而且漏得很具体：`tree._greedy_groups` 取最长同向串（上面
第一种），`forward.trailing_trend` 从末枢往回捋（可能给出第三种），两者分歧时
`scan.report` 硬挂一个 `alt_reading` 把 trailing 那一种单独摆出来。实测 30 只
缓存日线票里 4 只分歧、3 只因此 `kind` 判反。但「恰好这两种」不是枚举，只是
两个实现碰巧各有一个答案——中间那些切法一个都没露面。

**排序为什么仍然把 greedy 排第一。** 36课 p87 的「避繁就简」在这一层等于
「走势类型个数越少越简」，并列时再按**从左往右**定下来的那一种优先：走势
类型的边界是 72课 的背驰点，而背驰点是随时间走出来的，不能回头。trailing
那种从末尾往回捋的读法允许一个**已经走完**的趋势被后来的走势借走一个中枢，
同一个中枢同时属于两个走势类型——合法归合法（它确实是上面四种之一），但
排不到第一。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Leg, ZhongShu
from .zhongshu import (
    AMBIGUOUS,
    MAX_LEGS,
    MIN_LEGS,
    PANZHENG,
    TREND_DOWN,
    TREND_UP,
    classify,
    relation,
    usable_count,
    zhongshu_at,
)

# 每一层中枢起点最多往后挪几段。挪 MIN_LEGS 段意味着把一组本来重叠的段整个
# 扔掉不解释，那不是另一种读法，只是漏划，所以上限取 MIN_LEGS - 1 = 2。
MAX_SHIFT = MIN_LEGS - 1

# 枚举上限。分支因子 3、深度等于中枢个数，不设上限会指数爆炸。
# **这个封顶实测 50/50 次次卡住**，回的是采样不是全集，详见 `enumerate_readings`。
MAX_READINGS = 12

# 走势类型切分的枚举上限。一串 n 个依次同向的中枢有 2^(n-1) 种切法，n 不大
# 但几串连乘仍然会炸，所以照样要封顶。
MAX_GROUPINGS = 8


@dataclass(slots=True)
class Reading:
    """一种完整的中枢划法，以及由它直接推出的结论。

    36课 p87：「当下采取什么组合，就要按该种组合的具体图形意义来判断、操作。」
    所以中枢、走势类型、以及后续的买卖点都必须绑在同一个 Reading 上算。
    """

    zhongshus: list[ZhongShu]
    trend: str
    offsets: list[int] = field(default_factory=list)  # 每层中枢相对最早位置挪了几段
    orphan_legs: int = 0  # 没有被任何中枢解释的段数
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[tuple[int, int], ...]:
        return tuple((z.start_leg, z.end_leg) for z in self.zhongshus)

    @property
    def has_level_up(self) -> bool:
        """是否有中枢顶到了 33课 p79 的延伸上限（该升级别看了）。"""
        return any(z.level_up for z in self.zhongshus)

    @property
    def has_extension(self) -> bool:
        """是否出现延伸（中枢超过 3 段）。36课 p87 的「避繁就简」针对的就是它。"""
        return any(z.leg_count > MIN_LEGS for z in self.zhongshus)

    @property
    def ambiguous_links(self) -> int:
        """相邻中枢关系落在中心定理二三种结论之外的个数。"""
        return sum(
            1
            for a, b in zip(self.zhongshus, self.zhongshus[1:])
            if relation(a, b) == AMBIGUOUS
        )

    @property
    def score(self) -> float:
        """排序用的「避繁就简」分，越小越简。**只是建议，不是判决。**

        36课 p87 的原话是「如何根据当下的走势选择一种**最有利指导操作**的，
        就是考功夫的事情」——最有利与否依赖持仓和意图，程序不知道，所以这里
        只把「繁 / 简」这一个可量化的维度算出来。
        """
        return (
            4.0 * self.has_level_up  # 该升级别却按同级别读，最不可取
            + 2.0 * self.ambiguous_links  # 中枢关系说不清
            + 1.0 * self.has_extension  # 原文点名的「比较复杂」
            + 0.5 * self.orphan_legs  # 解释不了的段越多越差
            + 0.1 * sum(self.offsets)  # 同分时取最早起手
        )


def _walk(
    legs: list[Leg],
    usable: int,
    i: int,
    max_legs: int,
    acc: list[ZhongShu],
    offsets: list[int],
    out: list[Reading],
    budget: int,
) -> None:
    """从第 i 段往后递归枚举。每层允许中枢起点在最早位置上后挪 0..MAX_SHIFT 段。

    ``budget`` 是**搜索**预算，和调用方要几条（`limit`）是两件事。早先这里
    直接读模块全局 `MAX_READINGS`，于是 `enumerate_readings(legs, limit=100)`
    照样只回 12 条——`limit` 是个摆设。`_walk_groups` 一直是按参数收的，
    两个姐妹函数不该一个认参数一个认全局。
    """
    if len(out) >= budget:
        return

    # 先定位本层最早能成中枢的位置
    base = i
    while base + MIN_LEGS <= usable and zhongshu_at(legs, base, usable, max_legs=max_legs) is None:
        base += 1

    if base + MIN_LEGS > usable:  # 后面再也凑不出中枢了，这条读法到此为止
        zss = list(acc)
        out.append(
            Reading(
                zhongshus=zss,
                trend=classify(zss) if zss else PANZHENG,
                offsets=list(offsets),
                orphan_legs=_orphans(zss, usable),
            )
        )
        return

    seen: set[tuple[int, int]] = set()
    for shift in range(MAX_SHIFT + 1):
        zs = zhongshu_at(legs, base + shift, usable, index=len(acc), max_legs=max_legs)
        if zs is None:
            continue
        span = (zs.start_leg, zs.end_leg)
        if span in seen:
            continue
        seen.add(span)
        acc.append(zs)
        offsets.append(shift)
        _walk(legs, usable, zs.next_start, max_legs, acc, offsets, out, budget)
        offsets.pop()
        acc.pop()
        if len(out) >= budget:
            return


def _orphans(zss: list[ZhongShu], usable: int) -> int:
    covered = sum(z.leg_count for z in zss)
    return max(0, usable - covered)


def enumerate_readings(
    legs: list[Leg],
    *,
    require_confirmed: bool = True,
    max_legs: int = MAX_LEGS,
    limit: int = MAX_READINGS,
) -> list[Reading]:
    """枚举这段走势的中枢读法，按「避繁就简」排序。**是采样，不是穷举。**

    早先这里写的是「枚举**全部**合法的中枢读法」「返回的第一条就是程序推荐的
    读法」。两句都不成立，2026-10-21 在 25~30 只缓存日线票上测过：

    | | 笔 | 段 |
    |---|---|---|
    | 合法读法总数（中位） | ≥ 10⁶（跑到 10⁶ 仍未枚举完） | 8262（最少 27，最多 629856）|
    | `MAX_READINGS=12` 卡住的次数 | 25/25 | 25/25 |

    **50/50 次次卡住**，所以「全部」从来没有发生过；`len(result) == 1`
    仍然可信（真的只有一条时 DFS 是走完的），但它几乎不出现。

    更要紧的是截断发生在**去重和按 `score` 排序之前**（`_walk` 里收满就返回），
    于是回的第一条是「DFS 前 `budget` 条里最好的」，不是全局最好的。把搜索
    预算从 12 提到 5000 再比：

    - **9/60 条序列的首选读法直接变了**，且 9 条的 `score` 全都变小——
      原来那条按引擎自己的尺子就是更差的一条。
    - 预算 12 回的 12 条里，中位只有 **4 条**还留在预算 5000 的前 12 里。

    照 DFS 的形状，前 12 条只在**最后 3 个中枢**上分叉（实测 varying 位置
    形如 `[115,116,117]`，而笔级别中枢串中位有 72 个），靠前的位置永远锁在
    贪心读法上。和 `enumerate_groupings` 一样，分叉集中在尾巴上这件事本身是
    有用的那一半——买卖点挂在当下那个中枢上。**但两者有一处关键不同**：
    `Grouping.sort_key` 比的是尾段，而 `Reading.score` 是**全局**的
    （`has_extension`/`orphan_legs` 都遍历整串），拿全局分去排一组「只有尾巴
    不同、前面全都锁死」的样本，就是上面那 9/60 的来源。

    **注意：本函数目前没有任何生产调用方**（只有 `chan/__init__` 的再导出和
    `tests/test_stress.py`）。`scan/report.py` 走的是 `build_zhongshus` 的贪心
    单一读法，再在其上用 `enumerate_groupings` 枚举走势类型切分。也就是说
    C-amb 的「程序枚举中枢读法、模型选择」这一层**还没有接进管线**——要接之前
    先解决上面的排序问题，别把一个 12/8262 的样本当成「全部读法」交给模型。

    ``limit`` 是要几条，搜索预算同时取 ``max(limit, MAX_READINGS)``。
    """
    usable = usable_count(legs, require_confirmed)
    out: list[Reading] = []
    _walk(legs, usable, 0, max_legs, [], [], out, max(limit, MAX_READINGS))

    uniq: dict[tuple, Reading] = {}
    for r in out:
        uniq.setdefault(r.key, r)
    readings = sorted(uniq.values(), key=lambda r: r.score)
    for r in readings:
        r.notes = _notes(r)
    return readings[:limit]


def _notes(r: Reading) -> list[str]:
    """给模型看的人话提示，每条都对应原文一处明确说法。"""
    notes: list[str] = []
    if r.has_level_up:
        notes.append(
            "有中枢延伸到了 9 段，33课 p79：「一旦出现 6 段的延伸，加上形成中枢"
            "本身那三段，就构成更大级别的中枢了」——这里应当升一个级别去看。"
        )
    if r.has_extension:
        notes.append("存在中枢延伸。36课 p87：「尽量避繁就简」，有不延伸的组合时优先取那个。")
    if r.ambiguous_links:
        notes.append(
            f"有 {r.ambiguous_links} 对相邻中枢的关系不落在中心定理二的三种结论里，"
            "既不构成趋势也不构成更大级别中枢，需要人工判断级别。"
        )
    if r.orphan_legs:
        notes.append(f"有 {r.orphan_legs} 段走势不属于任何中枢（走势的首尾段，正常现象）。")
    if not notes:
        notes.append("这条读法没有延伸、没有歧义关系，是最干净的一种。")
    return notes


def is_ambiguous(readings: list[Reading]) -> bool:
    """这段走势在中枢层是否存在真实的多义性。"""
    return len(readings) > 1


# --------------------------------------------------------------------------
# 第二层：划好的中枢串怎么归成走势类型（见模块文档串的「多义性有两层」）
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Run:
    """一个走势类型在**中枢序列**里占的闭区间。``kind`` 是 17课 p36 的三分。

    下标是中枢下标，不是 leg 下标也不是K线下标——这一层只管「哪几个中枢归成
    一个走势类型」，落回时间轴是 `chan.tree.trend_bars` 的事。
    """

    kind: str
    start: int
    end: int

    @property
    def count(self) -> int:
        return self.end - self.start + 1


@dataclass(slots=True)
class Grouping:
    """一种走势类型切分：把整串中枢切成首尾相接、互不重叠的若干走势类型。

    「首尾相接、互不重叠」不是实现选择，是 72课 的那句话——「前后两个走势
    类型，就以该背驰点为分界」，分界是一个点，所以每个中枢**恰好**属于一个
    走势类型。`forward.trailing_trend` 违反的正是这一条。
    """

    runs: list[Run]
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[tuple[int, int], ...]:
        return tuple((r.start, r.end) for r in self.runs)

    @property
    def last(self) -> Run | None:
        """当下所在的走势类型。买卖点挂在它身上，所以它是读法之间最要紧的差别。"""
        return self.runs[-1] if self.runs else None

    @property
    def n_trends(self) -> int:
        return sum(1 for r in self.runs if r.kind != PANZHENG)

    @property
    def score(self) -> float:
        """「避繁就简」在这一层 = 走势类型个数越少越简（36课 p87）。"""
        return float(len(self.runs))

    @property
    def sort_key(self) -> tuple[float, tuple[int, ...]]:
        """并列时取**从左往右**先定下来的那一种，理由见模块文档串。

        ``-r.end`` 让「靠前的走势类型尽量长」排在前，于是
        `tree._greedy_groups` 给出的那一种恒为第一条——主口径不会因为加了
        这个枚举而悄悄换人。
        """
        return (self.score, tuple(-r.end for r in self.runs))


def _runs_at(zss: list[ZhongShu], i: int) -> list[Run]:
    """从第 i 个中枢起能构成的全部走势类型，**长的排在前**。

    长的在前不是美观问题：`limit` 是在深度优先的过程中截断的，先生成全单枢
    盘整的话，截断之后 greedy 那一种根本轮不到出场，首选就被换掉了。
    """
    out = [Run(PANZHENG, i, i)]   # 单枢盘整永远合法
    kind: str | None = None
    j = i
    while j + 1 < len(zss):
        r = relation(zss[j], zss[j + 1])
        if r not in (TREND_UP, TREND_DOWN):
            break
        if kind is None:
            kind = r
        elif r != kind:
            break   # 方向一变就不再是「依次同向」
        j += 1
        out.append(Run(kind, i, j))
    out.reverse()
    return out


def _walk_groups(zss: list[ZhongShu], i: int, acc: list[Run],
                 out: list[Grouping], limit: int) -> None:
    if len(out) >= limit:
        return
    if i >= len(zss):
        out.append(Grouping(runs=list(acc)))
        return
    for run in _runs_at(zss, i):
        acc.append(run)
        _walk_groups(zss, run.end + 1, acc, out, limit)
        acc.pop()
        if len(out) >= limit:
            return


def _group_notes(g: Grouping, first: Grouping) -> list[str]:
    notes: list[str] = []
    last = g.last
    if last is not None:
        notes.append(
            f"当下所在的走势类型：{last.kind}，含 {last.count} 个中枢"
            f"（中枢 {last.start}..{last.end}）。"
        )
    if g.key == first.key:
        notes.append("这是「避繁就简」排序下的首选读法（36课 p87）。")
    elif len(g.runs) > len(first.runs):
        notes.append(
            f"比首选多切出 {len(g.runs) - len(first.runs)} 个走势类型——"
            "17课 p36 没有禁止把一串同向中枢再切细，但 36课 p87 说「尽量避繁就简」。"
        )
    elif g.last != first.last:
        notes.append(
            "走势类型个数和首选一样多，分歧在边界落在哪里：这一种把靠后的走势"
            "类型拉长了，等于让一个已经走完的趋势交出一个中枢（72课 的分界是一"
            "个点，中枢只能属于一个走势类型）。合法，但不是从左往右先定下来的那种。"
        )
    if g.n_trends == 0 and len(g.runs) > 1:
        notes.append("这种切法下全程没有趋势，每个中枢各自成一个盘整。")
    return notes


def enumerate_groupings(zss: list[ZhongShu], *,
                        limit: int = MAX_GROUPINGS) -> list[Grouping]:
    """枚举这串中枢合法的走势类型切分，按「避繁就简」排序。

    第一条恒等于 `tree._greedy_groups` 的结果（见 `Grouping.sort_key`），所以
    这个函数只**增加**备选，不改主口径。实测 30 只缓存日线票 × bi/seg 共 60
    条中枢序列，**60/60 首选与 greedy 一字不差**。中枢串为空时返回空列表。

    **`limit` 是在深度优先的过程中截断的，所以保下来的是「尾巴上的变化」。**
    切法总数是各段同向串 2^(n-1) 的连乘，5分钟级别上千个中枢时它是天文数字，
    不可能全枚举。而 `_runs_at` 长的在前 + DFS 从右往左回溯，意味着前 `limit`
    条共享同一个（最长的）开头、只在靠后的位置分叉——这恰好是有用的那一半：
    买卖点挂在**当下所在**的那个走势类型上，开头怎么切不影响它。靠前位置的
    备选切法因此不会出现，这是有意的取舍，不是漏枚举。

    实测同一批 60 条序列：58 条有备选（所以「有没有备选」不能当筛子），
    20 条末段读法不同，且这 20 条末段的 `kind` 也全都不同。出场条件用
    `last_run_differs`。
    """
    if not zss:
        return []
    out: list[Grouping] = []
    _walk_groups(zss, 0, [], out, limit)
    uniq: dict[tuple, Grouping] = {}
    for g in out:
        uniq.setdefault(g.key, g)
    gs = sorted(uniq.values(), key=lambda g: g.sort_key)[:limit]
    for g in gs:
        g.notes = _group_notes(g, gs[0])
    return gs


def last_run_differs(gs: list[Grouping]) -> bool:
    """备选读法里有没有哪个**当下所在的走势类型**和首选不一样。

    分组多义性几乎总是存在——任何一串同向中枢都能再切细——所以光报「有多义」
    等于每只票都报，那个字段模型会学会跳过。真正值得摆出来的是末尾那一段读法
    不同的情形：买卖点就挂在它身上。
    """
    if len(gs) < 2:
        return False
    return any(g.last != gs[0].last for g in gs[1:])
