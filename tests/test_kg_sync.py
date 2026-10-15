"""kg sync 本地增量索引测试：.manon/ 布局、SHA256 增量、删除清理、词法兜底。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from matrixone_graph.cli import kg


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    """一个 2 文件互调的小项目 + cwd 切入 + 清空 embedding key。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "util.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (src / "app.py").write_text(
        "from util import add\n\ndef run(x):\n    return add(x, 1)\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MANON_EMBEDDING_API_KEY", raising=False)
    return tmp_path


def _sync(*args: str) -> dict:
    result = CliRunner().invoke(kg, ["sync", "-j", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _graph_entity_ids(proj: Path) -> list[str]:
    g = json.loads((proj / ".manon" / "graph" / "graph.json").read_text(encoding="utf-8"))
    nodes = g.get("entities") or g.get("nodes") or []
    return [n["id"] if isinstance(n, dict) else n for n in nodes]


def test_sync_builds_graph_in_manon(proj):
    summary = _sync()
    assert (proj / ".manon" / "graph" / "graph.json").exists()
    assert summary["files_synced"] == 2
    assert summary["vectors"] == "skipped"  # 未配 embedding key
    ids = _graph_entity_ids(proj)
    assert any("add" in i for i in ids)
    assert any("run" in i for i in ids)


def test_sync_incremental_noop(proj):
    _sync()
    assert (proj / ".manon" / "graph" / "meta.json").exists()
    again = _sync()
    assert again["files_synced"] == 0
    again = _sync()
    assert again["files_synced"] == 0
    assert again["entities_added"] == 0


def test_sync_picks_up_new_symbol(proj):
    _sync()
    src = proj / "src" / "util.py"
    src.write_text(
        "def add(a, b):\n    return a + b\n\n\ndef div(a, b):\n    return a / b\n",
        encoding="utf-8")
    summary = _sync()
    assert summary["files_synced"] == 1
    assert any("div" in i for i in _graph_entity_ids(proj))


def test_sync_removes_deleted_file_entities(proj):
    _sync()
    (proj / "src" / "app.py").unlink()
    summary = _sync()
    assert summary["files_deleted"] == 1
    ids = _graph_entity_ids(proj)
    assert not any("run" in i for i in ids)


def test_full_rebuild_ignores_hashes(proj):
    _sync()
    summary = _sync("--full")
    assert summary["files_synced"] == 2
    assert (proj / ".manon" / "graph" / "meta.json").exists()
    summary = _sync("--full")
    assert summary["files_synced"] == 2
