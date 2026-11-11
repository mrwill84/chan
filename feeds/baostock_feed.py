"""baostock 数据源。**分钟线的长历史只有它有**，这是引它进来的唯一理由。

东财（akshare）的分钟接口硬顶在约 1512 行、31 个交易日（见 `akshare_feed`
的 `MIN_BAR_CAP` 和 CONVENTIONS 六 C-min），30 分钟只能拿到 250 来根。250 根
连几个线段都划不出来，三级别联立就永远卡在日线那一层——`chan.multilevel`
的区间套会老老实实报 `coverage: "none"`，但报得再诚实也不解决问题。

实测深度（600519，2026-10 抓的）：

| 级别 | 起点 | 根数 |
|---|---|---|
| 日线 | 2001-08-27（上市首日） | 6095 |
| 30分钟 | 2020-01-02 | 13136 |
| 5分钟 | 2020-01-02 | 32256（2024 起算也有这么多） |

## 两个源的后复权**对不上**，不能混

同样是 600519、同样叫「后复权」：

| | 2024-01-02 收盘 | 2026-10-15 收盘 |
|---|---|---|
| akshare（东财） | 9397.44 | 8222.29（10-16） |
| baostock | ≈11819 | 10069.50 |

两边的除权除息还原口径不一样，差了两成多。单看任何一边都自洽，混在一起就
完蛋：日线说三卖在 8773，30 分钟说买点在 11800，人对不上账，而且**没有任何
报错**。所以缓存按源分目录存（`cache.path_for` 的 `source`），一次分析里的
所有级别必须同源。

## 连接是全局的

`baostock` 把 socket 挂在模块全局上，`login()` 不可重入也不是线程安全的。
这里做成进程内只登录一次、退出时登出。**多进程扫描时每个子进程各自登录**
——这正是 `scan.scanner` 用 `initializer` 而不是传连接对象的原因之一。

而且 `login()` 的默认参数就是 `user_id='anonymous'`：**所有进程共用一个
账号**。服务端按账号记会话，后登录的会把先前的顶掉，被顶掉的那个进程下
一次查询回 `10001001 用户未登录`。所以并发拉取时登录不是「开头做一次」
的事，查询失败要能把它重新做一遍（见 `_relogin`）。
"""

from __future__ import annotations

import atexit
import contextlib
import functools
import random
import sys
import threading
import time

from chan.types import Bar

from .base import Blacklisted, Level, exchange_of, normalize_code, to_bars

_FREQ = {Level.DAY: "d", Level.M30: "30", Level.M5: "5"}

# 后复权。baostock 的 1/2/3 = 后复权/前复权/不复权，和东财的字符串不是一回事。
_HFQ = "1"

# 分钟线最早到这天，比这早的 start 给了也没用。写在这里是为了让「拿不到数据」
# 能报成一句人话，而不是让调用方对着空列表猜。
MIN_EPOCH = "2020-01-01"

_lock = threading.Lock()
_logged_in = False


@contextlib.contextmanager
def _quiet():
    """把 baostock 的 `print` 赶到 stderr。

    `login()` / `logout()` 会直接往 stdout 打「login success!」。而
    `scan.cli` 的整个契约是 **stdout 只有 JSON**——Pi 扩展那边一个
    `JSON.parse` 就炸，报错还会指向一个完全无关的位置。
    """
    with contextlib.redirect_stdout(sys.stderr):
        yield


#: 登录重试次数。八个 worker 进程同时 `login()`，服务端会回 10002007
#: 「网络接收错误」——实测 8 个有 5 个失败。这不是凭据问题（baostock 的
#: login 根本不要凭据），是并发握手被挤掉了，退避重试就能过。
LOGIN_RETRIES = 6

#: 查询重试之间的基础等待秒数。真实值要再乘一个 0.5~1.5 的抖动，见 `_chunk`。
RETRY_WAIT = 1.0

#: 单次 `recv` 的超时秒数。**baostock 自己一个 `settimeout` 都没有**，socket
#: 是默认的阻塞模式——对端悄悄断掉时 `recv` 就永远等下去。
#:
#: 这不是假想。一轮 20 只票的日线回填，35 分钟一个文件都没写出来，进程 CPU
#: 全程 0.375 秒（没在转圈，是卡在 `recv`），卡在第一只上。隔夜拉 5224 只，
#: 一次这样就是整轮**无声地停在那里**——不报错、不退出、第二天回来一看还在
#: 「运行中」。
#:
#: 60 秒的依据：实测拉通一只票总共 1~20 秒，而这是**单次 recv** 的上限，
#: 比最坏观测值还宽三倍。
SOCKET_TIMEOUT = 60.0

