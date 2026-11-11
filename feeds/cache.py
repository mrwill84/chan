"""本地K线缓存。按 ``source/level/code`` 存 CSV，增量追加。

为什么用 CSV 不用 parquet：parquet 要 pyarrow，又是一个在 3.13 上可能出意外的
二进制依赖，而且 CSV 出问题时能直接拿记事本打开核对——这一层的首要目的是让
「划线不对时能查原始数据」这件事零摩擦。全市场扫描真的嫌慢了再换。

## 为什么路径里有 `source`

同一只 600519、同样叫「后复权」，东财和 baostock 给出来的价格差两成多
（见 `baostock_feed` 的对照表）：两边的除权除息还原口径不一样。各自自洽，
混在一起就不是了——日线来自东财、30 分钟来自 baostock 的话，区间套照样跑，
因为它比的是**时间戳**；但人读到的「日线三卖 8773 / 30 分钟买点 11800」对不
上账，而且**全程没有任何报错**。

无声的错是最贵的错，所以把源做进路径：两个源的数据在文件系统层面就不可能
落进同一个目录，一次分析里换了源就是「缓存里没有」，而不是「拿到了另一套
坐标系的价格」。
"""

from __future__ import annotations

import csv
from pathlib import Path

from chan.types import Bar
from chan.verify import bars_digest

from .base import Level, normalize_code, to_bars

DEFAULT_ROOT = Path.home() / ".chan_cache"

#: 默认源。选 baostock 而不是 akshare：只有它给得出分钟线的长历史（C-min），
#: 而三级别联立离了长分钟历史就只是个摆设。
DEFAULT_SOURCE = "baostock"

#: 回填的默认历史起点。**不是「能拉多少拉多少」。**
#:
#: 两个理由，一个是对的，一个是便宜的。
#:
#: 对的那个：分钟线本来就只到 2020-01-02（`baostock_feed.MIN_EPOCH`）。日线
#: 拉到 1997 年，三级别联立在 2020 年之前照样只能停在日线层、`coverage` 报
#: `none`。三个级别用同一个起点，整套分析的历史视野才是一句能说清的话。
#:
#: 便宜的那个：baostock 的耗时是按页算的。实测同一只票，全历史（5833 根）
#: 要 17~20 秒，2020 年起（1643 根）只要 1~3 秒。全市场 5224 只，这是十几
#: 小时和三四小时的差别。
#:
#: 代价要说清楚：截断历史会在窗口开头造出一个人为的分型和一笔。1600 多根
#: 日K往后，这点影响早就被冲掉了，但**真要考据某只票 2015 年的买卖点，就得
#: 单独按更早的 start 重拉一份**。
DEFAULT_START = "2020-01-01"

_HEADER = ["ts", "open", "high", "low", "close", "volume"]


def source_of(feed) -> str:
    """数据源的目录名。取 feed 自己声明的 `source`，取不到就报错。

    不默认成 `DEFAULT_SOURCE`：一个忘了声明 `source` 的新 feed 会把数据写进
    baostock 的目录里，而这正是这套分目录要防的那件事。
    """
    s = getattr(feed, "source", None)
    if not s:
        raise ValueError(
            f"{type(feed).__name__} 没有声明 `source`，不知道该把缓存写进哪个源的目录。"
        )
    return s


def path_for(
    code: str, level: Level, root: Path | None = None, *, source: str = DEFAULT_SOURCE
) -> Path:
    return (root or DEFAULT_ROOT) / source / level.value / f"{normalize_code(code)}.csv"


class CacheCorrupt(RuntimeError):
    """读出来的K线和写进去的那份对不上。"""


