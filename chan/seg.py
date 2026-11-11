"""线段。67课 p172 特征序列标准 + 71课 pp.181-182 的再分辨。

**不用** 65课 p169 的笔破坏标准——作者在 67课 p172 明确废弃了它：
「以后关于线段的划分，都以此精确的定义为基础。」

67课 p172 原文（`.source/book.txt` 第 1311-1322 行）：

  以向上笔开始的线段，可以用笔的序列表示：S1X1S2X2S3X3…SnXn。
  序列 X1X2…Xn 成为以向上笔开始线段的特征序列；序列 S1S2…Sn 成为以向下笔
  开始线段的特征序列。**特征序列两相邻元素间没有重合区间，称为该序列的一个
  缺口。**
  把每一元素看成是一 K 线，……也可以对此进行非包含处理。经过非包含处理的特征
  序列，成为标准特征序列。
  注意，**以向上笔开始的线段的特征序列，只考察顶分型**；以向下笔开始的线段，
  只考察底分型。

  第一种情况：顶分型中，第一和第二元素间**不存在**缺口 → 线段在该顶分型的
    高点处结束。
  第二种情况：顶分型中，第一和第二元素间**存在**缺口 → 如果从该分型最高点
    开始的向下一笔开始的序列的特征序列**出现底分型**，线段在该顶分型的高点
    处结束。
  强调：第二种情况下，后一特征序列不一定封闭前一特征序列的缺口，而且第二个
    序列中的分型，不分第一二种情况，只要有分型就可以。

71课 p182 把它翻译成了可直接实现的程序：

  「假设某转折点是两线段的分界点，然后对此用线段划分的两种情况去考察是否
    满足，如果满足其中一种，那么这点就是真正的线段的分界点；如果不满足，
    那就不是，原来的线段依然延续。」

  「特征序列的分型中，**第一元素就是该假设转折点前线段的最后一个特征元素，
    第二个元素就是从这转折点开始的第一笔**，这两者之间是同方向的，因此，
    如果这两者之间有缺口，那么就是第二种情况，否则就是第一种。」

  包含处理的不对称（71课 p182）：「在这假设的转折点前后那两元素，是不存在
    包含关系的……但假设的转折点后的顶分型的元素，是可以应用包含关系的。」
    —— 即第一元素与第二元素之间**禁止**合并，第二元素及其之后**允许**合并。

术语警告：这里的「缺口」是**特征序列缺口**（相邻元素区间无重合），和
`merge.has_gap` 的**K线跳空**（相邻原始K线之间无成交的价格区间，77课 p194）
是同名不同物的两回事。
"""

from __future__ import annotations

from .merge import has_inclusion
from .num import ge, gt, le, lt
from .num import overlap as _range_overlap
from .types import Bi, Direction, MergeConfig, Seg, SeqElem


def elem_of(bis: list[Bi], i: int) -> SeqElem:
    b = bis[i]
    return SeqElem(low=b.low, high=b.high, bi_indices=[i])


def has_seq_gap(a: SeqElem, b: SeqElem) -> bool:
    """特征序列缺口：两相邻元素间没有重合区间。67课 p172。

    端点刚好接触算不算重合？原文没说。这里按「算重合、不算缺口」处理
    （见 CONVENTIONS.md C-seqtouch），与 `num.overlap` 的口径一致。
    """
    return not _range_overlap(a.low, a.high, b.low, b.high)


def _contains(outer: SeqElem, inner: SeqElem) -> bool:
    return le(outer.low, inner.low) and le(inner.high, outer.high)