#: 分页大小对不上时的兜底值。实测 baostock 1.x 是 2000。
_PAGE_FALLBACK = 2000

#: 「用户未登录」。会话被顶掉之后，每次查询都回它，见 `_relogin`。
_NO_LOGIN_FALLBACK = "10001001"

#: 「网络接收错误」。`send_msg` 把一切异常都吞掉回 `None`，调用方统一翻成
#: 这个码——socket 超时、对端断开、连接根本没建起来，全长这一个样。
_RECV_FAIL_FALLBACK = "10002007"

#: 「黑名单用户」。**这个码绝对不能重试。**
#:
#: 拉得太猛会被封 IP。实测就是这么被封的：为了量并发收益，短时间内反复起
#: 多进程拉同一批票，之后 `login()` 开始回 `10001011 黑名单用户，请与管理员
#: 联系`。退避重试在这里是**反作用**——越撞封得越久。碰到它要立刻停、报清
#: 楚，让人去等或者换源。
_BLACKLIST_FALLBACK = "10001011"


@functools.cache
def _const(name: str, fallback):
    """读 baostock 的内部常量，取不到就用兜底值。

    这些数字我们是要拿来做判断的（分页边界、会话失效），写死在自己这边的话
    人家改了版本我们不会报错，只会开始做错误的判断。模块名 `contants` 是
    上游的拼写，不是这里写错了。
    """
    try:
        import baostock.common.contants as c

        return type(fallback)(getattr(c, name))
    except Exception:  # noqa: BLE001 - 内部常量，没了就用兜底值，不该拦住拉数据
        return fallback


def _page_size() -> int:
    """baostock 的分页大小。截断检测靠的是「行数正好是整页倍数」。"""
    return _const("BAOSTOCK_PER_PAGE_COUNT", _PAGE_FALLBACK)


def _reconnect_codes() -> frozenset[str]:
    """这些错误码的意思都是「这条连接/会话已经不能用了」，重查没意义。

    - `10001001` 用户未登录：会话被别的进程顶掉了（见 `_relogin`）。
    - `10002007` 网络接收错误：socket 超时或对端断开。

    两种都得**换一条连接**才有救，而 `bs.login()` 每次会新建 socket，所以
    重新登录正好就是重连。
    """
    return frozenset({
        _const("BSERR_NO_LOGIN", _NO_LOGIN_FALLBACK),
        _const("BSERR_RECVSOCK_FAIL", _RECV_FAIL_FALLBACK),
    })


def _blacklisted() -> str:
    return _const("BSERR_BLACKLIST_USER", _BLACKLIST_FALLBACK)


@contextlib.contextmanager
def _default_timeout():
    """让 baostock 在这段里新建的 socket 自带超时。

    **必须是建 socket 的时候设，不能等登录完再设。** socket 是 `bs.login()`
    内部建的，而登录自己那一次 `recv` 就可能永远回不来——真出事的正是这一步，
    等登录返回了再去 `settimeout` 的代码一行都跑不到。

    `setdefaulttimeout` 是进程级的，所以用完要还回去；已经建好的 socket 会
    保留建它时的超时，还回去不影响它。
    """
    import socket

    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(SOCKET_TIMEOUT)
    try:
        yield
    finally:
        socket.setdefaulttimeout(old)


def _set_timeout() -> None:
    """给已经建好的全局 socket 补一个超时。

    `_default_timeout` 已经覆盖了正常路径，这里是兜底：socket 要是在别处、
    早于我们建起来的，它就没继承到那个默认值。取不到就算了——拿不到 socket
    不该拦住拉数据，最坏情况只是退回到原来那个会卡住的行为。
    """
    try:
        import baostock.common.context as ctx

        sock = getattr(ctx, "default_socket", None)
        if sock is not None:
            sock.settimeout(SOCKET_TIMEOUT)
    except Exception:  # noqa: BLE001
        pass


def _login() -> None:
    """进程内登录一次。失败要抛——静默跳过的话后面每个查询都回空列表。"""
    global _logged_in
    with _lock:
        if _logged_in:
            return
        import baostock as bs

        last = ""
        for i in range(LOGIN_RETRIES):
            if i:
                # 退避**带抖动**：多进程是同一瞬间起来的，等固定时长只会让它们
                # 继续整齐地同时撞上去，重试多少次都是同一个结果。
                time.sleep(min(2**i, 20) * (0.5 + random.random()))
            with _quiet(), _default_timeout():
                r = bs.login()
            if r.error_code == "0":
                _set_timeout()
                _logged_in = True
                atexit.register(_logout)
                return
            if r.error_code == _blacklisted():
                # 重试会把封禁撞得更久，见 `_BLACKLIST_FALLBACK`。立刻停。
                raise Blacklisted(
                    f"baostock 把这个 IP 拉黑了：{r.error_code} {r.error_msg}。"
                    f"这是拉得太猛的结果，**不要重试**——越撞封得越久。"
                    f"等一段时间再来，或者 --source akshare 换个源。"
                )
            last = f"{r.error_code} {r.error_msg}"
        raise RuntimeError(
            f"baostock 登录失败，重试 {LOGIN_RETRIES} 次都没过：{last}。"
            f"10002007 多半是同时登录的进程太多，把 --jobs 调小试试。"
        )