def digest_path_for(
    code: str, level: Level, root: Path | None = None, *, source: str = DEFAULT_SOURCE
) -> Path:
    """摘要边车文件的路径。和 CSV 并排放，一只票一个。

    为什么要有它：`chan/verify.py` 的 `stable` 守的是**计算**（同一份输入算
    两遍比对），`bars_digest` 在 `analyze_one_checked` 里守的是**一次分析之内
    输入没被改动**。两者都从「K线已经在内存里了」这一刻开始算，而在那之前还有
    一段没人看的路：CSV 写了一半、磁盘翻了一位、或者 `pull` 在追加时被 Ctrl-C
    打断。那种损坏**不会抛异常**——截断的 CSV 仍然是合法 CSV，只是少了几百根
    K线，而少掉的那段历史只会表现为「这只票的中枢比记忆中少」。

    存的是 `bars_digest(bars)`（解析**之后**的对象），不是文件的 sha256。文件
    哈希只能发现字节变了；这个口径连「CSV 没变但解析出来的东西变了」也一并
    盖住，而后者正是换 Python 版本、换 `to_bars` 实现时会悄悄发生的事。
    """
    p = path_for(code, level, root, source=source)
    return p.with_name(p.name + ".sha")


def load(
    code: str, level: Level, root: Path | None = None, *,
    source: str = DEFAULT_SOURCE, check: bool = False,
) -> list[Bar]:
    """读缓存。``check=True`` 时和边车摘要对一遍，对不上抛 `CacheCorrupt`。

    **默认不检查**，原因不是开销（算一次摘要比解析 CSV 便宜得多），而是
    *没有边车的文件不该被当成坏的*：边车是后加的，缓存里已有的 CSV 一个都
    没有。默认打开的话整个缓存会一夜之间全部「损坏」。三态读数去
    `integrity()` 拿——那里「没记过」和「查过没事」是两个值。
    """
    p = path_for(code, level, root, source=source)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        rows = [
            (r["ts"], float(r["open"]), float(r["high"]), float(r["low"]),
             float(r["close"]), float(r["volume"]))
            for r in csv.DictReader(f)
        ]
    bars = to_bars(rows)
    if check:
        want = _read_digest(digest_path_for(code, level, root, source=source))
        got = bars_digest(bars)
        if want is not None and want != got:
            raise CacheCorrupt(
                f"{p} 和它的摘要对不上（记的是 {want[:12]}，读出来是 "
                f"{got[:12]}，{len(bars)} 根）。重拉：`python -m scan "
                f"--source {source} pull {code} --levels {level.value}`")
    return bars


def integrity(
    code: str, level: Level, root: Path | None = None, *, source: str = DEFAULT_SOURCE
) -> dict:
    """缓存体检。``ok`` 是**三态**：True / False / None。

    ``None`` 是「这份缓存从来没被记过摘要」，和 True 完全不是一回事。混成
    一个布尔，一整个没记过的缓存目录会报成「全部健康」——这正是
    `backtest.forward_walk.walk_health` 里 `checked` 那一位要防的同一件事，
    在数据这一侧的重演。
    """
    p = path_for(code, level, root, source=source)
    if not p.exists():
        return {"code": code, "level": level.value, "source": source,
                "exists": False, "ok": None, "recorded": None, "n": 0}
    bars = load(code, level, root, source=source)
    want = _read_digest(digest_path_for(code, level, root, source=source))
    got = bars_digest(bars)
    return {"code": code, "level": level.value, "source": source,
            "exists": True, "n": len(bars), "digest": got, "recorded": want,
            "ok": None if want is None else want == got}


def _read_digest(p: Path) -> str | None:
    try:
        s = p.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return s or None


def save(
    code: str, level: Level, bars: list[Bar], root: Path | None = None,
    *, source: str = DEFAULT_SOURCE,
) -> Path:
    p = path_for(code, level, root, source=source)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        for b in bars:
            w.writerow([b.ts, b.open, b.high, b.low, b.close, b.volume])
    # 边车**在 CSV 之后**写。顺序反过来的话，CSV 写到一半死掉会留下一个
    # 「摘要是新的、数据是旧的半截」的组合——那是最坏的一种，因为摘要对不上
    # 时我们会以为坏的是数据，而其实两份都不可信。这个顺序下崩在中间只会
    # 留下「摘要旧 / 数据半截」，照样报不一致，但含义清楚：重拉就是了。
    digest_path_for(code, level, root, source=source).write_text(
        bars_digest(bars), encoding="utf-8")
    return p


