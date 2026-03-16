"""Shared dependency bundle for MCP tool registration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ToolDependencies:
    client: object
    config: object
    sync: object
    hooks: object
    read_update_status: Callable[[], str | None]
    init_existing_project: Callable[..., tuple[str, list[str], list[str]]]
    init_match_or_create: Callable[..., tuple[str | None, list[str], list[str]] | str]
    build_hooks_lines: Callable[[str], list[str]]
    local_impact: Callable[[str, str, str, int], str]
