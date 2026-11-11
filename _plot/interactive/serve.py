"""看板的本地服务：静态文件 + 按需导出查看器数据。

    python _plot/interactive/serve.py            # 然后打开 http://localhost:8765/dashboard.html
    python _plot/interactive/serve.py 9000       # 换端口

看板上点一只没导出过的票，浏览器去要 `600519.js`，这里发现没有（或比缓存的
K线旧）就现跑 `_plot/export_interactive.py` 生成，再把文件给出去。直接双击
dashboard.html 也能用，只是没导出过的票打不开。

**导出放在子进程里跑**，服务本身只转发文件、不划线：这台机器上长寿命的 Python
进程算多了会坏内存（见 chan/verify.py），一只票一个短命进程是验证过的缓解办法。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from feeds import Level  # noqa: E402
from feeds.cache import path_for  # noqa: E402

CODE_JS = re.compile(r"^/(\d{6})\.js$")
# replay_<代码>_<yyyymmddHHMM>_<根数>.js，格式见 export_replay.name_of
REPLAY_JS = re.compile(r"^/replay_(\d{6})_(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)_(\d{1,4})\.js$")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


#: 生成数据的代码。比数据文件新就重新生成——改了导出格式（比如回放加了画图用的几何）之后，
#: 旧文件还在、K线缓存也没变，只看缓存的话旧文件会一直被拿去用
CODE_DEPS = [REPO / p for p in ("_plot/export_interactive.py", "_plot/export_replay.py",
                                "backtest/live.py", "scan/dashboard.py")]


def _stale(code: str, js: Path) -> bool:
    if not js.exists():
        return True
    src = [path_for(code, lv) for lv in (Level.M30, Level.M5)] + CODE_DEPS
    return any(p.exists() and p.stat().st_mtime > js.stat().st_mtime for p in src)


def _export(code: str, js: Path, args: list[str]) -> str | None:
    """``js`` 没有、或比缓存/生成代码旧，就跑 ``args`` 生成它。返回错误信息，成功返回 None。"""
    with _locks_guard:
        lock = _locks.setdefault(js.name, threading.Lock())
    with lock:  # 同一个文件同时要两次，只生成一遍
        if not _stale(code, js):
            return None
        r = subprocess.run(
            [sys.executable, *args],
            cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO), "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600,
        )
        if r.returncode != 0:
            return f"生成 {js.name} 失败（rc={r.returncode}）：{r.stderr[-800:]}"
        print(f"生成 {js.name}", file=sys.stderr)
        return None


def _route(path: str) -> tuple[str, str | None] | None:
    """要按需生成的数据文件 → (出错时写哪个全局变量, 错误信息)。不是这类文件返回 None。"""
    m = CODE_JS.match(path)
    if m:
        code = m.group(1)
        return "CHAN_ERROR", _export(code, HERE / f"{code}.js",
                                     [str(REPO / "_plot" / "export_interactive.py"), code])
    m = REPLAY_JS.match(path)
    if m:
        code, y, mo, d, h, mi, n = m.groups()
        # 回放要和查看器同一个窗口，先保证查看器数据是新的
        err = _export(code, HERE / f"{code}.js", [str(REPO / "_plot" / "export_interactive.py"), code])
        return "REPLAY_ERROR", err or _export(
            code, HERE / path.lstrip("/"),
            [str(REPO / "_plot" / "export_replay.py"), code, f"{y}-{mo}-{d} {h}:{mi}", n,
             "--jobs", str(max(1, (os.cpu_count() or 2) // 2))])
    return None


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        r = _route(self.path.split("?")[0])
        if r:
            var, err = r
            if err:
                # 查看器是用 <script> 加载的，回一段 JS 让它把错误显示出来
                body = f"window.{var} = {err!r};\n".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        super().do_GET()

    def end_headers(self):
        # 数据文件会重新生成，别让浏览器拿旧的
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(HERE)))
    print(f"http://localhost:{port}/dashboard.html", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