def standardize(
    elems: list[SeqElem],
    first_direction: Direction,
    ref: SeqElem | None = None,
) -> list[SeqElem]:
    """非包含处理，得到**标准特征序列**。67课 p172。

    合并规则与K线完全相同（65课 p168），方向参照取前一个元素。

    ``ref`` 是序列中紧邻的前一个元素（通常就是第一元素 e1）。它**只**用来给
    首次合并提供方向参照，自己不参与合并——这正是 71课 p182 的不对称规则：
    「在这假设的转折点前后那两元素，是不存在包含关系的……但假设的转折点后的
    顶分型的元素，是可以应用包含关系的。」禁止的是 e1 与 e2 相互合并，不是
    禁止拿 e1 当方向参照；e1 本来就是这条特征序列里真实存在的前一元素。

    漏掉 ``ref`` 曾造成一类真实 bug：暴跌中一根包含前元素的巨大反向笔被
    错误地向上合并，凭空造出一个高于 e1 的元素，伪造出顶分型，于是在下跌
    途中划出一条「顶低于底」的向上线段。

    连 ``ref`` 都没有时（整条序列的开头）才退回 ``first_direction``，取所
    考察线段的方向。这一步原文没写，是我方约定（CONVENTIONS.md C-seqdir）。
    """
    out: list[SeqElem] = []
    for e in elems:
        if not out:
            out.append(SeqElem(e.low, e.high, list(e.bi_indices)))
            continue
        cur = out[-1]
        if has_inclusion(cur.low, cur.high, e.low, e.high):
            prev = out[-2] if len(out) >= 2 else ref
            if prev is not None:
                d = Direction.UP if gt(cur.high, prev.high) else Direction.DOWN
            else:
                d = first_direction
            if d is Direction.UP:
                cur.low = max(cur.low, e.low)
                cur.high = max(cur.high, e.high)
            else:
                cur.low = min(cur.low, e.low)
                cur.high = min(cur.high, e.high)
            cur.bi_indices.extend(e.bi_indices)
        else:
            out.append(SeqElem(e.low, e.high, list(e.bi_indices)))
    return out


def _is_top(a: SeqElem, b: SeqElem, c: SeqElem) -> bool:
    return gt(b.high, a.high) and gt(b.high, c.high) and gt(b.low, a.low) and gt(b.low, c.low)


def _is_bottom(a: SeqElem, b: SeqElem, c: SeqElem) -> bool:
    return lt(b.low, a.low) and lt(b.low, c.low) and lt(b.high, a.high) and lt(b.high, c.high)


def find_seq_fractal(elems: list[SeqElem], want_top: bool) -> int | None:
    """在标准特征序列里找第一个指定方向的分型，返回中间元素的下标。"""
    for i in range(1, len(elems) - 1):
        trio = (elems[i - 1], elems[i], elems[i + 1])
        if _is_top(*trio) if want_top else _is_bottom(*trio):
            return i
    return None


def three_bi_overlap(bis: list[Bi], i: int) -> bool:
    """bis[i], bis[i+1], bis[i+2] 三笔是否有公共重合区间。

    65课 p169（`.source/book.txt` 第 1290-1291 行）：

      「线段有一个最基本的前提，就是**线段的前三笔，必须有重叠的部分**，这个
        前提在前面可能没有特别强调，这里必须特别强调一次。线段至少有三笔，但
        并不是连续的三笔就一定构成线段，这三笔必须有重叠的部分。」
      「只要构成有重叠部分的前三笔，那么必然会形成一线段。」

    77课 p196（第 1573-1574 行）再次写死，并点明它**也管第一种情况**：

      「线段开始的那三笔，必须有重合，开始三笔没有重合的，是构不成线段的。」
      「对于线段划分的第一种情况，如果第一笔出现笔破坏后，接着的一笔就创新高，
        而且再后一笔，根本就不触及笔破坏那一笔，那么，这时候，显然构成不了
        线段对线段的破坏，**因为后面这三笔没有重合，不可能构成一线段**。」

    不足三笔时返回 False——还没走出来的东西不能算数（78课 p197）。
    """
    if i + 2 >= len(bis):
        return False
    lo = max(bis[i].low, bis[i + 1].low, bis[i + 2].low)
    hi = min(bis[i].high, bis[i + 1].high, bis[i + 2].high)
    return le(lo, hi)


