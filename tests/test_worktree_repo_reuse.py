"""隔离树不单独建仓 —— 真建 git 仓、真开隔离树，跑真的解析函数。

判据不用 mock：这条判据的全部内容就是「git 说这棵树属于谁」，把 git 换成
假的等于把判据换成假的。本机 36 个仓里 16 个是隔离树副本，正是这条不在场
的产物；而它们从读数里看不出来——`repos_list` 里每一个都写着 `done`。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.ast import drop_project, main_worktree
from manon_mcp.tools.init import initialize_project


#: 夹具要在 pytest 的 tmp_path 下真开一棵隔离树，而本机的分支漂移守卫（T3）
#: 恰恰拦「建树落易失目录」——macOS 的 $TMPDIR 解析出来就是 /private/var/folders/…。
#: 那条守卫是对的，这里用它自己的逃生口显式绕开：**要绕的是夹具，不是判据**，
#: 且 CI 上（干净克隆、没装全局钩子）本来就没有这一层。
_FIXTURE_ENV = {**os.environ, "GIT_DRIFT_ALLOW": "1"}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True, env=_FIXTURE_ENV)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """一棵主树 + 一棵隔离树 + 主树下的一个子目录。"""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "commit", "-q", "--allow-empty", "-m", "base")
    _git(root, "worktree", "add", "-q", str(root / ".worktrees/iso"), "-b", "iso")
    (root / "backend").mkdir()
    return root


@pytest.fixture()
def main_path(repo: Path) -> str:
    """主树的绝对路径。**在同步夹具里算好**：async 用例里调 `Path.resolve()`
    会阻塞事件循环（ruff ASYNC240），而这里算一次两边都能用。"""
    return str(repo.resolve())


@pytest.fixture()
def iso_path(repo: Path) -> str:
    return str((repo / ".worktrees/iso").resolve())


def _must_not_create(*_args, **_kwargs):
    """建仓那一支。隔离树走到这里就是缺陷复发，所以它只会抛。"""
    raise AssertionError("隔离树不该走建仓那一支")


def _fake_existing(path: str, _proj: dict, progress_cb=None):
    """接既有登记那一支。**签名要跟真的一样**（真调用方按关键字传 `progress_cb`），
    不然这条用例测的是「参数名对不对」，不是「路径落在哪棵树上」。"""
    if progress_cb:
        progress_cb(50, "linking")
    return "main1234", [f"  repo ({path})"], ["  indexed"]


def test_隔离树解析回主工作树(repo: Path) -> None:
    assert main_worktree(str(repo / ".worktrees/iso")) == str(repo.resolve())


def test_隔离树的子目录也解析回主工作树(repo: Path) -> None:
    sub = repo / ".worktrees/iso/pkg"
    sub.mkdir()
    assert main_worktree(str(sub)) == str(repo.resolve())


def test_主树和它的子目录原样返回(repo: Path) -> None:
    """**按子目录单独建仓是既有用法**（注册表里就有两条：一个仓和它的 backend/）。

    这条判据只治隔离树，顺手把子目录也折叠掉的话，是拿一个缺陷换另一个。
    """
    assert main_worktree(str(repo)) == str(repo.resolve())
    assert main_worktree(str(repo / "backend")) == str((repo / "backend").resolve())


def test_不是_git_仓就原样返回(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert main_worktree(str(plain)) == str(plain.resolve())


def test_逃生口留痕且不折叠(repo: Path, monkeypatch, capsys) -> None:
    """确实要给某棵树单独建图谱时的出路。**每次留痕**，别变成常态。"""
    monkeypatch.setenv("MANON_WORKTREE_OWN_REPO", "1")
    iso = repo / ".worktrees/iso"
    assert main_worktree(str(iso)) == str(iso.resolve())
    assert "MANON_WORKTREE_OWN_REPO" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_init_在隔离树里不建新仓_而是接主树那一个(
        main_path: str, iso_path: str, monkeypatch) -> None:
    """整条路径的判据：从隔离树调 `manon_init`，落到主树的 repo_id 上。

    此前它按路径认仓，隔离树是新路径，于是走建仓那一支——一次也不报错。
    路径都由同步夹具算好：async 用例里调 `Path.resolve()` 会阻塞事件循环。
    """
    seen: list[str] = []

    def _get_project(path: str) -> dict | None:
        seen.append(path)
        if path == main_path:
            return {"repo_id": "main1234", "name": "repo", "last_sync": "", "file_hashes": {}}
        return None

    monkeypatch.setattr("manon_mcp.tools.init.get_project", _get_project)
    monkeypatch.setattr("manon_mcp.tools.init.needs_smart_analysis_refresh",
                        lambda _path, _proj: False)

    out = await initialize_project(
        project_path=iso_path,
        project_name="",
        ctx=None,
        client=SimpleNamespace(_get_no_auth=lambda _path: {"ok": True}),
        config=SimpleNamespace(API_URL="http://localhost:3700",
                               _get_client_version=lambda: "0.0.0"),
        read_update_status=lambda: None,
        init_existing_project=_fake_existing,
        init_match_or_create=_must_not_create,
        build_hooks_lines=lambda path: [f"  hooks -> {path}"],
    )

    assert seen and seen[0] == main_path, "查注册表用的还是隔离树那条路径"
    # 接主树那条既有登记走的是 init_existing_project，且拿到的是主树路径。
    assert f"  repo ({main_path})" in out
    # 钩子也装在主树上：隔离树与主树共用 $GIT_COMMON_DIR/hooks，装在隔离树上
    # 等于把落点写成一个随时会被拆掉的目录。
    assert f"  hooks -> {main_path}" in out
    # 复用主树图谱这件事必须说出来，否则模型会以为图谱覆盖了本树的改动。
    assert "隔离树" in out
    assert "不含本树未合并的改动" in out


@pytest.mark.asyncio
async def test_主树自己调_init_一个字都不多说(main_path: str, monkeypatch) -> None:
    """没折叠就不该出现那三行——一句每次都在的提醒，等于没有提醒。"""
    monkeypatch.setattr(
        "manon_mcp.tools.init.get_project",
        lambda _path: {"repo_id": "main1234", "name": "repo",
                       "last_sync": "", "file_hashes": {}})
    monkeypatch.setattr("manon_mcp.tools.init.needs_smart_analysis_refresh",
                        lambda _path, _proj: False)

    out = await initialize_project(
        project_path=main_path,
        project_name="",
        ctx=None,
        client=SimpleNamespace(_get_no_auth=lambda _path: {"ok": True}),
        config=SimpleNamespace(API_URL="http://localhost:3700",
                               _get_client_version=lambda: "0.0.0"),
        read_update_status=lambda: None,
        init_existing_project=_fake_existing,
        init_match_or_create=_must_not_create,
        build_hooks_lines=lambda _path: ["  hooks"],
    )
    assert "隔离树" not in out


def test_环境里没有这个逃生口时不留痕(repo: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("MANON_WORKTREE_OWN_REPO", raising=False)
    main_worktree(str(repo / ".worktrees/iso"))
    assert capsys.readouterr().err == ""


def test_并发树各自解析互不影响(repo: Path) -> None:
    """两棵隔离树都指向同一个主树——这正是「16 个仓本该是 1 个」那件事。"""
    _git(repo, "worktree", "add", "-q", str(repo / ".worktrees/iso2"), "-b", "iso2")
    a = main_worktree(str(repo / ".worktrees/iso"))
    b = main_worktree(str(repo / ".worktrees/iso2"))
    assert a == b == str(repo.resolve())


def test_逃生口空串不算开启(repo: Path, monkeypatch) -> None:
    """`MANON_WORKTREE_OWN_REPO=` 是「没开」，不是「开了」——空串在 shell 里
    是取消一个变量最常见的写法。"""
    monkeypatch.setenv("MANON_WORKTREE_OWN_REPO", "")
    assert main_worktree(str(repo / ".worktrees/iso")) == str(repo.resolve())


def test_git_不在时原样返回(repo: Path, monkeypatch) -> None:
    """探测不到不等于要新建一个仓：把 PATH 清空，行为必须是「原样返回」。"""
    monkeypatch.setenv("PATH", str(Path(os.devnull).parent))
    assert main_worktree(str(repo / ".worktrees/iso")) == str((repo / ".worktrees/iso").resolve())


@pytest.mark.asyncio
async def test_折回主树时摘掉隔离树那条陈旧登记(
        main_path: str, iso_path: str, monkeypatch) -> None:
    """**本机已经攒下 16 条这样的登记**（隔离树各建了一个仓）。

    留着它们，fold 之后钩子照样按最长匹配认到隔离树那条，模型继续被指向一个
    建完就冻住的图谱——修了 init 却不清登记，等于这条缺陷只对新树生效。
    """
    dropped: list[str] = []

    def _drop(path: str) -> bool:
        dropped.append(path)
        return True

    monkeypatch.setattr("manon_mcp.tools.init.drop_project", _drop)
    monkeypatch.setattr(
        "manon_mcp.tools.init.get_project",
        lambda _path: {"repo_id": "main1234", "name": "repo",
                       "last_sync": "", "file_hashes": {}})
    monkeypatch.setattr("manon_mcp.tools.init.needs_smart_analysis_refresh",
                        lambda _path, _proj: False)

    out = await initialize_project(
        project_path=iso_path,
        project_name="",
        ctx=None,
        client=SimpleNamespace(_get_no_auth=lambda _path: {"ok": True}),
        config=SimpleNamespace(API_URL="http://localhost:3700",
                               _get_client_version=lambda: "0.0.0"),
        read_update_status=lambda: None,
        init_existing_project=_fake_existing,
        init_match_or_create=_must_not_create,
        build_hooks_lines=lambda _path: ["  hooks"],
    )
    assert dropped == [iso_path], "摘的必须是隔离树那条，不是主树那条"
    assert "已摘掉本树那条陈旧登记" in out
    assert main_path in out


def test_drop_project_只动指名那一条(repo: Path, tmp_path: Path, monkeypatch) -> None:
    """真读真写一份注册表：摘掉一条，另一条必须原样在场。"""
    reg = tmp_path / "projects.json"
    monkeypatch.setattr("core.ast.project.PROJECTS_FILE", reg)
    monkeypatch.setattr("core.ast.project.PROJECTS_DIR", tmp_path)
    iso = str((repo / ".worktrees/iso").resolve())
    reg.write_text(json.dumps({"projects": {
        str(repo.resolve()): {"repo_id": "main1234"},
        iso: {"repo_id": "stale999"},
    }}), encoding="utf-8")

    assert drop_project(iso) is True
    left = json.loads(reg.read_text(encoding="utf-8"))["projects"]
    assert list(left) == [str(repo.resolve())]
    assert drop_project(iso) is False, "摘一条不在场的登记不该报成摘到了"
