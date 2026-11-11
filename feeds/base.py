"""数据层协议。划线引擎不认识任何数据源，只认识 `list[Bar]`。

所有实现必须遵守的三条硬约定，每一条都对应 CONVENTIONS.md 六、A股特有：

1. **必须复权。** 不复权时除权除息会造出假缺口和假分型，划线全错。
   这是最容易忽略又最致命的一条。本项目用**后复权**，不用前复权——东财的
   前复权是减法式的，长历史会算出负价（600519 实测 -314）。见 C-adj。
2. **剔除停牌日**，不补零、不前值填充。补出来的平盘K线会被包含处理吃掉，
   打乱K线计数，进而影响笔的间隔约束。
3. **`Bar.idx` 从 0 连续递增**，`ts` 单调递增。引擎全程靠 idx 回溯。
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Protocol

from chan.types import Bar

#: 境内行情站点。走翻墙代理是绕远路，而且代理本身会成为故障源：实测本机
#: `HTTP_PROXY=http://127.0.0.1:1235` 时整批拉取全军覆没在 `ProxyError` 上，
#: 绕开代理同一批票 0.6~1.3 秒一只、一次没失败。
#:
#: 放在 base 是因为**每个源都要这一手**——akshare 走东财、腾讯走 gtimg，
#: 各写一份的话新加的源一定会漏掉，而症状是「换了源还是 ProxyError」。
_DIRECT_HOSTS = (
    "eastmoney.com", "push2his.eastmoney.com",
    "sina.com.cn", "sinajs.cn",
    "gtimg.cn", "qt.gtimg.cn", "web.ifzq.gtimg.cn", "ifzq.gtimg.cn",
    "baostock.com",
)

#: 设成 "1" 就不绕代理了。给「本机必须走代理才能出网」的环境留的口子。
_KEEP_PROXY_ENV = "CHAN_KEEP_PROXY"


def bypass_proxy_for_data_hosts(extra: tuple[str, ...] = ()) -> None:
    """把行情站点的域名加进 `NO_PROXY`。

    **不能靠「把 HTTP_PROXY 清空」来绕过。** Windows 上 `urllib` 在环境变量缺
    失时会回落到**注册表里的系统代理**（`getproxies_registry`），所以
    `HTTP_PROXY= python ...` 看着是关了代理，实际还是照走不误——实测报的仍然
    是 `ProxyError`。唯一管用的是 `NO_PROXY`，它在两条路径上都生效。

    隔夜拉 5224 只时这一条很要命：代理抖一下就是几千条失败记录，而失败在这套
    系统里的表现是「这段时间没有买卖点」，不是报错。
    """
    if os.environ.get(_KEEP_PROXY_ENV) == "1":
        return
    for key in ("NO_PROXY", "no_proxy"):
        cur = [h for h in os.environ.get(key, "").split(",") if h.strip()]
        cur += [h for h in (*_DIRECT_HOSTS, *extra) if h not in cur]
        os.environ[key] = ",".join(cur)


class Blacklisted(RuntimeError):
    """数据源把我们封了（限流、黑名单）。**不能被「每只票兜底」那层吞掉。**

    批量拉取里每只票的异常都转成一条失败记录、继续下一只——对退市票、北交所
    票来说这是对的。但封禁是**全局状态**：接着跑只是拿 5224 次请求去撞同一
    堵墙，把封禁撞得更久，最后收获一份「全部失败」的报告。

    放在 base 而不是某个 feed 里，一是哪个源都可能封人，二是 `scan.puller`
    要 import 它——放在 `baostock_feed` 里就等于强迫只用 akshare 的人也把
    baostock 连带 import 进来。
    """


class Level(str, Enum):
    """级别。三级联立用日线定趋势、30分找买卖点、5分做区间套（27课 p67）。"""

    DAY = "day"
    M30 = "30min"
    M5 = "5min"

    @property
    def minutes(self) -> int:
        return {Level.DAY: 240, Level.M30: 30, Level.M5: 5}[self]


class DataFeed(Protocol):
    """数据源协议。akshare 做批量历史，Futu 做盘中实时，两者可互换。"""

    def get_bars(
        self, code: str, level: Level, start: str | None = None, end: str | None = None
    ) -> list[Bar]:
        """取K线。`start`/`end` 为 ``YYYY-MM-DD``，闭区间，None 表示不限。"""
        ...

    def list_universe(self) -> list[str]:
        """全市场代码表。"""
        ...


def normalize_code(code: str) -> str:
    """统一成 6 位数字代码。接受 ``sh600519`` / ``600519.SH`` / ``600519``。"""
    s = code.strip().upper()
    for p in ("SH", "SZ", "BJ"):
        if s.startswith(p):
            s = s[len(p) :]
        if s.endswith("." + p):
            s = s[: -len(p) - 1]
    s = s.lstrip(".")
    if not (len(s) == 6 and s.isdigit()):
        raise ValueError(f"无法识别的股票代码：{code!r}")
    return s


def exchange_of(code: str) -> str:
    """按首位判断交易所。60/68 沪，00/30 深，8/4/9 北交所。"""
    c = normalize_code(code)
    if c[0] == "6":
        return "sh"
    if c[0] in "03":
        return "sz"
    return "bj"


def to_bars(rows: list[tuple[str, float, float, float, float, float]]) -> list[Bar]:
    """把 ``(ts, open, high, low, close, volume)`` 行转成 Bar，并做基本体检。

    这里是脏数据的唯一拦截点：时间倒序、high<low、零价都在此报错，
    绝不让它们流进划线引擎——引擎里出现的异常才能确定是算法 bug。
    """
    bars: list[Bar] = []
    for i, (ts, o, h, low, c, v) in enumerate(rows):
        if h < low:
            raise ValueError(f"第 {i} 行 high {h} < low {low} @ {ts}")
        if low <= 0:
            raise ValueError(f"第 {i} 行价格非正：{low} @ {ts}")
        if bars and ts <= bars[-1].ts:
            raise ValueError(f"第 {i} 行时间未递增：{bars[-1].ts} -> {ts}")
        bars.append(
            Bar(idx=i, ts=ts, open=float(o), high=float(h), low=float(low),
                close=float(c), volume=float(v))
        )
    return bars
