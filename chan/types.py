"""核心数据类型。

层次：Bar（原始K线） -> MergedBar（包含处理后） -> Fractal（分型） -> Bi（笔）
     -> Seg（线段） -> ZhongShu（中枢） -> BSPoint（买卖点）

本模块只放数据结构，不放算法。所有算法模块都是纯函数，零 IO。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Direction(Enum):
    """方向。向上 / 向下。"""

    UP = 1
    DOWN = -1

    def flip(self) -> Direction:
        return Direction.DOWN if self is Direction.UP else Direction.UP


class FractalKind(Enum):
    """分型种类。62课 p161。"""

    TOP = 1
    BOTTOM = -1

    def flip(self) -> FractalKind:
        return FractalKind.BOTTOM if self is FractalKind.TOP else FractalKind.TOP


@dataclass(frozen=True, slots=True)
class Bar:
    """一根原始K线。

    idx 是它在原始序列中的下标，贯穿全流程用于回溯到真实时间和价格。
    ts 用 str（ISO 格式）而不是 datetime，避免时区问题污染纯计算层；
    数据层负责保证它是可比较的递增字符串。
    """

    idx: int
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"bar[{self.idx}] high {self.high} < low {self.low}")


@dataclass(slots=True)
class MergedBar:
    """包含处理后的K线，65课 p168 的 [di, gi] 区间。

    seq      在合并后序列中的下标
    low/high 即 d / g
    direction 产生这次合并时所处的方向；没有发生过合并的K线沿用进入时的方向
    begin_idx / end_idx  覆盖的原始K线下标闭区间
    merged_count 吃掉了多少根原始K线（1 表示没合并过）

    注意：合并是**顺序**进行的，不满足传递律（65课 p168 明说），所以必须
    「先合1-2得新K线，再拿新K线比第3根」，不能先算出所有两两包含关系。
    """

    seq: int
    low: float
    high: float
    direction: Direction
    begin_idx: int
    end_idx: int
    merged_count: int = 1

    @property
    def is_merged(self) -> bool:
        return self.merged_count > 1


@dataclass(slots=True)
class Fractal:
    """分型。62课 p161：只看高低点，不分阴阳，与开盘收盘价无关。

    m_idx 指向**中间**那根合并K线在合并序列中的下标，分型占据
    m_idx-1, m_idx, m_idx+1 三根。

    strength 是 82课 p212 的强弱标注，**仅作为标注输出，不参与任何过滤**
    ——它依赖开盘收盘价，而分型定义明确排除了这些。
    """

    kind: FractalKind
    m_idx: int
    high: float
    low: float
    begin_idx: int  # 原始K线下标：左侧K线的 begin_idx
    end_idx: int  # 原始K线下标：右侧K线的 end_idx
    peak_idx: int  # 原始K线下标：极值所在（中间合并K线的 begin_idx）
    ts: str = ""
    strength: str | None = None  # "strong" | "weak" | None(未标注)

    @property
    def price(self) -> float:
        """分型的代表价：顶取高点，底取低点。"""
        return self.high if self.kind is FractalKind.TOP else self.low


@dataclass(slots=True)
class Bi:
    """笔。77课 p195。

    direction 由起点分型决定：底->顶 为向上，顶->底 为向下。
    confirmed=False 表示这是序列末端尚未被后续分型确认的笔（盘后扫描时
    末端笔天然未完成，77课 p195「笔没完成」）。绝不能当成已确认信号。
    """

    index: int
    start: Fractal
    end: Fractal
    direction: Direction
    confirmed: bool = True

    @property
    def start_price(self) -> float:
        return self.start.price

    @property
    def end_price(self) -> float:
        return self.end.price

    @property
    def low(self) -> float:
        return min(self.start_price, self.end_price)

    @property
    def high(self) -> float:
        return max(self.start_price, self.end_price)

    @property
    def m_span(self) -> tuple[int, int]:
        """覆盖的合并K线下标闭区间（两端分型的中心）。"""
        return (self.start.m_idx, self.end.m_idx)

    @property
    def amplitude(self) -> float:
        return self.high - self.low


@dataclass(slots=True)
class SeqElem:
    """特征序列的一个元素。67课 p172：「把每一元素看成是一 K 线」。

    元素就是笔，但只取它的区间 [low, high]，并且可以像K线一样做包含处理，
    处理过的序列叫**标准特征序列**。

    ``bi_indices`` 记录这个元素由哪些笔合并而来，便于回溯核对。
    """

    low: float
    high: float
    bi_indices: list[int] = field(default_factory=list)

    @property
    def first_bi(self) -> int:
        return self.bi_indices[0]


@dataclass(slots=True)
class Seg:
    """线段。67课 p172 特征序列标准（65课 p169 的笔破坏标准已被作者废弃）。

    case   按 67课 的哪一种情况确认的：1 / 2；0 表示尚未确认
    pending  71课 p181「第三笔完全在第一笔的范围内」导致方向待定
    skipped_pending  途中跳过的那个始终没能了结的待定点（笔下标，-1 表示没有）。
      这条线段的终点**不是**本段的极值时，多半就是因为它——见 CONVENTIONS C-pendskip。
      只用于审计和画图，不参与任何判定。
    """

    index: int
    start_bi: int
    end_bi: int
    direction: Direction
    start: Fractal
    end: Fractal
    case: int = 0
    confirmed: bool = True
    pending: bool = False
    skipped_pending: int = -1
    #: 这一段的终点是被哪种「当下判断」口径提前判出来的（`chan.segend` 的方法
    #: 代号），空串表示走的是 67课 特征序列那条正路。**只用于审计和统计，不参与
    #: 任何判定**。提前判出的前段为 confirmed=True（否则中枢层会把它剪掉，
    #: 见 `segend.split_tail`）；它仍可能被后续K线推翻，稳定性必须靠走查测量。
    prov_end: str = ""

    @property
    def start_price(self) -> float:
        return self.start.price

    @property
    def end_price(self) -> float:
        return self.end.price

    @property
    def low(self) -> float:
        """**只是两端点的低者**，不是线段的真实最低点。

        71课 p190：「没有人规定分段的结束位置一定是最高、最低的。」线段内部
        完全可能有笔跑到两端之外。要真实极值请用 `zhongshu.leg_from_seg`，
        它遍历构成笔来算。中枢的高低点必须用真实极值（17课 p36）。
        """
        return min(self.start_price, self.end_price)

    @property
    def high(self) -> float:
        """只是两端点的高者，不是线段的真实最高点。见 `low` 的说明。"""
        return max(self.start_price, self.end_price)

    @property
    def bi_count(self) -> int:
        return self.end_bi - self.start_bi + 1


@dataclass(slots=True)
class MergeConfig:
    """包含处理与分型/笔的配置。

    每一项都对应 CONVENTIONS.md 里的一条登记，因为它们都是原文没写死的地方。
    """

    # 起手方向策略。原文空白：第一根K线没有 n-1，方向无定义。
    # "drop" —— 丢弃开头互相包含的K线，从第一对非包含相邻K线起算（默认）
    # "up" / "down" —— 对开头未定组强制指定方向
    initial_direction: str = "drop"

    # 跳空是否阻断包含合并。原文**完全没写**，默认 False（字面读法）。
    # 常见的「缺口不做包含处理」是把 78课 p199 的特征序列规则错误下放到K线层。
    gap_blocks_merge: bool = False

    # 笔的定义。"new" = 社区新笔（非原文），"old" = 77课 p195 原文标准。
    bi_mode: str = "new"

    # 注：「线段开始的三笔必须有重合」不做成开关。65课 p169 与 77课 p196
    # 两处都写死了，且 65课 说「这个前提在前面可能没有特别强调，这里必须
    # 特别强调一次」——这是原文规定，不是我方约定。

    # 线段：是否启用 71课 p181 的「第三笔完全在第一笔范围内」待定状态处理。
    # 关掉的话这种情形会退化成普通的特征序列包含处理。
    enable_pending_seg: bool = True

    # 「当下判断末段是否已经结束」的口径，见 `chan.segend`。
    #
    # None（默认）= 只认 67课 特征序列，也就是 78课 p197「线段必须被线段破坏
    # 才能确定其完成」。这是原文口径，也是所有既有实测数字的基线，**不要为了
    # 让某个方法好看就改默认值**。
    #
    # 给了方法代号就额外做一件事：把末段（恒为未确认）判一判，认为它其实已经
    # 在中途结束的话就拆成两段。前段按本口径算完成并进入中枢层，新末段未确认；
    # 前段仍可能随前缀重划消失，所以必须另报存活/稳定，不能把它当历史确认段。
    seg_end: str | None = None

    # 106课 的最低延伸负向过滤。只否决过弱、过短的反向走势，不独立造信号；
    # 默认 False，因此历史五种口径的结果仍可逐字节复现。
    seg_end_ma_filter: bool = False


@dataclass(slots=True, frozen=True)
class Leg:
    """一个「次级别走势类型」，中枢的构成单位。

    中枢的定义是级别无关的（17课 p36「被至少三个连续次级别走势类型所重叠的
    部分」），所以中枢算法不直接吃笔或线段，而是吃这个中性的区间类型。
    笔中枢喂 `legs_from_bis`，线段中枢喂 `legs_from_segs`。

    ``low``/``high`` 必须是这一段走势的**真实极值**，不是两端点——17课 p36
    的公式用的是「分别的高、低点」，而 71课 p190 明确说过终点未必是极值。
    """

    index: int
    direction: Direction
    start_price: float
    end_price: float
    low: float
    high: float
    src_start: int  # 在来源序列（笔/线段）里的起止下标，便于回溯核对
    src_end: int
    confirmed: bool = True


@dataclass(slots=True)
class ZhongShu:
    """走势中枢。17课 p36 定义 + 20课 p43 的 ZG/ZD/GG/DD 指标体系。

    ``zg``/``zd`` 由**前三个**次级别走势类型定死，中枢诞生后不再变（17课 p36
    「具体的计算以前三个连续次级别的重叠为准」）。3买/3卖判定用它。

    ``gg``/``dd``/``g``/``d`` 随中枢延伸浮动，是「围绕该中枢震荡」的包络
    （20课 p43 + 37课 p96）。趋势判定、中枢扩展判定用它。

    这两套指标的分工是中枢这一层最容易混淆的地方，见 CONVENTIONS C-zs。

    ``direction`` 是中枢的**形成方向**，即 Z 走势段的方向：回升形成的中枢
    其 Z 段向上，回调形成的向下（20课 p43）。它不代表中枢自身在涨或跌。
    """

    index: int
    direction: Direction
    start_leg: int
    end_leg: int
    zg: float
    zd: float
    gg: float
    dd: float
    g: float
    d: float
    confirmed: bool = True

    # 33课 p79：延伸到第 9 段时「就构成更大级别的中枢了」。置位表示这个中枢
    # 已经顶到同级别的延伸上限，**应当升一级去看**，见 CONVENTIONS C-ext。
    level_up: bool = False

    # 走势离开这个中枢的那一段（连续两段都不再触及，18课 p40）。None 表示
    # 还没走掉。见 CONVENTIONS C-leave / C-core。
    #
    # **它可能等于 `end_leg`**：17课 p36 要求中枢由前三段的重叠确定，所以当
    # 第三段自己就是那段飞走的走势时，它既是中枢的构成段、又是离开段。
    depart_leg: int | None = None

    @property
    def departed(self) -> bool:
        return self.depart_leg is not None

    @property
    def leg_count(self) -> int:
        return self.end_leg - self.start_leg + 1

    @property
    def leave_leg(self) -> int | None:
        """离开段在来源序列里的下标。20课 p43 第三类买卖点定理的起点。"""
        return self.depart_leg

    @property
    def core_end(self) -> int:
        """最后一段**围绕中枢震荡**的走势。GG/DD 的包络只数到这里。

        27课 p66（第 612 行）把 DD 说成「min(dn)，也就是**围绕该中枢震荡的
        最低点**」——离开段是飞走的那一段，不在「围绕震荡」之列。见 C-core。
        """
        return self.end_leg if self.depart_leg is None else self.depart_leg - 1

    @property
    def next_start(self) -> int:
        """下一个同级别中枢最早可能从第几段开始。

        33课 p79（第 630 行）：「整个走势其实就简化为**两个中枢与连接两者的
        一个走势**」——a+A+b+B+c 里的 b 是连接段，**不属于前后任何一个中枢**。
        所以要跨过离开段再找下一个中枢。
        """
        return self.end_leg + 1 if self.depart_leg is None else self.depart_leg + 1

    @property
    def height(self) -> float:
        return self.zg - self.zd

    def contains(self, price: float) -> bool:
        """价格是否落在中枢区间 [ZD, ZG] 内（含边界）。"""
        from .num import ge, le

        return ge(price, self.zd) and le(price, self.zg)
