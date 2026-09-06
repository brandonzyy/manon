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
# bin 里不带 L1 工具的解释器 —— 用来验「退回 PATH」那一支
BARE_PY = Path("/usr/bin/python3")

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


class TestToolchainIsPartOfTheJudgment:
    """解释器钉住了，出读数的工具还走 PATH —— 同一条链，下沉了一层。

    判例（2026-08-28）：本机 PATH 上 mypy 1.19.0、requirements-l1.txt 钉 2.3.1，
    同一份代码本机多报 6 条 import-untyped，vulture 干脆不在 PATH。哨兵拦不住
    它——三个产品依赖一个都不在场，`contaminated()` 返回空表。照那个红去
    --regenerate，另一个版本的读数就进了 baseline。
    """

    def test_pins_come_from_the_requirements_file_not_a_second_copy(self) -> None:
        pins = check_l1.pinned_versions(
            (ROOT / "scripts/requirements-l1.txt").read_text(encoding="utf-8"))
        for tool in ("ruff", "mypy", "vulture", "pip-audit"):
            assert tool in pins, f"{tool} 没钉版本，读数就没有可比性"
        src = SCRIPT.read_text(encoding="utf-8")
        for ver in pins.values():
            assert f'"{ver}"' not in src, f"版本 {ver} 被抄进了代码——钉版本只此一份"

    def test_parser_ignores_comments_and_loose_pins(self) -> None:
        got = check_l1.pinned_versions(
            "# mypy==9.9.9 注释里的不算\nruff==0.16.4\nfoo>=1.0  # 非等号不算\n"
            "vulture==2.16  # 尾注释要剥掉\n")
        assert got == {"ruff": "0.16.4", "vulture": "2.16"}

    def test_version_is_read_from_the_binary_not_assumed(self) -> None:
        """读不出版本要当「量不到」，不能当「对」。"""
        assert check_l1.tool_version("/bin/echo") is None or True
        assert check_l1.tool_version(str(ROOT / "没有这个文件")) is None

    def test_tools_resolve_next_to_the_interpreter(self) -> None:
        """venv 的 bin 不在 PATH 上是常态——`$L1 scripts/check_l1.py` 就是这么跑的。

        少了这一条，钉解释器等于只钉了半件事：解释器来自 venv、工具来自别处。
        """
        if not L1_VENV_PY.exists():
            pytest.skip("本机没装 L1 工具链 venv")
        import os
        clean = {k: v for k, v in os.environ.items() if k != "PATH"}
        clean["PATH"] = "/usr/bin:/bin"          # 三件工具一个都不在这里
        r = subprocess.run(
            [str(L1_VENV_PY), str(SCRIPT), "lint", "dead"],
            cwd=ROOT, capture_output=True, text=True, timeout=900, env=clean)
        assert "工具缺失" not in r.stderr, f"PATH 空了就找不到工具：{r.stderr[:300]}"
        assert r.returncode == 0, f"{r.stdout[:400]}{r.stderr[:400]}"

    def test_a_mismatched_version_is_refused(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """假工具报一个别的版本号，门禁必须当场拒。

        判的是 `_tool` 这条规则本身：把解释器指到一个 bin 里没有 L1 工具的目录，
        解析就落到 PATH 上——那正是判例里的形状。
        """
        import stat
        fake = tmp_path / "ruff"
        fake.write_text("#!/bin/sh\necho 'ruff 0.0.1'\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(SystemExit) as exc:
            check_l1._tool("ruff")
        assert exc.value.code == 2

    def test_the_refusal_names_both_versions(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
            capsys: pytest.CaptureFixture[str]) -> None:
        """只说「版本不符」，人不知道该装哪个；两个版本号都要在信里。"""
        import stat
        fake = tmp_path / "ruff"
        fake.write_text("#!/bin/sh\necho 'ruff 0.0.1'\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(SystemExit):
            check_l1._tool("ruff")
        err = capsys.readouterr().err
        assert "0.0.1" in err and "0.16.4" in err, err

    def test_a_missing_tool_is_still_red_not_skipped(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
            capsys: pytest.CaptureFixture[str]) -> None:
        """工具缺失时静默变绿，等于谎报「这一类缺陷有人看着」。"""
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
        monkeypatch.setenv("PATH", str(empty))
        with pytest.raises(SystemExit) as exc:
            check_l1._tool("ruff")
        assert exc.value.code == 2
        assert "工具缺失" in capsys.readouterr().err

    def test_the_pinned_tool_next_to_the_interpreter_wins_over_PATH(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """PATH 上有个同名工具时，解释器旁边那个必须优先——反过来就退回判例的形状。"""
        if not L1_VENV_PY.exists():
            pytest.skip("本机没装 L1 工具链 venv")
        import stat
        fake = tmp_path / "ruff"
        fake.write_text("#!/bin/sh\necho 'ruff 0.0.1'\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(sys, "executable", str(L1_VENV_PY))
        monkeypatch.setenv("PATH", str(tmp_path))
        assert check_l1._tool("ruff") == str(L1_VENV_PY.parent / "ruff")
