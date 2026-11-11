"""命令行入口。Pi 扩展就是 shell out 到这里，所以**只往 stdout 写 JSON**。

    python -m scan universe --save all.txt                      # 全市场代码表
    python -m scan pull --file all.txt --progress               # 日线回填，可续
    python -m scan pull --file all.txt --update                 # 盘后增量
    python -m scan analyze 600519 --levels day,30min
    python -m scan scan --file codes.txt --level day --within 3 --kinds 3
    python -m scan fullscan --file all.txt --state run.jsonl --jobs 8   # 可续传
    python -m scan nest 600519 --levels day,30min --cached-only
    python -m scan dash --jobs 16 --progress                    # 看板数据

`--source` 是**主解析器**上的参数，要写在子命令之前：`python -m scan
--source akshare analyze 600519`。默认 baostock——只有它给得出分钟线的长历史。
baostock 被封或东财限流时可以临时 `--source tencent`，但它**只有日线、只有最近
640 根**，而且缓存另存一个目录，和前两家的价不可比（C-tx / C-src）。

进度、警告一律走 stderr——stdout 里混进一个汉字，扩展那边 `JSON.parse` 就炸，
而且报错信息会指向完全无关的地方。这个教训不值得再交一次学费。
"""

from __future__ import annotations

import argparse
import json
import sys

from chan.verify import MAX_TRIES
from feeds import Level, cache, make_feed, normalize_code

from .report import analyze_one, scan_many

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def _feed(source: str):
    try:
        return make_feed(source)
    except ValueError as e:
        raise SystemExit(str(e)) from None


def _loader(cached_only: bool, source: str = cache.DEFAULT_SOURCE):
    """K线从哪来。``cached_only`` 时绝不发网络请求。

    全市场扫描默认走缓存：5400 只现拉一遍要几个小时，而盘后扫描的正确姿势是
    先跑一轮增量更新、再扫。两件事分开，扫描才敢随便重跑。

    `source` 一路传到底，**三个级别必须同源**：两个源的后复权口径差两成多
    （见 `feeds.cache` 的说明），混着读出来的价格对不上账还不报错。
    """
    if cached_only:
        return lambda code, level: cache.load(code, level, source=source)

    feed = _feed(source)
    return lambda code, level: cache.get_or_fetch(feed, code, level)


def _emit(obj: dict, out: str | None) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(json.dumps({"written": out, "bytes": len(text.encode())},
                         ensure_ascii=False))
    else:
        print(text)


def cmd_analyze(args) -> None:
    load = _loader(args.cached_only, args.source)
    levels = [Level(x) for x in args.levels.split(",")]
    code = normalize_code(args.code)
    # 带上源：价位数字跨源不可比（C-src），读报告的人得知道这是谁家的后复权
    out: dict = {"code": code, "source": args.source, "levels": {}}
    for lv in levels:
        try:
            bars = load(code, lv)
            out["levels"][lv.value] = analyze_one(
                bars, code, lv, zs_from=args.zs, recent=args.recent,
                include_quasi=not args.no_quasi,
            )
        except Exception as e:  # noqa: BLE001 - 一个级别拉不到不该拖垮另外两个
            out["levels"][lv.value] = {"code": code, "level": lv.value,
                                       "error": f"{type(e).__name__}: {e}"}
    _emit(out, args.out)


def _codes_from(args) -> list[str]:
    raw: list[str] = list(args.codes or [])
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            raw += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not raw:
        raise SystemExit("没给股票代码：用位置参数或 --file")
    seen: dict[str, None] = {}
    for c in raw:
        seen.setdefault(normalize_code(c), None)
    return list(seen)


def cmd_scan(args) -> None:
    codes = _codes_from(args)
    load = _loader(args.cached_only, args.source)

    def progress(n: int, total: int, code: str) -> None:
        if n % 50 == 0 or n == total:
            print(f"[{n}/{total}] {code}", file=sys.stderr)

    res = scan_many(
        codes,
        Level(args.level),
        load,
        zs_from=args.zs,
        within=args.within,
        kinds={int(x) for x in args.kinds.split(",")} if args.kinds else None,
        sides=set(args.sides.split(",")) if args.sides else None,
        include_quasi=args.quasi,
        confirmed_only=args.confirmed_only,
        on_progress=progress if args.progress else None,
    )
    if args.limit:
        res["hits"] = res["hits"][: args.limit]
    _emit(res, args.out)


