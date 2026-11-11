"""走势中枢。17课 p36 定义 + 20课 p43 的指标体系与中心定理。

17课 p36（`.source/book.txt` 第 281、308 行）：

  「某级别走势类型中，被**至少三个连续次级别走势类型所重叠**的部分，称为
    缠中说禅走势中枢。」
  「具体的计算以**前三个**连续次级别的重叠为准，严格的公式可以这样表示：
    次级别的连续三个走势类型 A、B、C，分别的高、低点是 a1\\a2, b1\\b2,
    c1\\c2。则，中枢的区间就是（max（a2,b2,c2），min（a1,b1,c1））。」
  「注意，次级别的前三个走势类型都是**完成的**才构成该级别的走势中枢。」

  递归终点（第 284 行）：「对最后不能分解的级别，其走势中枢……定义为至少三个
  该级别单位 K 线重叠部分。」

20课 p43（第 348 行）给出指标体系：

  「以后都把这些与中枢方向一致的次级别走势类型称为 Z 走势段，按中枢中的时间
    顺序，分别记为 Zn 等，而相应的高、低点分别记为 gn、dn，定义四个指标，
    **GG=max(gn), G=min(gn), D=max(dn), DD=min(dn)**，n 遍历中枢中所有 Zn。
    特别地，再定义 **ZG=min(g1、g2), ZD=max(d1、d2)**，显然，[ZD，ZG]就是
    走势中枢的区间。」

  中心定理一：「走势中枢的延伸**等价于**任意区间[dn，gn]与[ZD，ZG]有重叠。
    换言之，若有 Zn，使得 dn>ZG 或 gn<ZD，则必然产生高级别的走势中枢或趋势
    及延续。」

  中心定理二：「前后同级别的两个走势中枢，后 GG<前 DD 等价于下跌及其延续；
    后 DD>前 GG 等价于上涨及其延续。后 ZG<前 ZD 且后 GG>=前 DD，或后 ZD>前 ZG
    且后 DD=<前 GG，则等价于形成高级别的走势中枢。」

18课 p39（第 339 行）——趋势判定必须用包络而不是中枢区间：

  「在趋势里，同级别的前后走势中枢是**不能有任何重叠**的，这包括任何围绕走势
    中枢产生的**任何瞬间波动**之间的重叠。」

18课 p40（第 340 行）——延伸到哪里为止：

  「走势中枢以及其延伸。这种情况下，所有围绕走势中枢产生的**前后两个次级波动
    都必须至少有一个触及走势中枢的区间**。否则，就必然产生一个新的三次连续
    次级走势类型的重叠部分**离开**原来的走势中枢，这与走势中枢的延续矛盾。」

33课 p79（第 630 行）——离开段归谁：

  「整个走势其实就简化为**两个中枢与连接两者的一个走势**。」
  即 a+A+b+B+c 里的 b 是连接段，不属于前后任何一个中枢。

**ZG/ZD 与 GG/DD 的分工是这一层最容易搞混的地方**：前者诞生即定死、用于
3买3卖；后者随延伸浮动、用于趋势与扩展判定。见 CONVENTIONS C-zs。

走势多义性（哪一段起手算中枢）不在本模块处理，见 `chan/ambiguity.py`。
"""

from __future__ import annotations

from .num import ge, gt, le, lt
from .num import overlap as _overlap
from .types import Bi, Direction, Leg, Seg, ZhongShu

# 17课 p36：至少三个连续次级别走势类型
MIN_LEGS = 3

# 33课 p79（第 626 行）——消除「延伸 vs 更大级别中枢」这一类多义性的硬规则：
#
#   「例如，5 分钟级别的中枢不断延伸，出现 **9 段以上**的 1 分钟次级别走势。
#     站在 30 分钟级别的中枢角度，**3 个 5 分钟级别的走势重合**就形成了，而
#     9 段以上的 1 分钟次级别走势，**每 3 段构成一个 5 分钟的中枢**，这样也就
#     可以解释成这是一个 30 分钟的中枢。这种情况，只要对中枢延伸的数量进行
#     限制，就可以消除多义性，一般来说，中枢的延伸不能超过 5 段，也就是一旦
#     出现 6 段的延伸，加上形成中枢本身那三段，就构成更大级别的中枢了。」
#
# 所以 3+5=8 段**仍是同级别**，第 9 段才升级。
MAX_LEGS = MIN_LEGS + 5

