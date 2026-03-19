"""Tests for core/script_classifier.py — ScriptSignals, ScriptClassifier, helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.script_classifier import (
    ScriptClassifier,
    ScriptSignals,
    build_imported_paths,
    is_scripts_like_path,
    _is_tool_name,
)


# ── _is_tool_name ────────────────────────────────────────────────────────────

class TestIsToolName:
    @pytest.mark.parametrize("stem", [
        "deploy_prod", "deploy-prod",
        "setup_db", "db_setup",
        "install_deps",
        "migrate_schema",
        "seed_data",
        "admin_cli", "cli_admin",
        "run_server", "start_server", "stop_server",
        "init_project", "bootstrap_env",
        "cleanup_temp", "reset_db",
        "update_config", "config_update",
        "helper_utils", "utils_helper",
        "DEPLOY_PROD",   # case insensitive
    ])
    def test_tool_names_match(self, stem):
        assert _is_tool_name(stem) is True

    @pytest.mark.parametrize("stem", [
        "parser", "analyzer", "classifier", "client", "models",
        "utils", "config", "scanner", "store", "health",
        "main",         # plain "main" doesn't match any pattern
        "deployment",   # "deploy" but not "deploy_" prefix
    ])
    def test_source_names_no_match(self, stem):
        assert _is_tool_name(stem) is False


# ── ScriptSignals._from_source ────────────────────────────────────────────────

class TestScriptSignalsFromSource:
    def test_empty_source(self):
        s = ScriptSignals("foo.py", source="")
        assert s.imports == []
        assert s.exports == []
        assert s.has_main_guard is False
        assert s.line_count == 1

    def test_detects_imports(self):
        source = "import os\nimport sys\nfrom pathlib import Path\n"
        s = ScriptSignals("foo.py", source=source)
        assert "os" in s.imports
        assert "sys" in s.imports
        assert "pathlib" in s.imports

    def test_detects_public_exports(self):
        source = "def public_fn(): pass\ndef _private(): pass\nclass Pub: pass\n"
        s = ScriptSignals("foo.py", source=source)
        assert "public_fn" in s.exports
        assert "Pub" in s.exports
        assert "_private" not in s.exports

    def test_detects_main_guard(self):
        source = 'if __name__ == "__main__":\n    main()\n'
        s = ScriptSignals("foo.py", source=source)
        assert s.has_main_guard is True

    def test_no_main_guard(self):
        source = "def foo(): pass\n"
        s = ScriptSignals("foo.py", source=source)
        assert s.has_main_guard is False

    def test_detects_docstring(self):
        source = '"""Module docstring."""\n\ndef foo(): pass\n'
        s = ScriptSignals("foo.py", source=source)
        assert s.docstring == "Module docstring."

    def test_syntax_error_graceful(self):
        s = ScriptSignals("bad.py", source="def (broken:")
        assert s.imports == []
        assert s.exports == []

    def test_line_count(self):
        source = "a = 1\nb = 2\nc = 3\n"
        s = ScriptSignals("foo.py", source=source)
        assert s.line_count == 4  # 3 newlines + 1

    def test_async_function_exported(self):
        source = "async def handle(): pass\n"
        s = ScriptSignals("foo.py", source=source)
        assert "handle" in s.exports


class TestScriptSignalsFromParseResult:
    def test_basic_parse_result(self):
        pr = {
            "imports": [{"name": "os"}, {"name": "core.ast"}],
            "symbols": [{"name": "MyClass"}, {"name": "_private"}],
            "line_count": 50,
            "docstring": "Module doc",
            "has_main_guard": True,
        }
        s = ScriptSignals("mod.py", parse_result=pr)
        assert "os" in s.imports
        assert "core.ast" in s.imports
        assert "MyClass" in s.exports
        assert "_private" in s.exports
        assert s.line_count == 50
        assert s.docstring == "Module doc"
        assert s.has_main_guard is True

    def test_empty_parse_result(self):
        s = ScriptSignals("empty.py", parse_result={})
        assert s.imports == []
        assert s.exports == []
        assert s.has_main_guard is False
        assert s.line_count == 0

    def test_main_guard_fallback_from_str(self):
        pr = {"symbols": [{"name": "__main__"}], "has_main_guard": False}
        s = ScriptSignals("x.py", parse_result=pr)
        assert s.has_main_guard is True  # inferred from str representation


class TestScriptSignalsEmpty:
    def test_no_args(self):
        s = ScriptSignals("x.py")
        assert s.imports == []
        assert s.exports == []
        assert s.has_main_guard is False
        assert s.line_count == 0
        assert s.docstring == ""


# ── ScriptClassifier.classify ─────────────────────────────────────────────────

class TestClassify:
    def setup_method(self):
        self.clf = ScriptClassifier(project_packages=["core", "manon_mcp", "saas"])

    def _sig(self, stem, imports=None, exports=None, has_main=False):
        s = ScriptSignals.__new__(ScriptSignals)
        s.rel_path = f"{stem}.py"
        s.stem = stem
        s.imports = imports or []
        s.exports = exports or []
        s.has_main_guard = has_main
        s.line_count = 10
        s.docstring = ""
        return s

    # Signal 1: imported by project
    def test_imported_by_project_is_source(self):
        sig = self._sig("whatever")
        result, certain = self.clf.classify(sig, imported_by_project=True)
        assert result == "source_code"
        assert certain is True

    # Signal 2: imports project modules
    def test_imports_project_module_is_source(self):
        sig = self._sig("tool", imports=["core.ast.analysis"])
        result, certain = self.clf.classify(sig)
        assert result == "source_code"
        assert certain is True

    def test_imports_project_root_is_source(self):
        sig = self._sig("helper", imports=["saas"])
        result, certain = self.clf.classify(sig)
        assert result == "source_code"
        assert certain is True

    def test_imports_stdlib_only_not_definitive(self):
        sig = self._sig("helper", imports=["os", "sys", "json"])
        result, _ = self.clf.classify(sig)
        assert result != "source_code"  # stdlib doesn't count

    # Signal 3: tool name + single main
    def test_tool_name_with_main_is_tool_script(self):
        sig = self._sig("deploy_prod", has_main=True, exports=["main"])
        result, certain = self.clf.classify(sig)
        assert result == "tool_script"
        assert certain is True

    def test_tool_name_without_main_is_uncertain(self):
        sig = self._sig("deploy_prod", has_main=False)
        result, certain = self.clf.classify(sig)
        assert result == "uncertain"
        assert certain is False

    def test_tool_name_with_many_exports_is_uncertain(self):
        sig = self._sig("setup_db", has_main=True,
                        exports=["fn1", "fn2", "fn3", "fn4"])
        result, certain = self.clf.classify(sig)
        assert result == "uncertain"
        assert certain is False

    # Signal 4: uncertain
    def test_unknown_script_is_uncertain(self):
        sig = self._sig("mystery", imports=["requests"])
        result, certain = self.clf.classify(sig)
        assert result == "uncertain"
        assert certain is False

    # Priority: signal 1 beats everything
    def test_imported_beats_tool_name(self):
        sig = self._sig("deploy_prod", has_main=True, exports=["main"])
        result, certain = self.clf.classify(sig, imported_by_project=True)
        assert result == "source_code"

    # Priority: signal 2 beats tool name
    def test_project_import_beats_tool_name(self):
        sig = self._sig("deploy_prod", imports=["core.something"], has_main=True)
        result, certain = self.clf.classify(sig)
        assert result == "source_code"


# ── ScriptClassifier.classify_batch ──────────────────────────────────────────

class TestClassifyBatch:
    def setup_method(self):
        self.clf = ScriptClassifier(project_packages=["mypkg"])

    def _file(self, rel_path, imports=None, has_main=False, exports=None):
        return {
            "rel_path": rel_path,
            "parse_result": {
                "imports": [{"name": n} for n in (imports or [])],
                "symbols": [{"name": n} for n in (exports or [])],
                "has_main_guard": has_main,
                "line_count": 20,
                "docstring": "",
            },
        }

    def test_source_code_kept(self):
        files = [self._file("src/core.py", imports=["mypkg.utils"])]
        keep, uncertain = self.clf.classify_batch(files, set())
        assert len(keep) == 1
        assert len(uncertain) == 0

    def test_tool_script_dropped(self):
        files = [self._file("scripts/deploy_prod.py", has_main=True, exports=["main"])]
        keep, uncertain = self.clf.classify_batch(files, set())
        assert len(keep) == 0
        assert len(uncertain) == 0

    def test_uncertain_returned(self):
        files = [self._file("scripts/mystery.py", imports=["requests"])]
        keep, uncertain = self.clf.classify_batch(files, set())
        assert len(keep) == 0
        assert len(uncertain) == 1

    def test_imported_paths_override(self):
        files = [self._file("scripts/deploy_prod.py", has_main=True, exports=["main"])]
        keep, uncertain = self.clf.classify_batch(files, {"scripts/deploy_prod.py"})
        # Imported by project → source_code → kept
        assert len(keep) == 1

    def test_mixed_batch(self):
        files = [
            self._file("core/parser.py", imports=["mypkg.base"]),          # source
            self._file("scripts/deploy_prod.py", has_main=True),            # tool (stem matches)
            self._file("scripts/helper.py", imports=["requests"]),          # uncertain
        ]
        keep, uncertain = self.clf.classify_batch(files, set())
        assert len(keep) == 1
        assert len(uncertain) == 1


# ── is_scripts_like_path ──────────────────────────────────────────────────────

class TestIsScriptsLikePath:
    @pytest.mark.parametrize("path", [
        "scripts/deploy.py",
        "tools/scripts/run.py",
        "src/scripts/helper.py",
    ])
    def test_in_scripts_dir(self, path):
        assert is_scripts_like_path(path) is True

    @pytest.mark.parametrize("path", [
        "core/parser.py",
        "my_scripts.py",          # filename contains scripts but isn't in scripts/
        "scripts_helper/foo.py",  # dir named scripts_helper
        "src/core/main.py",
    ])
    def test_not_in_scripts_dir(self, path):
        assert is_scripts_like_path(path) is False


# ── build_imported_paths ──────────────────────────────────────────────────────

class TestBuildImportedPaths:
    def _make_files(self, *entries):
        """entries: (rel_path, imports_list)"""
        return [
            {
                "rel_path": path,
                "parse_result": {
                    "imports": [{"name": n} for n in imports],
                },
            }
            for path, imports in entries
        ]

    def test_direct_import(self):
        files = self._make_files(
            ("core/parser.py", []),
            ("scripts/run.py", ["core.parser"]),
        )
        imported = build_imported_paths(files, Path("."))
        assert "core/parser.py" in imported

    def test_stem_import(self):
        files = self._make_files(
            ("core/utils.py", []),
            ("main.py", ["utils"]),
        )
        imported = build_imported_paths(files, Path("."))
        assert "core/utils.py" in imported

    def test_no_imports(self):
        files = self._make_files(
            ("core/parser.py", []),
            ("scripts/run.py", []),
        )
        imported = build_imported_paths(files, Path("."))
        assert "core/parser.py" not in imported

    def test_non_py_files_ignored(self):
        files = [{"rel_path": "README.md", "parse_result": {"imports": []}}]
        imported = build_imported_paths(files, Path("."))
        assert "README.md" not in imported

    def test_full_module_path_match(self):
        files = self._make_files(
            ("core/ast/scanner.py", []),
            ("tools/run.py", ["core.ast.scanner"]),
        )
        imported = build_imported_paths(files, Path("."))
        assert "core/ast/scanner.py" in imported
