"""L1 判据的解释器不许随便挑 —— check_l1.py 的环境不变量。

产品依赖在解析路径上时 mypy 换一套解析结果（实测多报 6 条 import-untyped），
CI 刻意在装产品依赖之前跑 L1。裸 python3 跑出来的红与 CI 的红不是同一件事，
而照着那条红 --regenerate 会把幻影条目写进 baseline。

这份用例判三件：哨兵指的是真产品依赖（不是随口写的模块名）、判定函数本身
纯（探针可注入，不看跑用例时的环境）、门禁在真解释器上确实拒。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_l1.py"
L1_VENV_PY = Path.home() / ".cache" / "manon-l1-venv" / "bin" / "python"

_spec = importlib.util.spec_from_file_location("_check_l1", SCRIPT)
assert _spec and _spec.loader
check_l1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_l1)


def _requirements(*rel: str) -> str:
    return "\n".join((ROOT / r).read_text(encoding="utf-8").lower()
                     for r in rel if (ROOT / r).exists())


def _run(args: list[str], env_extra: dict[str, str] | None = None,
         python: Path | None = None) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([str(python or sys.executable), str(SCRIPT), *args],
                          cwd=ROOT, capture_output=True, text=True,
                          timeout=900, env=env)


class TestSentinelsNameRealProductDeps:
    """哨兵指错人时红的是这一格，不是那道门禁开始乱拒。"""

    def test_sentinel_list_is_not_empty(self) -> None:
        assert check_l1.PRODUCT_ONLY, "哨兵表空 = 这道门禁永远放行"

    @pytest.mark.parametrize("dist", [d for _m, d in check_l1.PRODUCT_ONLY])
    def test_declared_in_product_requirements(self, dist: str) -> None:
        text = _requirements("requirements.txt", "manon_mcp/requirements.txt")
        assert dist.lower() in text, f"{dist} 不在产品依赖表里，它不能当哨兵"

    @pytest.mark.parametrize("dist", [d for _m, d in check_l1.PRODUCT_ONLY])
    def test_absent_from_l1_toolchain(self, dist: str) -> None:
        text = _requirements("scripts/requirements-l1.txt")
        assert dist.lower() not in text, f"{dist} 也是 L1 工具链的依赖，拿它当哨兵会误伤 CI"


class TestContaminatedIsPure:
    """探针可注入 —— 这几格的结论不随跑用例时的环境变。"""

    def test_clean_interpreter_passes(self) -> None:
        assert check_l1.contaminated(lambda _n: None) == []

    def test_reports_distribution_names_not_import_names(self) -> None:
        got = check_l1.contaminated(lambda n: object() if n == "yaml" else None)
        assert got == ["pyyaml"]

    def test_reports_every_hit(self) -> None:
        got = check_l1.contaminated(lambda _n: object())
        assert got == [d for _m, d in check_l1.PRODUCT_ONLY]

    def test_probe_blowing_up_is_not_a_hit(self) -> None:
        def boom(_n: str) -> object:
            raise ValueError("namespace package without __spec__")
        assert check_l1.contaminated(boom) == []


@pytest.mark.skipif(not check_l1.contaminated(),
                    reason="当前解释器本来就干净，这几格测不到「拒」")
class TestRefusesADirtyInterpreter:
    """产品依赖在场时的三种走法：读数拒、逃生口放行、--regenerate 一律拒。"""

    def test_plain_run_is_refused(self) -> None:
        r = _run(["lint"])
        assert r.returncode == 2
        assert "产品依赖在场" in r.stderr
        assert "manon-l1-venv" in r.stderr, "拒了得说怎么办"

    def test_regenerate_is_refused_even_with_the_escape_hatch(self) -> None:
        before = {p: p.read_bytes() for p in (ROOT / "scripts/l1-baselines").glob("*.txt")}
        r = _run(["--regenerate"], {"MANON_L1_ALLOW_DIRTY": "1"})
        assert r.returncode == 2
        assert "对 --regenerate 无效" in r.stderr
        assert all(p.read_bytes() == b for p, b in before.items()), "基线被脏环境改写了"

    def test_escape_hatch_lets_a_reading_through_and_leaves_a_trace(self) -> None:
        r = _run(["nosuchgate"], {"MANON_L1_ALLOW_DIRTY": "1"})
        assert "MANON_L1_ALLOW_DIRTY=1" in r.stderr
        assert "不算判过" in r.stderr
        assert "不认识的判据" in r.stderr, "环境这一关没放行到下一关"


@pytest.mark.skipif(not L1_VENV_PY.exists(), reason="本机没装 L1 工具链 venv")
def test_the_real_l1_venv_reads_clean() -> None:
    """哨兵不许误伤真正该用的那个解释器。"""
    r = subprocess.run(
        [str(L1_VENV_PY), "-c",
         f"import importlib.util as u, sys; sys.path.insert(0, {str(ROOT / 'scripts')!r});"
         f"import importlib.util as _u;"
         f"spec=_u.spec_from_file_location('c', {str(SCRIPT)!r});"
         f"m=_u.module_from_spec(spec); spec.loader.exec_module(m);"
         f"print(m.contaminated())"],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]", f"L1 venv 里检出产品依赖：{r.stdout.strip()}"
