"""Tests for matrixone_graph.pipeline — import resolution and entity ID helpers."""
import pytest

from matrixone_graph.pipeline import _resolve_import_by_filepath, _make_entity_id


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