def _relogin() -> None:
    """会话被顶掉之后重新登录。

    `bs.login()` 的签名是 `login(user_id='anonymous', password='123456')`
    ——**所有人都是同一个账号**。服务端按账号记会话，后登录的会把先前的顶
    掉，被顶掉的那个进程下一次查询就回 `10001001 用户未登录`。

    这件事和限流长得完全不一样：连接还在、`query_history_k_data_plus` 照常
    返回，只是每次都回这个码。重查多少遍都是它，**必须重新登录**。不清掉
    `_logged_in` 的话 `_login` 看一眼标记就直接返回了，等于什么也没做。
    """
    global _logged_in
    with _lock:
        _logged_in = False
    _login()


def _logout() -> None:
    global _logged_in
    if not _logged_in:
        return
    import baostock as bs

    with _quiet():
        bs.logout()
    _logged_in = False


def _bs_code(code: str) -> str:
    """``600519`` → ``sh.600519``。北交所 baostock 没有，提前说清楚。"""
    ex = exchange_of(code)
    if ex == "bj":
        raise ValueError(f"baostock 不提供北交所数据：{code}。北交所走 akshare。")
    return f"{ex}.{normalize_code(code)}"


def _ts(date: str, t: str) -> str:
    """``20240102100000000`` → ``2024-01-02 10:00:00``。

    baostock 的分钟 `time` 字段是 17 位（含毫秒），而且是这根K线的**收盘时刻**
    ——和东财一致（30 分钟的第一根是 10:00:00 不是 09:30:00）。两个源在这点上
    碰巧同口径，所以同一只票换源之后时间戳还能对得上。
    """
    if not t:
        return date
    return f"{t[0:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:{t[12:14]}"


