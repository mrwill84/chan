"""akshare 数据源。批量历史用它，免费无额度限制。

**已知限制（实测 akshare 1.19.1 / 2026-10）**：

- 日线（`stock_zh_a_hist`）：全历史，可用。
- 分钟线（`stock_zh_a_hist_min_em`）：东财硬上限约 **1512 行且约 31 个交易日**，
  30 分钟只能取到约 252 根、5 分钟约 1512 根，和 `start_date` 给多早无关。
  30 分钟 252 根划不出几个线段，更划不出日线级别的中枢。
  → 30 分钟的长历史后面要换源（baostock 有完整 30 分钟），Futu 只用于盘中。
  这条限制登记在 CONVENTIONS.md 六。

**复权方式实测（600519，6022 根日线）**：

| adjust | 首根收盘 | 末根收盘 | 全段最低 |
|---|---|---|---|
| `qfq` | **-312.47** | 1316.80 | **-314.39** |
| `hfq` | 35.55 | 8222.29 | 25.88 |
| 不复权 | 35.55 | 1316.80 | 20.71 |

东财的前复权是**减法式**的（逐次扣掉分红），长历史累计下来直接变成负价，
根本没法用。所以本项目默认 **后复权**，理由见 CONVENTIONS.md C-adj。
"""

from __future__ import annotations

import time

from chan.types import Bar

from .base import Level, bypass_proxy_for_data_hosts, normalize_code, to_bars

# 东财分钟接口的行数上限，用于判断「是不是被截断了」
MIN_BAR_CAP = 1512

_PERIOD = {Level.M30: "30", Level.M5: "5"}


class AkshareFeed:
    """``sleep`` 是每次请求后的固定间隔，批量扫描时按股票分批用。

    ``adjust`` 默认 ``"hfq"``（后复权）。**不要改成 "qfq"**，除非你同时把历史
    起点截到近几年——东财的前复权是减法式的，长历史会算出负价。
    """

    #: 缓存目录名。和 baostock 的后复权口径对不上，两边的数据绝不能混（见 cache）。
    source = "akshare"

    #: 并发拉取的上限。东财是无状态 HTTP，没有 baostock 那种共用账号互相顶会话
    #: 的问题（见 `BaostockFeed.max_jobs`），所以**可以**并发——但压得很低。
    #:
    #: 这个数从 4 降到 2 是被实测打下来的：8 只票跑 `jobs=4`、外加失败重试，约
    #: 48 个请求挤在 30 秒里（≈1.6 req/s），之后东财**连着十几分钟拒绝连接**，
    #: 串行单只也一样被拒。串行带 `sleep` 是 ≈0.6 req/s，一直没出过事。
    #:
    #: **限流长什么样，之前这里写错了。** 原话是「限流回的是空数据不是报错」。
    #: 实际是直接把 TCP 连接掐掉：`RemoteDisconnected('Remote end closed
    #: connection without response')`。这其实是好消息——它会抛，不会无声地
    #: 变成一只「没有数据」的票；但也意味着它会被 `scan.puller` 的每只兜底接住，
    #: 整轮跑完才看得出来「这几千条失败其实是同一个原因」。
    max_jobs = 2

    def __init__(self, sleep: float = 0.3, retries: int = 3, adjust: str = "hfq") -> None:
        self.sleep = sleep
        self.retries = retries
        self.adjust = adjust
        bypass_proxy_for_data_hosts()

    # ------------------------------------------------------------------
    def get_bars(
        self, code: str, level: Level, start: str | None = None, end: str | None = None
    ) -> list[Bar]:
        import akshare as ak

        sym = normalize_code(code)
        if level is Level.DAY:
            df = self._call(
                ak.stock_zh_a_hist,
                symbol=sym,
                period="daily",
                start_date=(start or "19900101").replace("-", ""),
                end_date=(end or "20991231").replace("-", ""),
                adjust=self.adjust,
            )
            tscol = "日期"
        else:
            df = self._call(
                ak.stock_zh_a_hist_min_em,
                symbol=sym,
                period=_PERIOD[level],
                adjust=self.adjust,
                start_date=f"{start or '1990-01-01'} 09:30:00",
                end_date=f"{end or '2099-12-31'} 15:00:00",
            )
            tscol = "时间"

        if df is None or df.empty:
            return []

        rows = [
            (
                str(r[tscol]),
                r["开盘"],
                r["最高"],
                r["最低"],
                r["收盘"],
                r.get("成交量", 0.0),
            )
            for _, r in df.iterrows()
        ]
        # akshare 的日线已经只含交易日，停牌天然不在里面（CONVENTIONS 六）。
        # 这里只再防一手完全重复的时间戳。
        seen, clean = set(), []
        for row in rows:
            if row[0] in seen:
                continue
            seen.add(row[0])
            clean.append(row)
        return to_bars(clean)

    # ------------------------------------------------------------------
    def list_universe(self) -> list[str]:
        """全A股代码表。**不要在这一步顺手去拉行情**，先确认数量再分批。"""
        import akshare as ak

        df = self._call(ak.stock_info_a_code_name)
        return [normalize_code(c) for c in df["code"]]

    # ------------------------------------------------------------------
    def _call(self, fn, **kw):
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                out = fn(**kw)
                time.sleep(self.sleep)
                return out
            except Exception as exc:  # 网络抖动、接口限流
                last = exc
                time.sleep(self.sleep * (2**attempt))
        raise RuntimeError(f"akshare 调用失败（重试 {self.retries} 次）：{last}") from last


def truncated(bars: list[Bar], level: Level) -> bool:
    """分钟线是不是撞上了东财的行数上限。撞上了说明历史不完整，别拿去做级别递归。"""
    return level is not Level.DAY and len(bars) >= MIN_BAR_CAP
