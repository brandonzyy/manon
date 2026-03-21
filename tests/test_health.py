"""Tests for matrixone_graph.health — scoring functions and graph metrics."""
import pytest

from matrixone_graph.health import (
    _entity_module, _is_test_file,
    _score_mc, _score_cd, _score_fi, _score_dc,
    _score_fs, _score_td, _score_mf, _score_re,
    compute_graph_metrics, compute_score, scan_file,
    WEIGHTS,
)
from matrixone_graph.store import CodeGraph, Entity, Relation


# ── Helper function tests ────────────────────────────

class TestEntityModule:
    def test_simple(self):
        assert _entity_module("foo.bar.Baz") == "foo"

    def test_single(self):
        assert _entity_module("foo") == "foo"

    def test_empty(self):
        assert _entity_module("") == ""


class TestIsTestFile:
    @pytest.mark.parametrize("path,expected", [
        ("src/tests/test_foo.py", True),
        ("src/foo.test.ts", True),
        ("src/foo.spec.js", True),
        ("src/__tests__/bar.ts", True),
        ("src/utils/helper.py", False),
        ("src/test/integration.py", True),
        ("tests/test_mod.py", True),
        ("test_standalone.py", True),
        ("foo_test.go", True),
    ])
    def test_patterns(self, path, expected):
        assert _is_test_file(path) == expected


# ── Scoring function tests ───────────────────────────

class TestScoreFunctions:
    def test_score_mc(self):
        assert _score_mc(0.1) == 10
        assert _score_mc(0.3) == 8
        assert _score_mc(0.4) == 6
        assert _score_mc(0.8) == 4

    def test_score_cd(self):
        assert _score_cd(0) == 10
        assert _score_cd(1) == 6
        assert _score_cd(5) == 3

    def test_score_fi(self):
        assert _score_fi(0.03) == 10
        assert _score_fi(0.08) == 8
        assert _score_fi(0.15) == 6
        assert _score_fi(0.5) == 4

    def test_score_dc(self):
        assert _score_dc(0.05) == 10
        assert _score_dc(0.2) == 8
        assert _score_dc(0.4) == 6
        assert _score_dc(0.5) == 4
        assert _score_dc(0.8) == 2

    def test_score_fs(self):
        assert _score_fs(0.03) == 10
        assert _score_fs(0.08) == 8
        assert _score_fs(0.15) == 6
        assert _score_fs(0.3) == 4

    def test_score_td(self):
        assert _score_td(0.5) == 10
        assert _score_td(2.0) == 8
        assert _score_td(5.0) == 6
        assert _score_td(10.0) == 4

    def test_score_mf(self):
        assert _score_mf(0.10) == 10
        assert _score_mf(0.20) == 8
        assert _score_mf(0.30) == 6
        assert _score_mf(0.50) == 4

    def test_score_re(self):
        assert _score_re(0.05) == 10
        assert _score_re(0.15) == 8
        assert _score_re(0.25) == 6
        assert _score_re(0.40) == 4


# ── Integration: compute_graph_metrics ────────────────