# 升级点：书里那句话的另一半推导，两条路算出同一个 9，互为校验。
#
#   「9 段以上的 1 分钟次级别走势，**每 3 段构成一个 5 分钟的中枢**，这样也就
#     可以解释成这是一个 30 分钟的中枢。」
#
# 一个次级别中枢要 MIN_LEGS=3 段，3 个次级别中枢就要 3*3=9 段。而从延伸上限
# 那头算是 MAX_LEGS+1 = 9。同一个数。
UPGRADE_LEGS = MIN_LEGS * 3

TREND_UP = "up"
TREND_DOWN = "down"
PANZHENG = "panzheng"
HIGHER_LEVEL = "higher_level"
AMBIGUOUS = "ambiguous"


def legs_from_bis(bis: list[Bi]) -> list[Leg]:
    """把笔当作次级别走势类型，用于**笔中枢**。

    笔的两端就是它的极值，所以 low/high 直接取端点。
    """
    return [
        Leg(
            index=i,
            direction=b.direction,
            start_price=b.start_price,
            end_price=b.end_price,
            low=b.low,
            high=b.high,
            src_start=i,
            src_end=i,
            confirmed=b.confirmed,
        )
        for i, b in enumerate(bis)
    ]


def leg_from_seg(index: int, seg: Seg, bis: list[Bi]) -> Leg:
    """把线段转成 Leg，极值**遍历构成笔**来算。

    不能用 `Seg.low/.high`——那只是两端点。71课 p190：「没有人规定分段的结束
    位置一定是最高、最低的。」线段内部的笔完全可能跑到两端之外，而 17课 p36
    的中枢公式要的是这一段走势的真实「高、低点」。
    """
    span = bis[seg.start_bi : seg.end_bi + 1]
    return Leg(
        index=index,
        direction=seg.direction,
        start_price=seg.start_price,
        end_price=seg.end_price,
        low=min(b.low for b in span),
        high=max(b.high for b in span),
        src_start=seg.start_bi,
        src_end=seg.end_bi,
        confirmed=seg.confirmed,
    )


def legs_from_segs(segs: list[Seg], bis: list[Bi]) -> list[Leg]:
    """把线段当作次级别走势类型，用于**线段中枢**（标准用法）。"""
    return [leg_from_seg(i, s, bis) for i, s in enumerate(segs)]


def _overlap_range(legs: list[Leg]) -> tuple[float, float]:
    """17课 p36 的 (max(a2,b2,c2), min(a1,b1,c1))，对任意条数取公共重叠。"""
    return (max(x.low for x in legs), min(x.high for x in legs))


def legs_overlap(legs: list[Leg]) -> bool:
    lo, hi = _overlap_range(legs)
    return le(lo, hi)


