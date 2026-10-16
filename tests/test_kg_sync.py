"""kg sync/deep-query 本地增量索引测试：.manon/ 布局、SHA256 增量、删除清理、向量必需语义。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))  # chdir 到临时项目后仍可解析 codeindex/core

import pytest
from click.testing import CliRunner

from matrixone_graph.cli import kg

DUMMY_KEY = "sk-test-embedding"


class StubEmbeddingClient:
    """离线向量桩：文本哈希派生 8 维向量，同文本同向量。"""

    def __init__(self, *_args, **_kwargs):
        pass

    async def embed(self, texts):
        return [self._vec(t) for t in texts]

    async def embed_single(self, text):
        return self._vec(text)

    async def close(self):
        pass

    @staticmethod
    def _vec(text: str) -> list[float]:
        return [(hash((text, i)) % 97) / 97 for i in range(8)]


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    """一个 2 文件互调的小项目 + cwd 切入 + 注入向量桩与测试 key。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "util.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (src / "app.py").write_text(
        "from util import add\n\ndef run(x):\n    return add(x, 1)\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MANON_EMBEDDING_API_KEY", DUMMY_KEY)
    monkeypatch.setattr("matrixone_graph.embed.EmbeddingClient", StubEmbeddingClient)
    return tmp_path


def _sync(*args: str) -> dict:
    result = CliRunner().invoke(kg, ["sync", "-j", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _graph_entity_ids(proj: Path) -> list[str]:
    g = json.loads((proj / ".manon" / "graph" / "graph.json").read_text(encoding="utf-8"))
    nodes = g.get("entities") or g.get("nodes") or []
    return [n["id"] if isinstance(n, dict) else n for n in nodes]


def test_sync_requires_embedding_key(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "util.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MANON_EMBEDDING_API_KEY", raising=False)
    result = CliRunner().invoke(kg, ["sync", "-j"])
    assert result.exit_code != 0
    assert "MANON_EMBEDDING_API_KEY" in result.output + result.stderr


def test_query_requires_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MANON_EMBEDDING_API_KEY", raising=False)
    result = CliRunner().invoke(kg, ["query", "add"])
    assert result.exit_code != 0
    assert "MANON_EMBEDDING_API_KEY" in result.output + result.stderr


def test_sync_builds_graph_with_vectors(proj):
    summary = _sync()
    assert (proj / ".manon" / "graph" / "graph.json").exists()
    assert (proj / ".manon" / "graph" / "vectors.npz").exists()
    assert summary["files_synced"] == 2
    assert summary["vectors"] == "embedded"
    meta = json.loads((proj / ".manon" / "graph" / "meta.json").read_text(encoding="utf-8"))
    assert meta["vectors"] == "embedded"
    ids = _graph_entity_ids(proj)
    assert any("add" in i for i in ids)
    assert any("run" in i for i in ids)


def test_query_semantic_path(proj):
    _sync()
    assert (proj / ".manon" / "graph" / "vectors.npz").exists()
    result = CliRunner().invoke(kg, ["query", "add", "-j"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload["entities"], list)


def test_sync_incremental_noop(proj):
    _sync()
    assert (proj / ".manon" / "graph" / "meta.json").exists()
    again = _sync()
    assert again["files_synced"] == 0


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


def test_deep_query_requires_llm_key(proj, monkeypatch):
    _sync()
    assert (proj / ".manon" / "graph" / "vectors.npz").exists()
    monkeypatch.delenv("VERISPRING_API_KEY", raising=False)
    result = CliRunner().invoke(kg, ["deep-query", "add 在哪"])
    assert result.exit_code != 0
    assert "VERISPRING_API_KEY" in result.output + result.stderr


def test_deep_query_rounds_with_stubbed_llm(proj, monkeypatch):
    """LLM 桩返回一个 follow-up，验证两轮累积与解析链路。"""
    _sync()

    class StubLLM:
        async def __call__(self, messages, **_kwargs):
            if "Follow-up" not in messages[1]["content"]:
                return ('{"sub_questions":["add 的调用方"],"covered":["定位 add"],'
                        '"missing":[],"queries":["util add"],"reason":"need callers"}')
            return ('{"sub_questions":["add 的调用方"],"covered":["定位 add","调用方已查"],'
                    '"missing":[],"queries":[],"reason":"done"}')

    monkeypatch.setenv("VERISPRING_API_KEY", "sk-stub-verispring")
    monkeypatch.setattr("matrixone_graph.cli._llm_chat", StubLLM())

    class StubMG:
        def __init__(self, _repo_path, **_kwargs):
            self.kg_path = proj / ".manon" / "graph"
        async def query(self, q, **_kwargs):
            from matrixone_graph.pipeline import QueryResult
            return QueryResult(entities=[{"id": "src.util.add", "name": "add"}],
                               relations=[], chunks=[], context=f"ctx[{q}]")
        async def close(self):
            pass

    monkeypatch.setattr("matrixone_graph.MatrixoneGraph", StubMG)
    result = CliRunner().invoke(kg, ["deep-query", "add 的调用方是谁", "-j"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["context"]
    assert len(payload["rounds"]) >= 2
    assert payload["sub_questions"] == ["add 的调用方"]
