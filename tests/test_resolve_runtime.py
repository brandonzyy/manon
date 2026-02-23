"""Tests for runtime path resolution (JS/TS → entity IDs)."""

from __future__ import annotations

import pytest

from matrixone_graph.resolve_runtime import (
    _path_to_module_id,
    _is_project_file,
    resolve_js_edges,
    _expand_to_symbols,
)
from matrixone_graph.store import CodeGraph, Entity


ROOT = "/home/user/donnie/"


class TestPathToModuleId:
    def test_ts_file(self):
        assert _path_to_module_id("/home/user/donnie/electron/main.ts", ROOT) == "electron.main"

    def test_tsx_file(self):
        assert _path_to_module_id("/home/user/donnie/renderer/App.tsx", ROOT) == "renderer.App"

    def test_nested_path(self):
        assert _path_to_module_id(
            "/home/user/donnie/electron/orchestrator/intent-detector.ts", ROOT
        ) == "electron.orchestrator.intent-detector"

    def test_index_file(self):
        assert _path_to_module_id("/home/user/donnie/src/utils/index.ts", ROOT) == "src.utils"

    def test_js_extension(self):
        assert _path_to_module_id("/home/user/donnie/lib/helper.js", ROOT) == "lib.helper"

    def test_dts_extension(self):
        assert _path_to_module_id("/home/user/donnie/types/api.d.ts", ROOT) == "types.api"

    def test_python_extension(self):
        assert _path_to_module_id("/home/user/donnie/scripts/run.py", ROOT) == "scripts.run"

    def test_outside_project(self):
        assert _path_to_module_id("/other/path/file.ts", ROOT) is None

    def test_no_extension(self):
        # Files without known extensions keep their full name
        assert _path_to_module_id("/home/user/donnie/Makefile", ROOT) == "Makefile"


class TestIsProjectFile:
    def test_project_file(self):
        assert _is_project_file("/home/user/donnie/src/main.ts", ROOT)

    def test_node_modules(self):
        assert not _is_project_file("/home/user/donnie/node_modules/lodash/index.js", ROOT)

    def test_outside_project(self):
        assert not _is_project_file("/other/path/file.ts", ROOT)

    def test_dist_dir(self):
        assert not _is_project_file("/home/user/donnie/dist/main.js", ROOT)


class TestResolveJsEdges:
    def test_basic_resolution(self):
        raw = [
            {"from": "/home/user/donnie/electron/main.ts",
             "to": "/home/user/donnie/electron/utils.ts"},
            {"from": "/home/user/donnie/electron/main.ts",
             "to": "/home/user/donnie/electron/utils.ts"},  # duplicate
        ]
        result = resolve_js_edges(raw, "/home/user/donnie")
        assert result == {"electron.main->electron.utils": 2}

    def test_filters_node_modules(self):
        raw = [
            {"from": "/home/user/donnie/electron/main.ts",
             "to": "/home/user/donnie/node_modules/electron/index.js"},
        ]
        result = resolve_js_edges(raw, "/home/user/donnie")
        assert result == {}

    def test_filters_self_imports(self):
        raw = [
            {"from": "/home/user/donnie/src/index.ts",
             "to": "/home/user/donnie/src/index.ts"},
        ]
        result = resolve_js_edges(raw, "/home/user/donnie")
        assert result == {}

    def test_cross_directory(self):
        raw = [
            {"from": "/home/user/donnie/electron/main.ts",
             "to": "/home/user/donnie/renderer/App.tsx"},
        ]
        result = resolve_js_edges(raw, "/home/user/donnie")
        assert result == {"electron.main->renderer.App": 1}

    def test_with_graph_expansion(self):
        """When graph is provided, module→module edges expand to module→symbol."""
        graph = CodeGraph()
        graph.add_entity(Entity(
            id="electron.main", kind="module", name="electron.main",
        ))
        graph.add_entity(Entity(
            id="electron.utils.formatDate", kind="function",
            name="formatDate", file_path="electron/utils.ts",
        ))
        graph.add_entity(Entity(
            id="electron.utils.parseUrl", kind="function",
            name="parseUrl", file_path="electron/utils.ts",
        ))
        raw = [
            {"from": "/home/user/donnie/electron/main.ts",
             "to": "/home/user/donnie/electron/utils.ts"},
        ]
        result = resolve_js_edges(raw, "/home/user/donnie", graph=graph)
        # Should expand to symbol-level edges
        assert "electron.main->electron.utils.formatDate" in result
        assert "electron.main->electron.utils.parseUrl" in result
        assert "electron.main->electron.utils" not in result

    def test_windows_paths(self):
        raw = [
            {"from": "C:\\Users\\dev\\donnie\\electron\\main.ts",
             "to": "C:\\Users\\dev\\donnie\\electron\\utils.ts"},
        ]
        result = resolve_js_edges(raw, "C:\\Users\\dev\\donnie")
        assert result == {"electron.main->electron.utils": 1}

    def test_empty_input(self):
        assert resolve_js_edges([], "/home/user/donnie") == {}

    def test_missing_fields(self):
        raw = [{"from": "/home/user/donnie/a.ts"}, {"to": "/home/user/donnie/b.ts"}]
        assert resolve_js_edges(raw, "/home/user/donnie") == {}


class TestExpandToSymbols:
    def test_expands_known_modules(self):
        module_edges = {"a->b": 3}
        entity_modules = {"b": ["b.foo", "b.bar"]}
        result = _expand_to_symbols(module_edges, entity_modules)
        assert result == {"a->b.foo": 3, "a->b.bar": 3}

    def test_keeps_unknown_modules(self):
        module_edges = {"a->c": 1}
        entity_modules = {"b": ["b.foo"]}
        result = _expand_to_symbols(module_edges, entity_modules)
        assert result == {"a->c": 1}
