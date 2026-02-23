"""Tests for matrixone_graph.pipeline — import resolution and entity ID helpers."""
from pathlib import Path

import pytest

from matrixone_graph.pipeline import _resolve_import_by_filepath, _make_entity_id, _map_parse_result


class TestMakeEntityId:
    def test_with_module(self):
        assert _make_entity_id("foo.bar", "Baz") == "foo.bar.Baz"

    def test_empty_module(self):
        assert _make_entity_id("", "Baz") == "Baz"


class TestResolveImportByFilepath:
    def test_relative_sibling(self):
        result = _resolve_import_by_filepath(
            "src/orchestrator/skill-router.ts", "./tool-executor",
        )
        assert result == "src.orchestrator.tool-executor"

    def test_relative_parent(self):
        result = _resolve_import_by_filepath(
            "src/orchestrator/skill-router.ts", "../utils/helper",
        )
        assert result == "src.utils.helper"

    def test_absolute_import(self):
        result = _resolve_import_by_filepath(
            "src/foo.ts", "lodash",
        )
        assert result == "lodash"

    def test_dotted_filename(self):
        # Filenames with dots should be treated as single component
        result = _resolve_import_by_filepath(
            "tests/intent-detector.test.ts", "./mock-data",
        )
        assert result == "tests.mock-data"

    def test_deep_relative(self):
        result = _resolve_import_by_filepath(
            "a/b/c/d.ts", "../../x/y",
        )
        assert result == "a.x.y"

    def test_root_level(self):
        result = _resolve_import_by_filepath(
            "index.ts", "./utils",
        )
        assert result == "utils"


class TestMapParseResultDecorators:
    """Ensure decorators are extracted from both Annotation objects and dicts."""

    def _make_pr(self, annotations):
        from codeindex.parser import ParseResult, Symbol
        sym = Symbol(
            name="my_route", kind="function", signature="def my_route():",
            docstring="", line_start=1, line_end=5, annotations=annotations,
        )
        return ParseResult(
            path=Path("src/app.py"), symbols=[sym], imports=[],
            inheritances=[], calls=[], module_docstring="",
            namespace="", error=None, file_lines=6,
        )

    def test_annotation_objects(self):
        from codeindex.parser import Annotation
        pr = self._make_pr([Annotation(name="app.get", arguments={"path": "/"})])
        entities, _ = _map_parse_result(pr, "src.app")
        func_ent = [e for e in entities if e.name == "my_route"][0]
        assert func_ent.decorators == ["app.get"]

    def test_annotation_dicts(self):
        pr = self._make_pr([{"name": "app.get", "arguments": {"path": "/"}}])
        entities, _ = _map_parse_result(pr, "src.app")
        func_ent = [e for e in entities if e.name == "my_route"][0]
        assert func_ent.decorators == ["app.get"]

    def test_no_annotations(self):
        pr = self._make_pr([])
        entities, _ = _map_parse_result(pr, "src.app")
        func_ent = [e for e in entities if e.name == "my_route"][0]
        assert func_ent.decorators == []
