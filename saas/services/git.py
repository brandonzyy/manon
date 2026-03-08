"""DEPRECATED: Async git clone / pull for server-side indexing.

All repos now use MCP client-side scan_and_parse + sync-ast.
No production repos use git_url as of 2026-03.
This module can be removed once the git-clone path is fully retired."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..config import settings


async def clone_or_pull(repo_id: str, git_url: str, branch: str = "main") -> str:
    """Clone a repo (or pull if already cloned). Returns local path."""
    repo_dir = Path(settings.repos_dir) / repo_id
    if (repo_dir / ".git").exists():
        cmds = [
            f"git -C \"{repo_dir}\" checkout {branch}",
            f"git -C \"{repo_dir}\" pull --ff-only",
        ]
    else:
        repo_dir.mkdir(parents=True, exist_ok=True)
        cmds = [f"git clone --branch {branch} --single-branch {git_url} \"{repo_dir}\""]

    for cmd in cmds:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git failed: {stderr.decode()[:500]}")

    return str(repo_dir.resolve())