# 返回值约定：case 编号 1/2/3 = 线段在此结束；PENDING = 方向待定，停止前探；
# None = 此处不是终点，继续往后试。
PENDING = 0
# 情况一的一个子情形，单独编号只为诊断时能数出来，判定依据见 _try_end 注释。
CASE_ENGULF = 3


def _resolve_pending(
    bis: list[Bi], j: int, d: Direction, last: int | None = None
) -> bool | None:
    """71课 p181：转折点下来的第一笔，其后第三笔完全落在它的范围内，
    「这三笔就分不出是向上还是向下，这样也就定义不了什么特征序列」。

    原文给了两个出口：
      1、最终还是先破了第一笔的**结束位置** → 新线段成立，旧线段被破坏
      2、最终先破第一笔的**开始位置**       → 旧线段依然延续，新线段没有出现

    返回 True / False / None（两边都还没破，仍然待定）。

    **先碰到哪个算哪个**——这不是实现细节，是这段逻辑的核心。两个出口都扫到
    一次就返回，所以「出口一先成立、随后价格又跌回转折点之下」要算**成立**：
    那次跌回属于再下一条线段，和 `breaker_passes_c22` 文档里那句「约束的是
    终点，不是沿途每一根」是同一回事。

    ``last`` 把窗口封在某一笔（含）为止，不传就一路扫到数据末尾。C-engulf 那
    条路封到**破坏段自己走完**那一笔（`_breaker_seg_end`），理由见调用处。
    """
    first = bis[j + 1]
    start_px, end_px = first.start_price, first.end_price
    stop = len(bis) if last is None else min(last + 1, len(bis))
    for k in range(j + 2, stop):
        b = bis[k]
        if d is Direction.UP:
            if lt(b.low, end_px):
                return True
            if gt(b.high, start_px):
                return False
        else:
            if gt(b.high, end_px):
                return True
            if lt(b.low, start_px):
                return False
    return None


def breaker_passes_c22(turn: SeqElem, pivot: float, d: Direction) -> bool:
    """破坏方**自己**也得满足「顶高于底」。78课 p197。

    两句话合起来读：

      「线段必须被**线段**破坏才能确定其完成。」
      「同一线段中，两端的一顶一底，顶肯定要高于底，如果你划出一个不符合这
        基本要求的线段，那肯定是划错了。」

    来破坏的那一条也是线段，它以假设转折点为起点，``turn``（分型的中间元素）
    就是它自己的终点。向下的破坏段，终点必须低于起点；向上的镜像。

    **注意约束的是终点，不是沿途每一根。** 一条 50→40 的向下线段是成立的，
    哪怕价格随后涨到 55——那个 55 属于**再下一条**线段，不能回头否定它。
    早先这里写成「沿途一旦越回 pivot 就作废」，那是把约束加强错了：600519
    的 2004-04-08 顶 49.85 之后跌到 39.78、再涨过 49.85，整段 13 个月的上涨
    会被一刀切错。

    **这条拦住的是一类灾难性错划**：600519 后复权日线，2003-11-05 的 **29.68**
    被当成向上线段的终点，而「确认」它的第二特征序列底分型出现在 **2005-12 的
    78~93 元**——一条从 29.68 起步、终点却在 78 的「向下线段」，正是原文说的
    「那肯定是划错了」。不拦这一刀，616 笔里会有 564 笔挤在最后一条永远走不完
    的线段里。
    """
    return lt(turn.low, pivot) if d is Direction.UP else gt(turn.high, pivot)