def _envelope(
    legs: list[Leg], direction: Direction, *, has_entry: bool = False
) -> tuple[float, float, float, float]:
    """返回 (gg, dd, g, d)。**只遍历 Zn**，即与中枢形成方向一致的那些段。

    20课 p43 写得很死：「以后都把这些**与中枢方向一致**的次级别走势类型称为
    Z 走势段……GG=max(gn), G=min(gn), D=max(dn), DD=min(dn)，n 遍历中枢中
    **所有 Zn**。」

    曾经改成「遍历全部段」，那是错的。同一课 p43 前面就说清楚了为什么只数
    同向段：「中枢的公式都可以简化为 [max(a2,c2), min(a1,c1)]。显然，A、C 段，
    其方向与中枢形成的方向是一致的，由此可见，在中枢的形成与延伸中，由与中枢
    形成方向一致的次级别走势类型的区间重叠确定。」

    ``has_entry``——**首构成段只贡献远端极值**，近端那个不算。见 C-enter。

    `legs[0]` 的近端（向上段的低点 / 向下段的高点）在数值上**就是进入段的
    终点**：两段首尾相接，那个价位是上一段砸/拉出来的，不是「围绕中枢产生的
    瞬间波动」（18课 p39）。这是 C-core 的另一半——那里把**离开**段整段挡在
    GG/DD 之外，这里挡的是**进入**段漏进来的那一个端点。只做一半就是不对称。

    不这么做的后果是**单向**的，不是噪声：下跌走势里 a 砸出来的低点会把前中枢
    的 DD 往下拽，而判下跌要求「后 GG < 前 DD」，DD 被拽低就更难满足；上涨对称。
    全缓存 33 条序列实测（466 个中枢，其中 436 个有进入段）：旧口径下 207 个
    （47.5%）的 GG 或 DD 恰好钉在进入段的终点上，补上这半条规则后 131 个
    （30.0%）的包络真的变了，433 对相邻关系里变了 11 对——**6 对
    higher_level → up，5 对 higher_level → down，反方向一对都没有**。

    恒等式 `DD <= ZD <= ZG <= GG` 不受影响：ZD = max(d1,d2) >= d2，而剔掉 d1
    之后的 DD <= d2；ZG 一侧同理。
    """
    zn = [x for x in legs if x.direction is direction] or legs
    gs = [x.high for x in zn]
    ds = [x.low for x in zn]
    if has_entry and len(zn) > 1:
        # 首 Zn 的近端 = 进入段的终点。向上段剔低点，向下段剔高点。
        (ds if zn[0].direction is Direction.UP else gs).pop(0)
    return (max(gs), min(ds), min(gs), max(ds))


def usable_count(legs: list[Leg], require_confirmed: bool) -> int:
    """能参与构成中枢的段数。17课 p36：只有**完成的**走势类型才算数。"""
    n = len(legs)
    if require_confirmed:
        while n and not legs[n - 1].confirmed:
            n -= 1
    return n


