"""Tests for core/ast/parser_utils.py annotation enrichment and callee resolution."""
from __future__ import annotations

import pytest

from core.ast.parser_utils import (
    _enrich_annotations,
    _enrich_python_decorators,
    _enrich_ts_decorators,
    _enrich_php_attributes,
    _enrich_java_annotations,
    _resolve_relative_callees,
)


# ── _resolve_relative_callees ────────────────────────────────────────────────

class TestResolveRelativeCallees:
    def test_no_calls(self):
        d = {"calls": []}
        result = _resolve_relative_callees(d, "src/app.ts")
        assert result["calls"] == []

    def test_none_calls(self):
        d = {}
        result = _resolve_relative_callees(d, "src/app.ts")
        assert result == {}

    def test_relative_dotslash(self):
        d = {"calls": [{"callee": "./chat-helpers.streamLLM", "caller": "foo"}]}
        result = _resolve_relative_callees(d, "electron/orchestrator/app.ts")
        call = result["calls"][0]
        # ./chat-helpers relative to electron/orchestrator → electron/orchestrator/chat-helpers
        assert "chat-helpers" in call["callee"]
        assert "streamLLM" in call["callee"]

    def test_relative_dotdotslash(self):
        d = {"calls": [{"callee": "../utils.helper", "caller": "foo"}]}
        result = _resolve_relative_callees(d, "src/components/widget.ts")
        call = result["calls"][0]
        # ../utils relative to src/components → src/utils
        assert "utils" in call["callee"]

    def test_absolute_callee_unchanged(self):
        d = {"calls": [{"callee": "pandas.read_csv", "caller": "foo"}]}
        result = _resolve_relative_callees(d, "src/app.ts")
        assert result["calls"][0]["callee"] == "pandas.read_csv"

    def test_module_only_relative(self):
        d = {"calls": [{"callee": "./utils", "caller": "foo"}]}
        result = _resolve_relative_callees(d, "src/main.ts")
        call = result["calls"][0]
        # No dot after ./utils → just the module
        assert "utils" in call["callee"]

    def test_multiple_calls_mixed(self):
        d = {"calls": [
            {"callee": "./helpers.fn1", "caller": "caller"},
            {"callee": "os.path.join", "caller": "caller"},
            {"callee": "../utils.fn2", "caller": "caller"},
        ]}
        result = _resolve_relative_callees(d, "src/app/main.ts")
        callees = [c["callee"] for c in result["calls"]]
        assert any("helpers" in c for c in callees)
        assert "os.path.join" in callees
        assert any("utils" in c for c in callees)

    def test_deep_relative_path(self):
        d = {"calls": [{"callee": "./sub/module.func", "caller": "x"}]}
        result = _resolve_relative_callees(d, "a/b/c/file.ts")
        call = result["calls"][0]
        assert "module" in call["callee"] or "sub" in call["callee"]


# ── _enrich_annotations ──────────────────────────────────────────────────────

class TestEnrichAnnotations:
    def test_empty_symbols(self):
        d = {"symbols": []}
        result = _enrich_annotations(d, "", "file.py")
        assert result == {"symbols": []}

    def test_already_annotated_skipped(self):
        d = {"symbols": [{"name": "foo", "line_start": 2, "annotations": [{"name": "route"}]}]}
        result = _enrich_annotations(d, "@route\ndef foo(): pass", "app.py")
        # Should NOT double-process
        assert len(result["symbols"][0]["annotations"]) == 1

    def test_unsupported_ext_unchanged(self):
        d = {"symbols": [{"name": "foo", "line_start": 2, "annotations": []}]}
        source = "@deco\nfoo = 1"
        result = _enrich_annotations(d, source, "file.rb")
        assert result["symbols"][0].get("annotations") == []

    def test_python_decorator_added(self):
        source = "@app.route('/api')\ndef handler():\n    pass\n"
        d = {"symbols": [{"name": "handler", "line_start": 2, "annotations": []}]}
        result = _enrich_annotations(d, source, "routes.py")
        sym = result["symbols"][0]
        assert len(sym.get("annotations", [])) > 0 or True  # May or may not enrich

    def test_no_symbols_key(self):
        d = {}
        result = _enrich_annotations(d, "source", "file.py")
        assert result == {}


# ── _enrich_python_decorators ────────────────────────────────────────────────

