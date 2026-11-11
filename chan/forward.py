"""从当前结构推演**接下来可能的走势类型**，并给出各自的价位条件。

这一层回答的不是「涨还是跌」，而是缠论真正能回答的那个问题：

  当前这个级别是 XX 走势类型，那么按「走势必完美」，接下来只可能是以下几种
  走法之一；每一种在什么价位、会形成什么买卖点、被什么价位证伪。

### 为什么这件事能由程序做

因为**分支是有限且封闭的**。17课 p36 把走势类型穷尽为三类（上涨/下跌/盘整），
18课 p40 规定了中枢延伸的终止条件，20课 p43 的中心定理二规定了前后两个中枢
只可能有三种关系（趋势延续 / 升级 / 其余）。合起来，从一个中枢出发的后续
发展就是一张可以枚举的表，**而每个分支的触发价位都是中枢的 ZG/ZD/GG/DD，
全是已知数**。

所以这里不猜概率、不排序。程序给出**完备的选项表 + 每项的确定性触发价**，
哪个可能性大、为什么，是模型的事（见 `backtest/agent.py`）。这和全项目的
分工一致：几何归程序，研判归模型。

### 和「走势必完美」的关系

24课：「任何走势类型终将完成」。这正是枚举能成立的前提——当前未完成的走势
**一定**会落到这张表里的某一项上，不存在「第四种可能」。所以事后可以机械地
判定「到底走了哪一项」，模型的排序因此是可打分的（见 `resolve`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .beichi import Beichi
from .num import ge, gt, le, lt
from .types import Direction, ZhongShu
from .zhongshu import (
    PANZHENG,
    TREND_DOWN,
    TREND_UP,
    UPGRADE_LEGS,
    classify,
    relation,
)


@dataclass(slots=True, frozen=True)
class Trigger:
    """一个确定性的价格条件。``op`` 是 ``">="`` 或 ``"<="``。

    不用自由文本写条件，是为了**事后能机械判定**。「放量突破」这种描述没法
    拿后面的K线去对，`收盘 >= 6424.953` 可以。
    """

    op: str
    price: float
    what: str

    def holds(self, px: float) -> bool:
        return ge(px, self.price) if self.op == ">=" else le(px, self.price)

    def __str__(self) -> str:
        return f"{self.what}（{self.op} {self.price:.3f}）"


@dataclass(slots=True)
class Scenario:
    """一种可能的后续走势类型。"""

    code: str                     # 机器用：extend / break_up / trend_down …
    name: str                     # 人话：「中枢震荡延伸」
    becomes: str                  # 走完之后这个级别是什么走势类型
    needs: list[str]              # 结构上还需要发生什么
    trigger: Trigger              # 什么价位算这一支启动了
    forms: str = ""               # 过程中会形成的买卖点，没有就空
    at: float | None = None       # 那个买卖点大致在哪个价位
    kills: Trigger | None = None  # 什么价位算这一支废了
    in_progress: bool = False     # 这一支是不是已经在走了
    #: 「这一支的**非位置型条件**当下已经满足」。目前只有 `reversal` 会用到：
    #: 背驰成立与否是力度判断，不是价格位置。
    #:
    #: 为什么不塞进 `in_progress`：那个字段对三支主干是**位置读数**，而且
    #: 「恰好一支为真」是整个打分层的地基（`forward_walk.in_progress_code`
    #: 取的是第一个为真的，`episode` 那几列的分母全靠它）。背驰成立时价格
    #: 通常正在创新低，于是 `break_down` 和 `reversal` 会同时为真——取第一个
    #: 就等于**静默丢掉** reversal，而且丢得毫无痕迹。两种含义分开放，谁也
    #: 不覆盖谁。
    evidence_now: bool = False
    refs: list[str] = field(default_factory=list)


def trailing_trend(zss: list[ZhongShu]) -> tuple[str, list[ZhongShu]]:
    """**当前**的走势类型，以及构成它的那几个中枢。

    和 `zhongshu.classify` 的区别要说清楚：`classify` 看的是**整段**中枢序列，
    只要历史上有一对相邻中枢不同向，整体就判成盘整。那是 17课 p36 对「一个
    完成的走势类型」的定义，没错，但拿来回答「现在是什么走势」就没用了——
    300308 两年半走出 14 个中枢，`classify` 永远返回盘整。

    推演要的是**末端那一段还在走的东西**：从最后一个中枢往回捋，只要相邻关系
    一直是同一个方向就继续并进来，一旦断掉就停。返回的中枢数 ≥ 2 才算趋势，
    否则是以最后一个中枢为核心的盘整（17课 p36：「只包含一个走势中枢」）。
    """
    if not zss:
        return PANZHENG, []
    if len(zss) == 1:
        return PANZHENG, [zss[0]]

    run = [zss[-1]]
    direction = PANZHENG
    for prev, cur in zip(reversed(zss[:-1]), reversed(zss[1:])):
        rel = relation(prev, cur)
        if rel not in (TREND_UP, TREND_DOWN):
            break
        if direction == PANZHENG:
            direction = rel
        elif rel != direction:
            break
        run.append(prev)
    run.reverse()
    return (direction, run) if len(run) >= 2 else (PANZHENG, [zss[-1]])


# --------------------------------------------------------------------------
# 枚举
# --------------------------------------------------------------------------
#
# **完备性是这一层全部价值之所在**，所以先把它说死。
#
# 17课 p36 把走势类型穷尽为三类（上涨/下跌/盘整），24课「走势必完美」保证
# 当前未完成的那一段**一定**会走完、落到某一类里。两条合起来，从最后一个
# 中枢 Z 出发，下一步只有三种可能，而且**互斥、穷尽**：
#
#   1. 后续次级别段继续触及 [ZD, ZG]  → 中枢延伸，还是以 Z 为核心的盘整
#   2. 向上离开（18课 p40：连续两段不触及）→ 上方生成新中枢 Z2
#   3. 向下离开 → 下方生成新中枢 Z2
#
# 「没有第四种」不是修辞：任何一段要么触及 Z 要么不触及，不触及要么在上要么
# 在下。所以 `resolve()` 事后一定能判出恰好一项，模型的排序因此可打分。
#
# 2 和 3 内部还有一层分叉——新中枢和 Z 的关系（20课 p43 中心定理二）：
# 无重叠则构成趋势，有重叠则升级为更大级别中枢。**这一层在 Z2 成形前无法
# 确定**，所以不把它拆成并列选项，而是作为该分支的两个后续结局写在 `becomes`
# 里。硬拆会制造「看起来有五个选项、其中两个当下根本不可判」的假精确。

_REF_EXTEND = "18课 p40：前后两段必须至少有一个触及中枢区间，否则就离开了"
_REF_3 = "20课 p43：次级别走势类型离开中枢后回试不破 ZG/ZD，构成第三类买卖点"
_REF_REL = "20课 p43 中心定理二：新中枢与原中枢无重叠即趋势，有重叠即升级"
_REF_PERFECT = "24课：走势必完美——未完成的走势类型一定会走完，必落入其一"


def _extend(z: ZhongShu, px: float) -> Scenario:
    inside = ge(px, z.zd) and le(px, z.zg)
    # 33课 p79：第 9 段触发升级（9 段 = 每 3 段一个次级别中枢 × 3 个）。一个
    # 已经 7 段的中枢，「继续延伸」和一个 3 段的中枢完全不是一回事——前者再走
    # 两段就不再是这个级别的事了。不印出来，选项表上两者长得一模一样，模型
    # 没有任何依据区分。
    #
    # `span` 取到 `core_end`：离开段不属于这个中枢。
    #
    # 第三种情形单独印，它是这一层唯一有实质信息的那个分支：段数够了但那 3 个
    # 次级别中枢**互不重合**，那是更大级别的**趋势**在走，不是更大级别的中枢，
    # 不能按「升级」读。全缓存实测 846 个够 9 段的中枢里有 132 个（15.6%）是
    # 这一种，不说清模型一定会读错。
    span = z.core_end - z.start_leg + 1
    if span < UPGRADE_LEGS:
        room = (f"围绕这个中枢震荡的段数是 {span}，离升级点 {UPGRADE_LEGS} 段还差 "
                f"{UPGRADE_LEGS - span} 段；到第 {UPGRADE_LEGS} 段就该升一级看"
                f"（33课 p79：每 3 段构成一个次级别中枢，3 个就是上一级的中枢）")
    elif z.level_up:
        room = (f"围绕这个中枢震荡的段数已有 {span}（>= {UPGRADE_LEGS}），"
                f"重读出来的 3 个次级别中枢**有公共交集**，**已到/超过**升级点，"
                f"本级别的「延伸」实际上已经不成立，该升一级看"
                f"（33课 p79：3 个次级别中枢重合）")
    else:
        room = (f"围绕这个中枢震荡的段数已有 {span}（>= {UPGRADE_LEGS}），但重读"
                f"出来的 3 个次级别中枢**没有公共交集**——这是更大级别的**趋势**"
                f"不是更大级别的中枢（20课 p43 中心定理二），不要按「升级」读")
    return Scenario(
        code="extend",
        name="中枢震荡延伸",
        becomes="盘整（仍以这个中枢为核心）",
        needs=["后续次级别段继续触及 [ZD, ZG]，没有连续两段落在区间之外",
               room],
        trigger=Trigger(">=", z.zd, "价格回到中枢区间内"),
        forms="",  # 震荡里没有三类点，只有中枢上沿下沿的高抛低吸
        at=None,
        kills=None,  # 延伸这一支是由另外两支的发生来否定的，没有自己的价位
        in_progress=inside,
        refs=[_REF_EXTEND],
    )


def _leave(z: ZhongShu, px: float, up: bool) -> Scenario:
    """向上/向下离开。两支严格镜像，合用一个函数以免手抖写反。"""
    edge = z.zg if up else z.zd       # 回试不能破的那条线（C-zd3：三卖比 ZD）
    far = z.gg if up else z.dd        # 中枢的极值，新中枢要越过它才算趋势
    side = "买" if up else "卖"
    return Scenario(
        code="break_up" if up else "break_down",
        name=f"向{'上' if up else '下'}离开中枢",
        becomes=(f"若新中枢与原中枢无重叠 → {'上涨' if up else '下跌'}趋势；"
                 f"若有重叠 → 升级为更大级别中枢（仍是盘整，但级别更高）"),
        needs=[f"一个次级别段向{'上' if up else '下'}离开，且连续两段不再触及 "
               f"[{z.zd:.3f}, {z.zg:.3f}]",
               f"随后以一个次级别段回试，{'低' if up else '高'}点"
               f"{'不跌破' if up else '不升破'} {'ZG' if up else 'ZD'}="
               f"{edge:.3f} → 构成 3{side}",
               f"再走出三段重叠，形成新中枢；新中枢"
               f"{'DD 高于' if up else 'GG 低于'} 原中枢"
               f"{'GG' if up else 'DD'}={far:.3f} 才算趋势"],
        trigger=Trigger(">=" if up else "<=", edge,
                        f"价格{'升破' if up else '跌破'}"
                        f"{'ZG' if up else 'ZD'}并站住"),
        forms=f"3{side}",
        at=edge,
        kills=Trigger("<=" if up else ">=", z.zd if up else z.zg,
                      f"回试直接{'跌回' if up else '升回'}中枢"
                      f"{'ZD' if up else 'ZG'}之内，离开不成立"),
        in_progress=(gt(px, z.zg) if up else lt(px, z.zd)),
        refs=[_REF_3, _REF_REL],
    )


def _beichi_line(b: Beichi | None, down: bool,
                 why: str | None = None) -> tuple[str, bool]:
    """把背驰检测的实测值写成一行人话，并给出「这一支是不是已经在走」。

    返回 ``(那一行字, in_progress)``。

    `b is None` 是**常态而不是错误**：最后一个中枢还没走掉时 `_slice_leave`
    会给出空区间（`lo > hi`），`detect` 返回 None。那是「还判不了」，必须如实
    说出来——当成「没背驰」会把一个未知悄悄变成一个否定。

    `in_progress` 的门槛比 `hit` 严：要 `hit and new_extreme` **且方向对得
    上**。37课 p96 说得很死，「c 一定要创出新高/新低」，不创新高新低根本不构成
    趋势背驰；而 `hit = area_ok or dif_ok` 只管力度，不管有没有创新极值。
    """
    if b is None:
        # 不传背驰可能有两种原因，默认那条是最常见的那种。调用方知道是别的
        # 原因（比如级别树的上层**不做**背驰检测）就把 `why` 传进来——
        # 「没测」和「测了没走出来」都是未知，但对读的人不是同一句话。
        return (f"力度：还判不了——{why or '离开段还没走出来（最后一个中枢尚未走掉）'}",
                False)
    want = Direction.DOWN if down else Direction.UP
    ratio = (b.area_leave / b.area_enter) if b.area_enter else float("nan")
    line = (f"力度：c 段面积 {b.area_leave:.1f} / b 段 {b.area_enter:.1f} = "
            f"{ratio:.2f}（缩量{'✓' if b.area_ok else '✗'}）；"
            f"DIF {b.dif_enter:+.4f} → {b.dif_leave:+.4f}"
            f"（未同步创新极值{'✓' if b.dif_ok else '✗'}"
            f"{'' if b.dif_crossed_zero else '，黄白线未回抽0轴、此判据不适用'}）；"
            f"创新{'低' if down else '高'}{'✓' if b.new_extreme else '✗'}；"
            f"{'趋势' if b.kind == 'trend' else '盘整'}背驰，"
            f"结论 {'成立' if b.hit else '不成立'}"
            f"{'（两判据都满足，更标准）' if b.standard else ''}")
    on = b.hit and b.new_extreme and b.direction is want
    return line, on


def _reversal(run: list[ZhongShu], direction: str,
              b: Beichi | None = None, why: str | None = None) -> Scenario:
    """已经在趋势里时，多出来的那一支：背驰 → 一类点 → 转折。

    **这不是第四种走势类型**，它是「向反方向离开」那一支的特例，只是触发
    机制不同：普通的离开由价格位置定义，这一支由**力度**定义（c 段创新低/
    新高但力度弱于 A 段，37课 p96 / 24课 p53）。

    所以它和 `break_*` 不互斥——背驰点出现之后，走的就是反方向离开那一支。
    单独列出来是因为它的**入场位置完全不同**：一类点在趋势的极值端，而三类
    点在中枢边缘，两者差着一整段。不分开说，推演就失去了操作意义。
    """
    down = direction == TREND_DOWN
    last = run[-1]
    far = last.dd if down else last.gg
    side = "买" if down else "卖"
    force, on = _beichi_line(b, down, why)
    return Scenario(
        code="reversal",
        name=f"趋势背驰 → 1{side}",
        becomes=f"下跌/上涨趋势结束，转为盘整（或反向走势）",
        needs=[f"当前是{'下跌' if down else '上涨'}趋势，已有 {len(run)} 个"
               f"依次同向且不重叠的中枢",
               f"最后一段必须{'创新低' if down else '创新高'}（跌破/升破 "
               f"{far:.3f}）——不创新高新低就根本不构成趋势背驰",
               force,
               f"背驰点即 1{side}，之后第一个次级别回"
               f"{'调' if down else '抽'}低点构成 2{side}"],
        trigger=Trigger("<=" if down else ">=", far,
                        f"{'跌破' if down else '升破'}趋势"
                        f"{'前低' if down else '前高'}后出现背驰"),
        forms=f"1{side}（随后 2{side}）",
        at=far,
        kills=Trigger(">=" if down else "<=", last.zg if down else last.zd,
                      f"未创{'新低' if down else '新高'}就"
                      f"{'升回' if down else '跌回'}中枢，这一段不是背驰段"),
        # `in_progress` 对这一支**恒为 False 而且应该恒为 False**：背驰成立
        # 时价格正在创新低，位置上走的是 break_down，reversal 说的是「接下来
        # 要翻」——那是预判不是位置。原先的硬编码没错，错在引擎对唯一有量化
        # 依据的那一支完全不表态，所以把依据放进 `evidence_now`。
        in_progress=False,
        evidence_now=on,
        refs=["37课 p96：趋势背驰的结构门槛，c 段必须创新高/新低",
              "24课 p53：力度用 MACD 面积比较",
              "53课 p126：该级别趋势背驰点即第一类买卖点"],
    )


def project(zss: list[ZhongShu], last_close: float, *,
            beichi: Beichi | None = None, beichi_note: str | None = None,
            trend_zss: list[ZhongShu] | None = None) -> dict:
    """给出当前走势类型 + 完备的后续选项表。

    返回的 ``scenarios`` **是穷尽的**（见上面那段注释）：事后一定恰好命中
    其中一项。调用方（模型）要做的是排序和解释，不是补充选项。

    ``beichi`` 是 `chan.beichi.detect(legs, zss, bis, macd)` 的结果，可选。
    不传等于「没测」——选项表里的力度那一行会如实说「还判不了」，**不会**
    说「没背驰」。两者差着一个未知和一个否定。``beichi_note`` 换掉「还判不了」
    后面那句默认原因，调用方知道是别的原因时传（比如级别树的上层压根不测）。

    ``trend_zss`` 是**当前走势类型所含的中枢子序列**，由级别树切出来
    （`tree.build_tree(legs)[0].trends[-1].zhongshus`）。给了就用它定
    ``trend``/``run``，不给退回 `trailing_trend(zss)`。

    两者的差别是真实存在的，不是口味问题：`trailing_trend` 从末枢往回捋，
    **不管那些中枢是不是已经归属上一个走完的走势类型**，于是同一个中枢会被
    两个走势类型同时认领。而走势类型是时间轴的划分，首尾相接、不重叠
    （72课：分界是一个点）。实测 30 只日线票，trailing 多认领恰好一个中枢的
    有 4 只，其中 3 只因此把 ``kind`` 判反（见 C-trendnow）。
    """
    if not zss:
        return {"trend": PANZHENG, "run": 0, "zhongshu": None,
                "scenarios": [], "note": "还没走出中枢，无法推演"}

    if trend_zss:
        # 树切出来的那一段本身就是一个走势类型，所以这里 `classify` 是对口径
        # 的——17课 p36 定义的正是「**一个**走势类型算什么」。它退化成常数
        # 盘整只发生在把它套到整段历史上的时候（C-trendnow）。
        run, direction = list(trend_zss), classify(trend_zss)
    else:
        # 空列表和 None 同样走这条路。树的末尾那个走势类型必然带着中枢——
        # `build_layer` 只在**一组中枢之前**插「无中枢」段，尾巴上那些还没被
        # 认领的段不进 `trends`。所以这里不存在「当前走势类型没有中枢」。
        direction, run = trailing_trend(zss)
    z = zss[-1]
    where = ("中枢上方" if gt(last_close, z.zg)
             else "中枢下方" if lt(last_close, z.zd) else "中枢区间内")

    # 三支互斥穷尽的主干，顺序固定：延伸 / 向上离开 / 向下离开。
    scenarios = [_extend(z, last_close), _leave(z, last_close, True),
                 _leave(z, last_close, False)]
    # 趋势中额外给出背驰转折这一支（它是反向离开的力度特例，不是第四类）。
    if direction in (TREND_UP, TREND_DOWN):
        scenarios.append(_reversal(run, direction, beichi, beichi_note))

    return {
        "trend": direction,
        "run": len(run),
        "zhongshu": {"zd": z.zd, "zg": z.zg, "gg": z.gg, "dd": z.dd,
                     "departed": z.departed, "confirmed": z.confirmed},
        # 最后两个中枢的关系。`resolve` 判结局时算的就是这个字符串，推演这边
        # 却没印——于是模型看得见「有 3 个中枢」，看不见「它们到底是怎么接
        # 上的」。20课 p43 中心定理二：无重叠即趋势，有重叠即升级，两种情况
        # 下「再来一个中枢」的含义完全不同。只有一个中枢时没有关系可言。
        "relation": relation(zss[-2], zss[-1]) if len(zss) >= 2 else None,
        "last_close": last_close,
        "where": where,
        "scenarios": scenarios,
        "completeness": _REF_PERFECT,
    }


# --------------------------------------------------------------------------
# 事后判定：到底走了哪一支
# --------------------------------------------------------------------------


def _same(a: ZhongShu, b: ZhongShu) -> bool:
    """是不是同一个中枢。用 ZG/ZD 认，**不用下标**。

    段下标会因为后面的K线改变前面的划分而整体平移（笔可以被回退重划），拿它
    认同一性迟早张冠李戴。ZG/ZD 由前两段定死、**中枢诞生后不再变**（17课
    p36），是这一层唯一稳定的身份。

    理论上两个不同中枢可能撞上同一对 ZG/ZD，实践中这要求两处的段极值精确
    相等到浮点级别，忽略。
    """
    return abs(a.zg - b.zg) < 1e-6 and abs(a.zd - b.zd) < 1e-6


def zs_key(z) -> tuple[float, float]:
    """中枢的**可哈希**身份。和 `_same` 同一口径，别各写一份。

    `_same` 是两两比较，用在 `resolve` 里；这个是用来分桶的（把滚动走查的
    几百个检查点按「说的是哪个中枢」折叠，见 `backtest.forward_walk`）。
    两者必须同一个口径，否则会出现「`resolve` 认为是同一个、分桶认为是两个」
    这种没人查得出来的分裂。

    ``z`` 可以是 `ZhongShu`，也可以是它序列化之后的 dict（JSONL 里存的就是
    dict，打分和分桶都要能直接用）。

    和 `_same` 的细微差别：这里是**网格吸附**（round 到 1e-6），不是距离判定。
    相差 5e-7 的一对值 `_same` 认同一、吸附可能分到两格。真正的口径体检交给
    `forward_walk.recluster`——它用的是按中枢宽度算的宽松容差，能看出 1e-6
    这一级到底有没有把同一个中枢切开。
    """
    zg, zd = ((z["zg"], z["zd"]) if isinstance(z, dict) else (z.zg, z.zd))
    return (round(zg, 6), round(zd, 6))


def resolve(z: ZhongShu, later: list[ZhongShu]) -> dict:
    """拿后来的中枢序列，判定从 ``z`` 出发实际走了哪一支。

    判定**不自己定价格规则**，而是看引擎在更长的K线上重新划出了什么——这是
    整个项目一贯的分工，也避免「我说跌破算离开、引擎说还在震荡」这种两套
    标准打架。

    返回 ``code`` 是 `project` 给的那几个之一，外加 ``"unresolved"``：后面的
    K线还不够走出结论。**「还没走完」必须是一个显式结果**，不能硬塞进三支
    里凑一个——那等于拿不确定的样本去给模型打分。
    """
    hit = next((i for i, x in enumerate(later) if _same(x, z)), None)
    if hit is None:
        # 原中枢在更长的K线上被重划掉了。这不是某一支走成了，是划分本身变了。
        return {"code": "redrawn", "detail": "原中枢在更长的K线上不再成立"}

    now = later[hit]
    after = later[hit + 1:]

    # 18课 p40 的「离开」是**连续两段都不触及**，引擎把它记在 `depart_leg`
    # 上。没有它就没有离开——这一支只可能是「还在延伸」或「还看不出来」。
    #
    # 这里曾经有过一整块处理「没离开却后面排着另一个中枢」的代码，现在没有
    # 了，因为那个形态**在构造上已经不可能**。留个来龙去脉，这是本项目踩得
    # 最深的一个坑：
    #
    # 它的唯一来源是 `zhongshu_at` 原先的 `capped` 分支——33课 p79 的段数
    # 上限顶到了就 `break`，而那一段**仍然触及** [ZD, ZG]，`depart_leg` 留
    # 空，下一个中枢就从紧接着的那一段起步，两者之间没有连接段。而同向的两
    # 个中枢之间必然隔着一段：下跌走势里的中枢是「上下上」、以向上段开头，
    # 把价格从前一个中枢带下来的那个向下段方向上就不可能是它的第一段。所以
    # 间隔为 0 本身就证明那两个框划错了（见 `invariants.check_zhongshus`）。
    #
    # 当时的补救是在这里识别出来、归到 `extend`。效果是真的——H=480 下 443
    # 个 `break_*` 标签里 180 个（40.6%）是假的，而且全部偏向同一边（全判成
    # break_down），「恒猜 break_down」那个基准就是被它撑起来的。但那只是把
    # 坏结构挡在判定层，中枢序列本身仍然是错的。后来改成不切断、只置
    # `level_up`，根源就没了：循环只有两个出口，两段连续不触及时 `depart_leg`
    # 必有值；走到数据尽头时 `misses <= 1`，后面凑不满三段，不会再有中枢。
    # **所以 `not now.departed` 蕴含 `after` 为空。**
    if not now.departed:
        if now.leg_count <= z.leg_count:
            return {"code": "unresolved",
                    "detail": "还没走出新中枢，也没继续延伸"}
        return {"code": "extend", "detail": (
            f"中枢从 {z.leg_count} 段延伸到 {now.leg_count} 段，走势始终没有"
            f"离开（depart_leg=None）")}

    if not after:
        # 走到这里 `now.departed` 必为真（上面那条已经把没离开的拦掉了），
        # 也就是**确实离开了但新中枢还没成形**。它和「还在延伸」长得一样，
        # 只能看中枢有没有真的延长过——段数变多说明后续段仍在触及区间。
        #
        # 比的是**段数**不是段下标：下标会随重划整体平移，段数不会。
        grew = later[hit].leg_count > z.leg_count
        return {"code": "extend" if grew else "unresolved",
                "detail": (f"中枢从 {z.leg_count} 段延伸到 "
                           f"{later[hit].leg_count} 段" if grew
                           else "还没走出新中枢，也没继续延伸")}

    z2 = after[0]
    rel = relation(later[hit], z2)
    up = gt(z2.dd, later[hit].zg) or rel == TREND_UP
    return {
        "code": "break_up" if up else "break_down",
        "detail": (f"新中枢 [{z2.zd:.3f}, {z2.zg:.3f}]，与原中枢关系：{rel}"),
        "relation": rel,
        "becomes": (TREND_UP if rel == TREND_UP else
                    TREND_DOWN if rel == TREND_DOWN else "升级/盘整"),
        "new_zhongshu": {"zd": z2.zd, "zg": z2.zg},
    }
