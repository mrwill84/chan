"""让模型把这只票的历史重判一遍，然后和引擎、和基准同时对账。

`walk.py` 回答「信号什么时候看得见」，`score.py` 回答「看见之后价格怎么走」。
这里回答第三个问题：**模型读完当时那一版划线之后说的话，比引擎本身更准吗。**

### 三个对账口径，缺一不可

1. **对引擎**：模型和引擎同向还是反向。全同向说明模型只是在复述标签，这层
   就没有信息；反向的那些才是它真正在做判断的地方。
2. **对基准**：模型说涨、之后真涨的比例，要减去同区间任取一天的上涨比例。
   不减基准，牛市里一个永远说「涨」的模型能拿 60% 的「准确率」。
3. **对自己的止损**：模型给了 `invalidation`，就得查这个价位在持有期内被
   打穿过没有。说对方向但中途先被打穿，实盘是拿不到那笔收益的。

### 为什么写成可续跑

一次调用 60 秒上下，四十几个点就是三刻钟，中间任何一次超时都不该让前面的
白跑。所以逐条 append JSONL，重跑时按 ``(kind, side, bar)`` 跳过已有的。
**不缓存在内存里、不最后一次性写**——那样崩一次就全没了。
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from chan.types import Bar
from feeds import Level

from .agent import Call, ask_pi, judge, parse, prompt_at
from .score import HORIZONS, Outcome, base_rate


@dataclass(slots=True)
class Replay:
    """模型对一个买卖点说的话 + 事后对账的结果。"""

    kind: int
    side: str
    label: str
    bar: int
    ts: str
    first_seen: int
    first_seen_ts: str

    ok: bool = False
    direction: str = "unclear"
    horizon: int = 0
    invalidation: float | None = None
    confidence: float = 0.0
    why: str = ""
    error: str = ""
    raw: str = ""
    seconds: float = 0.0

    #: 模型方向和这个点的性质是否一致（买点说涨 / 卖点说跌 为一致）
    agrees: bool | None = None
    #: 持有期 -> "hit"/"miss"；模型说 unclear 或没有未来数据时这里没有条目
    verdict: dict[str, str] = field(default_factory=dict)
    #: 模型自己给的失效价，在最长持有期内被打穿过没有
    breached: bool | None = None

    @property
    def key(self) -> tuple[int, str, int]:
        return (self.kind, self.side, self.bar)


def _breached(inv: float | None, side: str, bars: list[Bar],
              start: int, horizon: int) -> bool | None:
    """失效价在 ``(start, start+horizon]`` 之间被打穿过没有。

    方向跟着信号走：买点的失效价在下方（跌破算打穿），卖点在上方。模型偶尔
    会把这个给反（买点给一个高于现价的"失效价"），那种情况下这个数会恒为
    True——**不要在这里纠正它**，那是模型答得不好，不是我们算得不对。
    """
    if inv is None:
        return None
    end = min(start + horizon, len(bars) - 1)
    if end <= start:
        return None
    window = bars[start + 1: end + 1]
    return (min(b.low for b in window) <= inv if side == "buy"
            else max(b.high for b in window) >= inv)


def _load_done(path: Path, *, retry_failed: bool = True,
               ) -> dict[tuple[int, str, int], Replay]:
    """读回已经问过的。坏行跳过而不是抛——半截的 JSONL 是续跑的常态。

    ``retry_failed`` 默认开着，**这不是可选的礼貌**：模型服务挂掉的时候，
    一整批点会在几十秒内各自记下一条 `Connection error`。要是把这些也当成
    「问过了」，续跑就会把它们全部跳过，最后得到一份看起来完整、实际上少了
    半数样本的报告——而且少掉的那半数是连续的一段，偏得最厉害。

    失败的行是**直接当没有**（而不是先记下再被覆盖）：这样无论文件里是
    「先失败后成功」还是「先成功后失败」，留下的都是那条成功的。
    """
    done: dict[tuple[int, str, int], Replay] = {}
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = Replay(**json.loads(line))
        except (json.JSONDecodeError, TypeError):
            continue
        if retry_failed and not r.ok:
            continue
        done[r.key] = r
    return done


def replay(
    outcomes: list[Outcome], bars: list[Bar], code: str, level: Level, *,
    out: Path, zs_from: str = "seg", model: str | None = None,
    horizons=HORIZONS, timeout: float = 600.0, on_step=None,
    blind: bool = False,
) -> list[Replay]:
    """对每个买卖点问一次模型，边问边写。

    ``outcomes`` 用 `score.evaluate` 的结果：那里已经把「有利方向的收益」
    算好了，`judge` 要的就是它。没有未来数据的点**照样问**——它们的答案
    进不了命中率，但那正是最新的信号，是实盘真要用的那几个。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    done = _load_done(out)
    hmax = max(horizons)
    res: list[Replay] = []

    with out.open("a", encoding="utf-8") as f:
        for i, o in enumerate(outcomes):
            s = o.sighting
            key = (s.kind, s.side, s.bar)
            if key in done:
                res.append(done[key])
                if on_step:
                    on_step(i, len(outcomes), done[key], True)
                continue

            r = Replay(kind=s.kind, side=s.side, label=s.label, bar=s.bar,
                       ts=s.ts, first_seen=s.first_seen,
                       first_seen_ts=bars[s.first_seen].ts)
            t0 = time.perf_counter()
            try:
                p = prompt_at(bars, code, level, s.first_seen, zs_from=zs_from,
                              focus=None if blind else key, blind=blind)
                c = parse(ask_pi(p, model=model, timeout=timeout))
            except Exception as e:  # noqa: BLE001 —— 一次失败不该终止整轮
                c = Call(t=s.first_seen, ok=False, error=f"{type(e).__name__}: {e}")
            r.seconds = time.perf_counter() - t0
            r.ok, r.direction, r.horizon = c.ok, c.direction, c.horizon
            r.invalidation, r.confidence = c.invalidation, c.confidence
            r.why, r.error, r.raw = c.why, c.error, c.raw[:2000]

            if c.ok and c.direction != "unclear":
                # 「一致」= 模型的方向和这个点的多空性质同向。
                r.agrees = (c.direction == "up") == (s.side == "buy")
                for h in horizons:
                    v = judge(c, s.side, o.rets.get(h))
                    if v:
                        r.verdict[str(h)] = v
                r.breached = _breached(c.invalidation, s.side, bars,
                                       s.first_seen, hmax)

            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            f.flush()  # 崩了也要留住已经花掉的那一分钟
            res.append(r)
            if on_step:
                on_step(i, len(outcomes), r, False)
    return res


