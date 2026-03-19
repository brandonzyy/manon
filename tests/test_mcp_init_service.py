"""Tests for manon_mcp.tools.init.initialize_project."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from manon_mcp.tools.init import initialize_project


class _FakeContext:
    def __init__(self) -> None:
        self.progress_calls: list[tuple[float, float, str]] = []
        self.info_calls: list[str] = []

    async def report_progress(self, current: float, total: float, message: str) -> None:
        self.progress_calls.append((current, total, message))

    async def info(self, message: str) -> None:
        self.info_calls.append(message)


@pytest.mark.asyncio
async def test_initialize_project_existing_repo(monkeypatch, tmp_path):
    project_path = str(tmp_path)
    ctx = _FakeContext()
    client = SimpleNamespace(_get_no_auth=lambda path: {"ok": True})
    config = SimpleNamespace(API_URL="http://localhost:3700", CLIENT_VERSION="1.2.3")

    monkeypatch.setattr(
        "manon_mcp.tools.init.get_project",
        lambda path: {"repo_id": "repo12345", "name": "demo", "last_sync": "", "file_hashes": {}},
    )
    monkeypatch.setattr("manon_mcp.tools.init.needs_smart_analysis_refresh", lambda path, proj: True)

    result = await initialize_project(
        project_path=project_path,
        project_name="demo",
        ctx=ctx,
        client=client,
        config=config,
        read_update_status=lambda: "[previous update]",
        init_existing_project=lambda *args, **kwargs: (
            "repo12345",
            ["  demo  (repo12345)"],
            ["  indexed"],
        ),
        init_match_or_create=lambda *args, **kwargs: None,
        build_hooks_lines=lambda path: ["  hooks ready"],
    )

    assert "<!-- DISPLAY_VERBATIM -->" in result
    assert "repo12345" in result
    assert "<!-- SMART_ANALYSIS_NEEDED -->" in result
    assert "<!-- MANON_DIR=" in result
    assert "<!-- MANON_PYTHON=" in result
    assert ctx.progress_calls
    assert any("Initialization complete" in message for message in ctx.info_calls)


@pytest.mark.asyncio
async def test_initialize_project_healthcheck_failure(tmp_path):
    result = await initialize_project(
        project_path=str(tmp_path),
        project_name="demo",
        ctx=None,
        client=SimpleNamespace(_get_no_auth=lambda path: (_ for _ in ()).throw(RuntimeError("boom"))),
        config=SimpleNamespace(API_URL="http://localhost:3700", CLIENT_VERSION="1.2.3"),
        read_update_status=lambda: None,
        init_existing_project=lambda *args, **kwargs: None,
        init_match_or_create=lambda *args, **kwargs: None,
        build_hooks_lines=lambda path: [],
    )

    assert "Manon API unreachable" in result
    assert "boom" in result
