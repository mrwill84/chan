"""把本地模型放进回测里：在**当时**那一版划线上让它预测，事后再对账。

和前面两个模块的分工：`walk.py` 量「信号什么时候看得见」，`score.py` 量
「看见之后价格怎么走」，这里量的是**模型读完引擎输出之后说的话对不对**。

### 唯一要守死的一条

喂给模型的提示词里，**不许出现 `first_seen` 之后的任何信息**。这比走查本身
更容易出错：引擎的输出是结构化的、切片一刀下去就干净了，而提示词是拼出来
的字符串，随手带进去一句「这只票后来涨到了多少」根本不会报错。

所以提示词统一由 `prompt_at()` 生成，它只接受一个 `t`，内部自己去
`analyze_one(bars[:t+1])`。调用方拿不到别的入口，想塞未来数据得先改这个
函数——那就是明着改，不是不小心漏的。

### 为什么要它给结构化答案

「这里可能有机会，但要注意风险」这种话没法对账。所以要求模型回一个 JSON：
方向、看多久、失效价位、信心。**失效价位是关键的一项**——它逼模型把话说死，
也让我们能算「它说的止损位被打穿了吗」。

### 这能证明什么

一只票、几十个点，**什么都证明不了**。它能做的是把流程跑通、把分歧暴露
出来：模型什么时候跟引擎唱反调、唱反调的时候谁对。要统计意义得上几百只票，
而那需要先有数据（见 C-tx：现在三个源都在限流）。
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field

from chan.types import Bar
from feeds import Level
from scan.report import analyze_one

#: 让模型回的东西。字段少是故意的——本地 27B 给的字段越多，胡说的越多。
SCHEMA = """{
  "direction": "up" | "down" | "unclear",   // 后市方向
  "horizon": <整数，看几根K线>,
  "invalidation": <价格，跌破/升破它就算这个判断废了>,
  "confidence": <0-1>,
  "why": "<一句话，必须引用引擎给的 reasons 里的课次页码>"
}"""

_PROMPT = """你是缠论研判助手。下面是划线引擎在**某一天收盘后**给出的结构，\
你只知道这些，不知道之后发生了什么。

{body}

请只输出一个 JSON，不要有别的话：
{schema}

注意：
- 方向说的是**这个买卖点之后**价格往哪走，不是现在是涨是跌。
- 标记为「未确认」的点可能被后面的K线抹掉，信心要相应降低。
- 「类买卖点」由盘整背驰构成，原文说它在小级别意义不大，信心要更低。
- 拿不准就给 unclear，蒙一个方向不会让你显得更有用。"""

#: 对照组。注意事项也要换掉——原来那几条讲的是「未确认」「类买卖点」这些
#: 标记，而这一组根本没有买卖点，照搬过来等于在暗示「这里有个点」。
_BLIND = """你是缠论研判助手。下面是划线引擎在**某一天收盘后**给出的结构，\
你只知道这些，不知道之后发生了什么。

{body}
（本次**不告诉你**引擎划出了什么买卖点，请只根据上面的中枢和位置自己判断。）

请只输出一个 JSON，不要有别的话：
{schema}

