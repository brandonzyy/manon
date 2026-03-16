"""Tests for the core AST helpers."""
from pathlib import Path

from codeindex.scanner import should_exclude

from core.ast import (
    PROJECTS_DIR,
    PROJECTS_FILE,
    count_scannable_files,
    find_project_by_repo_id,
    get_project,
    load_projects,
    save_projects,
    set_custom_excludes,
    set_project,
)
from core.ast.analysis import (
    collect_directory_signals,
    needs_smart_analysis_refresh,
    preview_project_structure,
    smart_analysis_signature,
)
from core.ast.config import _load_scan_config, get_always_exclude, get_auto_exclude_patterns


class TestProjectRegistry:
    def test_load_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.ast.project.PROJECTS_FILE", tmp_path / "missing.json")
        assert load_projects() == {"projects": {}}

    def test_save_and_lookup(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.ast.project.PROJECTS_FILE", tmp_path / "projects.json")
        monkeypatch.setattr("core.ast.project.PROJECTS_DIR", tmp_path)
        set_project(str(tmp_path), {"repo_id": "r1", "name": "demo"})
        assert get_project(str(tmp_path))["repo_id"] == "r1"
        found = find_project_by_repo_id("r1")
        assert found is not None
        assert found[1]["name"] == "demo"

    def test_save_projects_persists(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.ast.project.PROJECTS_FILE", tmp_path / "projects.json")
        monkeypatch.setattr("core.ast.project.PROJECTS_DIR", tmp_path)
        save_projects({"projects": {"x": {"repo_id": "a"}}})
        assert load_projects()["projects"]["x"]["repo_id"] == "a"


class TestScanConfig:
    def test_auto_exclude_generated_dirs(self, tmp_path):
        (tmp_path / ".venv.bak-20260315-015446").mkdir()
        (tmp_path / "indexes").mkdir()
        (tmp_path / "src").mkdir()
        patterns = get_auto_exclude_patterns(str(tmp_path))
        assert "**/.venv.bak-20260315-015446/**" in patterns
        assert "**/indexes/**" in patterns
        assert "**/src/**" not in patterns

    def test_load_scan_config_excludes_generated_and_tests(self, tmp_path):
        (tmp_path / ".venv.bak-20260315-015446").mkdir()
        (tmp_path / ".venv.bak-20260315-015446" / "site.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_sample.py").write_text("def test_ok(): pass", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1", encoding="utf-8")

        config, root, _ = _load_scan_config(str(tmp_path))
        assert should_exclude(tmp_path / ".venv.bak-20260315-015446", config.exclude, root)
        assert should_exclude(tmp_path / "tests", config.exclude, root)
        assert not should_exclude(tmp_path / "src", config.exclude, root)

    def test_set_custom_excludes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.ast.project.PROJECTS_FILE", tmp_path / "projects.json")
        monkeypatch.setattr("core.ast.project.PROJECTS_DIR", tmp_path)
        set_project(str(tmp_path), {"repo_id": "r1", "name": "demo"})
        set_custom_excludes(str(tmp_path), ["**/data/**"])
        assert "**/data/**" in get_project(str(tmp_path))["custom_excludes"]

    def test_always_exclude_contains_core_patterns(self):
        always = get_always_exclude()
        assert "**/.git/**" in always
        assert "**/node_modules/**" in always


class TestAnalysisSignals:
    def test_collect_directory_signals_omits_generated_dirs(self, tmp_path):
        (tmp_path / ".venv.bak-20260315-015446").mkdir()
        (tmp_path / ".venv.bak-20260315-015446" / "site.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "indexes").mkdir()
        (tmp_path / "indexes" / "cache.json").write_text("{}", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1", encoding="utf-8")

        signals = collect_directory_signals(str(tmp_path))
        assert "src" in signals["directories"]
        assert ".venv.bak-20260315-015446" not in signals["directories"]
        assert "indexes" not in signals["directories"]

    def test_smart_analysis_signature_refresh(self, tmp_path):
        (tmp_path / "src").mkdir()
        current = smart_analysis_signature(str(tmp_path))
        assert not needs_smart_analysis_refresh(
            str(tmp_path),
            {"smart_analysis_done": True, "smart_analysis_signature": current},
        )
        assert needs_smart_analysis_refresh(str(tmp_path), {"smart_analysis_done": True})
        (tmp_path / "web").mkdir()
        assert needs_smart_analysis_refresh(
            str(tmp_path),
            {"smart_analysis_done": True, "smart_analysis_signature": current},
        )

    def test_preview_project_structure(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1", encoding="utf-8")
        preview = preview_project_structure(str(tmp_path))
        assert "src/" in preview


class TestScanner:
    def test_count_scannable_files(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "b.py").write_text("y = 2", encoding="utf-8")
        (tmp_path / "README.md").write_text("# hi", encoding="utf-8")
        assert count_scannable_files(str(tmp_path)) >= 2
