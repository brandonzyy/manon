"""Tests for codeindex/parser_installer.py."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest

import subprocess
from codeindex.parser import get_all_extensions
from codeindex.parser_installer import (
    check_parser_installed,
    install_parsers,
    _try_pip_install,
    LANG_TO_PACKAGE,
    PIP_MIRRORS,
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

    def test_every_parsed_language_has_a_package(self):
        """A language parser.py detects but the installer cannot install gets
        collected and then silently never parsed — no grammar is installed."""
        assert set(get_all_extensions().values()) - set(LANG_TO_PACKAGE) == set()

    def test_no_orphan_package_entries(self):
        """Entries for languages parser.py never yields are dead weight."""
        assert set(LANG_TO_PACKAGE) - set(get_all_extensions().values()) == set()

    def test_typescript_and_tsx_share_one_package(self):
        assert LANG_TO_PACKAGE["typescript"] == LANG_TO_PACKAGE["tsx"]


class TestCheckParserInstalledIsGeneric:
    """check_parser_installed derives the module from LANG_TO_PACKAGE, so a new
    language needs one entry rather than a matching if/elif branch."""

    def test_generic_language_resolves(self):
        assert check_parser_installed("go") is True

    def test_dashes_become_underscores(self):
        with patch("builtins.__import__") as imp:
            assert check_parser_installed("c_sharp") is True
        imp.assert_called_once_with("tree_sitter_c_sharp")

    def test_import_error_is_false(self):
        with patch("builtins.__import__", side_effect=ImportError):
            assert check_parser_installed("go") is False


class TestProxyFallback:
    def test_retries_direct_when_proxy_is_set(self):
        """A CONNECT-only corporate proxy rejects PyPI outright."""
        with patch.dict("os.environ", {"HTTPS_PROXY": "https://corp:8443"}, clear=False):
            with patch("subprocess.check_call",
                       side_effect=[subprocess.CalledProcessError(1, "pip"), 0]) as call:
                assert _try_pip_install(["tree-sitter-go"], 30) is True
        assert call.call_count == 2
        assert "HTTPS_PROXY" not in call.call_args_list[1].kwargs["env"]

    def test_no_retry_without_proxy(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("subprocess.check_call",
                       side_effect=subprocess.CalledProcessError(1, "pip")) as call:
                assert _try_pip_install(["tree-sitter-go"], 30) is False
        assert call.call_count == 1

    def test_timeout_is_failure(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("subprocess.check_call",
                       side_effect=subprocess.TimeoutExpired("pip", 30)):
                assert _try_pip_install(["tree-sitter-go"], 30) is False


class TestPerPackageFallback:
    def test_one_missing_package_does_not_sink_the_batch(self):
        """tree-sitter-r is not published to PyPI. Under all-or-nothing batching
        it failed every grammar sharing its pip command."""
        def fake_install(packages, timeout, mirror=None):
            return "tree-sitter-r" not in packages

        with patch("codeindex.parser_installer.check_parser_installed", return_value=False):
            with patch("codeindex.parser_installer._try_pip_install", side_effect=fake_install):
                results = install_parsers({"go", "r"})

        assert results["go"] == "installed"
        assert results["r"] == "failed"

    def test_batch_success_skips_the_singles_pass(self):
        with patch("codeindex.parser_installer.check_parser_installed", return_value=False):
            with patch("codeindex.parser_installer._try_pip_install", return_value=True) as pip:
                results = install_parsers({"go", "rust"})
        assert results == {"go": "installed", "rust": "installed"}
        assert pip.call_count == 1  # single batch on the first mirror

    def test_total_failure_marks_all_failed(self):
        with patch("codeindex.parser_installer.check_parser_installed", return_value=False):
            with patch("codeindex.parser_installer._try_pip_install", return_value=False):
                results = install_parsers({"go", "rust"})
        assert results == {"go": "failed", "rust": "failed"}

    def test_single_package_not_retried_individually(self):
        """One package already saw every mirror during the batch pass."""
        with patch("codeindex.parser_installer.check_parser_installed", return_value=False):
            with patch("codeindex.parser_installer._try_pip_install", return_value=False) as pip:
                install_parsers({"go"})
        assert pip.call_count == len(PIP_MIRRORS)

    def test_shared_package_installed_once(self):
        with patch("codeindex.parser_installer.check_parser_installed", return_value=False):
            with patch("codeindex.parser_installer._try_pip_install", return_value=True) as pip:
                results = install_parsers({"typescript", "tsx"})
        assert results == {"typescript": "installed", "tsx": "installed"}
        assert pip.call_args[0][0] == ["tree-sitter-typescript"]