注意：
- `why` 里说你自己的依据就行，这一组没有给你 reasons，不用硬凑页码。
- 方向说的是**从最新这根K线往后**价格往哪走。
- 中枢的 ZG/ZD 是前两段定的，不随延伸变；GG/DD 才是全部段的极值。
- 拿不准就给 unclear，蒙一个方向不会让你显得更有用。"""


def prompt_at(bars: list[Bar], code: str, level: Level, t: int, *,
              zs_from: str = "seg", focus: tuple[int, str, int] | None = None,
              blind: bool = False) -> str:
    """生成第 ``t`` 根收盘后的提示词。**只看得到 `bars[:t+1]`。**

    ``focus`` 给 ``(kind, side, bar)`` 时，在正文里把那个点单独点出来，
    让模型判断的是它而不是随便哪个点。

    ``blind=True`` **把买卖点整段拿掉**，只留K线、走势类型、中枢和现价位置。

    为什么需要这个开关：带标签问出来的「和引擎一致率」是 100%——但提示词里
    白纸黑字写着「3买」还拿箭头指着它，模型说 up 可能只是在复述标签，不是在
    判断。不设对照组，就分不清「模型同意引擎」和「模型照抄引擎」，而这两件事
    对「该不该挂这一层」的结论完全相反。

    蒙住的做法是**整段删掉**而不是把标签改个词：`reasons` 里到处是
    「类1买」「三买」这类字样，逐条脱敏既繁琐又容易漏一处，漏了就等于没蒙。
    代价是模型也看不到背驰依据了——所以这一组测的是「它能不能独立读结构」，
    不是「它能不能复核引擎」。两组问的不是同一个问题，报告里要分开说。
    """
    if blind and focus:
        raise ValueError("blind 和 focus 不能同时给：蒙住了就没有「那一个点」")
    # 不给 `recent`：裁剪在下面自己做。`analyze_one` 的 `recent` 会把
    # `bspoints` 先砍到 5 个，那样 `focus` 找不找得到就成了碰运气。
    r = analyze_one(bars[: t + 1], code, level, zs_from=zs_from,
                    include_quasi=True, recent=None)
    if "error" in r:
        raise ValueError(r["error"])

    b = r["bars"]
    L = [f"股票 {code}，{level.value}，后复权。",
         f"K线 {b['count']} 根，{b['first_ts']} ~ {b['last_ts']}，"
         f"最新收盘 {b['last_close']}。",
         f"当前走势类型：{r['trend']}。", ""]

    if r["zhongshus"]:
        L.append("最近的中枢：")
        for z in r["zhongshus"][-3:]:
            L.append(f"  {z['start_ts']}~{z['end_ts']}  ZD {z['zd']} ~ ZG {z['zg']}"
                     f"  （GG {z['gg']} DD {z['dd']}）"
                     f"{' 已确认离开' if z.get('departed') else ''}")
        # **`departed=False` 不等于「还在中枢里」。** 18课 p40 要连续两段都不
        # 触及才算离开，最后一个中枢后面往往还凑不满两段完整的，于是价格早已
        # 掉到 ZD 下方一大截、这个标记仍然是 False。只印标记不印位置，模型会
        # 把「尚未确认离开」读成「仍在震荡」，方向就判反了。所以把现价和最后
        # 一个中枢的相对位置直接写出来，不让它去猜。
        z = r["zhongshus"][-1]
        c = b["last_close"]
        if c > z["zg"]:
            where = f"在最后一个中枢上方（高出 ZG {100 * (c / z['zg'] - 1):.1f}%）"
        elif c < z["zd"]:
            where = f"在最后一个中枢下方（低于 ZD {100 * (1 - c / z['zd']):.1f}%）"
        else:
            where = "仍在最后一个中枢区间内"
        if not z.get("departed"):
            where += "；但「离开」尚未确认（还不够两段连续不触及）"
        L += [f"现价{where}。", ""]

    if blind:
        return _BLIND.format(body="\n".join(L), schema=SCHEMA)

    # 要判断的那个点**必须出现在正文里**。它通常就是最后几个之一，但不能
    # 指望：万一它被挤出了这个窗口，`focus` 的星号就永远打不上，模型会去判
    # 另一个点，而我们拿它的答案对账 focus 的收益——错得悄无声息。
    shown = r["bspoints"][-5:]
    if focus:
        hit = [p for p in r["bspoints"]
               if (p["kind"], p["side"], p["bar"]) == focus]
        if hit and hit[0] not in shown:
            shown = [hit[0], *shown]
        elif not hit:
            raise ValueError(f"{focus} 不在第 {t} 步的划线里，focus 用错了")

    L.append("最近的买卖点：")
    for p in shown:
        flags = [] if p["confirmed"] else ["未确认"]
        if p["quasi"]:
            flags.append("类买卖点")
        if p["ambiguous"]:
            flags.append("原文有歧义")
        star = ""
        if focus and (p["kind"], p["side"], p["bar"]) == focus:
            star = "  ←← 就判断这一个"
        L.append(f"  {p['ts']}  {p['label']}  价位 {p['price']}"
                 f"  {' '.join(flags)}{star}")
        for why in (p.get("reasons") or [])[:3]:
            L.append(f"      依据：{why}")
    return _PROMPT.format(body="\n".join(L), schema=SCHEMA)


#: 推演版要的东西：给**完备选项表**排序，而不是说涨跌。
RANK_SCHEMA = """{
  "ranking": [            // 必须把下面列出的每一个 code 都排进来，一个不漏
    {"code": "<选项代号>", "p": <0-1 的概率>, "why": "<一句话>"}
  ],
  "watch": <最关键的那个观察价位>,
  "note": "<一句话总结：当前是什么走势类型，接下来最该盯什么>"
}"""

_RANK = """你是缠论研判助手。下面是划线引擎在**某一天收盘后**给出的结构，\
你只知道这些，不知道之后发生了什么。

