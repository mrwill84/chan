"""腾讯财经数据源。**应急用的日线源，不是主力源。**

2026-10-16 实测：baostock 把本机 IP 列了黑名单（`10001011`），东财在限流后
一直拒连（`RemoteDisconnected`），而腾讯 `gtimg.cn` 秒回、后复权日线能取到
当天。这个 feed 就是为了在那两家恢复之前让扫描跑得起来。

**为什么说它不是主力源**——三条硬限制，每一条都登记在 CONVENTIONS.md C-tx：

1. **日线硬上限 640 根**（约 2.6 年）。给 `count=2000`、给 `start_date=2015`
   都一样只回 640 根最新的。所以它**补不上 2020~2024 那段历史**，
   `python -m scan pull` 的默认起点 2020-01-01 它够不着。
2. **分钟线只有不复权的。** 30 分钟得走另一个接口（`kline/mkline`），而那个
   接口没有复权参数。不复权违反 `base.py` 的第 1 条硬约定（除权会造假缺口和
   假分型），所以这里**默认直接拒绝**，不是悄悄给一份脏数据。
3. **给不出全市场代码表。** 腾讯没有公开的列表接口，猜一个不如不给。

实测数据（600519 / 300308，2026-10-16）：

| 接口 | 结果 |
|---|---|
| `qt.gtimg.cn/q=` 快照 | 秒回，现价 1329.00 |
| `fqkline/get` day hfq count=640 | 641 根，2024-02-23 ~ 2026-10-16 |
| `fqkline/get` day hfq count=2000 | **还是 641 根**——上限是硬的 |
| `fqkline/get` day hfq 2015-01-01 起 | **还是 640 根**，起点 2024-02-26 |
| `fqkline/get` m30 | `bad params`，这个接口不吃分钟 |
| `kline/mkline` m30 count=640 | 640 根，约 4 个月，**不复权** |
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from chan.types import Bar

from .base import (
    Blacklisted,
    Level,
    bypass_proxy_for_data_hosts,
    exchange_of,
    normalize_code,
    to_bars,
)

#: 日线一次能拿到的最大根数。**给再大的 count 也不会多给。**
DAY_CAP = 640

#: 后复权日线接口。`param=<sym>,day,<start>,<end>,<count>,hfq`
_FQ_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="

#: 分钟线接口（**无复权参数**）。`param=<sym>,<period>,,<count>`
_MK_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/mkline?param="

_PERIOD = {Level.M30: "m30", Level.M5: "m5"}

#: 腾讯认 `sh600519` / `sz300308` 这种带市场前缀的写法。
_UA = {"User-Agent": "Mozilla/5.0"}


def _symbol(code: str) -> str:
    """6 位代码 → 腾讯的 `sh600519` 写法。

    **北交所直接拒绝。** `bj920000` 这种腾讯接得下来，但只回**当天那一根**，
    不回历史（实测 920000/920001/920002/920003/920005/920006 六只，无一例外
    只有 1 根）。放过去的后果不是「数据少」，是**缓存被污染**：一根K线也会
    落盘，之后 `pull --update` 看见文件存在就只做增量追加，于是这只票的
    「历史」永远是从我们第一次拉它的那天开始的几根——而且不会再有任何提示。
    """
    if exchange_of(code) == "bj":
        raise ValueError(
            f"腾讯只给北交所当天一根K线，不给历史，划不出线（{code}）。"
            f"北交所用 --source akshare。"
        )
    return exchange_of(code) + normalize_code(code)


def _rows(payload: dict, sym: str) -> list[list]:
    """从腾讯的回包里挖出K线数组。

    回包形状是 `{"data": {"sh600519": {"hfqday": [[...], ...]}}}`，但**键名随
    接口和复权方式变**（`day` / `hfqday` / `qfqday` / `m30`），而且同一个回包
    里还混着 `qt`、`prec` 这些非K线字段。所以不照键名取，取第一个非空的数组。

    这么写是因为键名猜错的症状太难查：回包是 200、JSON 也合法，只是取不到
    数据——在批量拉取里会变成「这只票没有K线」，和退市票、停牌票混在一起。
    """
    data = payload.get("data") or {}
    node = data.get(sym) or {}
    for key, val in node.items():
        if isinstance(val, list) and val and isinstance(val[0], list):
            return val
    return []


#: 判定为「被限流了」的 HTTP 状态码。
#:
#: **`501` 是实测出来的，不是猜的。** 2026-10-16 跑全市场回填，前 385 只一切
#: 正常，之后 `web.ifzq.gtimg.cn` 对**所有**代码一律回 `501 Not Implemented`
#: ——包括几分钟前刚拉成功过的 000001、600519。它不是「这个接口没实现」，是
#: 腾讯的限流话术。同一时刻 `qt.gtimg.cn` 的快照接口照常工作，所以封的是
#: K线那个域名，不是整个 IP。
#:
#: 这个码**没写进来之前的后果**已经见过了：`_call` 把它当普通失败，每只票
#: 退避重试三次（≈2.1 秒）再记一条失败，于是整轮从 0.55 秒/只慢到 1.4 秒/只，
#: 闷头又撞了十几分钟的墙才被人发现。
_THROTTLE_CODES = frozenset({403, 429, 501, 502, 503})


def _get(url: str, timeout: float) -> dict:
    """发一次请求。被封/被限流时抛 `Blacklisted`，不是普通失败。

    限流是**全局状态**，不是这一只票的问题。按普通异常处理的话
    `scan.puller` 会把它当成「这只票拉不到」接住，然后拿剩下 5000 次请求去撞
    同一堵墙（见 `Blacklisted` 的说明）。
    """
    req = urllib.request.Request(url, headers=_UA)
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read()
    except urllib.error.HTTPError as e:
        if e.code in _THROTTLE_CODES:
            raise Blacklisted(
                f"腾讯返回 HTTP {e.code}——这是限流，不是这只票的问题。"
                f"整轮停下，等一段时间再跑（见 C-tx）。url={url}"
            ) from e
        raise
    return json.loads(raw.decode("utf-8", errors="replace"))


class TencentFeed:
    """腾讯财经。**只给后复权日线**，分钟线和代码表都会明确报错。

    ``allow_unadjusted_min=True`` 才会去取分钟线，取回来的是**不复权**的。
    默认关着是因为 `base.py` 的第 1 条硬约定：不复权的除权缺口会造出假分型，
    划出来的线是错的——而错线不会报错，只会给出看起来很像样的买卖点。
    真要开，就别把结果和别的源混着看，也别拿去做区间套。
    """

    #: 缓存目录名。和另外两家的后复权基准对不上，**绝不能混**（C-src）。
    source = "tencent"

    #: 并发上限。**串行就已经超速了，这里不要再往上调。**
    #:
    #: 这个数原来是 2，注释写着「是猜的，不是实测出来的」。现在测出来了，
    #: 代价是把 K线接口也撞封了：`jobs=1`、`sleep=0.3`，实际 ≈1.3 请求/秒，
    #: 跑到第 385 只就全面 501（见 `_THROTTLE_CODES`）。
    max_jobs = 1

    #: 请求间隔的下限。1.3 请求/秒会被封，所以默认退到 ≈0.8 请求/秒。
    #:
    #: **但这个值不保证安全。** 真正的触发条件更像是「单位时间内的累计请求
    #: 数」而不是瞬时速率——东财是 ≈1.6 req/s 撞墙，腾讯 ≈1.3 req/s 撞墙，
    #: 两家都是几百个请求之后。全市场 5577 只想一口气拉完，大概率哪家都不行，
    #: 正确做法是分几天、每天几百只（见 C-tx）。
    MIN_SLEEP = 1.2

    def __init__(
        self,
        sleep: float = MIN_SLEEP,
        retries: int = 3,
        timeout: float = 20.0,
        *,
        allow_unadjusted_min: bool = False,
    ) -> None:
        # 调用方给了更小的间隔也抬回下限。测试里显式传 0 可以绕过——那条路
        # 不发真请求。线上压到 0.3 的代价已经付过一次了，不留第二次机会。
        self.sleep = sleep if sleep <= 0 else max(sleep, self.MIN_SLEEP)
        self.retries = retries
        self.timeout = timeout
        self.allow_unadjusted_min = allow_unadjusted_min
        bypass_proxy_for_data_hosts()

    # ------------------------------------------------------------------
    def get_bars(
        self, code: str, level: Level, start: str | None = None, end: str | None = None
    ) -> list[Bar]:
        """取K线。``start``/``end`` 为 ``YYYY-MM-DD`` 闭区间。

        **`start` 给得再早也只有 640 根。** 接口自己就截断，这里只能在拿到的
        640 根里再按区间筛。想知道是不是被截断了，用 `truncated()`。
        """
        sym = _symbol(code)
        if level is Level.DAY:
            url = f"{_FQ_URL}{sym},day,{start or ''},{end or ''},{DAY_CAP},hfq"
            rows = self._call(url)
            # 腾讯日线行：[日期, 开, 收, 高, 低, 量, ...]——**收在高低前面**，
            # 和常见的 OHLC 顺序不一样，顺手写成 o,h,l,c 会把高低开收全错位。
            out = [
                (r[0], float(r[1]), float(r[3]), float(r[4]), float(r[2]), float(r[5]))
                for r in rows
            ]
        else:
            if not self.allow_unadjusted_min:
                raise RuntimeError(
                    f"腾讯的分钟线接口没有复权参数，取回来的是不复权的，"
                    f"划线会错（见 base.py 第 1 条约定 / C-tx）。确实要用就传 "
                    f"allow_unadjusted_min=True，并且别和别的源混着看。"
                    f"（code={code} level={level.value}）"
                )
            url = f"{_MK_URL}{sym},{_PERIOD[level]},,{DAY_CAP}"
            rows = self._call(url)
            # 分钟行：[时间戳 yyyymmddHHMM, 开, 收, 高, 低, 量]
            out = [
                (_mts(r[0]), float(r[1]), float(r[3]), float(r[4]), float(r[2]),
                 float(r[5]) if len(r) > 5 else 0.0)
                for r in rows
            ]

        # 接口对 start/end 的支持不可靠（给 2015 年它照样只回最近 640 根），
        # 所以区间在这边再筛一次，保证调用方拿到的确实是它要的区间。
        if start:
            out = [r for r in out if r[0][:10] >= start]
        if end:
            out = [r for r in out if r[0][:10] <= end]
        return to_bars(out)

    # ------------------------------------------------------------------
    def list_universe(self) -> list[str]:
        """腾讯没有公开的全市场代码表接口。"""
        raise RuntimeError(
            "腾讯给不出全市场代码表。先用别的源存一份："
            "`python -m scan universe --source akshare --save all.txt`，"
            "之后 `--file all.txt` 一直用它——代码表几个月才变一次，不必每次重拉。"
        )

    # ------------------------------------------------------------------
    def _call(self, url: str) -> list[list]:
        sym = url.split("param=")[1].split(",")[0]
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                rows = _rows(_get(url, self.timeout), sym)
                time.sleep(self.sleep)
                return rows
            except Blacklisted:
                raise  # 全局状态，重试只会撞得更狠
            except Exception as exc:
                last = exc
                time.sleep(self.sleep * (2**attempt))
        raise RuntimeError(f"腾讯调用失败（重试 {self.retries} 次）：{last}") from last


def _mts(raw: str) -> str:
    """`202610161500` → `2026-10-16 15:00`。`to_bars` 要靠它比大小判时间递增。"""
    s = str(raw)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"


def truncated(bars: list[Bar], level: Level) -> bool:
    """是不是撞上了 640 根上限。

    撞上了**不代表数据有问题**，代表它只有这么多——日线 640 根≈2.6 年，划
    日线中枢够用，但不要拿它去回答「2021 年有没有买卖点」。
    """
    return len(bars) >= DAY_CAP
