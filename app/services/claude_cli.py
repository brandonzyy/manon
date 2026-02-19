"""CLI agent wrapper — spawn claude / codebuddy as subprocess, stream output."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Callable, Awaitable

log = logging.getLogger("manon.cli")

# Locate binaries
CLAUDE_BIN = shutil.which("claude") or "claude"
CODEBUDDY_BIN = shutil.which("codebuddy") or "codebuddy"

CLI_CONFIGS = {
    "claude": {
        "bin": CLAUDE_BIN,
        "args": lambda max_turns: ["--print", "--max-turns", str(max_turns)],
        "env_strip": ["CLAUDECODE"],
    },
    "codebuddy": {
        "bin": CODEBUDDY_BIN,
        "args": lambda max_turns: ["--print", "--max-turns", str(max_turns)],
        "env_strip": [],
    },
}


async def run_cli_chat(
    cli: str,
    prompt: str,
    *,
    cwd: str | None = None,
    max_turns: int = 10,
    on_output: Callable[[str], Awaitable[None]] | None = None,
    timeout: float = 300.0,
) -> str:
    """Run a CLI agent, stream stdout via on_output callback. Returns full output."""
    cfg = CLI_CONFIGS.get(cli)
    if not cfg:
        raise ValueError(f"Unknown CLI: {cli}. Available: {list(CLI_CONFIGS)}")

    cmd = [cfg["bin"], *cfg["args"](max_turns)]
    env = {**os.environ}
    for k in cfg.get("env_strip", []):
        env.pop(k, None)

    log.info("Spawning %s (max %d turns) in %s", cli, max_turns, cwd or ".")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )

    proc.stdin.write(prompt.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    full_output: list[str] = []
    buffer = ""

    try:
        async def read_stream():
            nonlocal buffer
            while True:
                chunk = await asyncio.wait_for(proc.stdout.read(512), timeout=60.0)
                if not chunk:
                    break
                text = chunk.decode(errors="replace")
                full_output.append(text)
                buffer += text
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if on_output and line.strip():
                        await on_output(line)
            if buffer.strip() and on_output:
                await on_output(buffer)

        await asyncio.wait_for(read_stream(), timeout=timeout)
    except asyncio.TimeoutError:
        log.warning("%s CLI timeout after %.0fs", cli, timeout)
        try:
            proc.kill()
        except Exception:
            pass
    except Exception as exc:
        log.error("%s CLI error: %s", cli, exc)

    await proc.wait()
    result = "".join(full_output)
    log.info("%s CLI finished (exit %s, %d chars)", cli, proc.returncode, len(result))
    return result
