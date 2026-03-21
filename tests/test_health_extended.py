"""Extended tests for matrixone_graph/health.py — scoring functions, edge cases, boundaries."""
import pytest
from pathlib import Path

from matrixone_graph.health import (
    _entity_module, _is_test_file, _compute_mc, _compute_cd,
    _compute_fi, _compute_dc, _compute_fs, _compute_mf, _compute_re,
    _score_mc, _score_cd, _score_fi, _score_dc,
    _score_fs, _score_td, _score_mf, _score_re,
    scan_file, scan_directory_debt, compute_score, compute_graph_metrics,
    WEIGHTS, _BUILTINS,
)
from matrixone_graph.store import CodeGraph, Entity, Relation


class TestEntityModule:
    def test_simple(self):
        assert _entity_module("foo.bar.Baz") == "foo"

    def test_single(self):
        assert _entity_module("foo") == "foo"

    def test_empty(self):
        assert _entity_module("") == ""


class TestIsTestFile:
    def test_test_prefix(self):
        assert _is_test_file("tests/test_foo.py")

    def test_test_dir(self):
        assert _is_test_file("tests/helpers.py")

    def test_spec_file(self):
        assert _is_test_file("src/foo.spec.ts")

    def test_test_suffix(self):
        assert _is_test_file("src/foo_test.py")

    def test_regular_file(self):
        assert not _is_test_file("src/foo.py")

    def test_windows_path(self):
        assert _is_test_file("tests\\test_foo.py")


class TestScoreFunctions:
    """Test all scoring boundary conditions."""

    def test_score_mc_boundaries(self):
        assert _score_mc(0.0) == 10
        assert _score_mc(0.2) == 10
        assert _score_mc(0.21) == 8
        assert _score_mc(0.35) == 8
        assert _score_mc(0.36) == 6
        assert _score_mc(0.5) == 6
        assert _score_mc(0.51) == 4

    def test_score_cd_boundaries(self):
        assert _score_cd(0) == 10
        assert _score_cd(1) == 6
        assert _score_cd(2) == 6
        assert _score_cd(3) == 3

    def test_score_fi_boundaries(self):
        assert _score_fi(0.0) == 10
        assert _score_fi(0.05) == 10
        assert _score_fi(0.06) == 8
        assert _score_fi(0.1) == 8
        assert _score_fi(0.11) == 6
        assert _score_fi(0.21) == 4

    def test_score_dc_boundaries(self):
        assert _score_dc(0.0) == 10
        assert _score_dc(0.1) == 10
        assert _score_dc(0.11) == 8
        assert _score_dc(0.25) == 8
        assert _score_dc(0.26) == 6
        assert _score_dc(0.46) == 4
        assert _score_dc(0.66) == 2

    def test_score_fs_boundaries(self):
        assert _score_fs(0.0) == 10
        assert _score_fs(0.05) == 10
        assert _score_fs(0.06) == 8
        assert _score_fs(0.1) == 8
        assert _score_fs(0.11) == 6
        assert _score_fs(0.21) == 4

    def test_score_td_boundaries(self):
        assert _score_td(0.0) == 10
        assert _score_td(1.0) == 10
        assert _score_td(1.1) == 8
        assert _score_td(3.0) == 8
        assert _score_td(3.1) == 6
        assert _score_td(6.1) == 4

    def test_score_mf_boundaries(self):
        assert _score_mf(0.0) == 10
        assert _score_mf(0.15) == 10
        assert _score_mf(0.16) == 8
        assert _score_mf(0.25) == 8
        assert _score_mf(0.26) == 6
        assert _score_mf(0.40) == 6
        assert _score_mf(0.41) == 4

    def test_score_re_boundaries(self):
        assert _score_re(0.0) == 10
        assert _score_re(0.10) == 10
        assert _score_re(0.11) == 8
        assert _score_re(0.20) == 8
        assert _score_re(0.21) == 6
        assert _score_re(0.35) == 6
        assert _score_re(0.36) == 4


