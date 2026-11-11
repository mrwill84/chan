"""算两遍比摘要：把「安静地算错」变成「吵闹地重算」。

**这一层防的不是 bug，是硬件。** 2026-10-20 实测这台机器会在长时间 Python
热循环里损坏进程内存：同一份确定性输入，一行光秃秃的 `continue` 抛
``TypeError``、`list[Fractal]` 里装进 ``int``、`SystemError: unknown opcode 255`、
纯 Python 的两行函数里 access violation、直接 segfault。换解释器（3.13 / 3.11）、
桩掉 numpy、清 `__pycache__` 都复现，物理内存还剩 106/128 GB。20 秒的
`analyze` 循环约 75% 失败。

崩掉的那些不可怕，**不崩的才可怕**：位翻在类型指针上会炸，翻在一个 float 上
不会。盘后批量扫全 A 股正是跑几小时的那种负载，它可以安静地写出错的中枢、
错的买卖点，而且没有任何东西会报错。

所以：同一段K线算两遍，比**整个结果**的摘要。两遍一致就信，不一致就再算，
按多数取——并且把这件事记下来（`Check.runs` / `Check.digests`），因为
「今天 5400 只里有几只需要重算」本身就是机器健康度的读数。

### 它防得住什么、防不住什么

防得住：任何落在**计算过程**里的位翻——中间对象、临时数组、字节码。两遍走的
是两条独立的分配路径，翻同一个位两次的概率可以忽略。

防不住，诚实列出来：

1. **输入本身在第一次计算之前就坏了。** 两遍读同一个 `bars` 列表，一起错。
   这一条要靠 `feeds.cache` 在装载时存一份摘要、用之前复核（见 `bars_digest`）。
2. **segfault。** 进程没了，这里的代码一行都不会执行。那是调用方的事——
   批量扫描得让子进程死得掉、主进程认得出来并重排。
3. **摘要计算自己被翻。** 无解，但它会表现为「两遍不一致」，于是触发重算，
   最坏情况是多算一遍。安全方向上的失败。

### 异常也是一种结果

`fn` 抛出来不算失败，算一个**取值**，和正常返回一起参与投票。理由：内存损坏
最常见的表现形式就是抛一个不可能的异常，把它当成「这次不算数」会让多数票永远
凑不齐；而真正该抛的异常（`InvariantError`）两遍都会抛、照样是多数，原样再抛
出去，调用方感觉不到这一层。
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass
from hashlib import blake2b
from typing import Any

#: 默认最多算几遍。2 遍一致就停，所以正常情况下的代价恰好是 2 倍。
#: 3 是「允许一次翻车」——两遍不一致时再来一遍凑多数。再多没有意义：
#: 连续两次都被翻到的机器，算几遍都不该信它的结果。
MAX_TRIES = 3


def _feed(h: Any, obj: Any) -> None:
    """把对象按**类型加前缀**喂进摘要。

    前缀不能省：没有它 ``[1, 2]`` 和 ``(1, 2)``、``"1"`` 和 ``1`` 会撞到一起。
    撞车在这里不是性能问题是正确性问题——两遍结果真的不一样却算出同一个摘要，
    这一层就白加了。
    """
    if obj is None:
        h.update(b"N")
    elif obj is True or obj is False:          # bool 必须在 int 前面
        h.update(b"T" if obj else b"F")
    elif isinstance(obj, int):
        h.update(b"i")
        h.update(obj.to_bytes((obj.bit_length() + 8) // 8 + 1, "little", signed=True))
    elif isinstance(obj, float):
        # 比**位**不比值：`-0.0` 和 `0.0` 相等但位不同，而我们找的就是位翻。
        # `nan != nan` 在这里也不碍事，比的是字节。
        h.update(b"d")
        h.update(struct.pack("<d", obj))
    elif isinstance(obj, str):
        b = obj.encode("utf-8")
        h.update(b"s")
        h.update(len(b).to_bytes(8, "little"))
        h.update(b)
    elif isinstance(obj, bytes | bytearray):
        h.update(b"b")
        h.update(len(obj).to_bytes(8, "little"))
        h.update(obj)
    elif isinstance(obj, list | tuple):
        # 列表和元组分开：`[1, 2]` 和 `(1, 2)` 不是同一个结果。
        h.update(b"[" if isinstance(obj, list) else b"(")
        h.update(len(obj).to_bytes(8, "little"))
        for x in obj:
            _feed(h, x)
    elif isinstance(obj, dict):
        # 按键排序：插入顺序在正常代码里是确定的，但我们怀疑的正是「正常」。
        h.update(b"{")
        h.update(len(obj).to_bytes(8, "little"))
        for k in sorted(obj, key=repr):
            _feed(h, k)
            _feed(h, obj[k])
    elif is_dataclass(obj) and not isinstance(obj, type):
        h.update(b"@")
        h.update(type(obj).__name__.encode())
        for f in fields(obj):
            _feed(h, f.name)
            _feed(h, getattr(obj, f.name))
    elif isinstance(obj, BaseException):
        # 异常参与投票（见模块文档）。只取类型和消息：traceback 对象里有行号和
        # 帧，两次必然不同，拿它进摘要会让所有异常都互不相同、永远凑不齐多数。
        h.update(b"!")
        h.update(type(obj).__name__.encode())
        _feed(h, str(obj))
    else:
        # 枚举、Direction 这类：退回 repr。它们在本项目里都是稳定可读的。
        h.update(b"?")
        h.update(type(obj).__name__.encode())
        _feed(h, repr(obj))


def digest(obj: Any) -> str:
    """结果的规范摘要。同样的值给同样的串，不同的值给不同的串。

    16 字节够了：这里要分辨的是「两遍一不一样」，不是防伪造。
    """
    h = blake2b(digest_size=16)
    _feed(h, obj)
    return h.hexdigest()


def bars_digest(bars: list) -> str:
    """K线的摘要。装载时存一份，用之前复核——这是 `stable` 唯一的盲区。

    单独一个函数而不是直接 `digest(bars)`，是为了让调用点自己说出意图：
    「我在校验输入」和「我在比较两遍的输出」是两件事。
    """
    return digest(bars)


@dataclass(slots=True)
class Check:
    """一次自检的账。``ok=False`` 的那些就是该人工回查的。

    ``runs`` 是实际算了几遍：正常恒为 2，出现 3 就说明这台机器当时翻了一个位。
    ``digests`` 把每一遍的摘要都留着，因为「三遍三个结果」和「三遍里两个一样」
    是完全不同的严重程度，只记一个布尔读不出来。
    """

    ok: bool
    runs: int
    digests: list[str]
    label: str = ""

    @property
    def retried(self) -> bool:
        """算了不止两遍——也就是这次真的撞上了一次损坏。"""
        return self.runs > 2

    def __str__(self) -> str:
        if self.ok and not self.retried:
            return f"{self.label} 两遍一致"
        uniq = len(set(self.digests))
        tail = f"（{self.runs} 遍出了 {uniq} 个不同结果：{self.digests}）"
        return (f"{self.label} 重算后取多数{tail}" if self.ok
                else f"{self.label} **凑不齐多数，结果不可信**{tail}")


def stable(fn: Callable[[], Any], *, tries: int = MAX_TRIES,
           label: str = "") -> tuple[Any, Check]:
    """把 ``fn`` 算到两遍一致为止，返回 ``(结果, 账)``。

    ``fn`` 必须是纯的——同样的输入给同样的输出。本项目的划线层按设计就是
    （`chan` 包不碰 IO、不碰网络、不碰时间），所以这个前提不是奢望。拿它去
    包一个会写文件或读时钟的函数，这一层会把那个副作用当成损坏。

    ``tries < 2`` 直接按一遍算、``ok=True``：调用方显式说了不要自检，不该在
    这里替它改主意，但账还是要记（``runs=1``），免得汇总表把「没查过」和
    「查过没事」混成一个数。

    凑不齐多数时**返回最后一遍的结果**而不是抛异常：抛了的话一只票能把整轮
    5400 只的扫描带崩，而这一层存在的理由恰恰是别让一次位翻毁掉一整晚。
    ``ok=False`` 是给调用方的信号，由它决定是跳过这只票还是照登并标红。
    """
    if tries < 2:
        out = _once(fn)
        return _unwrap(out), Check(ok=True, runs=1, digests=[digest(out)],
                                   label=label)

    seen: dict[str, Any] = {}
    counts: dict[str, int] = {}
    digests: list[str] = []
    for n in range(1, tries + 1):
        out = _once(fn)
        d = digest(out)
        digests.append(d)
        seen.setdefault(d, out)
        counts[d] = counts.get(d, 0) + 1
        if counts[d] >= 2:
            return (_unwrap(seen[d]),
                    Check(ok=True, runs=n, digests=digests, label=label))
    # 每一遍都不一样。最后一遍不比前几遍更可信，取它只是要有个返回值。
    return (_unwrap(out),
            Check(ok=False, runs=len(digests), digests=digests, label=label))


def _once(fn: Callable[[], Any]) -> Any:
    """跑一遍。抛出来的异常当成一个取值带回去（见模块文档）。

    `KeyboardInterrupt` / `SystemExit` 例外，原样往上扔：它们不是计算结果，
    把它们丢进投票意味着用户得按两次 Ctrl-C 才停得下来——第一次被当成「一票」
    收进去，然后又跑了一遍 `fn`。
    """
    try:
        return ("ok", fn())
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:      # noqa: BLE001 —— 故意的，连 SystemError 都要接
        return ("raised", e)


def _unwrap(out: tuple[str, Any]) -> Any:
    if out[0] == "raised":
        raise out[1]
    return out[1]
