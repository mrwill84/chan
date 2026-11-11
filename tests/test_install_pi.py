"""安装脚本。测的是**同步语义**，不是「文件拷过去了没有」。

值得测的只有三件事：生成的摘录和 `CONVENTIONS.md` 对不对得上、重复安装会不会
反复改写、源里删掉的文件会不会留在目标目录。第三条是真会咬人的——Pi 把
`extensions/chan/` 下每个 `.ts` 都当扩展加载，一个改名后的旧文件留在那儿
就是两套同名工具打架，而症状会出现在完全无关的地方。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.install_pi import REPO, VERBATIM, parse_sections, render_conventions

CONV = (REPO / "CONVENTIONS.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 摘录生成
# --------------------------------------------------------------------------


def test_every_verbatim_id_exists():
    """`VERBATIM` 里写错一个编号，skill 就静悄悄少一条约定。"""
    sec = parse_sections(CONV)
    assert [k for k in VERBATIM if k not in sec] == []


def test_missing_id_is_an_error_not_a_silent_skip():
    doc = CONV.replace("### C-zd3", "### C-zd3-renamed")
    with pytest.raises(SystemExit, match="C-zd3"):
        render_conventions(doc)


def test_verbatim_sections_are_copied_whole_not_summarized():
    """摘录必须是**原样搬运**。改写一遍就等于多了一个会漂移的副本。"""
    sec = parse_sections(CONV)
    out = render_conventions(CONV)
    for k in VERBATIM:
        body = sec[k]["body"]
        # 标题降了一级（### → ####），正文一字不动
        assert "\n".join(body.splitlines()[1:]).strip() in out
        assert f"#### {k} " in out


def test_sections_follow_source_order():
    """章节标题来回跳的话，读起来像抄串行了。"""
    sec = parse_sections(CONV)
    out = render_conventions(CONV)
    pos = [out.index(f"#### {k} ") for k in sec if f"#### {k} " in out]
    assert pos == sorted(pos)


def test_everything_not_quoted_is_still_indexed():
    """没全文搬的条目也得留个线头，否则模型根本不知道有这条可查。"""
    sec = parse_sections(CONV)
    out = render_conventions(CONV)
    for k in sec:
        assert f"`{k}`" in out or f"#### {k} " in out


def test_excerpt_stays_small_enough_to_read():
    """整表 800 多行还在长。模型一次 Read 吃掉几千 token 不值得。"""
    out = render_conventions(CONV)
    assert len(out.splitlines()) < len(CONV.splitlines())


def test_generated_file_says_it_is_generated():
    """没这句话，早晚有人直接改装过去的那份，然后被下一次安装抹掉。"""
    assert "别改" in render_conventions(CONV).splitlines()[2]


# --------------------------------------------------------------------------
# 同步
# --------------------------------------------------------------------------


@pytest.fixture
def pi_home(tmp_path, monkeypatch):
    """把安装目标指到 tmp_path，别碰真的 `~/.pi`。"""
    import tools.install_pi as ip

    monkeypatch.setattr(ip, "PI_HOME", tmp_path)
    monkeypatch.setattr(ip, "EXT_DST", tmp_path / "extensions" / "chan")
    monkeypatch.setattr(ip, "SKILL_DST", tmp_path / "skills" / "chan-buysell")
    return tmp_path


def test_install_then_check_reports_no_change(pi_home, capsys):
    from tools.install_pi import install

    install(check=False)
    capsys.readouterr()
    install(check=True)
    assert "0 个文件会变动" in capsys.readouterr().out


def test_check_writes_nothing(pi_home, capsys):
    from tools.install_pi import install

    install(check=True)
    assert not list(pi_home.rglob("*.ts"))


def test_chan_home_points_at_the_repo(pi_home):
    """扩展靠这个文件找引擎。指错了症状是「python 起不来」，极其误导。"""
    from tools.install_pi import install

    install(check=False)
    home = json.loads((pi_home / "extensions" / "chan" / "chan-home.json")
                      .read_text(encoding="utf-8"))["home"]
    assert (Path(home) / "scan" / "__main__.py").exists()


def test_stale_files_are_removed(pi_home):
    """源里改名/删掉的文件不能留在目标目录——见模块开头那条。"""
    from tools.install_pi import install

    install(check=False)
    stale = pi_home / "extensions" / "chan" / "index.old.ts"
    stale.write_text("export default function () {}", encoding="utf-8")
    install(check=False)
    assert not stale.exists()


def test_generated_files_survive_the_cleanup(pi_home):
    """`chan-home.json` 和 `conventions.md` 不在源目录里，别被当成残留删掉。"""
    from tools.install_pi import install

    install(check=False)
    install(check=False)
    assert (pi_home / "extensions" / "chan" / "chan-home.json").exists()
    assert (pi_home / "skills" / "chan-buysell" / "references"
            / "conventions.md").exists()


def test_skill_frontmatter_survives_the_copy(pi_home):
    """Pi 靠 frontmatter 的 name/description 决定要不要加载这个 skill。"""
    from tools.install_pi import install

    install(check=False)
    text = (pi_home / "skills" / "chan-buysell" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert "name: chan-buysell" in head and "description:" in head


def test_skill_promises_only_files_that_exist(pi_home):
    """SKILL.md 里写了 `references/xxx.md` 却没这个文件，模型会去 Read 然后撞墙。"""
    import re

    from tools.install_pi import install

    install(check=False)
    root = pi_home / "skills" / "chan-buysell"
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    refs = set(re.findall(r"`(references/[\w.-]+)`", text))
    assert refs, "SKILL.md 一个 references/ 都没提到，这条测试失效了"
    assert [r for r in refs if not (root / r).exists()] == []