class TestComputeMetricHelpers:
    def test_compute_mc_empty(self):
        result = _compute_mc([])
        assert result["ratio"] == 0
        assert result["cross_module"] == 0

    def test_compute_cd_no_cycles(self):
        result = _compute_cd([])
        assert result["cycles"] == 0

    def test_compute_fi_empty(self):
        result = _compute_fi([])
        assert result["ratio"] == 0

    def test_compute_fs_empty(self):
        result = _compute_fs({})
        assert result["oversized"] == 0
        assert result["total"] == 0

    def test_compute_mf_empty(self):
        result = _compute_mf({})
        assert result["ratio"] == 0.0
        assert result["tiny_modules"] == 0

    def test_compute_re_empty(self):
        result = _compute_re({}, [])
        assert result["ratio"] == 0.0
        assert result["barrel_modules"] == 0
        assert result["total_modules"] == 0


class TestScanFile:
    def test_scan_python_file(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("# TODO: fix this\nx = 1\n# FIXME: broken\n")
        result = scan_file(f)
        assert result["todos"] == 2

    def test_scan_test_file(self, tmp_path):
        f = tmp_path / "test_foo.py"
        f.write_text("# TODO: add more tests\n")
        result = scan_file(f)
        assert result["todos"] == 0  # test files excluded

    def test_scan_ts_any(self, tmp_path):
        f = tmp_path / "code.ts"
        f.write_text("const x: any = 1;\nconst y: any = 2;\n")
        result = scan_file(f)
        assert result["any_count"] == 2

    def test_scan_nonexistent(self, tmp_path):
        result = scan_file(tmp_path / "nope.py")
        assert result == {}

    def test_scan_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        result = scan_file(f)
        assert result["todos"] == 0


class TestScanDirectoryDebt:
    def test_scan_directory(self, tmp_path):
        (tmp_path / "a.py").write_text("# TODO: fix\nx = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        result = scan_directory_debt(tmp_path)
        assert result["todos"] >= 1
        assert result["total_lines"] >= 3

    def test_scan_directory_respects_index_excludes(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("# TODO: keep\nx = 1\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("# TODO: ignored in tests\n")
        (tmp_path / ".venv.bak-123").mkdir()
        (tmp_path / ".venv.bak-123" / "site.py").write_text("# TODO: ignored in backup env\n")

        result = scan_directory_debt(tmp_path)

        assert result["todos"] == 1
        assert result["total_lines"] < 10


class TestComputeScore:
    def test_perfect_score(self):
        metrics = {
            "mc": {"ratio": 0.0}, "cd": {"cycles": 0},
            "fi": {"ratio": 0.0}, "dc": {"ratio": 0.0},
            "fs": {"ratio": 0.0}, "mf": {"ratio": 0.0},
            "re": {"ratio": 0.0},
            "entity_count": 100, "relation_count": 200,
        }
        result = compute_score(metrics, {"todos": 0, "any_count": 0, "total_lines": 1000})
        assert result["score"] == 100.0

    def test_worst_score(self):
        metrics = {
            "mc": {"ratio": 1.0}, "cd": {"cycles": 10},
            "fi": {"ratio": 1.0}, "dc": {"ratio": 1.0},
            "fs": {"ratio": 1.0}, "mf": {"ratio": 1.0},
            "re": {"ratio": 1.0},
            "entity_count": 100, "relation_count": 200,
        }
        result = compute_score(metrics, {"todos": 100, "any_count": 100, "total_lines": 100})
        assert result["score"] < 50

    def test_no_debt_metrics(self):
        metrics = {
            "mc": {"ratio": 0.3}, "cd": {"cycles": 0},
            "fi": {"ratio": 0.05}, "dc": {"ratio": 0.1},
            "fs": {"ratio": 0.05}, "mf": {"ratio": 0.10},
            "re": {"ratio": 0.10},
            "entity_count": 50, "relation_count": 100,
        }
        result = compute_score(metrics)
        assert 0 <= result["score"] <= 100


class TestWeights:
    def test_weights_sum_to_100(self):
        assert sum(WEIGHTS.values()) == 100

    def test_all_dimensions_present(self):
        for dim in ("mc", "cd", "fi", "dc", "fs", "td", "mf", "re"):
            assert dim in WEIGHTS


class TestBuiltins:
    def test_common_builtins(self):
        assert "len" in _BUILTINS
        assert "print" in _BUILTINS
        assert "isinstance" in _BUILTINS

    def test_not_in_builtins(self):
        assert "my_function" not in _BUILTINS