{body}

### 接下来可能的走势（这张表是**穷尽**的）

按「走势必完美」和走势类型的三分法（17课 p36 / 24课），从当前这个中枢出发，
后续**只可能**是下面几支之一，不存在第四种。请给它们排序。

{options}

请只输出一个 JSON，不要有别的话：
{schema}

注意：
- **不要新增选项**，表是完备的；你的工作是排序和说明，不是补充。
- `p` 加起来应当接近 1。拿不准就把概率摊平，不要为了显得果断而押一个。
- 判断依据应当是中枢位置、离开确认与否、力度，不是K线形态。
- 标着「正在走」的那一支未必最终成立——它可能回试失败退回中枢。"""


#: `prompt_forward` 里属于「背驰证据」的那几行的前缀。蒙住组按它删行。
#:
#: 用前缀匹配而不是在组装时分支，是为了让**蒙不蒙得掉这件事可被检验**：
#: `blind=True` 却和明文组一字不差时，`replay_rankings` 会直接报错，而不是
#: 安静地跑出一个和实验组完全相同的「对照组」。第 E 块把背驰接进来之后，
#: 新增的行必须带上这个前缀，否则对照组立刻会喊。
BEICHI_MARK = "  力度："


def prompt_forward(bars: list[Bar], code: str, level: Level, t: int, *,
                   zs_from: str = "seg",
                   blind: bool = False) -> tuple[str, list[str]]:
    """推演版提示词：给完备选项表排序。返回 ``(提示词, 选项代号列表)``。

    和 `prompt_at` 的根本区别：那个问「这个买卖点之后涨还是跌」，答案是二元的，
    而且提示词里印着「3买」两个字，模型照着标签说 up 就能拿到 100% 的「一致
    率」——那不是判断。这个问的是**走势类型会怎么演化**，选项由程序穷举、
    事后可以机械判定走了哪一支（`chan.forward.resolve`），所以排序是可打分的。

    代号列表要返回出去：打分时必须确认模型没有自创选项、也没有漏排。

    ``zs_from`` 默认 ``"seg"``，和 `scan/report.py`、`chan/multilevel.py`、
    `tools/plot.py` 一致（C-zslevel）。**这里原先默认 `"bi"`**，整个 `backtest`
    包都是，于是模型排的是笔中枢上的选项，而人工验收的图和盘后扫描用的是段
    中枢——两边根本不是同一批中枢。600009 的 30 分钟上这个差距是 74 个 vs 10
    个，密度差 7.4 倍，排序打出来的分对不上扫描结果。plot.py 犯过同一个错，
    注释还在那里；这次一并改掉。
    """
    r = analyze_one(bars[: t + 1], code, level, zs_from=zs_from,
                    include_quasi=True, recent=None)
    if "error" in r:
        raise ValueError(r["error"])
    f = r["forward"]
    if not f["scenarios"]:
        raise ValueError(f.get("note", "没有可推演的中枢"))

    b = r["bars"]
    z = f["zhongshu"]
    L = [f"股票 {code}，{level.value}，后复权。",
         f"K线 {b['count']} 根，{b['first_ts']} ~ {b['last_ts']}，"
         f"最新收盘 {b['last_close']}。",
         f"当前走势类型：{f['trend']}（由 {f['run']} 个中枢构成）。",
         f"最后一个中枢 ZD {z['zd']:.3f} ~ ZG {z['zg']:.3f}"
         f"（GG {z['gg']:.3f} DD {z['dd']:.3f}），现价在{f['where']}。"]
    if not z["departed"]:
        L.append("注意：这个中枢的「离开」尚未确认（还不够两段连续不触及）。")

    opts = []
    for s in f["scenarios"]:
        # 「正在走」读起来像「这支最可能」。它不是——`in_progress` 只说现价
        # 在中枢的哪一侧（位置读数），实测 30 分钟级别与最终走成的走势类型
        # 吻合率 ≈ 50%（600009 38.6%/n=176，600519 52.0%/n=177）。措辞必须把
        # 「位置」和「概率」分开，否则本地模型会直接照抄成结论。
        tag = ("  ←← 现价位置落在这一支（位置读数，不是概率）" if s["in_progress"] else
               "  ←← 力度条件当下已满足" if s.get("evidence_now") else "")
        o = [f"**{s['code']}**：{s['name']}{tag}",
             f"  走完之后：{s['becomes']}",
             f"  启动条件：{s['trigger']['what']}"
             f"（{s['trigger']['op']} {s['trigger']['price']}）"]
        if s["forms"]:
            o.append(f"  过程中形成：{s['forms']}，大致在 {s['at']}")
        if s["kills"]:
            o.append(f"  证伪：{s['kills']['what']}"
                     f"（{s['kills']['op']} {s['kills']['price']}）")
        # 背驰那一行**不走 `需要：` 前缀**。它不是「还需要发生什么」，而是
        # 「现在实测是多少」——混在需要里既读着别扭，也会让 `BEICHI_MARK`
        # 匹配不上（前缀变成 `  需要：力度：`），蒙住组于是什么也删不掉而又
        # 不报错。前缀是这两组唯一的差别所在，不能靠巧合对上。
        o += [(f"{BEICHI_MARK}{n[len('力度：'):]}" if n.startswith("力度：")
               else f"  需要：{n}") for n in s["needs"]]
        if blind:
            o = [x for x in o if not x.startswith(BEICHI_MARK)]
            # 标签也要蒙。`evidence_now` 那个 tag 正是背驰结论的直接转述，
            # 删了数值却留着「力度条件当下已满足」，等于把答案留在标题行。
            o[0] = o[0].replace("  ←← 力度条件当下已满足", "")
        opts.append("\n".join(o))

    codes = [s["code"] for s in f["scenarios"]]
    return _RANK.format(body="\n".join(L), options="\n\n".join(opts),
                        schema=RANK_SCHEMA), codes


@dataclass(slots=True)
class Call:
    """模型在某一步说的话。``raw`` 留着，解析失败时唯一的线索就是它。"""

    t: int
    ok: bool
    direction: str = "unclear"
    horizon: int = 0
    invalidation: float | None = None
    confidence: float = 0.0
    why: str = ""
    raw: str = ""
    error: str = ""


_JSON = re.compile(r"\{.*\}", re.S)


def parse(raw: str) -> Call:
    """从模型输出里挖 JSON。

    本地模型十次有三次会在 JSON 外面包一层解释，或者套个 ```json 代码块。
    **这不算失败**，挖出来就行；真失败的是挖出来也不是合法 JSON，那种情况
    要如实记成 `ok=False`，不能默默当成 unclear——两者在统计里含义完全不同
    （一个是模型说不知道，一个是我们没读懂它）。
    """
    m = _JSON.search(raw)
    if not m:
        return Call(t=-1, ok=False, raw=raw, error="输出里找不到 JSON")
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return Call(t=-1, ok=False, raw=raw, error=f"JSON 解析失败：{e}")
    d2 = d if isinstance(d, dict) else {}
    direction = str(d2.get("direction", "unclear")).lower()
    if direction not in ("up", "down", "unclear"):
        direction = "unclear"
    inv = d2.get("invalidation")
    try:
        inv = float(inv) if inv is not None else None
    except (TypeError, ValueError):
        inv = None
    try:
        conf = max(0.0, min(1.0, float(d2.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    try:
        hz = int(d2.get("horizon", 0))
    except (TypeError, ValueError):
        hz = 0
    return Call(t=-1, ok=True, direction=direction, horizon=hz,
                invalidation=inv, confidence=conf,
                why=str(d2.get("why", ""))[:300], raw=raw)


#: pi 的真身。npm 的 `pi` / `pi.CMD` 都只是转发到这个文件。
_CLI_JS = "node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js"


@dataclass(slots=True)
class Ranking:
    """模型对选项表的排序。``order`` 是按概率降序的代号列表。"""

    ok: bool
    probs: dict[str, float] = field(default_factory=dict)
    why: dict[str, str] = field(default_factory=dict)
    watch: float | None = None
    note: str = ""
    missing: list[str] = field(default_factory=list)   # 漏排的
    invented: list[str] = field(default_factory=list)  # 自创的
    raw: str = ""
    error: str = ""

    @property
    def order(self) -> list[str]:
        return sorted(self.probs, key=lambda c: -self.probs[c])

    @property
    def top(self) -> str | None:
        return self.order[0] if self.probs else None


def parse_ranking(raw: str, codes: list[str]) -> Ranking:
    """解析排序，并**记下模型有没有乱来**。

    漏排和自创要单独记，不要悄悄补齐或丢弃：前者会让「它没想到这一支」被
    伪装成「它给了很低的概率」，后者会让「它根本没按表来」被当成正常作答。
    漏排的补 0——这是如实表示「它没给这一支任何概率」，不是替它填空。
    """
    m = _JSON.search(raw)
    if not m:
        return Ranking(ok=False, raw=raw, error="输出里找不到 JSON")
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return Ranking(ok=False, raw=raw, error=f"JSON 解析失败：{e}")
    if not isinstance(d, dict):
        return Ranking(ok=False, raw=raw, error="JSON 顶层不是对象")

    probs: dict[str, float] = {}
    why: dict[str, str] = {}
    invented: list[str] = []
    for item in d.get("ranking") or []:
        if not isinstance(item, dict):
            continue
        c = str(item.get("code", ""))
        try:
            p = max(0.0, min(1.0, float(item.get("p", 0))))
        except (TypeError, ValueError):
            p = 0.0
        if c not in codes:
            invented.append(c)
            continue
        probs[c] = p
        why[c] = str(item.get("why", ""))[:200]

    missing = [c for c in codes if c not in probs]
    for c in missing:
        probs[c] = 0.0
    try:
        watch = float(d.get("watch")) if d.get("watch") is not None else None
    except (TypeError, ValueError):
        watch = None
    # `ok` 要求**至少有一个非零概率**，不是 `bool(probs)`。上面刚给漏排的
    # 补了 0，所以模型一支都没排时 `probs` 也是满的（全 0）、字典非空；用
    # `bool(probs)` 判就会 ok=True，而 `top` 这时按 -0 稳定排序返回选项表里
    # 的第一个 code。那是把「模型什么都没说」伪装成「模型选了 break_down」，
    # 而且伪装得毫无痕迹——下游拿到的是一个结构完整、看着很正常的回答。
    return Ranking(ok=any(p > 0 for p in probs.values()),
                   probs=probs, why=why, watch=watch,
                   note=str(d.get("note", ""))[:300], missing=missing,
                   invented=invented, raw=raw)


def _pi_cmd() -> list[str]:
    """拼出调用 pi 的 argv，**绕开 Windows 的 .CMD 壳**。

    这里踩过一个不报错的坑，值得写下来：Windows 上 npm 装的 `pi` 是
    `pi.CMD`，`subprocess` 执行它会经过 `cmd.exe`，而 **`cmd.exe` 在换行处
    把参数截断**。于是 `pi -p "<多行提示词>"` 实际只送进去了第一行。

    这不会抛异常、退出码是 0、模型也会一本正经地回话——它只是在回答一个
    被砍掉正文的提示词。要不是本地模型老实说了「正文没跟过来」，这种错误
    会一路活到统计表里，变成「模型胡说八道」的假结论。

    所以不走壳，直接 `node cli.js`：argv 原样交给 node，换行留得住。
    """
    import shutil
    from pathlib import Path

    shim = shutil.which("pi") or shutil.which("pi.cmd")
    if not shim:
        raise RuntimeError("PATH 里找不到 pi。先 `npm i -g @earendil-works/pi`。")
    js = Path(shim).parent / _CLI_JS
    if not js.exists():
        # 装法不一样（比如直接下的二进制），那就只能用壳，但得提醒。
        return [shim]
    node = shutil.which("node")
    if not node:
        raise RuntimeError(f"找到了 {js}，但 PATH 里没有 node。")
    return [node, str(js)]


def ask_pi(prompt: str, *, model: str | None = None, timeout: float = 300.0) -> str:
    """跑一次 `pi -p`。拿不到就抛，不要返回空串冒充回答。"""
    cmd = [*_pi_cmd(), "-p", prompt]
    if model:
        cmd += ["--model", model]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"pi 退出码 {p.returncode}：{(p.stderr or '')[-400:]}")
    return p.stdout


def judge(call: Call, side: str, realized: float | None) -> str | None:
    """模型这一次说对了没有。

    回 ``"hit"`` / ``"miss"`` / ``None``（没法判：模型说不知道，或者没有未来数据）。
    **`unclear` 不算错。** 把它算成错会鼓励模型瞎猜一个方向，而瞎猜的
    50% 正确率在报告里会显得比老实说不知道更好看。
    """
    if realized is None or call.direction == "unclear" or not call.ok:
        return None
    # `realized` 存的是「对信号有利」方向的收益（`score._signed`）：买点有利
    # 是涨、卖点有利是跌。要还原成「价格到底涨没涨」，卖点这边得翻回去。
    went_up = realized > 0 if side == "buy" else realized < 0
    return "hit" if (call.direction == "up") == went_up else "miss"