def breaker_never_exceeds(
    bis: list[Bi], first: int, last: int, pivot: float, d: Direction
) -> bool:
    """破坏段在走出确认分型之前，不许越过它要破坏的那个点。

    这是 `breaker_passes_c22` 的**同一条原文、另一半约束**。78课 p197：

      「同一线段中，两端的一顶一底，顶肯定要高于底，如果你划出一个不符合这
        基本要求的线段，那肯定是划错了。」

    破坏向上线段的那条向下线段，起点就是假设的转折点 ``pivot``，那是它自己的
    顶。`breaker_passes_c22` 管的是它的**终点**要低于起点；这里管的是它**沿途
    不许高过起点**——否则它的顶根本不在起点上，它就不是一条从 pivot 起步的
    向下线段，pivot 也就不是什么转折点。

    和 `breaker_passes_c22` 文档里那句「约束的是终点，不是沿途每一根」不矛盾，
    两者的窗口不同：那句话说的是**分型之后**（50→40 的向下线段，之后涨到 55，
    那个 55 属于再下一条线段，不能回头否定它）。这里只看**分型之前**，即确认
    这次破坏所用到的那几笔。所以 ``last`` 必须是确认分型右侧元素的最后一笔，
    不能传序列末尾。

    **这条拦住的正是 300308 的错划**：2026-05-15 的 6961.6 被判为向上线段的
    终点，而「确认」它的第二特征序列第一个元素就是 bi61 —— 一笔从 6289 直冲
    8963 的上涨，比被破坏的那个顶还高 28.8%。于是 2026-06-22 的历史最高点
    8963 被埋进了一条「向下线段」的肚子里，日线 640 根只划出 10 段，建在线段
    上的中枢退化到只剩 1 个（还是 2024 年的）。

    加上这一条之后，j=59 不再成立，算法继续前探到 j=61，在那里走的是情况一、
    顶分型 (bi60, bi62, bi64) 干净成立，段正确地结在 8963。
    """
    for k in range(first, last + 1):
        if d is Direction.UP:
            if gt(bis[k].high, pivot):
                return False
        elif lt(bis[k].low, pivot):
            return False
    return True


# 找破坏段自己的终点时往后看多少笔。纯粹是算力闸门：没有哪条原文规定这个
# 数，但 `_breaker_seg_end` 本身是 O(n) 的 `_try_end` 扫描，不封顶的话整体
# 退化成 O(n³)。实测 600519 全历史里破坏段的长度中位数 3 笔、最长 19 笔，
# 60 笔已经远在分布之外；够不着就按「破坏段走不完」处理，见调用处。
_BREAKER_HORIZON = 60


def _breaker_seg_end(
    bis: list[Bi], j: int, d: Direction, cfg: MergeConfig
) -> int | None:
    """破坏段**自己**在哪一笔结束；走不完就返回 None。

    78课 p197「线段必须被线段破坏才能确定其完成」反过来读也成立：破坏段一旦
    自己走完了，再往后的行情就是**它后面那条线段**的事，回头否定不了它
    （和 `breaker_passes_c22` 里那句「约束的是终点，不是沿途每一根」同源）。

    所以这个位置就是 C-engulf 那个待定状态的天然截止线，见调用处。

    `_try_end` 用 ``nested=True`` 调进来，破坏段自己的 C-engulf 分支就退化成
    本地判断，不会再往下找一层——递归深度封死在 1 层。
    """
    bd = d.flip()
    stop = min(j + 1 + _BREAKER_HORIZON, len(bis))
    for m in range(j + 3, stop, 2):
        if _try_end(bis, j + 1, m, bd, cfg, nested=True):
            return m
    return None


