"""级别树。把一张平铺的中枢表变成「走势类型 → 次级别走势类型」的递归结构。

**为什么非要这一层。** `build_zhongshus` 给出的是一张**平表**，隐含假设表里
每一项都是同级别的东西。实测这假设是假的：600009 30分钟的 7 个中枢里，中枢5
是 3 段的本级别中枢，中枢4 是 23 段、横跨 2025 一整年的东西，两者并排列在一行
上，于是 `relation` / `classify` / b-c 力度全部在**跨级别**地比较。直接后果是
一段 87→63 的下跌被 `classify` 判成盘整，而人眼一看就是「下跌-盘整-下跌」。

树把「同级别」从一句口头约定变成结构约束：所有比较只在同一层的兄弟节点之间
发生，跨级别比较在类型上就不可能写出来。

---

**两步递归。合在前，拆在后**——顺序的理由写在 `build_layer` 的 docstring 里，
简单说就是先拆会把父中枢和邻居的关系（中心定理二，一条**等价**关系）连同整个
趋势一起挡掉，而拆本身只是 33课 p79 的一条**消歧约定**。

一、**合**（`_greedy_groups`）。17课 p36 的走势类型三分：一个中枢是盘整，
  两个以上依次同向不重叠的中枢是趋势。把本层中枢按这个规则归成若干**走势
  类型**，每个走势类型就是上一层的一个 `Leg`（它的 high/low 取**真实极值**，
  不是中枢区间——17课 p36 的公式要的是「分别的高、低点」，71课 p190 说过
  端点未必是极值）。然后对这串新 `Leg` 再跑一次 `build_zhongshus`，得到
  上一层的中枢。

二、**拆**（`split_extended`），只对**没并进趋势**的那些中枢。33课 p79：

  「9 段以上的 1 分钟次级别走势，每 3 段构成一个 5 分钟的中枢，这样也就可以
    解释成这是一个 30 分钟的中枢。」

  核心段 >= `UPGRADE_LEGS` 的中枢，本来就不是本级别的一个中枢，而是**上级别的
  一个中枢**。把它的核心段三等分，每份是一个本级别中枢。等分而不是硬切成
  3+3+3+…：n=23 时三等分给出 8/8/7，和手工划出来的三个盘整吻合；而固定 3 段
  一组会给出 7 个子中枢，读起来没有意义。n=9 时三等分恰好退化成 3/3/3，也就是
  书里那句话的字面形态。两头都对得上。

  **子中枢之间不要连接段。** 同级别两个中枢之间必须隔一个连接走势（33课 p79
  的 b），但这里的 3 个子中枢不是「同级别相邻的两个中枢」，它们是**上级别那
  一个中枢的三个构成部分**——「3 个次级别走势重合」说的就是它们直接相邻且
  重叠。强行在中间抠一段出来当连接段，会抠到一段仍然触及中枢区间的腿上，那是
  假的离开。

  拆开之后下一层的 `build_zhongshus` 会把这三份重新合成上级别中枢——拆正是
  为了让上一层有东西可合。父中枢并进了趋势的话这一步不做：那时它在本级别已经
  有对手盘，升级那种读法让位给中心定理二。

三、**并进趋势的那些，三等分的结果仍然留着**（`Trend.substructure`，C-sub）。

  让位的是**结构**，不是**信息**。600009 30分钟的中枢4 是 23 段、横跨整个
  2025 年的东西，它并进了下跌趋势，于是走势3 占 段21..59、从 2024-12-12 管到
  2026-09-28，**21 个月一个标签**。方向对，aAbBc 也对，可是中间无处下手。

  反过来硬拆也不行——实测把它拆开，走势3 变成「盘整/盘整/盘整/无中枢/盘整」
  五段，每段两三个月、能操作了，但「这是一个下跌趋势」整个没了，读起来是一串
  盘整；而且两种读法在 L1 都塌成同一个盘整，升上去救不回来。

  所以不二选一。走势类型一根不动，三等分的结果另挂在 `substructure` 上：

      走势3 down 段21..59  94.33 → 60.40
        └ 子枢 [84.02, 85.95] [84.10, 85.71] [82.72, 84.36]   ← 2025 全年

  读法是「下跌趋势，2025 整年在这三个本级别中枢里磨，2026-01 之后才是真正的
  离开段」。33课 p79 的走势多义性本来就不是二选一——同一段走势在两个级别上
  各有一个合法答案，两个都摆出来才是完整的回答。

  `split_from`（真的拆了）和 `substructure`（没拆，只是记下来）是两回事：前者
  改变了本层中枢序列、会影响上一层，后者纯附加，`relation` / `classify` /
  上一层一个字都不受影响。

---

**和 `ZhongShu.level_up` 的关系：两个不同的问题，都要保留。**

`level_up` 看的是**头 9 段**有没有公共交集（`zhongshu.zhongshu_at`），它单调、
便宜、不会因为后面继续延伸而翻案，适合扫描时挂警告：「这里已经够格升一级看」。

树看的是**三等分之后那 3 个子中枢**有没有公共交集，回答的是「升上去之后长什么
样」。n > 9 时两者可以不一致——头 9 段重合不蕴含全部核心段重合。不一致不是
bug，是信息：说明升级是由开头那段震荡支撑的，后半程已经漂走了。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .num import le
from .types import Bi, Direction, Leg, ZhongShu
from .zhongshu import (
    MIN_LEGS,
    PANZHENG,
    TREND_DOWN,
    TREND_UP,
    UPGRADE_LEGS,
    _envelope,
    _overlap_range,
    build_zhongshus,
    relation,
)

# 一只票不可能有无穷多级别；到顶之后 `build_zhongshus` 自然返回空，这个上限
# 只是防呆，避免结构退化时死循环。
MAX_LEVELS = 5

# 第四种「走势类型」：一段**本级别看不出中枢**的走势。它不是 17课 p36 的三分
# 之一（盘整和趋势都至少要一个中枢），正因为如此才要单独标出来——见 C-nozs。
#
# **值是机器键，全英文**，和同层的 `TREND_UP`/`TREND_DOWN`/`PANZHENG`
# （`chan.zhongshu`，值为 "up"/"down"/"panzheng"）一致。这里原先是中文串
# 「无中枢」，于是 `scan.report` 要在 JSON 出口处专门归一一次，而 `_plot/`
# 和测试又直接拿中文当枚举值——同一个概念在三处有两种写法。中文只在显示层
# 出现，见 `KIND_CN`。
NO_ZHONGSHU = "no_zhongshu"

#: 走势类型代号怎么说人话。**只给显示层用**，不要拿它的键去做判断。
#: 渲染侧（`pi/extensions/chan/index.ts` 的 `TREND_CN`）另有一份，那边是
#: TypeScript、跨不了语言，两处要一起改。
KIND_CN = {
    TREND_UP: "上涨趋势",
    TREND_DOWN: "下跌趋势",
    PANZHENG: "盘整",
    NO_ZHONGSHU: "本级别无中枢（该降级别看）",
}


@dataclass(slots=True)
class Trend:
    """一个**走势类型**（17课 p36）。它同时是上一层的一个次级别走势类型。

    ``kind`` 为 PANZHENG 时 ``zhongshus`` 恰有一个；为 TREND_UP/TREND_DOWN 时
    至少两个且依次同向不重叠。

    ``substructure`` 是**次级别读法**：本走势类型里够格三等分的中枢，各自摊开
    成的那 3 个本级别中枢（C-sub）。平时是空的。

    ``start_leg``/``end_leg`` 是在**本层 legs** 里的闭区间下标，**首尾相接**：
    收在末枢的离开段（aAbBc 里的 c），下一个走势类型紧接着从下一根段起步，
    中间不留空（72课：「前后两个走势类型，就以该背驰点为分界」——分界是一个
    点）。所以中枢之间的连接段、走势类型之间那些还没长成中枢的段，全都含在
    某一个走势类型里。两处例外，都是诚实的空缺：第一个中枢之前的段（没有上
    家可挂），和末枢还没走出去时它后面的段（走势类型未完成）。
    """

    level: int
    index: int
    kind: str
    zhongshus: list[ZhongShu]
    start_leg: int
    end_leg: int
    low: float
    high: float
    start_price: float
    end_price: float
    substructure: list[ZhongShu] = field(default_factory=list)

    @property
    def direction(self) -> Direction:
        return Direction.UP if self.end_price >= self.start_price else Direction.DOWN


@dataclass(slots=True)
class Layer:
    """树的一层。``legs`` 是这一层的次级别走势类型，``zhongshus`` 是本层中枢。"""

    level: int
    legs: list[Leg]
    zhongshus: list[ZhongShu]
    trends: list[Trend]
    # 本层每个中枢是由上一层哪几个走势类型构成的。level 0 时为空。
    parent_of: dict[int, list[int]] = field(default_factory=dict)
    # 被 `split_extended` 拆开的中枢：{拆出来的子中枢下标: 原中枢的核心段数}
    split_from: dict[int, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# 一、拆：过度延伸的中枢其实是上级别的中枢
# --------------------------------------------------------------------------


def _mk_zhongshu(legs: list[Leg], i: int, j: int, index: int,
                 confirmed: bool) -> ZhongShu | None:
    """用 legs[i..j] 直接造一个中枢，不做延伸也不找离开段。

    ZD/ZG 仍然只由**前三段**定（17课 p36「具体的计算以前三个连续次级别的重叠
    为准」），后面的段算延伸。整组没有公共重叠时返回 None——那就不是中枢。
    """
    if j - i + 1 < MIN_LEGS:
        return None
    zd, zg = _overlap_range(legs[i : i + MIN_LEGS])
    if not le(zd, zg):
        return None
    direction = legs[i].direction
    # `has_entry=i>0`：拆出来的第 2、3 个子中枢前面没有正经连接段，但它们的首段
    # 仍然是从上一段的终点起步的，那个端点同样不属于本子中枢（C-enter）。
    gg, dd, g, d = _envelope(legs[i : j + 1], direction, has_entry=i > 0)
    return ZhongShu(
        index=index, direction=direction, start_leg=i, end_leg=j,
        zg=zg, zd=zd, gg=gg, dd=dd, g=g, d=d,
        confirmed=confirmed, level_up=False, depart_leg=None,
    )


def split_extended(legs: list[Leg], z: ZhongShu) -> list[ZhongShu] | None:
    """把核心段 >= `UPGRADE_LEGS` 的中枢三等分成 3 个本级别中枢。

    返回 None 表示不该拆，三种情形：段数不够；三等分之后有一份凑不出中枢；
    **三份凑出来了但互不重合**。后两者都是**有意义的否定**——20课 p43 中心
    定理二说那是更大级别的**趋势**，不是更大级别的中枢，这时候保持原样
    （一个延伸中枢）反而是对的。

    第三条是补上的。33课 p79 的原话是「3 个 5 分钟级别的走势**重合**就形成
    了」，重合是升级的前提，不是副产品；`zhongshu.level_up` 那边一直在查
    （C-ext，全缓存 846 个够 9 段的中枢里 132 个、15.6% 过不了），这边漏了，
    于是同一个中枢可能 `level_up=False` 却照样被拆成三份。
    『各自能成中枢』不蕴含『三者有公共区间』：延伸只要求每段触及 [ZD, ZG]，
    蹭到边就算数，前三段贴上沿、后三段缩下沿是常见形态。

    离开段不参与三等分：它不属于这个中枢（33课 p79）。
    """
    lo, hi = z.start_leg, z.core_end
    n = hi - lo + 1
    if n < UPGRADE_LEGS:
        return None
    # 三等分，余数从前往后摊。n>=9 保证每份 >= 3。
    base, rem = divmod(n, MIN_LEGS)
    out: list[ZhongShu] = []
    i = lo
    for k in range(MIN_LEGS):
        size = base + (1 if k < rem else 0)
        child = _mk_zhongshu(legs, i, i + size - 1, len(out), confirmed=True)
        if child is None:
            return None
        out.append(child)
        i += size
    # 「3 个次级别走势**重合**」——比的是 3 个中枢区间，和 C-ext 同一口径
    if not le(max(z.zd for z in out), min(z.zg for z in out)):
        return None
    return out


def _renumber(z: ZhongShu, index: int) -> ZhongShu:
    if z.index == index:
        return z
    return ZhongShu(
        index=index, direction=z.direction, start_leg=z.start_leg,
        end_leg=z.end_leg, zg=z.zg, zd=z.zd, gg=z.gg, dd=z.dd, g=z.g, d=z.d,
        confirmed=z.confirmed, level_up=z.level_up, depart_leg=z.depart_leg,
    )


# --------------------------------------------------------------------------
# 二、合：把本层中枢归成走势类型，走势类型就是上一层的 Leg
# --------------------------------------------------------------------------


def _greedy_groups(zss: list[ZhongShu]) -> list[tuple[str, list[ZhongShu]]]:
    """17课 p36 的走势类型三分。贪心取**最长**的同向趋势段。

      「某完成的走势类型只包含一个走势中枢，就称为该级别的盘整。」
      「某完成的走势类型至少包含两个以上依次同向的走势中枢，就称为该级别的
        趋势……趋势中的走势中枢之间必须绝对不存在重叠。」

    「依次同向」由 `relation` 判（18课 p39 要求用 GG/DD 包络，不是 ZD/ZG）。
    """
    out: list[tuple[str, list[ZhongShu]]] = []
    i = 0
    while i < len(zss):
        j = i
        kind = PANZHENG
        while j + 1 < len(zss):
            r = relation(zss[j], zss[j + 1])
            if r not in (TREND_UP, TREND_DOWN):
                break
            if kind == PANZHENG:
                kind = r
            elif r != kind:
                break
            j += 1
        out.append((kind, zss[i : j + 1]))
        i = j + 1
    return out


def _goes_with(kind: str, leg: Leg) -> bool:
    """这根段是不是跟走势类型**同向**——两头的 a 和 c 都要过这一关。

    aAbBc 里 a 和 c 与趋势同向是定义的一部分：上涨趋势 a↑A b↑B c↑，下跌趋势
    a↓A b↓B c↓。一根向下的段既当不了上涨趋势的收尾，也当不了它的开头。

    **c 这一头。** 段是交替的，所以离开段的方向由末枢核心段数的奇偶决定，
    反向不是偶发：上涨趋势的末枢由 a↑ 进入、首构成段朝下，偶数段时末构成段
    朝上、离开段朝下。全缓存 33 条序列里趋势中已走出的末枢 123 个，**92 个
    （74.8%）离开段反向**。不查的后果是标签说反话：

        300308 日线 走势0 down  309.30 → 799.44   ← 「下跌」收在高 490 块处
        600006 日线 走势0 up      7.07 →   5.81   ← 「上涨」收在低处

    **a 这一头。** 对称的毛病，样子不一样：

        600519 日线 走势2 down  359.83 → 453.84
          a = 段13 ↑ 359.83 → 658   首枢[558,658] → 末枢[453,529]
          真正的下跌只有 658 → 453，向上的 a 把标签拖反了

    盘整没有先验方向——它的方向就是由这两根段定的——所以恒为真。于是统一成
    一句话：**走势类型两端都落在朝自己方向走的段上**，`start_price` 和
    `end_price` 因此各是该方向上的一个局部极值，而不是半路上的随便一点。
    """
    if kind == TREND_UP:
        return leg.direction is Direction.UP
    if kind == TREND_DOWN:
        return leg.direction is Direction.DOWN
    return True


def _mk_trend(legs: list[Leg], kind: str, group: list[ZhongShu],
              level: int, index: int, start_leg: int | None = None) -> Trend:
    """走势类型 = 首枢的第一构成段 一直到 末枢的**离开段**（含）。

    末枢的离开段算在里面，这是 aAbBc 里的那个 c。72课 p?? 说死了分界只是一
    个点，不是一段空白：

      「前后两个走势类型，就以该背驰点为分界」

    而 20课 p43 的 `a+B+b = (a+B1)+B2+(B3+b)` 也是严丝合缝的切分，没有剩料。
    早先这里取的是 `group[-1].end_leg`——中枢最后一根**延伸**段，离开段在它
    后面一根——于是每两个走势类型之间漏掉一根段，上一层的折线是断的（600009
    30分钟丢了段11 和 段21，8.69 元和 10.31 元的真实走势在 L1 不存在），而
    `build_zhongshus` 求三段重叠时默认折线首尾相接。见 `_plot/gap.py`。

    下一个走势类型因此不带自己的进入段——它的 a 就是上一个的 c。这不是妥协：
    20课 那个 (a+B1)+B2+(B3+b) 里，B2 两头都光着，照样是一个走势类型。

    `depart_leg is None` 表示末枢还没走出去，走势类型尚未完成，此时收在
    `end_leg`，后面那些段如实留着没人认领。

    `start_leg` 由 `build_layer` 定：**首枢的进入段**，也就是首枢第一构成段
    的前一根。20课 p43 的 a 是**一根**段，不是一串——全缓存 438 个走势类型里
    414 个（94.5%）首枢之前本来就只有 0 或 1 根，剩下 24 个多出来的 52 根另
    立门户（`NO_ZHONGSHU`），不许并进来。并进来的后果是标签直接说反话：

        600519 日线 走势1 down  25.68 → 453.84
          进入段 7 根（段7..13，25.68 一路涨到 658）
          真正的下跌只有 首枢[558,658] → 末枢[453,529]

    只有第一个走势类型没有上家，此时取首枢的第一构成段（它的 a 在起点之前，
    本级别无从追溯）。

    `substructure` 见 C-sub：走势类型本身不动，次级别读法另挂一行。
    """
    last = group[-1]
    a = group[0].start_leg if start_leg is None else start_leg
    # 退回 `core_end` 而不是 `end_leg`：三段中枢里 `end_leg` **就是**离开段
    # （C-leave，`depart == end_leg`），退到 `end_leg` 等于没退。
    b = last.core_end
    if last.depart_leg is not None and _goes_with(kind, legs[last.depart_leg]):
        b = last.depart_leg
    # 末枢还没走出去（`depart_leg is None`）时 `core_end` **就是** `end_leg`，
    # 上面那句没有可退之处，而中枢最后一根震荡段照样可能朝着反方向。再往回退
    # 到最近一根同向段：走势类型未完成，它到目前为止只走到那里，后面那几根如实
    # 留着没人认领。退到末枢起点为止——再退就把中枢本身切掉了。
    while b > last.start_leg and not _goes_with(kind, legs[b]):
        b -= 1
    span = legs[a : b + 1]
    sub: list[ZhongShu] = []
    for z in group:
        for kid in split_extended(legs, z) or ():
            sub.append(_renumber(kid, len(sub)))
    return Trend(
        level=level, index=index, kind=kind, zhongshus=group,
        start_leg=a, end_leg=b,
        low=min(x.low for x in span), high=max(x.high for x in span),
        start_price=span[0].start_price, end_price=span[-1].end_price,
        substructure=sub,
    )


def _mk_gap(legs: list[Leg], i: int, j: int, level: int, index: int) -> Trend:
    """一段**本级别找不出中枢**的走势，`legs[i..j]`。

    上一个走势类型收尾之后、下一个中枢成形之前，正常只隔着一根 a。隔着三根
    五根七根，意思是这一程里连「三段重叠」都凑不出来——缠论里任何走势都由
    中枢构成，凑不出来只有一个解释：**结构在本级别之下**，这一级看不见。

    所以它既不是盘整也不是趋势（两者都至少要一个中枢），`zhongshus` 是空的。
    留着它而不是丢掉，是因为丢掉会在上一层的折线里戳个洞（C-join）；标成
    `NO_ZHONGSHU` 而不是硬塞给邻居，是因为塞进去标签就说反话。对操作而言这
    个标签本身有用：**这里要降一个级别看**。
    """
    span = legs[i : j + 1]
    return Trend(
        level=level, index=index, kind=NO_ZHONGSHU, zhongshus=[],
        start_leg=i, end_leg=j,
        low=min(x.low for x in span), high=max(x.high for x in span),
        start_price=span[0].start_price, end_price=span[-1].end_price,
    )


def build_layer(legs: list[Leg], parents: list[ZhongShu], level: int
                ) -> tuple[list[ZhongShu], dict[int, int], list[Trend]]:
    """归并 + 拆，返回 (本层中枢, {子中枢下标: 走势类型下标}, 走势类型)。

    **先归并、后拆。顺序反过来就是错的，这一条是踩出来的。**

    早先的顺序是先把每个过度延伸的中枢拆成 3 个子中枢，再在拆完的串上归并，
    并靠一道守卫（「子中枢不参与归并」）防止子中枢被拉去和外面的中枢组趋势。
    守卫本身是对的——子中枢是上级别那**一个**中枢的三个构成部分，被拉走一个
    就凑不出「三个次级别走势重合」，上级别中枢永远形成不了。

    错的是时机：拆在前，**父中枢和它邻居的关系根本没机会被评估**。拆完之后
    `relation` 看到的是子中枢的包络，那是另一个数。

    600009 30分钟（起点段16）就是这么丢掉一整个下跌趋势的：

        拆之前   中枢0 [89.03, 96.96]   中枢1 [77.44, 92.55]   中枢2 [59.55, 66.87]
                 0→1 higher_level       1→2 down   ← 后GG 66.87 < 前DD 77.44
        拆之后   中枢1 变成三个子中枢，守卫把 1→2 这条关系连同整个趋势一起挡掉

    于是 44 根段被读成 5 个盘整，而拆之前它是「盘整 + 一个 段21..59 的下跌
    趋势」——标准的 a+A+b+B+c。

    **定理压约定。** 20课 p43 中心定理二说的是「后 GG<前 DD **等价于**下跌
    及其延续」，是等价关系；33课 p79 的延伸上限原文自己讲明了身份——

      「只要对中枢延伸的数量进行限制，**就可以消除多义性**」

    ——那是一条消歧**约定**。两者冲突时先认定理：父中枢并得进趋势就保持完整，
    并不进去（自己单独成一个盘整）才退回升级那种读法、拆成三个子中枢。这样
    守卫要防的事仍然防住了：拆出来的子中枢永远各自单独成一个盘整走势类型，
    不可能被拉进任何趋势——因为归并在它们诞生之前就已经做完了。

    ``split_from`` 的值从「原中枢在父串里的下标」改成了**本层走势类型的下标**，
    因为父串在这个顺序下只是中间产物，下标对调用方没有意义；画图那边要的也
    只是「这个中枢是不是拆出来的」。
    """
    zss: list[ZhongShu] = []
    split_from: dict[int, int] = {}
    trends: list[Trend] = []
    # 走势类型首尾相接，不许留空（72课：「前后两个走势类型，就以该背驰点为
    # 分界」）。`cur` 是下一个走势类型该从哪根段起步；None 表示还没有上家。
    cur: int | None = None

    def add(kind: str, group: list[ZhongShu]) -> None:
        nonlocal cur
        # 进入段只取一根（20课 p43 的 a），而且得和趋势同向；更早的段、以及
        # 方向不对的那根，自成一个「无中枢」走势
        head = group[0].start_leg
        if cur is None:
            a = head
        elif head > cur and _goes_with(kind, legs[head - 1]):
            a = head - 1
        else:
            a = max(cur, head)
        if cur is not None and a > cur:
            trends.append(_mk_gap(legs, cur, a - 1, level, len(trends)))
        trends.append(_mk_trend(legs, kind, group, level, len(trends), a))
        cur = trends[-1].end_leg + 1

    for kind, group in _greedy_groups(parents):
        # 只有**孤零零一个**中枢才谈得上拆：并进了趋势就说明它在本级别有对手盘，
        # 中心定理二已经表过态了。
        kids = split_extended(legs, group[0]) if len(group) == 1 else None
        if kids is None:
            base = len(zss)
            zss += [_renumber(z, base + k) for k, z in enumerate(group)]
            add(kind, zss[base:])
            continue
        for kid in kids:
            split_from[len(zss)] = len(trends)
            zss.append(_renumber(kid, len(zss)))
            add(PANZHENG, zss[-1:])
    return zss, split_from, trends


def trend_to_leg(t: Trend, index: int) -> Leg:
    """走势类型 → 上一层的次级别走势类型。

    high/low 取**真实极值**而不是中枢区间：17课 p36 的中枢公式用的是这一段
    走势的「分别的高、低点」，而 71课 p190 说过「没有人规定分段的结束位置
    一定是最高、最低的」。拿中枢区间去算上级别中枢会把区间算窄。
    """
    return Leg(
        index=index, direction=t.direction,
        start_price=t.start_price, end_price=t.end_price,
        low=t.low, high=t.high,
        src_start=t.start_leg, src_end=t.end_leg, confirmed=True,
    )


# --------------------------------------------------------------------------
# 建树
# --------------------------------------------------------------------------


def build_tree(legs: list[Leg], *, require_confirmed: bool = True,
               max_levels: int = MAX_LEVELS, start: int = 0) -> list[Layer]:
    """从线段（或笔）一路递归到建不出中枢为止。``[0]`` 是本级别。

    ``start`` 只作用于 level 0，换的是 33课 p79 那个起点多义性：同一串段，
    从第几段起找第一个中枢，给出的是不同的合法读法。上面各层的起点由下一层
    的走势类型切分唯一决定，没有自由度。
    """
    layers: list[Layer] = []
    cur = legs
    for level in range(max_levels):
        zss = build_zhongshus(cur, require_confirmed=require_confirmed,
                              start=start if level == 0 else 0)
        if not zss:
            if level:   # 顶到头了；level 0 没中枢也要记一层空的
                break
        zss, split_from, trends = build_layer(cur, zss, level)
        layers.append(Layer(level=level, legs=cur, zhongshus=zss,
                            trends=trends, split_from=split_from))
        if len(trends) < MIN_LEGS:
            break   # 上一层连三段都凑不出，不可能有中枢
        cur = [trend_to_leg(t, i) for i, t in enumerate(trends)]
    # 回填 parent_of：上一层第 k 个中枢由本层哪几个走势类型构成
    for a, b in zip(layers, layers[1:]):
        b.parent_of = {z.index: list(range(z.start_leg, z.end_leg + 1))
                       for z in b.zhongshus}
    return layers


# --------------------------------------------------------------------------
# 四、落回K线下标
#
# 上层的 `Trend` 身上一个时间字段都没有，`start_leg`/`end_leg` 是**本层 legs**
# 的下标。跟人说话要日期，就得一路往下翻到 level 0 再去查笔的 `peak_idx`。
# --------------------------------------------------------------------------


def leg_bars(layers: list[Layer], bis: list[Bi], level: int,
             idx: int) -> tuple[int, int]:
    """第 ``level`` 层第 ``idx`` 根 leg 覆盖的K线下标闭区间。

    上层 leg 的 ``src_start``/``src_end`` 指向的是**下一层 legs** 的下标——
    不是笔，也不是K线（`trend_to_leg` 填的就是下一层的 `start_leg`/`end_leg`）。
    所以必须逐层往下翻，翻到 level 0 才轮到 `Leg.src_*` 真的是笔下标。

    **翻的时候两端要分开翻。** 一根上层 leg 的起点来自它第一根子 leg 的起点、
    终点来自最后一根子 leg 的终点，中间那些子 leg 的下标和它无关；用同一个
    idx 同时取两端，下一层就会取到同一根子 leg 上去，区间直接塌成一点。
    """
    lo = hi = idx
    while level > 0:
        lo = layers[level].legs[lo].src_start
        hi = layers[level].legs[hi].src_end
        level -= 1
    return (bis[layers[0].legs[lo].src_start].start.peak_idx,
            bis[layers[0].legs[hi].src_end].end.peak_idx)


def trend_bars(layers: list[Layer], bis: list[Bi], t: Trend) -> tuple[int, int]:
    """走势类型覆盖的K线下标闭区间。"""
    return (leg_bars(layers, bis, t.level, t.start_leg)[0],
            leg_bars(layers, bis, t.level, t.end_leg)[1])


def layer_to_bars(layers: list[Layer], bis: list[Bi], level: int):
    """给 `chan.beichi` 用的 leg→K线 映射。**上层判背驰必须传这个。**

    `beichi.span_of` 默认按 level 0 处理（``Leg.src_*`` 是笔下标）。上层 leg
    的 ``src_*`` 指的是下一层 legs，直接拿去查 `bis` 不会越界也不会报错——
    上层 legs 比笔少得多，下标永远落在范围内——只会安静地取错分型，于是
    MACD 面积按一段错误的K线窗口去积。这就是从前上层一律 ``beichi=None``
    的原因（见 C-upperbeichi）。

    用法：``detect(legs, zss, bis, m, to_bars=layer_to_bars(layers, bis, L))``
    其中 ``m`` 仍是**原始K线**上的 MACD——面积是那段K线上的客观量，不随
    从哪一层看而变。
    """
    def f(i: int) -> tuple[int, int]:
        return leg_bars(layers, bis, level, i)
    return f
