"""把划线结果画出来，用眼睛验收。

缠论这东西最终还是要人看一遍才放心：不变量断言能抓住「违反原文」的错误，
抓不住「合规但明显不是人会那么划的」那种。后者只有图能看出来。

### 画什么、不画什么

画 K 线、笔、线段、中枢框、买卖点。**不画** MACD、均线、成交量——它们和
划线无关，挤在图里只会让笔和段看不清。背驰要单独看的时候再说。

### 两个容易把图画错的地方

1. **横轴用K线序号，不用日期。** A股有停牌和长假，按日期排会在图上留出
   大片空白，而缠论里「相邻」指的是相邻K线，不是相邻日历日。空白会让人
   误判分型和笔的长度。日期只在刻度标签上出现。

2. **笔和段都要画在原始K线下标上。** 它们内部存的是合并K线下标或笔下标，
   直接拿去当 x 会整体错位——而且错得很均匀，看起来像「画对了但有点偏」，
   最难发现。统一走 `Fractal.peak_idx`（原始下标）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")  # 无 GUI，直接出文件
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle

from chan import analyze
from chan.types import Bar, Bi, Direction, Leg, Seg, ZhongShu
from chan.zhongshu import build_zhongshus, legs_from_bis, legs_from_segs

# 红涨绿跌（A股习惯，和欧美相反）。中枢用半透明填充，免得盖住K线。
UP_C, DOWN_C = "#d23b3b", "#2e9e4f"

# 笔和段**按方向上色**，不是按「笔/段」上色。方向是结构信息：78课 p197 规定
# 向上笔开始的线段必以向上笔结束，图上一眼看出哪条是向上段，才能当场验「顶到
# 顶/底到底」这种错划。单色折线把这一层信息藏起来了。
#
# 方向取自 `Bi.direction` / `Seg.direction`，**不是**比较两端高低。两者在
# 未确认末段上会分叉（428 只里 25 只，见 C-prov）：末段的终点是「此刻价格」
# 而不是转折点，可能停在反向笔上。按高低着色会把那 25 条染成反方向。
#
# 深色给段（主角），浅色给笔（背景）。K线降到 α=.4，让线浮上来——密集区
# 不压暗K线的话，笔那条细线会整段糊进同色的K线里，等于没画。
SEG_UP, SEG_DN = "#b3141c", "#13703a"
BI_UP, BI_DN = "#e8716c", "#4fa37c"

# 两层中枢必须一眼分得开。**它们不是同一个东西的两种画法，是两个级别。**
# 17课 p36 的中枢由「次级别走势类型」构成；日线级别的次级别是**线段**，所以
# 段中枢才是标准读法，123买卖点、背驰、趋势判定全部建在它上面。笔中枢比它
# 低一级（十只票实测 876 个 vs 132 个，密度差 6.6 倍），只能当背景参考。
#
# 所以样式上刻意不对称：段中枢实心填充、深色粗边、带 GG/DD 包络；笔中枢
# 空心、细虚线边、不画包络。混在一张图上时谁主谁次不用看图例。
ZS_SEG = {"face": "#9467bd", "edge": "#6a3d9a", "alpha": 0.18, "lw": 1.3,
          "ls": "solid", "tag": "段", "envelope": True, "fs": 7.5,
          "anchor": "top"}
ZS_BI = {"face": "#17becf", "edge": "#0f7b85", "alpha": 0.0, "lw": 0.7,
         "ls": (0, (4, 3)), "tag": "笔", "envelope": False, "fs": 6,
         "anchor": "bottom"}


def _candles(ax, bars: list[Bar]) -> None:
    """K线。单根宽度固定 0.6，影线用 1px 竖线。

    不用 mplfinance：它要 pandas，而整个项目到现在只依赖 numpy。
    """
    for i, b in enumerate(bars):
        c = UP_C if b.close >= b.open else DOWN_C
        ax.vlines(i, b.low, b.high, color=c, linewidth=0.6, alpha=0.4, zorder=1)
        lo, hi = min(b.open, b.close), max(b.open, b.close)
        ax.add_patch(Rectangle((i - 0.3, lo), 0.6, max(hi - lo, 1e-9),
                               facecolor=c, edgecolor=c, linewidth=0.4,
                               alpha=0.4, zorder=2))


def _polyline(ax, pts: list[tuple[int, float]], dirs: list[Direction],
              up_c: str, down_c: str, lw: float,
              dashed_tail: bool = False, zorder: float = 3) -> None:
    """按方向分段上色画折线。``dashed_tail`` 把最后一段画成虚线。

    末端那一笔/段天然未完成（C-prov），实线画出来会让人当成已确认的结构，
    然后对着一条后面可能被整段抹掉的线做判断。虚线是这张图上唯一的
    「这里别太当真」的提示。

    ``dirs[i]`` 是 ``pts[i] -> pts[i+1]`` 这一节的方向，由 `Bi`/`Seg` 自己的
    `direction` 字段给出，不从两端高低反推（理由见 `SEG_UP` 上方注释）。
    """
    if len(pts) < 2:
        return
    for i, (p, q) in enumerate(zip(pts, pts[1:])):
        tail = dashed_tail and i == len(pts) - 2
        ax.plot([p[0], q[0]], [p[1], q[1]],
                color=up_c if dirs[i] is Direction.UP else down_c,
                linewidth=lw, linestyle="--" if tail else "-",
                solid_capstyle="round", zorder=zorder)


def _bi_points(bis: list[Bi]) -> list[tuple[int, float]]:
    return ([(bis[0].start.peak_idx, bis[0].start_price)]
            + [(b.end.peak_idx, b.end_price) for b in bis])


def _seg_points(segs: list[Seg]) -> list[tuple[int, float]]:
    return ([(segs[0].start.peak_idx, segs[0].start_price)]
            + [(s.end.peak_idx, s.end_price) for s in segs])


def _zs_span(z: ZhongShu, legs: list[Leg], bis: list[Bi],
             end_leg: int) -> tuple[int, int]:
    """中枢在**原始K线**下标上的横跨范围。`Leg.src_*` 存的是笔下标。"""
    return (bis[legs[z.start_leg].src_start].start.peak_idx,
            bis[legs[end_leg].src_end].end.peak_idx)


def _zhongshus(ax, zss: list[ZhongShu], legs: list[Leg], bis: list[Bi],
               lo: int = 0, hi: int = 1 << 30,
               style: dict | None = None) -> None:
    """中枢框 + GG/DD 包络。

    ``lo`` / ``hi`` 是可见范围，整个落在外面的中枢直接不画。**这不只是
    省事**：`ax.text` 默认 `clip_on=False`，框被 xlim 裁掉了，标签还是会照
    画，结果一串 `#0 #1 #2…` 漂在坐标轴外面的空白里（`--last` 一开就有）。

    **框只画到 `core_end`，不画到 `end_leg`。** 离开段是飞走的那一段，把它
    圈进框里，图上看起来就像中枢一直震荡到了离开的位置——而那正是判断
    「离开确认了没有」时最不能含糊的地方（18课 p40 / C-core）。

    ZG/ZD 用实心框（前两段定死、不再变），GG/DD 用虚线（随延伸浮动）。
    两者在图上必须一眼能分开：三买三卖比的是框的边，趋势判定比的是虚线。

    ``style`` 选 `ZS_SEG` 还是 `ZS_BI`——段中枢和笔中枢差一个级别，不能画成
    一个样（见两个常量上面的注释）。标签带级别前缀（`段#3` / `笔#17`），
    两层叠在一张图上时不会看串。
    """
    st = style or ZS_SEG
    face = st["face"] if st["alpha"] > 0 else "none"
    for z in zss:
        x0, x1 = _zs_span(z, legs, bis, z.core_end)
        if x1 < lo or x0 > hi:
            continue
        w = max(x1 - x0, 1)
        ax.add_patch(Rectangle((x0, z.zd), w, max(z.zg - z.zd, 1e-9),
                               facecolor=face, alpha=st["alpha"] or 1.0,
                               fill=st["alpha"] > 0,
                               edgecolor=st["edge"], linewidth=st["lw"],
                               linestyle=st["ls"], zorder=0))
        if st["envelope"]:
            for y in (z.gg, z.dd):
                ax.hlines(y, x0, x1, color=st["edge"], linewidth=0.7,
                          linestyle=":", alpha=0.75, zorder=0)
        if not z.confirmed:
            # 未确认的中枢打斜线底纹。光靠标签后面那个 `?` 不够：它是 6~7.5 号
            # 字，一张 5000 像素宽的图上缩到几个像素，而「这个框还可能不成立」
            # 恰恰是看图时最该先知道的一件事。底纹是整框可见的，躲不开。
            # `fill=False` 的笔中枢照样能画 hatch，用的是 edgecolor。
            ax.add_patch(Rectangle((x0, z.zd), w, max(z.zg - z.zd, 1e-9),
                                   facecolor="none", fill=False,
                                   edgecolor=st["edge"], linewidth=0,
                                   hatch="///", alpha=0.35, zorder=0))
        tag = f"{st['tag']}#{z.index}"
        if z.level_up:
            tag += "↑9"           # 顶到延伸上限，该升级看（33课 p79）
        if not z.confirmed:
            tag += "?"
        if z.departed and st["envelope"]:
            # 离开段画成**从框右沿伸出去的一道横线**，高度取它飞出去的那一边
            # （向上离开画在 ZG，向下画在 ZD）。
            #
            # 原先画的是一道竖线，位置在离开段的结束处——那是个**恒等于下一个
            # 中枢左边缘**的位置，因为 leg 首尾相接，`depart_leg` 的终点就是下
            # 一个中枢首 leg 的起点（600006 十一个离开段无一例外）。竖线整条压
            # 在下一个框的边上，等于没画。要看的本来就不是「在哪结束」，是
            # 「从框里伸出去多远」。
            _, dx = _zs_span(z, legs, bis, z.depart_leg)
            up = legs[z.depart_leg].direction is Direction.UP
            ax.hlines(z.zg if up else z.zd, x1, dx, color=st["edge"],
                      linewidth=1.4, linestyle="-.", alpha=0.8, zorder=0)
        # 段标签贴框顶、笔标签贴框底。**不能两层都贴顶**：整个落在窗口左边
        # 的中枢，标签会被 `max(x0, lo)` 钳到同一个 x 上，这时只要两层的 ZG
        # 接近就叠成一团乱码。600025 的 `段#3`(ZG 12.295) 和 `笔#27`(ZG
        # 12.291) 差 4 厘，在图上完全看不出是两个标签。
        top = st["anchor"] == "top"
        ax.text(max(x0, lo), z.zg if top else z.zd, tag, fontsize=st["fs"],
                color=st["edge"], va="bottom" if top else "top",
                ha="left", zorder=4, clip_on=True)


def _bspoints(ax, pts, lo: int = 0, hi: int = 1 << 30) -> None:
    """买卖点。买在下方朝上、卖在上方朝下，未确认的空心。

    空心/实心这个区别不是美观问题：盘后扫描时末端一定有一批未确认的点
    （C-prov），它们在图上和已确认的点长得一样的话，人工抽查就会把
    「后面可能被抹掉的线索」当成「已经成立的信号」。
    """
    for p in pts:
        if not lo <= p.bar <= hi:
            continue          # 可见范围之外的点连标签都别画，理由见 _zhongshus
        buy = p.is_buy
        y = p.price
        ax.plot(p.bar, y, marker="^" if buy else "v", markersize=7,
                markerfacecolor=((UP_C if buy else DOWN_C) if p.confirmed
                                 else "none"),
                markeredgecolor=UP_C if buy else DOWN_C,
                markeredgewidth=1.2, zorder=5)
        # 标签带上它挂的中枢编号。买卖点全部是**相对某个中枢**定义的（20课
        # p43 的三买比的是那个中枢的 ZG），而那个中枢的框经常根本不在窗口里
        # ——000651 的 `3买` 挂在段#18 上，框早在窗口左边之外，图上只剩一个
        # 孤零零的三角，没法核对。编号一写出来就能回去查。
        label = (p.label + ("~" if p.quasi else "") + ("?" if p.ambiguous else "")
                 + f"@{p.zs_index}")
        ax.annotate(label, (p.bar, y), fontsize=7,
                    color=UP_C if buy else DOWN_C,
                    xytext=(0, -12 if buy else 9), textcoords="offset points",
                    ha="center", zorder=5, clip_on=True)


def _provisional(ax, bars: list[Bar], segs: list[Seg],
                 lo: int, hi: int) -> str:
    """把**未确认区**整片底色标出来，并返回一句说明文字。

    原先「未确认」在图上只有四个记号：末段虚线、买卖点空心三角、中枢标签后面
    的 `?`、类买卖点的 `~`。四个都是小记号，而它们说的是同一件大事——**这一
    片的划线随时会被后面的行情改写**（C-redraw）。600009 的 30 分钟图上，那片
    区域横跨大半年，可图上唯一的提示是最右边一小截虚线，看图的人根本意识不到
    边界在哪。

    边界取**最后一条已确认线段的终点**。78课 p197「线段必须被线段破坏才能确定
    其完成」：在那个点之前的划分已经被后续线段破坏过一次，站得住；之后的全部
    悬着——末段本身、建在末段上的中枢、挂在那些中枢上的买卖点，一个都跑不掉。

    所以边界**不是**「最后一个未确认中枢的左沿」那种更窄的取法：中枢是建在段
    上的，段没定，中枢的 ZG/ZD 连算都还没算准。宁可圈大，不圈漏。
    """
    if not segs or segs[-1].confirmed:
        return ""
    done = [s for s in segs if s.confirmed]
    x0 = done[-1].end.peak_idx if done else segs[0].start.peak_idx
    if x0 > hi:
        return ""
    ax.axvspan(max(x0, lo), hi, color="#9a9a9a", alpha=0.08, zorder=-1)
    if x0 >= lo:
        ax.axvline(x0, color="#666", linewidth=1.0, linestyle=(0, (6, 4)),
                   alpha=0.85, zorder=1)
    tail = segs[-1]
    note = (f"未确认区：{bars[x0].ts} 起 · 段#{tail.index} "
            f"{tail.end_bi - tail.start_bi + 1}笔未被线段破坏")
    if tail.pending:
        # 71课 p181/p182 的待定：两个出口都还没碰到。比「单纯没走完」更弱，
        # 连方向都还定不下来，值得单独说一句。
        note += " · 待定(71课)"
    # 贴在坐标区顶内侧，x 用数据坐标、y 用轴坐标（get_xaxis_transform）。
    # 加白底框：这片区域右侧往往正是最新行情，直接压字会看不清。
    ax.text(max(x0, lo), 0.995, " " + note,
            transform=ax.get_xaxis_transform(), ha="left", va="top",
            fontsize=8, color="#444", zorder=6,
            bbox={"facecolor": "white", "alpha": 0.78, "edgecolor": "#bbb",
                  "boxstyle": "round,pad=0.25", "linewidth": 0.6})
    return note


def _cjk_font() -> None:
    """让中文标签不变成豆腐块。找不到中文字体就退回英文，别画一堆□。"""
    for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
        try:
            matplotlib.font_manager.findfont(name, fallback_to_default=False)
        except Exception:  # noqa: BLE001 - 字体没装是常态，不该中断画图
            continue
        plt.rcParams["font.sans-serif"] = [name]
        plt.rcParams["axes.unicode_minus"] = False
        return


def _date_idx(bars: list[Bar], ts: str, default: int) -> int:
    """把日期串换成K线下标。停牌/非交易日取**之后**第一根，越界夹到两端。"""
    if not ts:
        return default
    for i, b in enumerate(bars):
        if b.ts >= ts:
            return i
    return len(bars)


def plot(bars: list[Bar], code: str, out: str, *, zs_from: str = "seg",
         last: int | None = None, title: str = "",
         log: bool | None = None,
         since: str = "", until: str = "") -> str:
    """划一次线并出图。``last`` 只画最后 N 根（全量 640 根笔会糊成一团）。

    **划线用全量K线，只是画的时候裁。** 反过来做（先裁K线再划线）得到的是
    另一份划分——前面的K线决定着笔怎么接、线段怎么分，裁掉它们等于在看一张
    「假装历史不存在」的图，而那不是引擎实际在用的那一份。

    ``since`` / ``until`` 按日期开窗，和 ``last`` 一样只影响显示。要这个是因为
    `last` 只能从右边裁：带「离开段」的中枢基本都在早年，十只票的最后 320 根里
    一个都没有，`core_end` 截断和离开段那条点划线光靠 `last` 根本看不到。

    ``zs_from`` 取 `seg`（默认）/ `bi` / `both`。**默认必须和 `scan/report.py`、
    `chan/multilevel.py`、pi 扩展一致**——它们全是 `seg`。这里曾经默认 `bi`，
    后果是人工验收的那张图和引擎实际在用的中枢不是一回事：眼睛过的是笔中枢，
    agent 判买卖点用的是段中枢，等于那一层根本没被看过。

    `both` 把两层叠在一张图上，用来看级别关系。**买卖点仍然只按段中枢算**
    （17课 p36 的次级别走势类型，日线就是线段），不随 `both` 变。
    """
    _cjk_font()
    a = analyze(bars, check=False)
    bis, segs = a.bis, a.segs
    bi_legs = legs_from_bis(bis)
    seg_legs = legs_from_segs(segs, bis)
    # 买卖点的基准层：both 时取段中枢，和单独跑 seg 的结果逐点一致
    legs = bi_legs if zs_from == "bi" else seg_legs
    zss = build_zhongshus(legs)

    from chan import macd
    from chan.buysell import find_bspoints
    pts = find_bspoints(legs, zss, bis, macd([b.close for b in bars]),
                        include_quasi=True)

    lo = _date_idx(bars, since, 0)
    hi = min(_date_idx(bars, until, len(bars)), len(bars) - 1)
    if last is not None:
        lo = max(lo, hi + 1 - last)
    # 宽度按**可见**根数算，不按总根数。按总根数算的话，`--last 260` 看一只
    # 6595 根的票会得到一张 40 英寸宽、只画了 260 根的图：K线被拉成一片，两边
    # 全是空白，tight_layout 还会报警。
    #
    # 高度跟着宽度长，上限 13 英寸。死高 8 英寸是按日线窗口定的（几百根K线，
    # 宽度到不了 40 的上限）；30 分钟一屏一千多根直接顶到上限，5:1 的长条里
    # 笔和段挤成一团平线，中枢框扁得看不出上下沿。横轴撑满了就只能从纵轴找
    # 空间。窄图（宽度没到上限的）保持原样，aspect 不变。
    w = max(14, min(40, (hi - lo + 1) / 22))
    fig, ax = plt.subplots(figsize=(w, max(8, min(13, w / 3.2))))
    _candles(ax, bars)
    _polyline(ax, _bi_points(bis), [b.direction for b in bis],
              BI_UP, BI_DN, 1.1, not bis[-1].confirmed, zorder=3)
    if segs:
        _polyline(ax, _seg_points(segs), [s.direction for s in segs],
                  SEG_UP, SEG_DN, 2.1, not segs[-1].confirmed, zorder=4)
    # 画的顺序是「低级别在下、高级别在上」：笔中枢先铺底，段中枢压在上面。
    n_bi = n_seg = 0
    if zs_from in ("bi", "both"):
        zs_bi = zss if zs_from == "bi" else build_zhongshus(bi_legs)
        n_bi = len(zs_bi)
        _zhongshus(ax, zs_bi, bi_legs, bis, lo, hi, ZS_BI)
    if zs_from in ("seg", "both"):
        zs_seg = zss if zs_from == "seg" else build_zhongshus(seg_legs)
        n_seg = len(zs_seg)
        _zhongshus(ax, zs_seg, seg_legs, bis, lo, hi, ZS_SEG)
    _bspoints(ax, pts, lo, hi)
    _provisional(ax, bars, segs, lo, hi)

    ax.set_xlim(lo - 2, hi + 2)
    vis = bars[lo:hi + 1]
    vlo, vhi = min(b.low for b in vis), max(b.high for b in vis)
    # 后复权全历史动辄 25 倍价差（C-adj），线性轴上早年全挤成一条线，根本没法
    # 用眼睛验收。价差超过 8 倍就换对数轴。**只影响显示，不影响判定**：中枢靠
    # 区间重叠定义（17课 p36），重叠关系在单调变换下不变；划线早已算完。
    use_log = log if log is not None else (vhi / max(vlo, 1e-9) > 8)
    if use_log:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(matplotlib.ticker.ScalarFormatter())
        ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        ax.set_ylim(vlo * 0.94, vhi * 1.06)
    else:
        pad = (vhi - vlo) * 0.06
        ax.set_ylim(vlo - pad, vhi + pad)

    step = max(1, (hi - lo + 1) // 14)
    ticks = list(range(lo, hi + 1, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([bars[i].ts for i in ticks], rotation=45,
                       fontsize=7, ha="right")
    zs_txt = "  ".join(
        t for t in (f"笔中枢{n_bi}" if n_bi or zs_from == "bi" else "",
                    f"段中枢{n_seg}" if n_seg or zs_from == "seg" else "")
        if t
    )
    ax.set_title(title or f"{code}  笔{len(bis)} 段{len(segs)}  {zs_txt}"
                          f"  买卖点{len(pts)}（按"
                          f"{'笔' if zs_from == 'bi' else '段'}中枢）", fontsize=11, pad=22)
    ax.grid(alpha=0.15, linewidth=0.5)
    # 折线按方向分段画，Rectangle 也不会自动进图例——两者都得补代理图元。
    # 没有它们，读者只能靠猜哪个红是段、哪个红是笔、哪层中枢是哪层。
    handles = [Line2D([], [], color=SEG_UP, lw=2.1),
               Line2D([], [], color=SEG_DN, lw=2.1),
               Line2D([], [], color=BI_UP, lw=1.1),
               Line2D([], [], color=BI_DN, lw=1.1),
               Line2D([], [], color="#777", lw=1.4, linestyle="--")]
    labels = ["线段↑", "线段↓", "笔↑", "笔↓", "未确认"]
    for st, n in ((ZS_BI, n_bi), (ZS_SEG, n_seg)):
        if not n:
            continue
        handles.append(Patch(facecolor=st["face"] if st["alpha"] > 0 else "none",
                             alpha=st["alpha"] or 1.0, fill=st["alpha"] > 0,
                             edgecolor=st["edge"], linewidth=st["lw"],
                             linestyle=st["ls"]))
        labels.append(f"{st['tag']}中枢")
    if n_seg:
        # GG/DD 和离开段只有段中枢画，所以绑在 n_seg 上。三买三卖比的是框的
        # 边（ZG/ZD），趋势判定比的是虚线（GG/DD）——两者必须能分清是哪条。
        handles += [Line2D([], [], color=ZS_SEG["edge"], lw=0.7, ls=":"),
                    Line2D([], [], color=ZS_SEG["edge"], lw=1.4, ls="-.")]
        labels += ["GG/DD", "离开段"]
    # 图例摆在坐标区**外面**（标题下方一条横排）。放 `upper left` 时它会盖住
    # 左上角的行情：000651 的 2025-07-23 那个 `1卖` 整个被压在图例底下，而左
    # 上角恰恰常是窗口里的高点所在，也就是卖点最可能出现的地方。
    ax.legend(handles, labels, loc="lower left", bbox_to_anchor=(0, 1.0),
              fontsize=8, ncol=len(labels), framealpha=0.9,
              borderaxespad=0.4, columnspacing=1.4, handlelength=1.6)
    # 记号表放图脚，不塞进图例。图例已经有 9 项，再加四项会挤成两行把图压扁；
    # 而这四个说的是**同一件事的四种写法**（都在讲「未确认」），排在一起比拆进
    # 图例里更容易一眼读完。
    fig.text(0.995, 0.004,
             "未确认：灰底=未确认区  虚线=末段  斜纹+?=中枢  空心三角=买卖点"
             "   ~=类(盘整背驰)   ?=原文歧义(买卖点标签)",
             ha="right", va="bottom", fontsize=7, color="#777")
    fig.tight_layout(rect=(0, 0.022, 1, 1))
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def main(argv: list[str]) -> int:
    import argparse

    from feeds import Level, cache

    ap = argparse.ArgumentParser(description="画出划线结果，人工抽查用")
    ap.add_argument("code")
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--level", default="day")
    ap.add_argument("--zs-from", default="seg",
                    choices=["seg", "bi", "both"],
                    help="中枢建在线段上（默认，标准读法）/ 笔上 / 两层都画")
    ap.add_argument("--last", type=int, default=None,
                    help="只画最后 N 根（划线仍用全量）")
    ap.add_argument("--since", default="", help="显示起始日期 YYYY-MM-DD")
    ap.add_argument("--until", default="", help="显示结束日期 YYYY-MM-DD")
    ap.add_argument("--source", default="tencent")
    ap.add_argument("--log", dest="log", action="store_true", default=None)
    ap.add_argument("--no-log", dest="log", action="store_false")
    a = ap.parse_args(argv)

    bars = cache.load(a.code, Level(a.level), source=a.source)
    out = a.out or f"{a.code}_{a.level}_{a.zs_from}.png"
    print(plot(bars, a.code, out, zs_from=a.zs_from, last=a.last,
               log=a.log, since=a.since, until=a.until))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