def cmd_fullscan(args) -> None:
    """全市场扫描。和 `scan` 的区别只有一个：**结果边算边落盘，可以续传。**

    几十只用 `scan` 就够了，省一个文件。几千只必须用这个——一次 Ctrl-C
    作废半小时的计算，第二次就没人敢跑了。
    """
    from pathlib import Path

    from .scanner import collect, run_scan

    codes = _codes_from(args)

    def progress(n: int, total: int, rec: dict) -> None:
        if n % 50 == 0 or n == total:
            print(f"[{n}/{total}] {rec['code']} {rec.get('status', '')}", file=sys.stderr)

    res = run_scan(
        codes,
        Level(args.level),
        Path(args.state),
        zs_from=args.zs,
        within=args.within,
        kinds={int(x) for x in args.kinds.split(",")} if args.kinds else None,
        sides=set(args.sides.split(",")) if args.sides else None,
        include_quasi=args.quasi,
        confirmed_only=args.confirmed_only,
        source=args.source,
        jobs=args.jobs,
        verify=args.verify,
        force=args.force,
        on_progress=progress if args.progress else None,
    )
    tries = res["health"]["tries"]
    # 命中清单从落盘文件读回来，而不是从这一轮的内存里取——续传时大部分
    # 命中本来就不在这一轮里算过。
    res.update(collect(Path(args.state), limit=args.limit))
    # `collect` 的 health 统计的是**整个文件**（含续传带进来的旧记录），比这一
    # 轮的全；但它读不到 `tries`（不在配置行里），所以把这一轮的补回去。
    res["health"]["tries"] = tries
    _emit(res, args.out)
    _explain_empty(res)
    _warn_unstable(res)


def _warn_unstable(res: dict) -> None:
    """自检出事时吵到 stderr 上。

    这一层的全部价值就是「把安静地算错变成吵闹地重算」，而 JSON 里的
    `health` 字段不吵——0 命中那次的教训是，人不会去翻 JSON。
    """
    h = res.get("health") or {}
    if h.get("recomputed_count"):
        print(f"\n注意：{h['recomputed_count']} 只票两遍结果不一致，重算后取的多数。"
              f"结果可信，但**这台机器在翻位**：{', '.join(h['recomputed'][:10])}",
              file=sys.stderr)
    if h.get("unstable_count"):
        print(f"\n**{h['unstable_count']} 只票凑不齐多数，结果不可信**："
              f"{', '.join(h['unstable'][:10])}\n"
              "随机损坏不挑票，所以这一轮的输出整体都该打问号，不是只打这几只。"
              "先查内存（见 chan/verify.py），再谈结果。", file=sys.stderr)


def _explain_empty(res: dict) -> None:
    """一只都没命中时，把「为什么」印到 stderr。

    JSON 里本来就有 `missed_because`，但 0 命中的那一刻人是不会去翻 JSON 的
    ——看见 `"hit_count": 0` 就直接下结论「今天没机会」了。而实测过的情形是：
    391 只全部有买卖点，97 只就在 5 根K线之内，只是**全是 quasi 且未确认**，
    被默认筛条件整批挡掉。那次的 0 和「市场上没有买卖点」毫无关系。
    """
    if res.get("hit_count") or res.get("failed_count") == res.get("scanned"):
        return
    from .scanner import MISS_REASONS

    why = res.get("missed_because") or {}
    print("\n一只都没命中。这不一定是「没有买卖点」，看看是被哪道筛子挡的：",
          file=sys.stderr)
    for reason, n in why.items():
        print(f"  {n:5d} 只  {MISS_REASONS.get(reason, reason)}", file=sys.stderr)


def cmd_pull(args) -> None:
    """把K线拉进缓存。扫描本身只读缓存，拉数据是**单独一件事**。

    分开的理由和 `fullscan` 续传一样：拉取是 IO 密集、会被限流、会半路断；
    划线是纯 CPU、秒级。混在一个命令里，一次网络抖动就让人分不清是没拉到
    还是没算对。
    """
    import time

    from .puller import run_pull

    codes = _codes_from(args)
    levels = [Level(x) for x in args.levels.split(",")]
    mode = "refresh" if args.refresh else ("update" if args.update else "missing")
    t0 = time.monotonic()

    # 小批量每只都报，几千只时每 20 只报一次。固定成 20 的话，拉十只票的人
    # 全程看不到任何输出，分不清是在跑还是卡死了。
    step = 1 if len(codes) <= 50 else 20

    def progress(n: int, total: int, rec: dict) -> None:
        if n % step and n != total:
            return
        # 带上预计剩余：几个小时的活，没有这个数就只能盯着看
        el = time.monotonic() - t0
        eta = el / n * (total - n)
        print(f"[{n}/{total}] {rec['code']}  已用 {el / 60:.0f}min  "
              f"剩约 {eta / 60:.0f}min", file=sys.stderr)

    out = run_pull(codes, levels, source=args.source, mode=mode, jobs=args.jobs,
                   start=args.start,
                   on_progress=progress if args.progress else None)
    # 把 jobs 压下去这件事要说出来。用户给了 8 却按 1 跑，慢得莫名其妙的话，
    # 下一步多半是去怀疑网络。
    if out["jobs"] < out["jobs_requested"]:
        print(f"注意：--jobs {out['jobs_requested']} 压到了 {out['jobs']}，"
              f"{out['source']} 并发拉取只会更慢（见 scan/puller.py 的实测表）。",
              file=sys.stderr)
    _emit(out, args.out)


