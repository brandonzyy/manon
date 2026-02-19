"""Git service — clone/pull repos into repos_dir/{project_id}/."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("manon.git")


async def _run(cmd: str, cwd: str | None = None) -> str:
    proc = await asyncio.create_subprocess_shell(
        cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git command failed ({proc.returncode}): {stderr.decode()[:500]}")
    return stdout.decode()


async def clone_or_pull(project_id: str, git_url: str, branch: str = "main") -> str:
    """Clone repo if not present, otherwise pull. Returns local path."""
    s = get_settings()
    repo_dir = Path(s.repos_dir) / project_id
    if (repo_dir / ".git").exists():
        log.info("Pulling %s in %s", branch, repo_dir)
        await _run(f"git checkout {branch}", cwd=str(repo_dir))
        await _run("git pull --ff-only", cwd=str(repo_dir))
    else:
        repo_dir.mkdir(parents=True, exist_ok=True)
        log.info("Cloning %s → %s", git_url, repo_dir)
        await _run(f"git clone --branch {branch} --single-branch {git_url} {repo_dir}")
    return str(repo_dir)
