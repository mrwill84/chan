"""全市场扫描，可中断续传。

5400 只票跑一轮要几十分钟。中途断电、Ctrl-C、某只票触发一个划线 bug——
不能让这些把已经算完的几千只一起作废。所以结果**一只一行、边算边落盘**，
重跑时读回已有的那些、只补没算的。

落盘格式是 JSONL：第一行是这轮的配置，之后每行一只票。选它不是因为好看，
是因为**追加写在崩溃时最安全**——写坏最多坏最后一行，前面的全部可读。
整份 JSON 做不到这点，写到一半断电就是个废文件。

## 换了筛选条件就不能接着续

`within`、`kinds`、`include_quasi` 这些一改，已算的记录和新算的就不是同一回事，
混在一起的结果没有任何意义，而且**看不出来**——它长得和正常结果一模一样。
所以配置行会被逐字比对，对不上直接拒绝，要么换个输出文件，要么 `--force` 重来。

级别和 `zs_from` 同理，更不用说。
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from chan.verify import MAX_TRIES
from feeds import Level
from feeds.cache import DEFAULT_SOURCE

HEADER = "__config__"

HIT, MISS, FAIL = "hit", "miss", "fail"


def _config(level: Level, zs_from: str, within: int, kinds, sides,
            include_quasi: bool, confirmed_only: bool,
            source: str = DEFAULT_SOURCE) -> dict:
    """这轮扫描的身份。续传时逐字比对的就是它。

    `source` 也在里面：两个数据源的后复权口径不一样（见 `feeds.cache`），
    同一套筛选条件跑出来的命中根本不是同一批。
    """
    return {
        HEADER: 1,
        "level": level.value,
        "source": source,
        "zs_from": zs_from,
        "within": within,
        "kinds": sorted(kinds) if kinds else None,
        "sides": sorted(sides) if sides else None,
        "include_quasi": include_quasi,
        "confirmed_only": confirmed_only,
    }


class ConfigMismatch(Exception):
    """续传时发现配置变了。不能静默接着写——混出来的结果看不出错。"""


@dataclass(slots=True)
class Progress:
    """已落盘的部分。`done` 的键就是「这只不用再算了」。"""

    path: Path
    config: dict | None = None
    done: dict[str, dict] = field(default_factory=dict)
    broken_tail: bool = False

    @property
    def counts(self) -> dict[str, int]:
        out = {HIT: 0, MISS: 0, FAIL: 0}
        for r in self.done.values():
            out[r.get("status", FAIL)] = out.get(r.get("status", FAIL), 0) + 1
        return out


def read_progress(path: Path) -> Progress:
    """读回已有结果。坏掉的尾行直接丢，不报错。

    崩溃时最后一行多半是写了一半的。那一只重算一遍就是了，为它拒绝整个文件
    就把续传这件事本身给废了——而续传正是这个模块存在的理由。
    """
    p = Progress(path=path)
    if not path.exists():
        return p
    with path.open(encoding="utf-8") as f:
        lines = f.read().splitlines()
    for n, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # 只容忍**最后**一行坏。中间坏掉说明文件被别的东西动过，
            # 那就不是崩溃残留，继续用它是在拿错误结果冒充完整结果。
            if n == len(lines) - 1:
                p.broken_tail = True
                break
            raise ConfigMismatch(f"{path} 第 {n + 1} 行不是 JSON，文件已损坏，不能续传")
        if HEADER in rec:
            p.config = rec
        elif "code" in rec:
            p.done[rec["code"]] = rec
    return p


def _check_resumable(prog: Progress, cfg: dict) -> None:
    if prog.config is None:
        if prog.done:
            raise ConfigMismatch(f"{prog.path} 没有配置行，无法确认是同一轮扫描")
        return
    diff = {k: (prog.config.get(k), v) for k, v in cfg.items() if prog.config.get(k) != v}
    if diff:
        human = "；".join(f"{k}：原 {a!r} → 现 {b!r}" for k, (a, b) in diff.items())
        raise ConfigMismatch(
            f"{prog.path} 是另一套条件下的结果，不能接着写（{human}）。"
            f"换个 --out，或加 --force 重来。"
        )


def repair(prog: Progress) -> None:
    """崩溃残留的半行会让下一次追加接在它后面，拼出一行谁也读不了的垃圾。

    所以续写之前先按已读到的记录重写一遍。5400 行而已，没必要为省这一次
    重写去算字节偏移——utf-8 加换行符转换，偏移算错的概率比省下的时间值钱。
    """
    if not prog.broken_tail:
        return
    with prog.path.open("w", encoding="utf-8", newline="\n") as f:
        if prog.config is not None:
            f.write(json.dumps(prog.config, ensure_ascii=False) + "\n")
        for rec in prog.done.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    prog.broken_tail = False


class Writer:
    """一只一行，写完就 flush。

    不缓冲：缓冲能快一点，但代价是崩溃时丢掉一整块已经算完的结果，
    而这个模块的全部意义就是不丢。扫 5400 只的瓶颈在划线不在 IO。
    """

    def __init__(self, path: Path, cfg: dict, *, fresh: bool):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        new = fresh or not path.exists()
        self.f = path.open("w" if fresh else "a", encoding="utf-8", newline="\n")
        if new:
            self._line(cfg)

    def _line(self, obj: dict) -> None:
        self.f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.f.flush()
        os.fsync(self.f.fileno())

    def write(self, rec: dict) -> None:
        self._line(rec)

    def close(self) -> None:
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# --------------------------------------------------------------------------
# 干活的
# --------------------------------------------------------------------------

# 子进程里的任务参数。multiprocessing 在 Windows 上走 spawn，每个子进程都要
# 重新 import 一遍本模块，所以这些必须是模块级的可 pickle 对象，不能是闭包。
_TASK: dict = {}


def _init(task: dict) -> None:
    _TASK.update(task)


def _one(code: str) -> dict:
    """算一只。**任何异常都转成 FAIL 记录**，绝不让它冒到池子外面。

    一只票崩掉就带走整个 worker、进而让 `imap_unordered` 抛出来，那这一轮
    剩下的几千只就白跑了——而这正是续传要防的事，没道理在这里自己制造一次。
    """
    from chan.types import Bar  # noqa: F401  （子进程里预热导入，避免首只特别慢）

    from feeds import Level
    from feeds.cache import load

    from .report import _match, analyze_one_checked

    t = _TASK
    level = Level(t["level"])
    # 任务字典要跨进程 pickle，所以 root 是字符串存的，用的时候转回来
    root = Path(t["root"]) if t["root"] else None
    try:
        bars = load(code, level, root, source=t["source"])
        if not bars:
            return {"code": code, "status": FAIL,
                    "error": f"{t['source']} 的缓存里没有这只票的K线"}
        # quasi 一律划进来，筛在 `_match` 里做——否则「被 quasi 滤掉的」和
        # 「压根没有买卖点」在结果里分不出来，而这两件事的含义差着十万八千里。
        r = analyze_one_checked(bars, code, level, tries=t.get("verify", 1),
                                zs_from=t["zs_from"], include_quasi=True)
        # 自检的账只在**不正常时**落盘：5400 行里每行都写一个 `"runs": 2`
        # 等于没写，而这一条要的是在日志里跳出来。正常那些由 `tries` 和
        # 总数反推即可。
        v = r.get("verify", {})
        chk = ({"runs": v["runs"], "ok": v["ok"], "bars": v["bars_stable"]}
               if v.get("runs", 1) > 2 or not v.get("ok", True)
               or not v.get("bars_stable", True) else None)
        if "error" in r:
            return {"code": code, "status": FAIL, "error": r["error"]}
        hit, why = _match(r, within=t["within"],
                          kinds=set(t["kinds"]) if t["kinds"] else None,
                          sides=set(t["sides"]) if t["sides"] else None,
                          confirmed_only=t["confirmed_only"],
                          include_quasi=t["include_quasi"])
        if hit is None:
            # 没命中的也要落盘。不记的话续传时分不清「算过没中」和「还没算」，
            # 每次重跑都要把几千只没中的重算一遍，续传就只剩一半作用。
            # `why` 里是**为什么**没中，汇总出来就是「0 命中」那行的解释。
            return {"code": code, "status": MISS, "trend": r["trend"],
                    "last_ts": r["bars"]["last_ts"],
                    **({"verify": chk} if chk else {}), **why}
        return {"code": code, "status": HIT,
                **({"verify": chk} if chk else {}), **hit}
    except Exception as e:  # noqa: BLE001
        return {"code": code, "status": FAIL, "error": f"{type(e).__name__}: {e}"}


def run_scan(
    codes: list[str],
    level: Level,
    out: Path,
    *,
    zs_from: str = "seg",
    within: int = 5,
    kinds: set[int] | None = None,
    sides: set[str] | None = None,
    include_quasi: bool = False,
    confirmed_only: bool = False,
    root: Path | None = None,
    source: str = DEFAULT_SOURCE,
    jobs: int = 1,
    verify: int = MAX_TRIES,
    force: bool = False,
    on_progress=None,
) -> dict:
    """扫一轮，边算边写 `out`。再跑一次同样的命令就是续传。

    `force=True` 丢掉已有结果重来。`jobs>1` 开多进程——划线是纯 CPU，
    5400 只在单核上要几十分钟。

    `verify` 是每只票算几遍（默认开，见 `chan.verify`）。30 只日线单进程实测
    4.1s → 6.7s：划线那部分确实翻倍，但读 CSV 和起解释器不翻，所以整轮的墙上
    时间大约 +60% 而不是 +100%。

    它**不进 `_config`**：
    算两遍和算一遍在硬件正常时给同一个答案，它不是筛选条件，改了它不该让
    已经算完的几千只作废。代价是续传出来的结果里，早先那批没查过——所以
    `health` 报的是「本轮查了几只」而不是「文件里有几只是好的」。

    **只读缓存，不联网。** 全市场回填是另一件事（`feeds/` 的活），和扫描
    混在一起的话，一次中断就分不清是数据没拉全还是没算完。
    """
    cfg = _config(level, zs_from, within, kinds, sides, include_quasi,
                  confirmed_only, source)
    out = Path(out)
    prog = Progress(path=out) if force else read_progress(out)
    if not force:
        _check_resumable(prog, cfg)
        repair(prog)

    todo = [c for c in codes if c not in prog.done]
    task = {"level": level.value, "zs_from": zs_from, "within": within,
            "kinds": sorted(kinds) if kinds else None,
            "sides": sorted(sides) if sides else None,
            "include_quasi": include_quasi, "confirmed_only": confirmed_only,
            "root": str(root) if root else None, "source": source,
            "verify": verify}

    n_done = len(prog.done)
    total = len(codes)
    with Writer(out, cfg, fresh=force) as w:
        for rec in _iterate(todo, task, jobs):
            prog.done[rec["code"]] = rec
            w.write(rec)
            n_done += 1
            if on_progress is not None:
                on_progress(n_done, total, rec)

    counts = prog.counts
    return {
        "out": str(out),
        "level": level.value,
        "zs_from": zs_from,
        "total": total,
        "computed_now": len(todo),
        "resumed": total - len(todo),
        "hit_count": counts[HIT],
        "miss_count": counts[MISS],
        "failed_count": counts[FAIL],
        "missed_because": _missed_because(prog),
        "health": _health(prog, verify),
        "filter": {k: v for k, v in cfg.items() if k != HEADER},
    }


def _health(prog, tries: int | None = None) -> dict:
    """这台机器在这一轮里翻了几个位（见 `chan.verify`）。

    `recomputed` 非空 = 真的发生过损坏，只是被多数票挡住了。`unstable` 非空
    = **有票的结果不可信**，而且既然损坏是随机的，那一轮的输出整体都该打
    问号，不是只打这几只。两者都列出代号而不只给个数，否则回查不了。
    """
    rec = [r for r in prog.done.values() if r.get("verify")]
    bad = [r["code"] for r in rec
           if not r["verify"].get("ok", True) or not r["verify"].get("bars", True)]
    return {
        "tries": tries,
        "recomputed_count": len(rec) - len(bad),
        "unstable_count": len(bad),
        "recomputed": sorted(r["code"] for r in rec if r["code"] not in bad),
        "unstable": sorted(bad),
    }


#: 没命中的原因 → 人话。`stale` 单独一条，因为它是唯一一个
#: 「**筛条件挡住了，不是市场没有**」说得最直白的情形。
MISS_REASONS = {
    "stale": "有合格的买卖点，只是比 --within 要求的旧",
    "quasi": "最近的点全是类买卖点（quasi），被默认筛掉了——加 --quasi 看",
    "confirmed": "最近的点都落在未完成的笔/段上，被 --confirmed-only 筛掉了",
    "kind": "有买卖点，但不是 --kinds 要的那几类",
    "side": "有买卖点，但不是 --sides 要的方向",
    "none": "这只票上一个买卖点都没划出来",
}


def _missed_because(prog) -> dict[str, int]:
    """把没命中的理由汇总成一张表。

    **「0 命中」是个会骗人的数字。** 它既可能是「这批票上没有买卖点」，也可能
    是「有，但被筛条件整批挡掉了」——实测遇到过后者：391 只扫出 0 命中，其中
    97 只在 5 根K线内都有点，无一例外是 quasi 且未确认，于是默认筛条件把它们
    全滤了。只报一个 0，读的人只会得出「全市场没机会」这个错误结论。
    """
    out: Counter[str] = Counter()
    for rec in prog.done.values():
        if rec.get("status") == MISS:
            out[rec.get("miss_reason", "none")] += 1
    return dict(out.most_common())


#: 子进程整池死掉后最多重建几次。随机损坏每次只会吃掉几只，20 次足够跑完
#: 一整轮；真有一只票**每次**都能让解释器崩，那重建多少次也是白搭，到数就
#: 把剩下的记成 FAIL，让这一轮有个结果。
MAX_POOL_RESTARTS = 20


def _iterate(todo: list[str], task: dict, jobs: int, fn=None):
    """单进程就直接跑，多进程才起池子。

    `jobs=1` 不走 multiprocessing：Windows 上 spawn 要把整个解释器再起一遍，
    几十只的小扫描光启动就比算得久，而且调试时异常栈会被池子拦一道。

    ## 为什么不是 `multiprocessing.Pool`

    因为**子进程会死**。`chan.verify` 记的那种内存损坏，有一部分是直接
    segfault——那时 `_one` 的 `except` 一行都不会执行，进程没了。`Pool` 对这件
    事的反应是：把任务的结果**永远等下去**，`imap_unordered` 就那么挂住，几千只
    票的一轮扫描变成一个卡死的进程。这比崩掉还糟，崩了至少看得见。

    `ProcessPoolExecutor` 会把所有在飞的 future 置成 `BrokenProcessPool`，于是
    「谁没跑完」是可知的。拿到这个信号就重建池子、只重排没回来的那些。代价是
    把那一瞬间在飞的几只重算一遍——它们当中只有一只是真凶，但分不出来是哪只，
    而重算是幂等的，宁可多算。

    不用 `chunksize`：一次打包 8 只，一个 segfault 就带走 8 只的去向。逐只提交
    的调度开销相比一次划线可以忽略，换来的是崩溃粒度。

    `fn` 只为测试留：上面这段重建逻辑平时不执行，不给它一个能真的杀掉子进程
    的入口，它就是一段没人验过的代码——而它恰恰是出事那天才会跑的那段。
    """
    fn = fn or _one
    if jobs <= 1:
        _init(task)
        for code in todo:
            yield fn(code)
        return

    from concurrent.futures import ProcessPoolExecutor, as_completed
    from concurrent.futures.process import BrokenProcessPool

    pending = list(todo)
    for attempt in range(MAX_POOL_RESTARTS + 1):
        if not pending:
            return
        lost: list[str] = []
        with ProcessPoolExecutor(max_workers=jobs, initializer=_init,
                                 initargs=(task,)) as ex:
            futs = {ex.submit(fn, c): c for c in pending}
            # 不在这里 break：池子一坏，**所有**未完成的 future 都会立刻以
            # BrokenProcessPool 收场，所以这个循环照样会走完，顺手还能把已经
            # 算完、只是还没轮到的那些捞回来。break 的话它们要白算第二遍。
            for f in as_completed(futs):
                code = futs[f]
                try:
                    rec = f.result()
                except BrokenProcessPool:
                    lost.append(code)
                    continue
                except Exception as e:  # noqa: BLE001
                    # `_one` 自己吞掉了一切，走到这里只能是池子层面的毛病
                    # （结果 pickle 不了之类）。记成 FAIL，别停。
                    rec = {"code": code, "status": FAIL,
                           "error": f"子进程返回不了结果：{type(e).__name__}: {e}"}
                yield rec
        pending = lost

    for code in pending:
        yield {"code": code, "status": FAIL,
               "error": f"子进程反复崩溃（重建池子 {MAX_POOL_RESTARTS} 次仍未算出）"}


def collect(path: Path, *, limit: int | None = None) -> dict:
    """把 JSONL 读成和 `scan_many` 同形的摘要，交给 CLI 和扩展。"""
    prog = read_progress(Path(path))
    hits = [r for r in prog.done.values() if r.get("status") == HIT]
    failed = [{"code": r["code"], "error": r.get("error", "")}
              for r in prog.done.values() if r.get("status") == FAIL]
    hits.sort(key=lambda h: (h["point"]["ts"], h["code"]), reverse=True)
    cfg = prog.config or {}
    return {
        "level": cfg.get("level", ""),
        "zs_from": cfg.get("zs_from", ""),
        "source": cfg.get("source", ""),
        "scanned": len(prog.done),
        "hit_count": len(hits),
        "failed_count": len(failed),
        "missed_because": _missed_because(prog),
        # 从文件读回来时 `tries` 不可知（不在配置行里，见 `run_scan`），
        # 所以给 None——「没查过」和「查了 0 只有事」不能混成一个数。
        "health": _health(prog),
        "filter": {k: v for k, v in cfg.items() if k != HEADER
                   and k not in ("level", "zs_from", "source")},
        "hits": hits[:limit] if limit else hits,
        "failed": failed,
    }
