"""Project registry management - shared between MCP and web clients."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECTS_DIR = Path.home() / ".manon"
PROJECTS_FILE = PROJECTS_DIR / "projects.json"


def load_projects() -> dict:
    """Load projects registry from ~/.manon/projects.json."""
    if PROJECTS_FILE.exists():
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    return {"projects": {}}


def save_projects(data: dict) -> None:
    """Save projects registry to ~/.manon/projects.json."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_project(local_path: str) -> dict | None:
    """Get project info by local path."""
    norm = str(Path(local_path).resolve()).replace("\\", "/")
    return load_projects()["projects"].get(norm)


def set_project(local_path: str, info: dict) -> None:
    """Set project info for local path."""
    norm = str(Path(local_path).resolve()).replace("\\", "/")
    data = load_projects()
    data["projects"][norm] = info
    save_projects(data)


def drop_project(local_path: str) -> bool:
    """把一条登记从注册表里摘掉。摘到了回 True。

    只有一个调用点：隔离树折回主树时，清掉它自己那条陈旧登记。留着它的后果不是
    多一行——会话钩子按**最长匹配**认仓（仓套仓时内层说了算），于是隔离树那条
    一直赢，模型继续被指向一个建完就冻住的图谱。
    """
    norm = str(Path(local_path).resolve()).replace("\\", "/")
    data = load_projects()
    if norm not in data["projects"]:
        return False
    del data["projects"][norm]
    save_projects(data)
    return True


def find_project_by_repo_id(repo_id: str) -> tuple[str, dict] | None:
    """Find project by repo_id. Returns (local_path, info) or None."""
    for path, info in load_projects()["projects"].items():
        if info.get("repo_id") == repo_id:
            return path, info
    return None


def main_worktree(local_path: str) -> str:
    """`local_path` 所属那棵 git 仓的**主工作树**；不在隔离树里就原样返回。

    隔离树是主树的一份副本。给它单独建一个仓，等于把同一份代码索引两遍——而两份
    里只有主树那一份会被 push 钩子更新（钩子脚本里写的是主树路径），另一份建完
    那一刻就冻住，之后读出来是陈旧结构，**且从读数里看不出陈旧**。实测本机 36 个
    仓里 16 个是这么来的，一次也没被更新过。

    判据用 `git worktree list --porcelain` 的第一条（git 保证主工作树排第一），
    不自己从 `--git-common-dir` 推父目录：子模块的 common dir 是
    `<super>/.git/modules/<名字>`，取父目录会得到一个根本不是工作树的路径，
    而它同样长得像一条合法路径。

    **只在 `local_path` 真落在隔离树里时改写**：`show-toplevel` 等于主树时，
    传进来的要么是主树本身、要么是它下面的子目录，而按子目录单独建仓是既有用法
    （注册表里就有两条），不该被这条顺手改掉。

    逃生口 `MANON_WORKTREE_OWN_REPO=1`：确实要给某棵树单独建图谱时用，每次留痕。
    """
    here = str(Path(local_path).resolve())
    if os.environ.get("MANON_WORKTREE_OWN_REPO"):
        print(f"MANON_WORKTREE_OWN_REPO=1: not folding {here} into its main worktree",
              file=sys.stderr)
        return here
    toplevel = _git(here, "rev-parse", "--show-toplevel")
    if not toplevel:
        return here                                   # 不是 git 仓，或没有 git
    listing = _git(here, "worktree", "list", "--porcelain")
    main = ""
    for line in listing.splitlines():
        if line.startswith("worktree "):
            main = line[len("worktree "):].strip()
            break
        if line.strip() == "bare":
            return here                               # 裸仓没有主工作树
    if not main:
        return here
    main = str(Path(main).resolve())
    return main if main != str(Path(toplevel).resolve()) else here


def _git(cwd: str, *args: str) -> str:
    """跑一条 git，失败一律回空串——**探测不到不等于要新建一个仓**。

    git 不在、路径不是仓、命令超时，三种都该走「原样返回」那条路，
    而不是把调用方推去建仓。
    """
    try:
        done = subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                              text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""
