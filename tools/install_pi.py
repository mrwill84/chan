"""把 `pi/` 下的扩展和 skill 装到 `~/.pi/agent/`。

**单向同步：仓库 → Pi。** 装过去的那份是副本，改它没有意义，下次装就被覆盖。
要改就改仓库里的，然后重跑这个脚本。

顺带做一件不能手工做的事：`pi/skills/chan-buysell/references/conventions.md`
是从仓库根的 `CONVENTIONS.md` **生成**的，不是手写的。那张表 800 多行、还在长，
手抄一份进 skill 迟早和本体对不上——而模型读到一条过期的约定，比读不到更坏。

用法::

    python -m tools.install_pi            # 装
    python -m tools.install_pi --check    # 只看会动哪些文件，不写
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PI_HOME = Path.home() / ".pi" / "agent"

EXT_SRC = REPO / "pi" / "extensions" / "chan"
EXT_DST = PI_HOME / "extensions" / "chan"
SKILL_SRC = REPO / "pi" / "skills" / "chan-buysell"
SKILL_DST = PI_HOME / "skills" / "chan-buysell"

# --------------------------------------------------------------------------
# 从 CONVENTIONS.md 里挑
# --------------------------------------------------------------------------

# 全文照搬进 skill 的条目。挑的标准只有一条：**模型读引擎输出时用得上**。
#
# 划线内部怎么实现（C01 起手方向、C09 线段标准、C-ema 初值…）模型管不着，
# 它既不划线也改不了这些；塞进去只是稀释注意力。留在索引里，需要时去仓库读。
VERBATIM = [
    "C-zs",       # ZG/ZD 与 GG/DD 的分工——模型最容易混的一对
    "C-gg",
    "C-leave",
    "C-ext",      # 9 段上限 → level_up 警告的来源
    "C-amb",      # 走势多义性，正是要模型来选的地方
    "C13",        # 背驰口径三选一，决定了 reasons 里那些面积数字怎么读
    "C-quasi",    # quasi 标记
    "C17",        # 二买两套口径 → source 字段
    "C-zd3",      # 三卖比 ZD
    "C15",        # ambiguous 标记
    "C18",        # ambiguous 的第二个来源：4 段的三类点
    "C16",
    "C-bs",       # 位置约束与重合关系
    "C-macdlaw",  # macd_law_ok 是标注不是门槛
    "C-prov",     # confirmed=false
    "C-zero",     # 0 命中不等于没有买卖点——最容易被说反的一条
    "C-lag",      # 点的日期不是它能被看见的日期，3买 平均差 21 根
    "C-depart",   # departed=False 不等于还在中枢里
    "C02",        # 跳空，用户一定会问
    "C03",        # 新笔非原文
    "C-adj",      # 后复权——价位数字对不对得上全靠它
    "C-min",      # 分钟线历史不够长
    "C-src",      # 两个源的后复权对不上，价位数字跨源不可比
    "C-tx",       # 应急源只有 640 根日线——「没数据」不等于「没信号」
    "C-nest",     # 区间套收缩的是答案不是输入
    "C-nestdir",  # 方向冲突以大级别为准
    "C-nestbreak",  # coverage 三态：没数据 ≠ 没买卖点
]

_HEAD = re.compile(r"^### (C[-\w]*\d*[a-z]?|C-\w+)\s+(.*)$")


def parse_sections(md: str) -> dict[str, dict]:
    """切出 `### Cxx 标题` 为单位的小节。

    返回 ``{id: {"title", "body", "chapter"}}``，`body` 含标题行本身，
    这样贴进生成文件里不用再拼。
    """
    out: dict[str, dict] = {}
    chapter = ""
    cur: dict | None = None
    for line in md.splitlines():
        if line.startswith("## "):
            chapter = line[3:].strip()
            cur = None
        m = _HEAD.match(line)
        if m:
            cur = {"title": m.group(2).strip(), "chapter": chapter, "lines": [line]}
            out[m.group(1)] = cur
            continue
        # `#### ` 是小节内的子标题，不另起一节；`## ` 和 `---` 才是边界
        if cur is not None:
            if line.startswith("## ") or line.strip() == "---":
                cur = None
            else:
                cur["lines"].append(line)
    for s in out.values():
        s["body"] = "\n".join(s["lines"]).rstrip()
    return out


_PREAMBLE = """# 自定规则登记表（摘录）

**这个文件是生成的，别改。** 源头是 chan 仓库根目录的 `CONVENTIONS.md`，
改那边然后重跑 `python -m tools.install_pi`。

`rules.md` 放的是原文怎么说；这里放的是**原文没说、由实现自己定**的那部分。
用户问「为什么这里判成三买」而答案不在原文里时，多半就在下面某一条。

下半部分是完整索引——仓库里还有这些条目，需要时去 `CONVENTIONS.md` 读全文。

---