class BaostockFeed:
    """``retries`` 只对查询生效。登录自己有一套重试（见 `LOGIN_RETRIES`）。"""

    #: 缓存目录名。和 akshare 的后复权口径对不上，两边的数据绝不能混（见 cache）。
    source = "baostock"

    #: **并发拉取对 baostock 是净亏损，别开。** 同一组 8 只票实测：
    #:
    #: | 并发 | 总耗时 | 每只 | 最慢一只 | 失败 |
    #: |---|---|---|---|---|
    #: | 1 | 77s | 9.6s | 16s | 0 |
    #: | 2 | 103s | 12.9s | 71s | 0 |
    #: | 4 | 102s | 12.7s | 91s | 0 |
    #: | 8 | 181s | 22.6s | 181s | 1 |
    #:
    #: 单调变差，不是「超过某个数才变差」。原因在上面模块文档里：大家共用
    #: `anonymous` 这一个账号，互相顶会话，每被顶一次当前这段就得从头重拉。
    #: 加进程只是加互相顶的频率。这个数字是给 `scan.puller` 读的上限。
    max_jobs = 1

    def __init__(self, retries: int = 3) -> None:
        self.retries = retries

    # ------------------------------------------------------------------
    def get_bars(
        self, code: str, level: Level, start: str | None = None, end: str | None = None
    ) -> list[Bar]:
        import baostock as bs

        _login()
        minute = level is not Level.DAY
        fields = ("date,time,open,high,low,close,volume" if minute
                  else "date,open,high,low,close,volume")
        lo = start or (MIN_EPOCH if minute else "1990-01-01")
        if minute and lo < MIN_EPOCH:
            lo = MIN_EPOCH

        rows = self._query(
            bs, _bs_code(code), fields, lo, end or "2099-12-31", _FREQ[level]
        )

        out = []
        seen: set[str] = set()
        for r in rows:
            ts = _ts(r[0], r[1]) if minute else r[0]
            px = r[2:] if minute else r[1:]
            # 停牌日 baostock 会给一行全 0 或空串。补零会造出假的平盘K线污染
            # 分型（CONVENTIONS 六），所以这里是**丢掉**而不是填充。
            if any(x in ("", None) for x in px[:4]) or float(px[0] or 0) <= 0:
                continue
            if ts in seen:
                continue
            seen.add(ts)
            out.append((ts, float(px[0]), float(px[1]), float(px[2]),
                        float(px[3]), float(px[4] or 0)))
        return to_bars(out)

    # ------------------------------------------------------------------
    def list_universe(self) -> list[str]:
        """全A股代码表，**只要正常上市的股票**。

        `query_all_stock` 连指数一起给（`sh.000001` 是上证指数不是平安银行），
        拿它当股票池会在扫描里混进一堆指数。走 `query_stock_basic` 按
        `type == "1"`（股票）、`status == "1"`（上市）筛。
        """
        import baostock as bs

        _login()
        with _quiet():  # 出错时它会往 stdout 打中文，而 stdout 只能有 JSON
            rs = bs.query_stock_basic()
        if rs.error_code != "0":
            raise RuntimeError(f"baostock 取代码表失败：{rs.error_code} {rs.error_msg}")
        codes = []
        while rs.next():
            code, _name, _ipo, _out, typ, status = rs.get_row_data()[:6]
            if typ != "1" or status != "1":
                continue
            try:
                codes.append(normalize_code(code))
            except ValueError:
                continue  # 指数、基金之类，代码形态对不上就不是股票
        return codes

    # ------------------------------------------------------------------
    def _query(self, bs, code: str, fields: str, start: str, end: str,
               freq: str) -> list[list[str]]:
        """查全一只票的K线。**不信任 baostock 自己的翻页。**

        `ResultData.next()` 在当前页吃完、且刚好满页时会再向服务器要下一页。
        那次请求失败时——连接被挤掉、回了空串——它 `return False`，和
        「数据到此为止」**一模一样**，`error_code` 还留在 `"0"`。

        实测：4 个进程并发拉，600050 回 4000 根、600051 回 2000 根，串行拉是
        5832 和 7156。两个数字都是 2000 的整数倍，正是断在翻页处。这种缓存存
        下去，后面所有的笔、段、中枢都是错的，而且**全程没有任何报错**——
        这是这个项目里最该防的一类错。

        所以拿到整页倍数时不猜，**从最后一天起再要一次**：真结束了就回空，
        断了就能接着往下拿。这样「刚好在页边界上结束」和「断在页边界上」就
        不再是同一个现象了，不用靠概率去赌。

        重叠的那一天会被重复取到，由 `get_bars` 的 `seen` 去重。
        """
        page = _page_size()
        rows: list[list[str]] = []
        lo = start
        # 5 分钟线四万多根 ≈ 40 页。给足余量，但必须有上限——服务端一直回整页
        # 的话，没有这个数就是个死循环。
        for _ in range(200):
            chunk = self._chunk(bs, code, fields, lo, end, freq)
            rows += chunk
            if not chunk or len(chunk) % page:
                return rows
            nxt = chunk[-1][0]
            if nxt == lo:
                # 一整页落在同一天（分钟线有可能），再问还是这一页
                return rows
            lo = nxt
        return rows

    def _chunk(self, bs, code: str, fields: str, start: str, end: str,
               freq: str) -> list[list[str]]:
        """要一段，带重试。失败要抛——回空列表会被读成「这只票没数据」。

        重试前先分辨是哪种失败：`_reconnect_codes()` 里那几个的意思是「这条
        连接已经废了」，原地重查一万遍还是它，得先重新登录换一条。其余的才
        是重查能好的。

        查询也要套 `_quiet()`：baostock 在 socket 出错时会 `print` 两行中文到
        **stdout**，而 `scan.cli` 的契约是 stdout 只有 JSON。平时碰不到，偏偏
        在出错那一刻把扩展那边的 `JSON.parse` 一起带下水。
        """
        last = ""
        for i in range(self.retries):
            if i:
                # 抖动。重登录会反过来顶掉别人，几个进程同时撞上来的话，
                # 等固定时长只会让它们排着队互相顶，谁都拉不完。
                time.sleep(RETRY_WAIT * (0.5 + random.random()))
            with _quiet():
                rs = bs.query_history_k_data_plus(
                    code, fields, start_date=start, end_date=end,
                    frequency=freq, adjustflag=_HFQ,
                )
            err, out = rs.error_code, []
            if err == "0":
                with _quiet():
                    while rs.next():
                        out.append(rs.get_row_data())
                # 翻页失败时 next() 才把失败码写回 error_code，所以读完要再看
                # 一眼——开头那次检查管不到翻页。（回空串那条路不改
                # error_code，那种情况由 `_query` 的整页倍数检查接住。）
                err = rs.error_code
                if err == "0":
                    return out
                last = f"{err} {rs.error_msg}（翻页时断的）"
            else:
                last = f"{err} {rs.error_msg}"
            if err in _reconnect_codes():
                _relogin()
        raise RuntimeError(f"baostock 查询失败（重试 {self.retries} 次）：{last}")