def cmd_universe(args) -> None:
    """全市场代码表。**只取代码，不碰行情。**

    单独一个命令是为了让「股票池是什么」这件事可审计：代码表落成一个文本
    文件，`pull` 和 `fullscan` 都 `--file` 它。扫描结果对不上时，第一个要排除
    的就是「这两次跑的根本不是同一批票」。
    """
    codes = _feed(args.source).list_universe()
    if args.prefix:
        want = tuple(args.prefix.split(","))
        codes = [c for c in codes if c.startswith(want)]
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write("\n".join(codes) + "\n")
    _emit({"source": args.source, "count": len(codes),
           "saved": args.save, "sample": codes[:20]}, args.out)


def cmd_nest(args) -> None:
    """区间套：大级别定方向，小级别收缩时机（27课 p67）。"""
    from chan.multilevel import analyze_level, nest

    load = _loader(args.cached_only, args.source)
    code = normalize_code(args.code)
    levels = [Level(x) for x in args.levels.split(",")]

    las = []
    for lv in levels:
        try:
            las.append(analyze_level(lv.value, load(code, lv), zs_from=args.zs,
                                     include_quasi=not args.no_quasi))
        except Exception as e:  # noqa: BLE001
            from chan.multilevel import LevelAnalysis

            las.append(LevelAnalysis(lv.value, (), "none",
                                     error=f"{type(e).__name__}: {e}"))

    n = nest(las, side={"buy": True, "sell": False}.get(args.side),
             kinds={int(x) for x in args.kinds.split(",")} if args.kinds else None)
    _emit(_nest_json(code, las, n, args.source), args.out)


def _nest_json(code: str, las, n, source: str) -> dict:
    from .report import _bs_json

    def pt(a, p):
        if p is None:
            return None
        # `_bs_json` 要原始K线取时间戳，这里只有 ts 元组，拿一个最小替身喂它
        return _bs_json(p, [_TsOnly(t) for t in a.ts])

    by_name = {a.name: a for a in las}
    return {
        "code": code,
        "source": source,
        "agreement": n.agreement,
        "notes": list(n.notes),
        "levels": {a.name: {"trend": a.trend, "bars": len(a.ts),
                            "first_ts": a.ts[0] if a.ts else "",
                            "last_ts": a.ts[-1] if a.ts else "",
                            "zhongshu_count": len(a.zhongshus),
                            "bspoint_count": len(a.points),
                            "error": a.error}
                   for a in las},
        "steps": [{"level": s.level, "window": list(s.window),
                   "coverage": s.coverage, "ts": s.ts, "note": s.note,
                   "point": pt(by_name[s.level], s.point),
                   "against": [pt(by_name[s.level], p) for p in s.against]}
                  for s in n.steps],
        "precise": ({"level": n.precise.level, "ts": n.precise.ts}
                    if n.precise else None),
    }


def cmd_dash(args) -> None:
    """看板数据。不给代码就扫缓存里 30/5 分钟都有的全部票。"""
    from pathlib import Path

    from .dashboard import cached_codes, run

    codes = (_codes_from(args) if args.codes or args.file
             else cached_codes(args.source))

    def progress(n: int, total: int, rec: dict) -> None:
        if n % 100 == 0 or n == total:
            print(f"[{n}/{total}] {rec['code']} {rec.get('status', '')}", file=sys.stderr)

    res = run(codes, Path(args.dest), n30=args.n30, jobs=args.jobs,
              verify=args.verify, source=args.source,
              on_progress=progress if args.progress else None)
    _emit(res, args.out)
    if res["health"]["unstable"]:
        print(f"警告：{len(res['health']['unstable'])} 只算了几遍凑不齐多数，"
              "看板上标红，这些行不可信（见 chan/verify.py）", file=sys.stderr)