def _try_end(
    bis: list[Bi], s: int, j: int, d: Direction, cfg: MergeConfig,
    nested: bool = False,
) -> int | None:
    """假设 bis[j] 的终点就是线段终点，按 67课 两种情况检验。

    ``nested`` 表示这次调用来自 `_breaker_seg_end`（在问「破坏段自己走完了
    没有」）。此时 C-engulf 分支不再去找破坏段的破坏段，只做本地判断。
    """
    if j + 1 >= len(bis) or j < 1:
        return None

    # 门槛零：78课 p197「同一线段中，两端的一顶一底，**顶肯定要高于底**，如果
    # 你划出一个不符合这基本要求的线段，那肯定是划错了」。原文把这条当成「划
    # 错了」的判据，那就别划出来——拿它当构造约束，而不是只当事后检查。
    #
    # 这条拦住的是这样一类错划：行情一路下跌，算法却死抱着开头那根向上笔不放，
    # 直到很晚才在半山腰凑出一个特征序列顶分型，划出一条顶比底还低的「向上
    # 线段」。随机行情压力测试里 60 例能撞出 3 例。
    #
    # 注意**不能**加强成「终点必须是全段最高/最低点」——71课 p190 明确否掉：
    # 「没有人规定分段的结束位置一定是最高、最低的，关键要有至少三笔」。
    if d is Direction.UP:
        if not gt(bis[j].end_price, bis[s].start_price):
            return None
    elif not lt(bis[j].end_price, bis[s].start_price):
        return None

    # 旧线段要被破坏，就得有个新线段来破坏它；而新线段开始的三笔必须有重合
    # （65课 p169 / 77课 p196）。这条**两种情况都管**，77课 p196 是拿第一种
    # 情况举的例。三笔还没走出来时一律不确认，宁可让末段多挂几天。
    if not three_bi_overlap(bis, j + 1):
        return None

    e1 = elem_of(bis, j - 1)  # 转折点前线段的最后一个特征元素
    e2 = elem_of(bis, j + 1)  # 从转折点开始的第一笔

    pivot = bis[j].end_price  # 假设的转折点，也是破坏方那条线段的起点

    if not has_seq_gap(e1, e2):
        # ---------------- 第一种情况 ----------------
        # 转折点后第一个元素**吞掉**了转折点前最后一个元素。
        #
        # 这时标准的分型检验在结构上**不可能**通过：_is_top 要求 after[0].high
        # 低于 e1.high，而 after[0] ⊇ e2 ⊇ e1，恒不成立。不特判的话，这个位置
        # 就永远判不出终点。
        #
        # 71课 p182（`.source/book.txt` 第 1410 行）正是讲这一种：
        #
        #   「最早破坏那笔就是转折点下来的第一笔，这种情况下，这一笔……属于中间
        #     地带，既不能说是前面一段的特征序列，更不能说是后一段的特征序列，
        #     在这里情况下，**即使出现似乎有特征序列的包含关系的走势，也不能算**
        #     ……**一旦该笔延伸出三笔以上，那么新的线段就形成了**。」
        #
        # 78课 p199（第 1605 行）给了理由：「如果分界点两边出现特征序列的包含
        # 关系，那证明**对原线段转折的力度特别大**，那当然不能用包含关系破坏
        # 这种力度的呈现。」
        #
        # 所以这里不是「没分型 → 段继续」，而是「力度大到用不了分型 → 段已破坏」。
        # 「延伸出三笔以上」就是上面已经过了的 three_bi_overlap(bis, j+1) 这一关。
        #
        # **这条拦住的是一类灾难性错划**：600519 后复权日线 2003-09-23 的 25.88
        # 是 2003 年的底，向下线段本该在此结束，但转折点后的反弹笔 [25.88,29.68]
        # 吞掉了转折点前的反弹笔 [26.23,28.05]，底分型永远凑不出来，于是这条向下
        # 线段一路挂到 2026 年、吞掉 576 笔。
        #
        # 但注意原文那句话的落点是「属于**待定状态**」——和 p181 那个「三笔分不出
        # 方向」的待定用的是同一个词。既然是待定，就该用 p181 给的那对出口去结：
        # 后面先破破坏笔的**结束位置** → 新线段成立；先破它的**开始位置**（也就是
        # 这个假设的转折点本身）→ 旧线段依然延续，新线段没有出现。
        #
        # **这条拦住的是反向的灾难性错划**：600519 的 2008-02-27，向下线段跌到
        # 699.03，次日起四天弹到 871.55，吞掉了前一次反抽 [754.90, 799.82]，看着
        # 力度十足；可再下一笔直接砸到 617.10，破掉 699.03，随后是整轮 2008 年
        # 崩盘。那一弹只是个一笔的多头陷阱，876→342 本该是一条向下线段。不加这道
        # 闸，它会被切成「一条到 699 的跌」+「一条从 699 起步、2.75 年走不完的涨」。
        if _contains(e2, e1):
            # 出口用 71课 p182 **这一段自己给的那个**（`.source/book.txt` 1410 行）：
            # 「属于待定状态，**一旦该笔延伸出三笔以上，那么新的线段就形成了**」
            # ——不是 p181（1413 行）那对「先破第一笔的结束/开始位置」。两处都叫
            # 待定，出口却不是一个：1413 行讲的是第三笔完全落在第一笔范围内、三笔
            # 分不出方向；这里讲的是转折力度大到包含关系「不能算」。
            #
            # 「延伸出三笔以上」就是上面已经过了的 three_bi_overlap(bis, j+1)。
            #
            # 借错出口的代价：600519 的 2024-09-20 低点 7345.8，破坏笔 bi565 三周
            # 暴涨 45% 到 10683.1。按 p181 的出口一，得等价格升破 10683.1 才认
            # 7345.8 是个底——永远等不到。于是 9863.7→8240.5 被划成一条向下线段，
            # 把中间那根 45% 的反弹整个吞掉（图上一眼就看得出来）。
            #
            # 2008 年那个多头陷阱靠 C-over 拦，不靠这个出口：向下线段跌到 699.03，
            # 反弹笔 699.03→871.55 吞掉了前一次反抽，看着力度十足，可紧接着的
            # bi 直接砸到 617.10——破坏段自己跌回了它声称要破坏的那个底之下，
            # 那 699.03 就不是底。窗口取确认这次破坏所用的那三笔。
            # **窗口封到破坏段自己走完为止**（`_breaker_seg_end`）。这不是
            # 调出来的参数，是 78课 p197 那句话的直接后果：破坏段一旦自己
            # 成了段，再往后的行情归它后面那条线段管，回头否定不了它。
            #
            # 两个极端都试过，都不行：
            #
            # **不封**：600519 的 2024-09-19 低点 8862.4，破坏笔三周涨 53% 到
            # 13587。扫到数据末尾，2026-06-25 一个 8827.4 的低点——**21 个月
            # 后、隔着 41 笔**——把它否了。于是 12390→10115 被划成一条向下线段，
            # 当中那根 53% 的反弹整个被吞掉（图上一眼就看得出来）。何况「至今
            # 没了结」依赖全部后续K线，明天多来一根就可能翻供，已确认的线段被
            # 改写，tests/test_stress.py 的增量稳定性当场就红。
            #
            # **按笔数封死（j+3）**：600519 从 71 段掉到 39 段。2015-12-21 的
            # 1393.8 被当成顶确认掉，可价格 2016-07 就涨到 2030——那个「向下
            # 破坏段」压根走不完，于是从 1393.8 起一条向下线段一路挂到 2026 年
            # 的 9881，338~615 笔全埋在里面。正确的否定来自 +4 笔处的 1460.6，
            # 只差一笔就被窗口切掉了。
            #
            # 这两例的区别恰恰不在距离上（+4 该听，+41 不该听），在**破坏段
            # 自己站住了没有**：2024 那条 bi565 起的向上段 2024-11-08 就走完了；
            # 2015 那条 bi338 起的向下段 60 笔之内根本没走完，它就不是破坏段，
            # 1393.8 也就不是顶。
            #
            # **出口二只在破坏段还没站住时才有效。** `_breaker_seg_end` 返回
            # 一个具体的 m，意思就是「bi[j+1..m] 自己是一条成立的线段」——那
            # 这条线段的**内部**那些跌回枢轴之下的笔，就是 71课 p190 说的
            # 「没有人规定分段的结束位置一定是最高、最低的」，没有否决权。
            # 拿它们去否定枢轴，等于一边认定 bi[j+1] 起了一条线段、一边又说
            # bi[j] 不是分界点，自相矛盾。
            #
            # 600025 的 2026-01-29 低点 10.915 就死在这上面：破坏笔六周涨 20%
            # 到 13.132（标准 C-engulf），破坏段到 bi175 走完；可窗口里 bi174
            # 跌到 10.903——比枢轴低 **0.11%**、五个月后——把整个转折否了。
            # 于是 12.578→12.007 被划成一条 17 笔的「向下线段」，内部却在
            # 10.90~13.13 之间来回了两轮，图上是一条横穿十个月的平线。
            #
            # 2008 那个多头陷阱不受影响：那条「破坏段」压根没走完（m=None），
            # 它就不是线段，出口二照 71课 p181 原样生效。2015 的 1393.8 同理。
            last = j + 3 if nested else _breaker_seg_end(bis, j, d, cfg)
            resolved = _resolve_pending(bis, j, d, last=last)
            if resolved is False:
                return None          # 2008 那个多头陷阱：破坏笔自己先被破掉
            if resolved is True:
                return CASE_ENGULF
            if last is None:
                # 破坏段自己还没站住，两个出口也都没碰到——那就还没有「线段
                # 破坏线段」这回事（78课 p197）。1410 行管这个状态叫的正是
                # **待定**，交给 C-pendskip 往后接着试，别在这里拍板。
                return PENDING
            return (
                CASE_ENGULF
                if breaker_never_exceeds(bis, j + 1, j + 3, pivot, d)
                else None
            )

        # 71课 p181 的另一种待定：转折点下来的第三笔完全落在第一笔范围内。
        # 出口和上面共用 `_resolve_pending`，区别只在触发条件。
        if (
            cfg.enable_pending_seg
            and j + 3 < len(bis)
            and _contains(e2, elem_of(bis, j + 3))
        ):
            resolved = _resolve_pending(bis, j, d)
            if resolved is True:
                return 1
            if resolved is False:
                return None
            return PENDING

        # 第一元素不参与包含合并（71课 p182），但它是序列里真实的前一元素，
        # 要作为方向参照传进去
        after = standardize(
            [elem_of(bis, k) for k in range(j + 1, len(bis), 2)], d, ref=e1
        )
        if len(after) < 2:
            return None
        trio = (e1, after[0], after[1])
        ok = _is_top(*trio) if d is Direction.UP else _is_bottom(*trio)
        if not (ok and breaker_passes_c22(after[0], pivot, d)):
            # after[0] 是破坏那条线段的终点所在元素，它自己也得满足 78课 p197
            return None
        # 确认这次破坏用到的是 j+1 .. after[1] 的最后一笔，这一段里不许有哪
        # 一笔反过来越过 pivot（78课 p197 的另一半，见 breaker_never_exceeds）
        return 1 if breaker_never_exceeds(
            bis, j + 1, max(after[1].bi_indices), pivot, d) else None

    # ---------------- 第二种情况 ----------------
    # 从转折点开始的那一笔（方向与 d 相反）所开启的序列，其特征序列方向为 d
    second = standardize(
        [elem_of(bis, k) for k in range(j + 2, len(bis), 2)], d.flip()
    )
    # 71课 p181：「如果第二特征序列没有三个元素，就根本不存在出现分段中
    # 第二种情况的可能」
    if len(second) < 3:
        return None
    m = find_seq_fractal(second, want_top=d is Direction.DOWN)
    if m is None:
        return None
    # 这个分型的中间元素就是破坏段自己的终点，同样要过 78课 p197 这一关。
    # 没有它，600519 会拿 2005-12 的 78 元去「确认」2003-11 的 29.68 是顶。
    if not breaker_passes_c22(second[m], pivot, d):
        return None
    # 终点低于起点还不够——沿途也不许越回去。300308 的 6961.6 就是栽在这里：
    # 第二特征序列头一个元素 bi61 直接冲到 8963，比被破坏的顶还高 28.8%。
    #
    # 注意窗口从 **j+1** 起而不是 j+2。第二特征序列是从 j+2 开始取的（那些是
    # 与 d 同向的笔），但破坏段本身从 j+1 就开始了，中间那些反向笔同样属于它。
    return 2 if breaker_never_exceeds(
        bis, j + 1, max(second[m].bi_indices), pivot, d) else None