def zhongshu_at(
    legs: list[Leg], i: int, usable: int, *, index: int = 0, max_legs: int = MAX_LEGS
) -> ZhongShu | None:
    """假定中枢从 ``legs[i]`` 起步，把它一路延伸到底。不成立返回 None。

    这是整个中枢层唯一的构造函数，`build_zhongshus` 和 `enumerate_readings`
    都走它——起点不同产生的就是 33课 p79 说的那种多义性，算法本身是一样的。
    """
    if i + MIN_LEGS > usable:
        return None
    trio = legs[i : i + MIN_LEGS]
    if not legs_overlap(trio):
        return None

    zd, zg = _overlap_range(trio)
    # Z 走势段的方向 = 第一段的方向（20课 p43：与中枢形成方向一致）
    direction = trio[0].direction
    base_end = i + MIN_LEGS - 1

    # ---- 延伸到哪里为止 ----
    #
    # 18课 p40：「走势中枢以及其延伸。这种情况下，所有围绕走势中枢产生的
    # **前后两个次级波动都必须至少有一个触及走势中枢的区间**。否则，就必然
    # 产生一个新的三次连续次级走势类型的重叠部分**离开**原来的走势中枢，这与
    # 走势中枢的延续矛盾。」
    #
    # 所以终止条件是**连续两段都不触及** [ZD, ZG]。不能只看单段：相邻两段首尾
    # 相接，离开那一段的起点就在中枢里，单看它永远「有重叠」。
    #
    # **段数上限完全不参与这个循环。** 这一条踩过两次，一次比一次深：
    #
    # 第一次是把 `k - i + 1 <= max_legs` 写进循环条件，后果是一个本来已经走掉
    # 的中枢，只因为第二段「不触及」落在第 9 段上没被看到，就留下
    # `departed=False`，飞走的那一段被当成中枢的一部分、GG/DD 被它撑爆（见
    # C-core 的 600519 实例）。那次的结论是「看要看到底，只是不并进来」。
    #
    # 第二次就是「不并进来」这半句——见下。
    #
    # **`max_legs` 只是「该升一级看」的阈值，不是切断点。** 原先顶到上限就
    # `break`，`depart_leg` 留空，而 `build_zhongshus` 照样从 `end_leg + 1`
    # 起下一个中枢——于是两个中枢之间**一段都不隔**，见 `check_zhongshus`。
    #
    # 那一段的身份会自相矛盾：它既触及本中枢区间（`capped` 正是在它触及时
    # 跳出的），又当了下一个中枢的第一构成段、把自己的端点写进了那个中枢的
    # ZD/ZG。而且新中枢的奇偶性必然是反的——下跌走势里的中枢该是 上下上，
    # 截断生出来的却是 下上下。全缓存 66 组里 64 组有这个毛病，相邻对约 24%。
    #
    # 33课 p79 说延伸到第 9 段「就构成更大级别的中枢了」：那是**一个**更大的
    # 中枢，不是两个。所以这里一路延伸到真正离开为止，只把 `level_up` 置起来
    # 告诉调用方该升一级；切开反而制造出书里不存在的结构。
    #
    # 这样一来不变量是**构造上**成立的：循环只有两种出口。两段连续不触及时
    # `depart_leg` 必有值，`next_start = depart_leg + 1`，中间隔着离开段；
    # 走到数据尽头时 `misses <= 1`，`last_touch` 之后最多剩 1 段，凑不满
    # `MIN_LEGS`，根本不会再有下一个中枢。
    last_touch = base_end
    misses = 0
    k = base_end + 1
    while k < usable and misses < 2:
        if _overlap(legs[k].low, legs[k].high, zd, zg):
            last_touch = k
            misses = 0
        else:
            misses += 1
        k += 1
    departed = misses >= 2

    # 真的走掉了的话，**离开段不算中枢的一部分**。20课 p43 第三类买卖点定理：
    # 「一个次级别走势类型向上**离开**走势中枢，然后以一个次级别走势类型回试，
    #   其低点不跌破 ZG，则构成第三类买点」——离开段和回试段都在中枢之外，
    # 否则趋势永远成立不了：离开段的高点会灌进 GG，和下一个中枢必然重叠。
    #
    # 离开段就是 `last_touch`：它从中枢里起步所以必然「触及」，然后一去不回，
    # 后面两段才双双落空。结构上中枢最少要有三段（17课 p36 的前三段重叠），
    # 所以 `end` 不能退到 `base_end` 之下——**第三段自己飞走时，它既是构成段
    # 又是离开段**，`end_leg == depart_leg`，由 `core_end` 去区分两种身份。
    depart_leg = last_touch if departed else None
    end = max(last_touch - 1, base_end) if departed else last_touch

    # GG/DD 只覆盖**围绕中枢震荡**的那些段，离开段不算（27课 p66 第 612 行，
    # 见 CONVENTIONS C-core）。`core_end` 可能比 `end` 小一段。
    core_end = end if depart_leg is None else depart_leg - 1
    gg, dd, g, d = _envelope(legs[i : core_end + 1], direction, has_entry=i > 0)

    # ---- 该不该升一级看 ----
    #
    # 33课 p79：到了升级点说明这里已经不是普通的延伸了。**这是个标记，不是
    # 切断**（见上面循环那段注释）。
    #
    # 两个条件缺一不可，书里那句话两半都写了：
    #
    #   「**3 个** 5 分钟级别的走势**重合**就形成了」
    #
    # 一、**数量**：够 9 段（= 3 个次级别中枢，见 `UPGRADE_LEGS`）。范围取
    #     `core_end` 而不是 `end_leg`：离开段本来就不属于这个中枢。
    #
    # 二、**重合**：**重合的对象是「中枢」，不是「段」。** 这是本项目在这一
    #     条上栽过的第三跤，前两次见上面循环那段注释。
    #
    #     把 9 段按书里说的「每 3 段构成一个中枢」切成 3 组，每组是一个次级别
    #     中枢、区间是那 3 段的 `[max(low), min(high))]`（17课 p36 的公式）。
    #     升级要的是**这 3 个区间有公共交集**。
    #
    #     这件事会塌缩得很干净：3 个区间的交 = 那 9 段全体的交，所以直接
    #     `legs_overlap(前 9 段)` 就行，怎么分组都不影响结果。顺带这也解释了
    #     为什么方向必然是「上下上」或「下上下」——段方向交替，隔 3 段就反向，
    #     所以 3 个子中枢的方向自动交替，不用额外检查。
    #
    #     曾经写成「5 个 Zn 有公共交集」（`D=max(dn) <= G=min(gn)`），错在
    #     **对象选错了**：Zn 是段不是中枢。全缓存实测两者差 190 个（846 个
    #     够 9 段的中枢里，旧判据 524、新判据 714），差额全是被延伸到第 11、
    #     13 段之后的摆动否决掉的——而书里锚定的是「形成中枢本身那三段」加上
    #     「延伸出来的 6 段」，就是开头这 9 段，后面再长也不该回头翻案。
    #
    #     锚定头 9 段还白捡一个性质：`level_up` 只依赖 `legs[i:i+9]`，中枢继续
    #     延伸不会把已挂上的标志摘掉。旧判据会闪——9 段时挂上、11 段时新来的
    #     Zn 破坏交集又掉下来，实时扫描看到的信号忽明忽暗。
    #
    #     反例长什么样：延伸只要求每段「触及 [ZD, ZG]」，蹭到边就算。第 4 段
    #     只蹭上沿、第 5 段只蹭下沿时，9 段整体没有公共交集，重读出来的 3 个
    #     次级别中枢互不重合。按 20课 p43 中心定理二那是更大级别的**趋势**而
    #     不是更大级别的中枢（用户给的那张图正是这个：6 个中枢一路向下、两两
    #     不沾边，是下跌趋势，不升级），这时候提示「升一级看」就是错的。
    #
    # **不要求中枢已经走完。** 原先这里还 `and end + 1 < usable`（即
    # `confirmed`），那是错的：升级提示说的是「这 9 段已经发生了」，不是「这
    # 个中枢的故事已经讲完了」。而且恰恰相反——站在右边缘、中枢还在走的时候
    # 才是最需要这句提醒的时刻，那正是实时扫描唯一关心的位置。参与计数的段
    # 本来就都是确认过的（`usable_count` 已经把未完成的末段切掉了），所以
    # `confirmed` 在这里多加的信息只有「后面还有一段」，和升级与否无关。
    upgrade_legs = max_legs + 1
    level_up = core_end - i + 1 >= upgrade_legs and legs_overlap(
        legs[i : i + upgrade_legs]
    )
    return ZhongShu(
        index=index,
        direction=direction,
        start_leg=i,
        end_leg=end,
        zg=zg,
        zd=zd,
        gg=gg,
        dd=dd,
        g=g,
        d=d,
        confirmed=end + 1 < usable,  # 后面还有段才谈得上中枢已走完
        level_up=level_up,
        depart_leg=depart_leg,
    )


