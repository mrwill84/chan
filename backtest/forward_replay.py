"""让模型给走势选项表排序，逐条可续跑。

和 `replay.py` 的分工：那边问「这个买卖点之后涨还是跌」，答案二元、而且提示词
里印着「3买」两个字，模型照标签说 up 就能拿满分。这边问**走势类型会演化成哪
一类**，选项由 `chan.forward.project` 穷举、事后由 `resolve` 机械判定，所以排序
是可打分的。

### 只问 episode 首点

`forward_walk.episodes` 把检查点按中枢身份折成 episode。同一个中枢上的几十个
检查点共享同一个结局，它们不是几十个样本，是一个；按检查点问就是 667 次调用
（一次一分钟上下 ≈ 11 小时）换 10 个独立样本。按首点问是 **10 次**。

取首点而不是末点、也不是平均，理由在 `forward_walk.summarize_projections` 的
docstring 里（Jensen：先平均概率再打分恒等于白送分）。这里只重复其中一句：
**把判断记在它第一次真能被做出的那一刻的账上**——和 C-lag 的 `first_seen`、
`score.py:55-56` 的入场价是同一条规则。

### 续跑键是检查点下标

不是 `(kind, side, bar)`——那是买卖点回放的键，这里没有买卖点。下标在前缀切片
下不变（`walk.py:36-40` 的理由同样成立），所以 ``i`` 就够了。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from chan.types import Bar
from feeds import Level

from .agent import BEICHI_MARK, ask_pi, parse_ranking, prompt_forward
from .forward_walk import episodes


@dataclass(slots=True)
class RankReplay:
    """模型对一个检查点的选项表说的话。"""

    i: int
    ts: str
    zs: str

    ok: bool = False
    probs: dict[str, float] = field(default_factory=dict)
    why: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    invented: list[str] = field(default_factory=list)
    watch: float | None = None
    note: str = ""
    error: str = ""
    raw: str = ""
    seconds: float = 0.0
    blind: bool = False


def _load_done(path: Path, *, retry_failed: bool = True) -> dict[int, RankReplay]:
    """读回已经问过的。坏行跳过而不是抛——半截 JSONL 是续跑的常态。

    ``retry_failed`` 默认开着，理由和 `replay._load_done` 一字不差：模型服务
    挂掉时一整批点会在几十秒内各记一条 `Connection error`，把这些当成「问过
    了」，续跑就会得到一份看起来完整、实际少了连续一整段的报告。
    """
    done: dict[int, RankReplay] = {}
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = RankReplay(**json.loads(line))
        except (json.JSONDecodeError, TypeError):
            continue
        if retry_failed and not r.ok:
            continue
        done[r.i] = r
    return done


def replay_rankings(
    recs: list[dict], bars: list[Bar], code: str, level: Level, *,
    out: Path, zs_from: str = "seg", model: str | None = None,
    timeout: float = 600.0, on_step=None, blind: bool = False,
) -> dict[int, dict]:
    """对每个 episode 首点问一次模型，边问边写。

    返回 ``{i: {"probs":..., "ok":..., "missing":...}}``，可直接喂给
    `forward_walk.score_rankings`。

    ``blind`` 是对照组开关（D4）：不印背驰数值，用来分辨模型是在读数还是在
    判断。**结果要写到另一个文件**，别和带数值的混在一起——这条规矩和
    `prompt_at` 的蒙住组一字不差（`agent.py:92-99`）。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    done = _load_done(out)
    firsts = [e["first"] for e in episodes(recs)]
    res: dict[int, dict] = {}

    with out.open("a", encoding="utf-8") as f:
        for n, rec in enumerate(firsts):
            i, z = rec["i"], rec["zs"]
            tag = f"{z['zd']:.2f}~{z['zg']:.2f}" if z else "—"
            if i in done:
                res[i] = _as_answer(done[i])
                if on_step:
                    on_step(n, len(firsts), done[i], True)
                continue

            r = RankReplay(i=i, ts=rec["ts"], zs=tag, blind=blind)
            t0 = time.perf_counter()
            try:
                p, codes = prompt_forward(bars, code, level, i,
                                          zs_from=zs_from, blind=blind)
                if blind:
                    # 对照组必须**确实蒙住了东西**。第 E 块之前选项表里根本
                    # 没有背驰数值，这时 blind 组和明文组一字不差，跑出来的
                    # 「对照」是同一个实验做了两遍——而两组分数相同会被读成
                    # 「模型没在读标签」，正好是结论的反面。宁可报错。
                    full, _ = prompt_forward(bars, code, level, i,
                                             zs_from=zs_from)
                    if full == p:
                        raise ValueError(
                            f"blind 组和明文组完全相同（检查点 {i}）："
                            f"选项表里没有任何 {BEICHI_MARK!r} 开头的行可蒙。"
                            f"第 E 块把背驰接进来之前，对照组没有意义。")
                k = parse_ranking(ask_pi(p, model=model, timeout=timeout),
                                  codes)
                r.ok, r.probs, r.why = k.ok, k.probs, k.why
                r.missing, r.invented = k.missing, k.invented
                r.watch, r.note, r.raw = k.watch, k.note, k.raw[:2000]
                r.error = k.error
            except Exception as e:  # noqa: BLE001 —— 一次失败不终止整轮
                r.error = f"{type(e).__name__}: {e}"
            r.seconds = time.perf_counter() - t0

            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            f.flush()  # 崩了也要留住已经花掉的那一分钟
            res[i] = _as_answer(r)
            if on_step:
                on_step(n, len(firsts), r, False)
    return res


def _as_answer(r: RankReplay) -> dict:
    return {"probs": r.probs, "ok": r.ok, "missing": r.missing,
            "invented": r.invented}
