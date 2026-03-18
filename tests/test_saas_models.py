"""Tests for saas/models.py — all Pydantic request/response models."""
import pytest
from pydantic import ValidationError

from saas.models import (
    RepoCreate, RepoOut, IndexStatus,
    FileSyncData, SyncAstRequest, SearchResult, ImpactResult,
    UsageSummary, TenantCreate, TenantOut, RegisterRequest,
    DeepQueryRequest, MergeDynamicRequest,
)


class TestRepoCreate:
    def test_defaults(self):
        r = RepoCreate(name="test")
        assert r.branch == "main"
        assert r.git_url == ""
        assert r.source_type == ""
        assert r.local_path is None

    def test_with_git_url(self):
        r = RepoCreate(name="test", git_url="https://github.com/x/y.git")
        assert r.git_url == "https://github.com/x/y.git"

    def test_with_local_path(self):
        r = RepoCreate(name="test", local_path="/tmp/repo", source_type="local")
        assert r.source_type == "local"


class TestRepoOut:
    def test_full(self):
        r = RepoOut(
            id="abc", name="test", git_url="", branch="main",
            local_path=None, index_status="done", created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        assert r.id == "abc"
        assert r.index_stats is None


class TestIndexStatus:
    def test_create(self):
        s = IndexStatus(repo_id="r1", status="done", stats={"files": 10})
        assert s.stats["files"] == 10


class TestFileSyncData:
    def test_create(self):
        f = FileSyncData(rel_path="a.py", hash="abc", parse_result={"symbols": []})
        assert f.rel_path == "a.py"
        assert f.hash == "abc"


class TestSyncAstRequest:
    def test_defaults(self):
        r = SyncAstRequest()
        assert r.files == []
        assert r.deleted_files == []
        assert r.full_reindex is False

    def test_with_files(self):
        f = FileSyncData(rel_path="a.py", hash="h", parse_result={})
        r = SyncAstRequest(files=[f], deleted_files=["b.py"])
        assert len(r.files) == 1
        assert r.deleted_files == ["b.py"]


class TestSearchResult:
    def test_defaults(self):
        s = SearchResult()
        assert s.entities == []
        assert s.context == ""


class TestImpactResultModel:
    def test_defaults(self):
        r = ImpactResult()
        assert r.commit == ""
        assert r.changed_symbols == []
        assert r.risk == {}


class TestUsageSummary:
    def test_create(self):
        u = UsageSummary(tenant_id="t1", period_days=30, total_calls=100, total_tokens=5000)
        assert u.total_calls == 100
        assert u.by_endpoint == {}


class TestTenantCreate:
    def test_defaults(self):
        t = TenantCreate(name="test")
        assert t.tier == "free"


class TestTenantOut:
    def test_create(self):
        t = TenantOut(id="t1", name="test", tier="pro", api_key="key")
        assert t.api_key == "key"


class TestRegisterRequest:
    def test_default_name(self):
        r = RegisterRequest()
        assert r.name == "anonymous"


class TestDeepQueryRequest:
    def test_defaults(self):
        r = DeepQueryRequest(question="what is X?")
        assert r.max_rounds == 3

    def test_max_rounds_bounds(self):
        r = DeepQueryRequest(question="q", max_rounds=5)
        assert r.max_rounds == 5
        with pytest.raises(ValidationError):
            DeepQueryRequest(question="q", max_rounds=0)
        with pytest.raises(ValidationError):
            DeepQueryRequest(question="q", max_rounds=6)


class TestMergeDynamicRequest:
    def test_defaults(self):
        m = MergeDynamicRequest()
        assert m.edges == {}
        assert m.raw_edges == []
        assert m.project_root == ""

    def test_with_edges(self):
        m = MergeDynamicRequest(edges={"a->b": 3})
        assert m.edges["a->b"] == 3