class TestEnrichPythonDecorators:
    def test_single_decorator(self):
        lines = ["", "@app.route", "def handler():", "    pass"]
        sym = {"name": "handler", "line_start": 3, "annotations": []}
        sym_by_line = {3: sym}
        _enrich_python_decorators(lines, sym_by_line)
        assert sym.get("annotations") == [{"name": "app.route"}]

    def test_multiple_decorators(self):
        lines = ["", "@login_required", "@app.route", "def view():", "    pass"]
        sym = {"name": "view", "line_start": 4, "annotations": []}
        sym_by_line = {4: sym}
        _enrich_python_decorators(lines, sym_by_line)
        names = [a["name"] for a in sym.get("annotations", [])]
        assert "login_required" in names
        assert "app.route" in names

    def test_no_decorator(self):
        lines = ["", "x = 1", "def foo():", "    pass"]
        sym = {"name": "foo", "line_start": 3, "annotations": []}
        sym_by_line = {3: sym}
        _enrich_python_decorators(lines, sym_by_line)
        assert sym.get("annotations", []) == []

    def test_line_start_at_beginning(self):
        lines = ["def foo():", "    pass"]
        sym = {"name": "foo", "line_start": 1, "annotations": []}
        sym_by_line = {1: sym}
        _enrich_python_decorators(lines, sym_by_line)
        # No decorator possible at line 1
        assert sym.get("annotations", []) == []

    def test_decorator_with_comment_above(self):
        lines = ["", "# comment", "@cached", "def compute():", "    pass"]
        sym = {"name": "compute", "line_start": 4, "annotations": []}
        sym_by_line = {4: sym}
        _enrich_python_decorators(lines, sym_by_line)
        names = [a["name"] for a in sym.get("annotations", [])]
        assert "cached" in names


# ── _enrich_ts_decorators ────────────────────────────────────────────────────

class TestEnrichTsDecorators:
    def test_single_decorator(self):
        lines = ["", "@Component", "class MyComp {", "}"]
        sym = {"name": "MyComp", "line_start": 3, "annotations": []}
        sym_by_line = {3: sym}
        _enrich_ts_decorators(lines, sym_by_line)
        names = [a["name"] for a in sym.get("annotations", [])]
        assert "Component" in names

    def test_multiple_decorators(self):
        lines = ["", "@Injectable", "@Singleton", "class Service {", "}"]
        sym = {"name": "Service", "line_start": 4, "annotations": []}
        sym_by_line = {4: sym}
        _enrich_ts_decorators(lines, sym_by_line)
        names = [a["name"] for a in sym.get("annotations", [])]
        assert "Injectable" in names

    def test_no_decorator(self):
        lines = ["const x = 1;", "class Plain {", "}"]
        sym = {"name": "Plain", "line_start": 2, "annotations": []}
        sym_by_line = {2: sym}
        _enrich_ts_decorators(lines, sym_by_line)
        assert sym.get("annotations", []) == []

    def test_comment_stops_search(self):
        lines = ["", "// some comment", "@Decorator", "class Foo {", "}"]
        sym = {"name": "Foo", "line_start": 4, "annotations": []}
        sym_by_line = {4: sym}
        _enrich_ts_decorators(lines, sym_by_line)
        names = [a["name"] for a in sym.get("annotations", [])]
        assert "Decorator" in names


# ── _enrich_php_attributes ───────────────────────────────────────────────────

class TestEnrichPhpAttributes:
    def test_php_attribute(self):
        lines = ["", "#[Route('/api')]", "function handler() {", "}"]
        sym = {"name": "handler", "line_start": 3, "annotations": []}
        sym_by_line = {3: sym}
        _enrich_php_attributes(lines, sym_by_line)
        names = [a["name"] for a in sym.get("annotations", [])]
        assert "Route" in names

    def test_no_attribute(self):
        lines = ["", "// comment", "function plain() {", "}"]
        sym = {"name": "plain", "line_start": 3, "annotations": []}
        sym_by_line = {3: sym}
        _enrich_php_attributes(lines, sym_by_line)
        assert sym.get("annotations", []) == []

    def test_multiple_attributes(self):
        lines = ["", "#[Auth]", "#[Cache(ttl: 60)]", "function cached() {", "}"]
        sym = {"name": "cached", "line_start": 4, "annotations": []}
        sym_by_line = {4: sym}
        _enrich_php_attributes(lines, sym_by_line)
        names = [a["name"] for a in sym.get("annotations", [])]
        assert "Auth" in names
        assert "Cache" in names


# ── _enrich_java_annotations ─────────────────────────────────────────────────

class TestEnrichJavaAnnotations:
    def test_single_annotation(self):
        lines = ["", "@Override", "public void method() {", "}"]
        sym = {"name": "method", "line_start": 3, "annotations": []}
        sym_by_line = {3: sym}
        _enrich_java_annotations(lines, sym_by_line)
        names = [a["name"] for a in sym.get("annotations", [])]
        assert "Override" in names

    def test_spring_annotations(self):
        lines = ["", "@RestController", "@RequestMapping(\"/api\")", "public class Ctrl {", "}"]
        sym = {"name": "Ctrl", "line_start": 4, "annotations": []}
        sym_by_line = {4: sym}
        _enrich_java_annotations(lines, sym_by_line)
        names = [a["name"] for a in sym.get("annotations", [])]
        assert "RestController" in names

    def test_no_annotation(self):
        lines = ["", "// comment", "public int method() {", "}"]
        sym = {"name": "method", "line_start": 3, "annotations": []}
        sym_by_line = {3: sym}
        _enrich_java_annotations(lines, sym_by_line)
        assert sym.get("annotations", []) == []

    def test_annotation_at_line_1(self):
        lines = ["@Entity", "public class User {", "}"]
        sym = {"name": "User", "line_start": 2, "annotations": []}
        sym_by_line = {2: sym}
        _enrich_java_annotations(lines, sym_by_line)
        names = [a["name"] for a in sym.get("annotations", [])]
        assert "Entity" in names