def last_ts(
    code: str, level: Level, root: Path | None = None, *, source: str = DEFAULT_SOURCE
) -> str | None:
    bars = load(code, level, root, source=source)
    return bars[-1].ts if bars else None


def update(
    feed, code: str, level: Level, *, root: Path | None = None,
    start: str | None = None,
) -> tuple[list[Bar], int]:
    """增量更新：只补缓存末尾之后的K线。回 ``(全部K线, 新增根数)``。

    盘后一轮全市场更新，每只票新增 1 根日K。整条历史重拉是六千倍的浪费，
    而 baostock 一只票要二十秒——5000 只就是一整天和一分钟的差别。

    **从缓存最后一根的当天重拉，不是次日。** 盘中拉过一次的话，缓存里那根
    当日K线是半截的：收盘价是当时的现价，最高最低也只走到那一刻。从次日
    起拉会把这根半截K线永久留在历史里，它不报错，只是那天的高低点是错的，
    而分型恰好只看高低点。所以重叠的那一天**以新取的为准**。

    增量能成立的前提是后复权：基准锚在上市首日，新的分红送股只影响其后的
    价格，历史值永不改写。前复权每除权一次全部历史都要重算，只能整条重拉。

    ``start`` 只在缓存是空的、退回整条拉取时才用得上（默认
    `DEFAULT_START`）。已经有缓存的时候起点由缓存末尾决定，给了也没有意义
    ——**往前补历史不是这个函数的事**，它只往后追。要把起点提前，用
    `get_or_fetch(..., refresh=True, start=...)` 整条重拉。
    """
    src = source_of(feed)
    cached = load(code, level, root, source=src)
    if not cached:
        bars = feed.get_bars(code, level, start=start or DEFAULT_START)
        if bars:
            save(code, level, bars, root, source=src)
        return bars, len(bars)

    # ts 可能是 "2024-01-02" 或 "2024-01-02 15:00:00"，feed 要的是日期
    fresh = feed.get_bars(code, level, start=cached[-1].ts[:10])
    if not fresh:
        return cached, 0

    rows = {b.ts: b for b in cached}
    added = sum(1 for b in fresh if b.ts not in rows)
    rows.update({b.ts: b for b in fresh})  # 重叠以新的为准
    bars = to_bars([
        (b.ts, b.open, b.high, b.low, b.close, b.volume)
        for b in sorted(rows.values(), key=lambda b: b.ts)
    ])
    save(code, level, bars, root, source=src)
    return bars, added


def get_or_fetch(
    feed, code: str, level: Level, *, root: Path | None = None, refresh: bool = False,
    start: str | None = None,
) -> list[Bar]:
    """缓存优先。源从 `feed.source` 取，不另给参数——两者对不上就是静默写错目录。

    用**后复权**的一个额外好处就在这里：后复权把基准锚在上市首日，新的分红送股
    只影响其后的价格，**历史值永不改写**，所以缓存可以安全地只追加增量。前复权
    不行——它的基准锚在最后一天，每除权一次全部历史价格都会变，只能整条重拉。

    ``start`` 默认 `DEFAULT_START`。**命中缓存时它不生效**：缓存里存的是哪
    一段就返回哪一段，这里不会为了一个更早的 start 去重拉。要换历史视野就
    带 ``refresh=True``。
    """
    src = source_of(feed)
    if not refresh:
        cached = load(code, level, root, source=src)
        if cached:
            return cached
    bars = feed.get_bars(code, level, start=start or DEFAULT_START)
    if bars:
        save(code, level, bars, root, source=src)
    return bars