def build_zhongshus(
    legs: list[Leg],
    *,
    require_confirmed: bool = True,
    start: int = 0,
    max_legs: int = MAX_LEGS,
) -> list[ZhongShu]:
    """从一串次级别走势类型里划出中枢序列（**贪心最早**那一种读法）。

    从 ``start`` 开始往后找第一组三段重叠，延伸到底，然后从它之后的第一段
    重新找——中枢之间不重叠地依次排开。

    ``start`` 不等于 0 时给出的是另一种合法读法；全部读法见
    `enumerate_readings`，这里只是其中最早的那条。

    **找不到中枢时往后滑两段，不是一段。** 段是严格交替的，所以「隔几段起步」
    和「起步段朝哪个方向」是同一件事。一个中枢的第一构成段方向必然与**进入段**
    相反——17课 p36 的 aAbBc 里，下跌走势 a 向下进来，A 只能是 上下上；上涨
    反之。逐段滑会把奇偶性滑反，造出一串「下跌走势里的下上下中枢」。

    这正是 `zhongshu_at` 里 MAX_LEGS 那段注释（见上）点名过的同一个毛病，只是
    那里是截断造出来的，这里是滑动造出来的，当时只堵了前者。600009 30分钟上
    的实例：中枢4 的离开段是 段45(下)，之后

        起点46(上) 空 | 起点47(下) 成枢 | 起点48(上) 空 | 起点49(下) 成枢
        起点50(上) 空 | 起点51(下) 成枢 | 起点52(上) 成枢 [60.77,63.47]

    逐段滑会先撞上 47 和 51 这两个下上下的，把 2026-01 到 2026-06（87.99 →
    60.77）一整段下跌剁成三截；更坏的是 起点51 那个把 74.55→60.77 这根 13.78
    元的下跌段吞成了中枢的第一构成段，core 因此凑到 9 段，`level_up` 跟着假阳。
    保奇偶滑动直接落到 起点52，下跌段完整留在外面当连接段。

    首个中枢之前没有进入段，方向无从锚定，那时仍然逐段试——这个起点自由度本来
    就是 33课 p79 说的多义性，由 ``start`` / `enumerate_readings` 管。
    """
    usable = usable_count(legs, require_confirmed)
    out: list[ZhongShu] = []
    i = start
    step = 1
    while i + MIN_LEGS <= usable:
        zs = zhongshu_at(legs, i, usable, index=len(out), max_legs=max_legs)
        if zs is None:
            i += step
            continue
        out.append(zs)
        i = zs.next_start  # 跨过离开段，它是连接两个中枢的 b（33课 p79）
        step = 2           # 从此奇偶性被进入段锚定
    return out