def build_segs(bis: list[Bi], cfg: MergeConfig | None = None) -> list[Seg]:
    """划线段。末尾那段必定 ``confirmed=False``。

    78课 p197：「线段必须被线段破坏才能确定其完成」，所以只有通过了 67课
    两种情况之一检验的线段才标 confirmed。
    """
    cfg = cfg or MergeConfig()
    segs: list[Seg] = []
    s = 0
    pending = False

    while s + 2 < len(bis):
        # 65课 p169 / 77课 p196：开始三笔没有重合的，构不成线段。此时 bis[s]
        # 不是任何线段的起点——价格朝反方向跑掉了——往后挪一笔重试。开头这些
        # 挪掉的笔不属于任何线段，这正是 78课 p197 说的「新股上市后最开始的
        # 那一段」之外无段可归的情形。
        if not three_bi_overlap(bis, s):
            s += 1
            continue

        d = bis[s].direction
        hit: tuple[int, int] | None = None
        first_pending = -1
        # 线段至少三笔（77课 p196），且必须结束于同方向的笔（78课 p197），
        # 所以候选终点只能是 s+2, s+4, …
        for j in range(s + 2, len(bis), 2):
            r = _try_end(bis, s, j, d, cfg)
            if r == PENDING:
                # 待定**不等于**「到此为止」。71课 p182 给的是逐点检验法：
                # 「假设某转折点是两线段的分界点，然后对此用线段划分的两种情况
                #   去考察是否满足，如果满足其中一种，那么这点就是真正的线段的
                #   分界点；**如果不满足，那就不是，原来的线段依然延续**。」
                # 一个始终没了结的待定点，两种情况一个都没满足，那它就不是分界
                # 点，该往后接着试。记下第一个待定点只为事后审计（C-pendskip）。
                #
                # 早先这里是 `break`，等于把「这一点还说不准」读成了「后面都不
                # 用看了」，那是比原文更强的一句话。代价是实测的：428 只里 143
                # 只(33%)末段卡在待定上，冻住全样本 6% 的笔。最极端的 600006，
                # 向下线段 28.42→11.42 之后反弹 149% 到 28.39——离 28.42 只差
                # 三分钱没破掉——于是 134 笔、四年半全挂在一条走不完的线段里。
                if first_pending < 0:
                    first_pending = j
                continue
            if r:
                hit = (j, r)
                break
        pending = first_pending >= 0
        if hit is None:
            break
        j, case = hit
        segs.append(
            Seg(
                index=len(segs),
                start_bi=s,
                end_bi=j,
                direction=d,
                start=bis[s].start,
                end=bis[j].end,
                case=case,
                confirmed=True,
                skipped_pending=first_pending,
            )
        )
        s = j + 1
        pending = False

    # 末端尚未被线段破坏的部分，作为未完成线段输出
    if s <= len(bis) - 1:
        segs.append(
            Seg(
                index=len(segs),
                start_bi=s,
                end_bi=len(bis) - 1,
                direction=bis[s].direction,
                start=bis[s].start,
                end=bis[-1].end,
                case=0,
                confirmed=False,
                pending=pending,
            )
        )
    return segs


def confirmed_segs(segs: list[Seg]) -> list[Seg]:
    return [s for s in segs if s.confirmed]
