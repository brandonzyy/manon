"""Tests for manon_mcp.tools.init.initialize_project."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from manon_mcp.tools import init as init_module
from manon_mcp.tools.init import initialize_project, resolve_scan_python, venv_python


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
    config = SimpleNamespace(API_URL="http://localhost:3700", _get_client_version=lambda: "1.2.3")

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
        config=SimpleNamespace(API_URL="http://localhost:3700", _get_client_version=lambda: "1.2.3"),
        read_update_status=lambda: None,
        init_existing_project=lambda *args, **kwargs: None,
        init_match_or_create=lambda *args, **kwargs: None,
        build_hooks_lines=lambda path: [],
    )

    assert "Manon API unreachable" in result
    assert "boom" in result


def test_venv_python_layout():
    path = venv_python("/opt/manon")
    assert path.parent.parent.name == ".venv"
    assert path.stem == "python"


def test_resolve_scan_python_prefers_running_venv(monkeypatch, tmp_path):
    """A venv owns the tree-sitter grammars — never hand back its base interpreter."""
    venv = tmp_path / ".venv"
    bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True)
    interpreter = bin_dir / ("python.exe" if os.name == "nt" else "python")
    interpreter.touch()
    (venv / "pyvenv.cfg").write_text("executable = /opt/homebrew/bin/python3\n")

    base = tmp_path / "base-python"
    base.touch()
    monkeypatch.setattr(sys, "executable", str(interpreter))
    monkeypatch.setattr(sys, "_base_executable", str(base), raising=False)

    assert resolve_scan_python() == str(interpreter)


def test_resolve_scan_python_falls_back_to_manon_venv(monkeypatch, tmp_path):
    """Caller is a bare system python (often PEP 668) → route scans to <manon_dir>/.venv."""
    manon_venv = venv_python(Path(init_module.__file__).resolve().parents[2])
    if not manon_venv.exists():
        pytest.skip("checkout has no .venv")

    system_python = tmp_path / "system-python"
    system_python.touch()
    monkeypatch.setattr(sys, "executable", str(system_python))
    monkeypatch.setattr(sys, "_base_executable", str(system_python), raising=False)

    assert resolve_scan_python() == str(manon_venv)
