"""逐根「当下」复盘的数据，给 viewer.html 的回放模式用。

回放不限长度，按页存：一页 PAGE 根，文件名 replay_<代码>_<起点>_<PAGE>，
查看器走到一页快完时按下一根的时间要下一页。用 serve.py 的话在查看器里点
「从这根开始回放」就行，页都是现生成的，不用手跑。

不开服务、直接双击 viewer.html 的话，先在仓库根目录把页都生成好：
    PYTHONPATH=. python _plot/export_replay.py 000651 "2026-09-29 10:00" --pages 0   # 一直到最新一根
    PYTHONPATH=. python _plot/export_replay.py 000651 "2026-09-29 10:00"             # 只生成第一页
然后打开 viewer.html?code=000651&replay=replay_000651_202609291000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backtest.live import PAGE, run

sys.stdout.reconfigure(encoding="utf-8")


def name_of(code: str, ts: str, n: int) -> str:
    """文件名，serve.py 按同一个格式反解出参数：replay_<代码>_<yyyymmddHHMM>_<根数>"""
    return f"replay_{code}_{''.join(c for c in ts if c.isdigit())[:12]}_{n}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("code")
    p.add_argument("start", help="从哪根 5 分钟开始，如 '2026-09-29 10:00'")
    p.add_argument("n", nargs="?", type=int, default=PAGE, help=f"一页走多少根，默认 {PAGE}（查看器按这个要）")
    p.add_argument("--pages", type=int, default=1, help="生成几页，0 = 一直到最新一根")
    p.add_argument("--jobs", type=int, default=16)
    p.add_argument("--out-dir", default="_plot/interactive")
    a = p.parse_args()
    start, k = a.start, 0
    while start and (a.pages == 0 or k < a.pages):
        out = Path(a.out_dir) / f"{name_of(a.code, start, a.n)}.js"
        r = run(a.code, start, a.n, out, jobs=a.jobs)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        start, k = r["next"], k + 1


if __name__ == "__main__":
    main()