def cmd_checkcache(args) -> None:
    """缓存体检。三态计数：``ok`` / ``corrupt`` / ``unrecorded``。

    **不要把 `unrecorded` 并进 `ok`。** 边车是后加的，存量缓存一个都没有，
    并了之后这条命令会在一个从未被核对过的缓存上回「全部健康」——那比不跑
    还糟，因为它给了一个假的安心。

    ``--rewrite`` 只补没有的。对不上的那些**故意不覆盖**：摘要和数据不一致
    时，对的是哪一份这里不知道，改账只会把证据抹掉。那种情况要重拉。
    """
    codes = _codes_from(args)
    levels = [Level(x.strip()) for x in args.levels.split(",") if x.strip()]
    rows, tally = [], {"ok": 0, "corrupt": 0, "unrecorded": 0, "missing": 0}
    for code in codes:
        for lv in levels:
            r = cache.integrity(code, lv, source=args.source)
            if not r["exists"]:
                state = "missing"
            elif r["ok"] is None:
                state = "unrecorded"
                if args.rewrite:
                    cache.digest_path_for(code, lv, source=args.source) \
                        .write_text(r["digest"], encoding="utf-8")
                    state = "recorded"
            else:
                state = "ok" if r["ok"] else "corrupt"
            tally[state if state != "recorded" else "ok"] = \
                tally.get(state if state != "recorded" else "ok", 0) + 1
            rows.append({"code": code, "level": lv.value, "state": state,
                         "n": r["n"], "digest": r.get("digest"),
                         "recorded": r.get("recorded")})
    bad = [r for r in rows if r["state"] == "corrupt"]
    _emit({"source": args.source, "n": len(rows), "tally": tally,
           "corrupt": bad,
           "note": ("unrecorded 不是「健康」，是「从来没人核对过」"
                    if tally.get("unrecorded") else ""),
           "rows": rows}, args.out)


