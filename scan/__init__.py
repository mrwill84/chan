"""扫描层：把划线引擎的结果变成**给别人读**的东西。

`chan/` 返回的是 dataclass，适合 Python 里继续算；这一层把它压成 JSON，
给 Pi 扩展（进而给本地模型）和命令行用。

分界线：**这一层不做任何判断**。哪些点值得看、三个级别怎么合起来读，是
skill 和模型的事。这里只负责如实搬运，外加把「哪些字段不可靠」标出来。
"""

from .report import analyze_one, scan_many
from .scanner import ConfigMismatch, collect, read_progress, run_scan

__all__ = [
    "analyze_one",
    "collect",
    "ConfigMismatch",
    "read_progress",
    "run_scan",
    "scan_many",
]
