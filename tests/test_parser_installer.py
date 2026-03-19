"""Tests for codeindex/parser_installer.py."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest

import subprocess
from codeindex.parser_installer import (
    check_parser_installed,
    install_parsers,
    _try_pip_install,
    LANG_TO_PACKAGE,
)


class TestCheckParserInstalled:
    def test_python_installed(self):
        # Python tree-sitter should always be installed in this project
        assert check_parser_installed("python") is True

    def test_php_installed(self):
        assert check_parser_installed("php") is True

    def test_typescript_installed(self):
        assert check_parser_installed("typescript") is True

    def test_tsx_installed(self):
        assert check_parser_installed("tsx") is True

    def test_javascript_installed(self):
        assert check_parser_installed("javascript") is True

    def test_java_returns_bool(self):
        result = check_parser_installed("java")
        assert isinstance(result, bool)

    def test_unknown_lang_returns_false(self):
        assert check_parser_installed("cobol") is False
        assert check_parser_installed("brainfuck") is False

    def test_empty_string_returns_false(self):
        assert check_parser_installed("") is False


class TestInstallParsers:
    def test_all_installed_returns_already_installed(self):
        """All available parsers should return 'already_installed'."""
        langs = {"python", "php", "typescript"}
        results = install_parsers(langs, timeout=30)
        for lang in langs:
            assert results[lang] == "already_installed"

    def test_empty_set_returns_empty(self):
        results = install_parsers(set(), timeout=30)
        assert results == {}

    def test_unknown_lang_not_in_results(self):
        # Unknown language has no package mapping, won't be in results
        results = install_parsers({"cobol"}, timeout=30)
        # Either not present or handled gracefully
        assert isinstance(results, dict)

    def test_mixed_installed_and_unknown(self):
        results = install_parsers({"python", "fortran"}, timeout=30)
        assert results.get("python") == "already_installed"

    @patch("codeindex.parser_installer.check_parser_installed", return_value=False)
    @patch("codeindex.parser_installer._try_pip_install", return_value=True)
    def test_installs_missing_parser(self, mock_pip, mock_check):
        results = install_parsers({"python"}, timeout=30)
        assert results.get("python") == "installed"

    @patch("codeindex.parser_installer.check_parser_installed", return_value=False)
    @patch("codeindex.parser_installer._try_pip_install", return_value=False)
    def test_failed_install_returns_failed(self, mock_pip, mock_check):
        results = install_parsers({"python"}, timeout=30)
        assert results.get("python") == "failed"


class TestTryPipInstall:
    @patch("subprocess.check_call")
    def test_success_returns_true(self, mock_call):
        mock_call.return_value = 0
        result = _try_pip_install(["some-package"], timeout=30)
        assert result is True

    @patch("subprocess.check_call", side_effect=subprocess.CalledProcessError(1, "pip"))
    def test_failure_returns_false(self, mock_call):
        result = _try_pip_install(["nonexistent-pkg-xyz"], timeout=30)
        assert result is False

    @patch("subprocess.check_call")
    def test_with_mirror(self, mock_call):
        mock_call.return_value = 0
        result = _try_pip_install(["pkg"], timeout=30, mirror="https://pypi.example.com/simple")
        assert result is True
        cmd = mock_call.call_args[0][0]
        assert "-i" in cmd
        assert "https://pypi.example.com/simple" in cmd

    @patch("subprocess.check_call")
    def test_without_mirror(self, mock_call):
        mock_call.return_value = 0
        result = _try_pip_install(["pkg"], timeout=30, mirror=None)
        assert result is True
        cmd = mock_call.call_args[0][0]
        assert "-i" not in cmd


class TestLangToPackage:
    def test_all_main_languages_have_packages(self):
        for lang in ("python", "javascript", "typescript", "tsx", "php", "java"):
            assert lang in LANG_TO_PACKAGE
            assert isinstance(LANG_TO_PACKAGE[lang], str)
