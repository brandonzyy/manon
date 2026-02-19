"""LoomGraph CLI wrapper — async subprocess calls to loomgraph commands."""

from __future__ import annotations

import asyncio
import json
import logging

from ..config import get_settings

log = logging.getLogger("manon.loomgraph")


async def _run_cli(*args: str, timeout: float = 60.0) -> str:
    s = get_settings()
    cmd = [s.loomgraph_bin, *args]
    log.info("loomgraph %s", " ".join(args))
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("LoomGraph CLI timeout")
    if proc.returncode != 0:
        raise RuntimeError(f"LoomGraph CLI error ({proc.returncode}): {stderr.decode()[:500]}")
    return stdout.decode()


async def search(query: str, *, workspace: str | None = None, mode: str = "local") -> str:
    args = ["search", query, "--mode", mode]
    if workspace:
        args += ["--workspace", workspace]
    return await _run_cli(*args)


async def graph(symbol: str, *, workspace: str | None = None, direction: str = "both", depth: int = 2) -> str:
    args = ["graph", symbol, "--direction", direction, "--depth", str(depth)]
    if workspace:
        args += ["--workspace", workspace]
    return await _run_cli(*args)


async def impact(*, workspace: str | None = None, file: str | None = None, staged: bool = False) -> str:
    args = ["impact"]
    if file:
        args += ["--file", file]
    elif staged:
        args.append("--staged")
    if workspace:
        args += ["--workspace", workspace]
    return await _run_cli(*args)


async def deps(symbol: str, *, workspace: str | None = None) -> str:
    args = ["deps", symbol]
    if workspace:
        args += ["--workspace", workspace]
    return await _run_cli(*args)


async def overview(*, workspace: str | None = None) -> str:
    args = ["overview"]
    if workspace:
        args += ["--workspace", workspace]
    return await _run_cli(*args)


async def index_repo(repo_path: str, *, workspace: str | None = None) -> str:
    args = ["index", repo_path]
    if workspace:
        args += ["--workspace", workspace]
    return await _run_cli(*args, timeout=300.0)


async def update_index(repo_path: str, *, workspace: str | None = None) -> str:
    args = ["update", repo_path]
    if workspace:
        args += ["--workspace", workspace]
    return await _run_cli(*args, timeout=300.0)