class TestComputeGraphMetrics:
    def _build_graph(self) -> CodeGraph:
        g = CodeGraph()
        # Module entities
        g.add_entity(Entity(id="mod_a", kind="module", name="mod_a", file_path="mod_a.py"))
        g.add_entity(Entity(id="mod_b", kind="module", name="mod_b", file_path="mod_b.py"))
        # Functions
        g.add_entity(Entity(id="mod_a.foo", kind="function", name="foo", file_path="mod_a.py", line_start=1, line_end=10))
        g.add_entity(Entity(id="mod_a._bar", kind="function", name="_bar", file_path="mod_a.py", line_start=12, line_end=80))
        g.add_entity(Entity(id="mod_b.baz", kind="function", name="baz", file_path="mod_b.py", line_start=1, line_end=5))
        # Relations
        g.add_relation(Relation(src_id="mod_a.foo", tgt_id="mod_b.baz", kind="calls"))
        g.add_relation(Relation(src_id="mod_a._bar", tgt_id="mod_a.foo", kind="calls"))
        g.add_relation(Relation(src_id="mod_a", tgt_id="mod_b", kind="imports"))
        return g

    def test_metrics_structure(self):
        g = self._build_graph()
        m = compute_graph_metrics(g)
        assert "mc" in m and "cd" in m and "fi" in m and "dc" in m
        assert "fs" in m and "mf" in m and "re" in m
        assert "tc" not in m and "id" not in m
        assert m["entity_count"] == 5
        assert m["relation_count"] == 3

    def test_dead_code_detection(self):
        g = self._build_graph()
        m = compute_graph_metrics(g)
        # mod_a._bar has no callers (zero in-degree), private → dead
        # mod_a.foo is called by _bar → alive
        # mod_b.baz is called by foo → alive
        assert m["dc"]["dead_count"] == 1  # only _bar

    def test_dc_excludes_entry_points(self):
        """Dunder methods, test entities, and classes should not count as dead code."""
        g = CodeGraph()
        g.add_entity(Entity(id="mod", kind="module", name="mod", file_path="mod.py"))
        # Dunder method — excluded
        g.add_entity(Entity(id="mod.Cls.__init__", kind="method", name="__init__", file_path="mod.py"))
        # Class — excluded
        g.add_entity(Entity(id="mod.Cls", kind="class", name="Cls", file_path="mod.py"))
        # Test entity — excluded
        g.add_entity(Entity(id="tests.test_mod.test_foo", kind="function", name="test_foo", file_path="tests/test_mod.py"))
        # Private function with zero in-degree — dead
        g.add_entity(Entity(id="mod._orphan", kind="function", name="_orphan", file_path="mod.py"))
        # Private function with caller — alive
        g.add_entity(Entity(id="mod._used", kind="function", name="_used", file_path="mod.py"))
        g.add_relation(Relation(src_id="mod._orphan", tgt_id="mod._used", kind="calls"))
        m = compute_graph_metrics(g)
        assert m["dc"]["dead_count"] == 1  # only mod._orphan
        assert m["dc"]["excluded_entry_points"] == 3  # __init__, Cls, test_foo
        assert m["dc"]["total"] == 2  # only _orphan + _used are checkable

    def test_dc_excludes_decorated(self):
        """Decorated functions (framework entry points) should not count as dead code."""
        g = CodeGraph()
        g.add_entity(Entity(id="mod", kind="module", name="mod", file_path="mod.py"))
        # Decorated route handler — excluded
        g.add_entity(Entity(id="mod.get_users", kind="function", name="get_users",
                            file_path="mod.py", decorators=["router.get"]))
        # Private function with zero in-degree — dead
        g.add_entity(Entity(id="mod._helper", kind="function", name="_helper", file_path="mod.py"))
        m = compute_graph_metrics(g)
        assert m["dc"]["dead_count"] == 1  # only _helper
        assert m["dc"]["excluded_entry_points"] == 1  # get_users

    def test_dc_excludes_structural_kinds(self):
        """type_alias, interface, property, enum, field, namespace, constructor should not count as dead code."""
        g = CodeGraph()
        g.add_entity(Entity(id="mod", kind="module", name="mod", file_path="src/mod.ts"))
        # Structural kinds — all excluded
        g.add_entity(Entity(id="mod.MyType", kind="type_alias", name="MyType", file_path="src/mod.ts"))
        g.add_entity(Entity(id="mod.IFoo", kind="interface", name="IFoo", file_path="src/mod.ts"))
        g.add_entity(Entity(id="mod.Color", kind="enum", name="Color", file_path="src/mod.ts"))
        g.add_entity(Entity(id="mod.Color.Red", kind="enum_member", name="Red", file_path="src/mod.ts"))
        g.add_entity(Entity(id="mod.Cls.x", kind="property", name="x", file_path="src/mod.ts"))
        g.add_entity(Entity(id="mod.Cls.sdk", kind="field", name="sdk", file_path="src/mod.ts"))
        g.add_entity(Entity(id="mod.Session", kind="namespace", name="Session", file_path="src/mod.ts"))
        g.add_entity(Entity(id="mod.Cls.constructor", kind="constructor", name="constructor", file_path="src/mod.ts"))
        # Public variable — excluded
        g.add_entity(Entity(id="mod.FOO", kind="variable", name="FOO", file_path="src/mod.ts"))
        # Variable (public + private) — excluded (closure access not tracked)
        g.add_entity(Entity(id="mod._secret", kind="variable", name="_secret", file_path="src/mod.ts"))
        # Test infrastructure helper — excluded
        g.add_entity(Entity(id="mod._resetForTesting", kind="function", name="_resetForTesting", file_path="src/mod.ts"))
        # Genuinely private function — checkable
        g.add_entity(Entity(id="mod._helper", kind="function", name="_helper", file_path="src/mod.ts"))
        m = compute_graph_metrics(g)
        assert m["dc"]["dead_count"] == 1  # only _helper
        assert m["dc"]["excluded_entry_points"] == 11  # MyType, IFoo, Color, Red, x, sdk, Session, constructor, FOO, _secret, _resetForTesting

    def test_oversized_functions(self):
        g = self._build_graph()
        m = compute_graph_metrics(g)
        # bar: 80-12=68 lines > 50 → oversized
        # foo: 10-1=9 lines → ok
        # baz: 5-1=4 lines → ok
        assert m["fs"]["oversized"] == 1

    def test_no_cycles(self):
        g = self._build_graph()
        m = compute_graph_metrics(g)
        assert m["cd"]["cycles"] == 0

    def test_mf_tiny_modules(self):
        """Files with < 3 function/method/class entities should count as tiny."""
        g = CodeGraph()
        # mod_a has 2 functions → tiny
        g.add_entity(Entity(id="mod_a", kind="module", name="mod_a", file_path="mod_a.py"))
        g.add_entity(Entity(id="mod_a.f1", kind="function", name="f1", file_path="mod_a.py"))
        g.add_entity(Entity(id="mod_a.f2", kind="function", name="f2", file_path="mod_a.py"))
        # mod_b has 3 functions → not tiny
        g.add_entity(Entity(id="mod_b", kind="module", name="mod_b", file_path="mod_b.py"))
        g.add_entity(Entity(id="mod_b.f1", kind="function", name="f1", file_path="mod_b.py"))
        g.add_entity(Entity(id="mod_b.f2", kind="function", name="f2", file_path="mod_b.py"))
        g.add_entity(Entity(id="mod_b.f3", kind="function", name="f3", file_path="mod_b.py"))
        m = compute_graph_metrics(g)
        assert m["mf"]["tiny_modules"] == 1  # mod_a
        assert m["mf"]["total_modules"] == 2

    def test_re_barrel_modules(self):
        """Modules with imports but no functions should be detected as barrels."""
        g = CodeGraph()
        # barrel: has imports-out but no function/method/class
        g.add_entity(Entity(id="barrel", kind="module", name="barrel", file_path="barrel.py"))
        # real module: has a function
        g.add_entity(Entity(id="real", kind="module", name="real", file_path="real.py"))
        g.add_entity(Entity(id="real.foo", kind="function", name="foo", file_path="real.py"))
        # barrel imports from real
        g.add_relation(Relation(src_id="barrel", tgt_id="real", kind="imports"))
        m = compute_graph_metrics(g)
        assert m["re"]["barrel_modules"] == 1  # barrel
        assert m["re"]["total_modules"] == 2
        assert "single_impl_interfaces" not in m["re"]  # removed