# --------------------------------------------------------------------------
# 中心定理二：两个同级别中枢的关系
# --------------------------------------------------------------------------


def relation(prev: ZhongShu, cur: ZhongShu) -> str:
    """20课 p43 中心定理二。返回 TREND_UP / TREND_DOWN / HIGHER_LEVEL / AMBIGUOUS。

      「后 GG<前 DD 等价于下跌及其延续；后 DD>前 GG 等价于上涨及其延续。
        后 ZG<前 ZD 且后 GG>=前 DD，或后 ZD>前 ZG 且后 DD=<前 GG，则等价于
        形成高级别的走势中枢。」

    用 GG/DD 而不是 ZG/ZD 判趋势，是 18课 p39 要求的：「同级别的前后走势中枢
    是不能有任何重叠的，这包括任何围绕走势中枢产生的任何瞬间波动之间的重叠。」
    """
    if lt(cur.gg, prev.dd):
        return TREND_DOWN
    if gt(cur.dd, prev.gg):
        return TREND_UP
    if lt(cur.zg, prev.zd) and ge(cur.gg, prev.dd):
        return HIGHER_LEVEL
    if gt(cur.zd, prev.zg) and le(cur.dd, prev.gg):
        return HIGHER_LEVEL
    return AMBIGUOUS


def classify(zss: list[ZhongShu]) -> str:
    """17课 p36 的走势类型三分：上涨 / 下跌 / 盘整。

      「某完成的走势类型只包含一个走势中枢，就称为该级别的盘整。」
      「某完成的走势类型至少包含两个以上**依次同向**的走势中枢，就称为该级别
        的趋势。该方向向上就称为上涨，向下就称为下跌。注意，趋势中的走势中枢
        之间必须**绝对不存在重叠**。」

    返回 TREND_UP / TREND_DOWN / "panzheng"。只要有一对相邻中枢不构成同向
    趋势关系，整体就不是趋势。
    """
    if len(zss) < 2:
        return PANZHENG
    rels = [relation(a, b) for a, b in zip(zss, zss[1:])]
    if all(r == TREND_UP for r in rels):
        return TREND_UP
    if all(r == TREND_DOWN for r in rels):
        return TREND_DOWN
    return PANZHENG