class _TsOnly:
    """只有 `ts` 的 Bar 替身。`_bs_json` 要的就只是这一个字段。"""

    __slots__ = ("ts",)

    def __init__(self, ts: str):
        self.ts = ts


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="scan", description="缠论划线 JSON 出口")
    p.add_argument("--out", help="写到文件而不是 stdout")
    # 放在主解析器上（要写在子命令**之前**）：源是贯穿全局的东西，让每个子命令
    # 各带一份迟早会出现「analyze 用了这个源、fullscan 用了那个源」。
    p.add_argument("--source", default=cache.DEFAULT_SOURCE,
                   choices=["baostock", "akshare", "tencent"],
                   help="数据源。三家的后复权口径不同，缓存分目录存，不能混。"
                        "tencent 是应急源：只有日线、只有最近 640 根（见 C-tx）")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="单只股票，多级别完整结构")
    a.add_argument("code")
    a.add_argument("--levels", default="day", help="逗号分隔，如 day,30min,5min")
    a.add_argument("--zs", default="seg", choices=["seg", "bi"])
    a.add_argument("--recent", type=int, default=5, help="只回最近 N 个中枢/买卖点")
    a.add_argument("--no-quasi", action="store_true", help="丢掉类买卖点")
    a.add_argument("--cached-only", action="store_true")
    a.set_defaults(func=cmd_analyze)

    s = sub.add_parser("scan", help="批量扫描，回最近出过买卖点的票")
    s.add_argument("codes", nargs="*")
    s.add_argument("--file", help="每行一个代码的文本文件")
    s.add_argument("--level", default="day", choices=[x.value for x in Level])
    s.add_argument("--zs", default="seg", choices=["seg", "bi"])
    s.add_argument("--within", type=int, default=5, help="最后一个买卖点距今 N 根以内")
    s.add_argument("--kinds", help="只要某几类，如 1,3")
    s.add_argument("--sides", help="buy / sell，逗号分隔")
    s.add_argument("--quasi", action="store_true", help="把类买卖点也算进来")
    s.add_argument("--confirmed-only", action="store_true", help="丢掉未完成笔上的点")
    s.add_argument("--limit", type=int, help="最多回几条")
    s.add_argument("--progress", action="store_true", help="进度写 stderr")
    s.add_argument("--cached-only", action="store_true", default=True)
    s.add_argument("--fetch", dest="cached_only", action="store_false",
                   help="缓存缺失时现拉（慢，全市场别用）")
    s.set_defaults(func=cmd_scan)

    g = sub.add_parser("fullscan", help="全市场扫描，边算边落盘，可中断续传")
    g.add_argument("codes", nargs="*")
    g.add_argument("--file", help="每行一个代码的文本文件")
    g.add_argument("--state", required=True,
                   help="进度文件（JSONL）。再跑一次同样的命令就是续传")
    g.add_argument("--level", default="day", choices=[x.value for x in Level])
    g.add_argument("--zs", default="seg", choices=["seg", "bi"])
    g.add_argument("--within", type=int, default=5)
    g.add_argument("--kinds", help="只要某几类，如 1,3")
    g.add_argument("--sides", help="buy / sell，逗号分隔")
    g.add_argument("--quasi", action="store_true")
    g.add_argument("--confirmed-only", action="store_true")
    g.add_argument("--jobs", type=int, default=1, help="进程数。几千只建议给核数")
    g.add_argument("--verify", type=int, default=MAX_TRIES, metavar="N",
                   help=f"每只算几遍比摘要，不一致就重算取多数（默认 {MAX_TRIES}，"
                        "1 = 关掉）。防的是硬件位翻不是 bug，代价一倍 CPU")
    g.add_argument("--force", action="store_true", help="丢掉已有结果重来")
    g.add_argument("--limit", type=int, help="摘要里最多回几条命中")
    g.add_argument("--progress", action="store_true")
    g.set_defaults(func=cmd_fullscan)

    v = sub.add_parser("universe", help="全市场代码表（只取代码，不拉行情）")
    v.add_argument("--save", help="写成每行一个代码的文本文件，给 pull/fullscan 用")
    v.add_argument("--prefix", help="只要某些开头，如 60,00,30（逗号分隔）")
    v.set_defaults(func=cmd_universe)

    u = sub.add_parser("pull", help="把K线拉进本地缓存（扫描只读缓存）")
    u.add_argument("codes", nargs="*")
    u.add_argument("--file", help="每行一个代码的文本文件")
    u.add_argument("--levels", default="day", help="逗号分隔，如 day,30min,5min")
    u.add_argument("--update", action="store_true",
                   help="增量补末尾（盘后每天跑这个）。不给就是「缓存没有才拉」")
    u.add_argument("--refresh", action="store_true",
                   help="整条重拉。换源或怀疑历史有问题时才用，很慢")
    u.add_argument("--start", default=None,
                   help=f"历史起点，默认 {cache.DEFAULT_START}（和分钟线同一天）。"
                        "提前它会显著变慢：耗时按页算")
    u.add_argument("--jobs", type=int, default=1,
                   help="进程数。会被压到数据源的上限——baostock 是 1，"
                        "实测并发只会更慢（见 scan.puller）")
    u.add_argument("--progress", action="store_true", help="进度和预计剩余写 stderr")
    u.set_defaults(func=cmd_pull)

    c = sub.add_parser("checkcache",
                       help="缓存体检：读出来的K线和写进去的那份对不对得上")
    c.add_argument("codes", nargs="*")
    c.add_argument("--file", help="每行一个代码的文本文件")
    c.add_argument("--levels", default="day", help="逗号分隔，如 day,30min,5min")
    c.add_argument("--rewrite", action="store_true",
                   help="给**没有**摘要的缓存补记一份。只补没有的，"
                        "**不覆盖对不上的**——那种要重拉，不是改账")
    c.set_defaults(func=cmd_checkcache)

    n = sub.add_parser("nest", help="区间套：大级别定方向，小级别收缩时机")
    n.add_argument("code")
    n.add_argument("--levels", default="day,30min,5min", help="**从大到小**排")
    n.add_argument("--zs", default="seg", choices=["seg", "bi"])
    n.add_argument("--side", choices=["buy", "sell"], help="只从买点或卖点出发")
    n.add_argument("--kinds", help="出发点只要某几类，如 1,2")
    n.add_argument("--no-quasi", action="store_true")
    n.add_argument("--cached-only", action="store_true")
    n.set_defaults(func=cmd_nest)

    d = sub.add_parser("dash", help="看板数据：30/5 分钟最新买卖点 + 区间套，一只一行")
    d.add_argument("codes", nargs="*", help="不给就扫缓存里 30/5 分钟都有的全部票")
    d.add_argument("--file", help="每行一个代码的文本文件")
    d.add_argument("--dest", default="_plot/interactive/dashboard_data.js")
    d.add_argument("--n30", type=int, default=2000,
                   help="30 分钟取最后几根，和查看器同一个窗口")
    d.add_argument("--jobs", type=int, default=1, help="进程数。全部票建议给核数的一半")
    d.add_argument("--verify", type=int, default=MAX_TRIES, metavar="N",
                   help=f"每只算几遍比摘要（默认 {MAX_TRIES}，1 = 关掉）")
    d.add_argument("--progress", action="store_true")
    d.set_defaults(func=cmd_dash)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