class TestComputeScore:
    def test_perfect_score(self):
        metrics = {
            "mc": {"ratio": 0.1}, "cd": {"cycles": 0},
            "fi": {"ratio": 0.01}, "dc": {"ratio": 0.01},
            "fs": {"ratio": 0.01}, "mf": {"ratio": 0.05},
            "re": {"ratio": 0.05},
            "entity_count": 100, "relation_count": 200,
        }
        result = compute_score(metrics)
        assert result["score"] == 100.0
        assert result["grade"] == "A"

    def test_low_score(self):
        metrics = {
            "mc": {"ratio": 0.9}, "cd": {"cycles": 5},
            "fi": {"ratio": 0.5}, "dc": {"ratio": 0.5},
            "fs": {"ratio": 0.5}, "mf": {"ratio": 0.5},
            "re": {"ratio": 0.5},
            "entity_count": 100, "relation_count": 200,
        }
        result = compute_score(metrics, {"todos": 100, "any_count": 50, "total_lines": 1000})
        assert result["score"] < 50
        assert result["grade"] == "D"

    def test_dimensions_count(self):
        metrics = {
            "mc": {"ratio": 0.3}, "cd": {"cycles": 0},
            "fi": {"ratio": 0.05}, "dc": {"ratio": 0.1},
            "fs": {"ratio": 0.05}, "mf": {"ratio": 0.10},
            "re": {"ratio": 0.10},
            "entity_count": 50, "relation_count": 100,
        }
        result = compute_score(metrics)
        assert len(result["dimensions"]) == 8

    def test_weights_sum_to_100(self):
        assert sum(WEIGHTS.values()) == 100
