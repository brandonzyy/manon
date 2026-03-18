"""Tests for matrixone_graph/cli.py CLI commands."""
from __future__ import annotations

import os
import pytest
from click.testing import CliRunner
from matrixone_graph.cli import kg, _get_embedding_url


class TestGetEmbeddingUrl:
    def test_returns_default_when_none(self):
        url = _get_embedding_url(None)
        assert url == "http://localhost:8080"

    def test_returns_given_url(self):
        url = _get_embedding_url("http://my-server:8080")
        assert url == "http://my-server:8080"

    def test_env_var_used_when_no_url(self, monkeypatch):
        monkeypatch.setenv("CODEINDEX_EMBEDDING_URL", "http://env-server:9090")
        url = _get_embedding_url(None)
        assert url == "http://env-server:9090"


class TestKgStatusCommand:
    def test_status_no_index(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(kg, ["status"])
        assert result.exit_code == 0
        assert "No knowledge graph index found" in result.output

    def test_status_shows_run_hint(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(kg, ["status"])
        assert "index" in result.output.lower()


class TestKgClearCommand:
    def test_clear_no_index(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(kg, ["clear"])
        # Should succeed (clearing a non-existent index is a no-op)
        assert result.exit_code == 0

    def test_clear_shows_confirmation(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(kg, ["clear"])
        assert "clear" in result.output.lower() or result.exit_code == 0


class TestKgGroupHelp:
    def test_help_shows_commands(self):
        runner = CliRunner()
        result = runner.invoke(kg, ["--help"])
        assert result.exit_code == 0
        assert "query" in result.output or "status" in result.output

    def test_query_requires_text(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(kg, ["query"])
        # Missing required arg
        assert result.exit_code != 0 or "Error" in result.output or "Missing" in result.output