def summarize_replay(
    replays: list[Replay], outcomes: list[Outcome], bars: list[Bar], *,
    horizons=HORIZONS,
) -> dict:
    """三个口径一起报。

    最有用的一项是最后那个 **分流对比**：把引擎的信号按「模型同不同意」
    劈成两半，分别看收益。如果两半没差别，模型这一层就是纯成本——每个点
    一分钟换来零信息。这是唯一能回答「值不值得挂个 LLM」的数。
    """
    by_key = {(o.sighting.kind, o.sighting.side, o.sighting.bar): o
              for o in outcomes}
    spoke = [r for r in replays if r.ok and r.direction != "unclear"]

    res: dict = {
        "n": len(replays),
        "parsed": sum(r.ok for r in replays),
        "unclear": sum(r.ok and r.direction == "unclear" for r in replays),
        "failed": sum(not r.ok for r in replays),
        "mean_seconds": (statistics.fmean(r.seconds for r in replays)
                         if replays else 0.0),
        "mean_confidence": (statistics.fmean(r.confidence for r in spoke)
                            if spoke else 0.0),
        "agree_with_engine": (statistics.fmean(float(r.agrees) for r in spoke)
                              if spoke else None),
        "gave_invalidation": sum(r.invalidation is not None for r in spoke),
        "breached": sum(bool(r.breached) for r in spoke),
        "h": {},
        "split": {},
    }

    for h in horizons:
        hs = str(h)
        v = [r.verdict[hs] for r in spoke if hs in r.verdict]
        if not v:
            continue
        hit = sum(x == "hit" for x in v) / len(v)
        # 模型说的是「价格涨不涨」，所以基准要拿**它自己说的方向**那一侧。
        # 全部按做多基准比会系统性偏袒看空的回答（或反之）。
        up = [r for r in spoke if hs in r.verdict and r.direction == "up"]
        bw_up, _ = base_rate(bars, h, "buy")
        bw_dn, _ = base_rate(bars, h, "sell")
        mix = ((len(up) * bw_up + (len(v) - len(up)) * bw_dn) / len(v))
        res["h"][hs] = {"n": len(v), "hit_rate": hit, "base": mix,
                        "edge": hit - mix, "n_up": len(up)}

    for h in horizons:
        hs = str(h)
        rows = {}
        for name, keep in (("同意", True), ("反对", False)):
            rs = [by_key[r.key].rets.get(h) for r in spoke if r.agrees is keep]
            rs = [x for x in rs if x is not None]
            if not rs:
                continue
            rows[name] = {"n": len(rs),
                          "win_rate": sum(x > 0 for x in rs) / len(rs),
                          "mean_ret": statistics.fmean(rs)}
        if rows:
            res["split"][hs] = rows
    return res


def render_replay(s: dict) -> str:
    L = [f"模型复判：问了 {s['n']} 个点，"
         f"解析成功 {s['parsed']}，其中说不知道 {s['unclear']}，"
         f"失败 {s['failed']}；平均 {s['mean_seconds']:.0f} 秒/个。"]
    if s["agree_with_engine"] is None:
        return "\n".join(L + ["", "没有一个可用的方向判断，下面无从谈起。"])

    L += [f"给了方向的那些：平均信心 {s['mean_confidence']:.2f}，"
          f"和引擎同向 {100 * s['agree_with_engine']:.0f}%。",
          f"给了失效价 {s['gave_invalidation']} 个，"
          f"持有期内被打穿 {s['breached']} 个。", ""]

    L.append("方向准确率（对的是「价格涨没涨」，基准按它自己说的方向加权）：")
    for h, r in s["h"].items():
        L.append(f"  {h:>3} 根：{r['n']:>3} 个，命中 {100 * r['hit_rate']:.1f}%，"
                 f"基准 {100 * r['base']:.1f}%，"
                 f"edge {100 * r['edge']:+.1f}%  （其中看涨 {r['n_up']} 个）")

    L += ["", "分流：同一批引擎信号，按模型同不同意劈开后的真实收益——",
          "（两边差不多的话，这层模型就是白花的时间）"]
    for h, rows in s["split"].items():
        parts = [f"{k} n={v['n']} 胜率 {100 * v['win_rate']:.0f}% "
                 f"均值 {100 * v['mean_ret']:+.2f}%" for k, v in rows.items()]
        L.append(f"  {h:>3} 根： " + "   |   ".join(parts))
    return "\n".join(L)
