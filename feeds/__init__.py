"""数据层。划线引擎零 IO，所有网络访问都在这个包里。"""

from .base import (
    Blacklisted,
    DataFeed,
    Level,
    exchange_of,
    normalize_code,
    to_bars,
)


def make_feed(source: str) -> DataFeed:
    """按名字造 feed。**在这里而不在 CLI 里**：多进程拉取的 worker 也要造它，
    两处各写一份的话，迟早有一处漏掉新加的源，而症状是「换了源没生效」。

    import 推迟到分支里：akshare 启动要两秒多，只用 baostock 的人不该付这个钱。
    """
    if source == "baostock":
        from .baostock_feed import BaostockFeed

        return BaostockFeed()
    if source == "akshare":
        from .akshare_feed import AkshareFeed

        return AkshareFeed()
    if source == "tencent":
        from .tencent_feed import TencentFeed

        return TencentFeed()
    raise ValueError(f"不认识的数据源：{source}（有 baostock / akshare / tencent）")


__all__ = ["Blacklisted", "DataFeed", "Level", "exchange_of", "make_feed", "normalize_code", "to_bars"]