## 和读输出直接相关的条目（全文）
"""


def render_conventions(md: str) -> str:
    """生成 skill 要读的那份摘录。

    只搬 `VERBATIM` 里那些，其余压成一行索引。整份搬过来有 800 多行，模型
    一次 Read 就吃掉几千 token，其中九成是它根本用不上的实现细节。
    """
    sec = parse_sections(md)
    miss = [k for k in VERBATIM if k not in sec]
    if miss:
        # 条目被改名或删掉了。静默跳过的话 skill 会少掉一条约定而没人发现，
        # 所以直接报错——这是仓库自己的不一致，装之前就该修。
        raise SystemExit(f"CONVENTIONS.md 里找不到这些条目：{miss}（改名了？）")

    parts = [_PREAMBLE]
    chapter = ""
    # 按**原文件顺序**排，不按 `VERBATIM` 的书写顺序。否则章节标题会来回跳
    # （…二、笔 → 一、K线 → 二、笔…），读起来像抄串行了。
    order = {k: i for i, k in enumerate(sec)}
    for k in sorted(VERBATIM, key=lambda k: order[k]):
        s = sec[k]
        if s["chapter"] != chapter:
            chapter = s["chapter"]
            parts.append(f"\n### 〔{chapter}〕\n")
        # 降一级：原文里是 `###`，这里挂在 `##` 摘录节下面
        parts.append("#" + s["body"] + "\n")

    parts.append("\n---\n\n## 其余条目索引\n")
    parts.append("需要时在仓库 `CONVENTIONS.md` 里按编号搜。\n")
    chapter = ""
    for k, s in sec.items():
        if k in VERBATIM:
            continue
        if s["chapter"] != chapter:
            chapter = s["chapter"]
            parts.append(f"\n**{chapter}**\n")
        parts.append(f"- `{k}` {s['title']}")
    return "\n".join(parts).rstrip() + "\n"


# --------------------------------------------------------------------------
# 安装
# --------------------------------------------------------------------------


def _write(path: Path, text: str, *, check: bool, log: list[str]) -> None:
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == text:
        log.append(f"  = {path}")
        return
    log.append(f"  {'+' if old is None else '~'} {path}")
    if not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 统一 \n：Pi 那边 jiti 和 Markdown 都不在乎，但两次安装之间
        # 换行符一变，上面那个 `old == text` 的比较就永远不相等了。
        path.write_text(text, encoding="utf-8", newline="\n")


def _sync_dir(src: Path, dst: Path, *, check: bool, log: list[str],
              skip: set[Path] = frozenset()) -> None:
    """把 `src` 下的文件逐个铺到 `dst`，顺带删掉目标里多出来的。

    不用 `shutil.copytree(dirs_exist_ok=True)`：那样删不掉改名后的残留文件，
    而 Pi 会把 `extensions/chan/` 下**每个** `.ts` 都当扩展加载，一个旧版本
    留在那儿就是两套同名工具打架。
    """
    want: set[Path] = set()
    for f in sorted(src.rglob("*")):
        if f.is_dir() or "__pycache__" in f.parts:
            continue
        rel = f.relative_to(src)
        want.add(rel)
        _write(dst / rel, f.read_text(encoding="utf-8"), check=check, log=log)
    if not dst.exists():
        return
    for f in sorted(dst.rglob("*")):
        rel = f.relative_to(dst)
        if f.is_dir() or rel in want or rel in skip:
            continue
        log.append(f"  - {f}  （源里已没有，删掉）")
        if not check:
            f.unlink()


def install(*, check: bool) -> int:
    log: list[str] = []

    log.append(f"扩展  {EXT_SRC} → {EXT_DST}")
    # `chan-home.json` 是装出来的、不在源目录里，别让清理步骤把它当残留删掉
    _sync_dir(EXT_SRC, EXT_DST, check=check, log=log,
              skip={Path("chan-home.json")})
    # 扩展靠这个找引擎。写成文件而不是写死在 index.ts 里，仓库挪了位置
    # 重装一次就好，源码不用动。
    _write(EXT_DST / "chan-home.json",
           json.dumps({"home": str(REPO)}, ensure_ascii=False, indent=2) + "\n",
           check=check, log=log)

    log.append(f"skill  {SKILL_SRC} → {SKILL_DST}")
    _sync_dir(SKILL_SRC, SKILL_DST, check=check, log=log,
              skip={Path("references/conventions.md")})
    _write(SKILL_DST / "references" / "conventions.md",
           render_conventions((REPO / "CONVENTIONS.md").read_text(encoding="utf-8")),
           check=check, log=log)

    print("\n".join(log))
    changed = sum(1 for line in log if line.startswith(("  +", "  ~", "  -")))
    if check:
        print(f"\n--check：{changed} 个文件会变动，没有实际写入。")
    else:
        print(f"\n装好了，{changed} 个文件变动。重启 pi 生效。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 pi/ 下的扩展和 skill 装到 ~/.pi/agent/")
    ap.add_argument("--check", action="store_true", help="只报告会动哪些文件，不写")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    return install(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
