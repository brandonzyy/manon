"""Tests for saas/routers/classify.py — POST /api/v1/classify-scripts."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from saas.auth import TenantContext
from saas.routers.classify import (
    ClassifyScriptsRequest,
    FileSummary,
    _build_classify_prompt,
    classify_scripts,
)


# ── _build_classify_prompt ────────────────────────────────────────────────────

class TestBuildClassifyPrompt:
    def test_empty_files(self):
        prompt = _build_classify_prompt([])
        assert "请分类以下文件" in prompt

    def test_single_file(self):
        f = FileSummary(
            path="scripts/deploy.py",
            imports=["os", "sys"],
            exports=["main"],
            docstring="Deploy script",
            lines=50,
            has_main=True,
        )
        prompt = _build_classify_prompt([f])
        assert "scripts/deploy.py" in prompt
        assert "50" in prompt
        assert "os" in prompt
        assert "main" in prompt
        assert "Deploy script" in prompt
        assert "True" in prompt

    def test_multiple_files(self):
        files = [
            FileSummary(path="a.py", lines=10),
            FileSummary(path="b.py", lines=20),
        ]
        prompt = _build_classify_prompt(files)
        assert "a.py" in prompt
        assert "b.py" in prompt

    def test_imports_capped_at_8(self):
        f = FileSummary(
            path="x.py",
            imports=[f"mod{i}" for i in range(15)],
        )
        prompt = _build_classify_prompt([f])
        # 8 imports shown; mod8..mod14 are truncated
        assert "mod7" in prompt
        assert "mod8" not in prompt

    def test_no_imports_shows_placeholder(self):
        f = FileSummary(path="x.py", imports=[])
        prompt = _build_classify_prompt([f])
        assert "(无)" in prompt


# ── FileSummary model ─────────────────────────────────────────────────────────

class TestFileSummary:
    def test_defaults(self):
        f = FileSummary(path="foo.py")
        assert f.imports == []
        assert f.exports == []
        assert f.docstring == ""
        assert f.lines == 0
        assert f.has_main is False

    def test_full(self):
        f = FileSummary(
            path="bar.py", imports=["os"], exports=["Foo"],
            docstring="doc", lines=42, has_main=True,
        )
        assert f.path == "bar.py"
        assert f.has_main is True


# ── classify_scripts endpoint ─────────────────────────────────────────────────

def _make_ctx():
    return TenantContext(tenant_id="t1", tier="pro", rate_limit=300)


class TestClassifyScriptsEndpoint:
    @pytest.mark.asyncio
    async def test_empty_files_returns_empty(self):
        body = ClassifyScriptsRequest(files=[])
        with patch("saas.routers.classify.settings") as mock_settings:
            mock_settings.llm_api_key = "key"
            result = await classify_scripts(body, _make_ctx())
        assert result == {"results": {}}

    @pytest.mark.asyncio
    async def test_no_llm_key_raises_503(self):
        from fastapi import HTTPException
        body = ClassifyScriptsRequest(files=[FileSummary(path="x.py")])
        with patch("saas.routers.classify.settings") as mock_settings:
            mock_settings.llm_api_key = ""
            with pytest.raises(HTTPException) as exc:
                await classify_scripts(body, _make_ctx())
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_successful_classification(self):
        body = ClassifyScriptsRequest(files=[
            FileSummary(path="scripts/deploy.py"),
            FileSummary(path="core/parser.py"),
        ])
        llm_response = '{"results": {"scripts/deploy.py": "tool_script", "core/parser.py": "source_code"}}'
        with patch("saas.routers.classify.settings") as mock_settings, \
             patch("saas.routers.classify.llm_chat", new_callable=AsyncMock) as mock_llm, \
             patch("saas.routers.classify.parse_json") as mock_parse, \
             patch("saas.routers.classify.record_usage", new_callable=AsyncMock):
            mock_settings.llm_api_key = "key"
            mock_llm.return_value = llm_response
            mock_parse.return_value = {
                "results": {
                    "scripts/deploy.py": "tool_script",
                    "core/parser.py": "source_code",
                }
            }
            result = await classify_scripts(body, _make_ctx())

        assert result["results"]["scripts/deploy.py"] == "tool_script"
        assert result["results"]["core/parser.py"] == "source_code"

    @pytest.mark.asyncio
    async def test_invalid_llm_values_filtered(self):
        """LLM returning unexpected values should be stripped from results."""
        body = ClassifyScriptsRequest(files=[FileSummary(path="x.py")])
        with patch("saas.routers.classify.settings") as mock_settings, \
             patch("saas.routers.classify.llm_chat", new_callable=AsyncMock), \
             patch("saas.routers.classify.parse_json") as mock_parse, \
             patch("saas.routers.classify.record_usage", new_callable=AsyncMock):
            mock_settings.llm_api_key = "key"
            mock_parse.return_value = {
                "results": {
                    "x.py": "invalid_value",
                    "y.py": "tool_script",
                }
            }
            result = await classify_scripts(body, _make_ctx())

        # invalid_value filtered out; y.py not in request files but still normalized
        assert "x.py" not in result["results"]
        assert result["results"].get("y.py") == "tool_script"

    @pytest.mark.asyncio
    async def test_llm_failure_raises_502(self):
        from fastapi import HTTPException
        body = ClassifyScriptsRequest(files=[FileSummary(path="x.py")])
        with patch("saas.routers.classify.settings") as mock_settings, \
             patch("saas.routers.classify.llm_chat", new_callable=AsyncMock) as mock_llm, \
             patch("saas.routers.classify.record_usage", new_callable=AsyncMock):
            mock_settings.llm_api_key = "key"
            mock_llm.side_effect = Exception("timeout")
            with pytest.raises(HTTPException) as exc:
                await classify_scripts(body, _make_ctx())
        assert exc.value.status_code == 502
        assert "timeout" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_record_usage_called(self):
        body = ClassifyScriptsRequest(files=[FileSummary(path="x.py")])
        with patch("saas.routers.classify.settings") as mock_settings, \
             patch("saas.routers.classify.llm_chat", new_callable=AsyncMock), \
             patch("saas.routers.classify.parse_json") as mock_parse, \
             patch("saas.routers.classify.record_usage", new_callable=AsyncMock) as mock_usage:
            mock_settings.llm_api_key = "key"
            mock_parse.return_value = {"results": {"x.py": "source_code"}}
            await classify_scripts(body, _make_ctx())
        mock_usage.assert_awaited_once_with("t1", "classify.scripts", None)

    @pytest.mark.asyncio
    async def test_only_valid_values_in_results(self):
        """Both 'tool_script' and 'source_code' pass through; nothing else does."""
        body = ClassifyScriptsRequest(files=[FileSummary(path="a.py")])
        with patch("saas.routers.classify.settings") as mock_settings, \
             patch("saas.routers.classify.llm_chat", new_callable=AsyncMock), \
             patch("saas.routers.classify.parse_json") as mock_parse, \
             patch("saas.routers.classify.record_usage", new_callable=AsyncMock):
            mock_settings.llm_api_key = "key"
            mock_parse.return_value = {"results": {
                "a.py": "source_code",
                "b.py": "tool_script",
                "c.py": "UNKNOWN",
                "d.py": "",
            }}
            result = await classify_scripts(body, _make_ctx())

        assert result["results"]["a.py"] == "source_code"
        assert result["results"]["b.py"] == "tool_script"
        assert "c.py" not in result["results"]
        assert "d.py" not in result["results"]
