"""day0 施工器的判据 —— 装出来的是一套真能红真能绿的棘轮，不是文件堆。

两条：文件装齐且幂等；装出的判据「存量冻结后绿 / 新增即红」。
工具链不在 PATH 时第二条 skip（CI 在装完 scripts/requirements-l1.txt 后跑
pytest，所以 CI 上必跑；本机没装工具链时不能拿半套判据说它坏了）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DAY0 = Path(__file__).resolve().parent.parent / "skills/assurance/scripts/day0.py"
TOOLS = all(shutil.which(t) for t in ("ruff", "mypy", "vulture"))


def _day0(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(DAY0), str(target)],
                          capture_output=True, text=True, timeout=900)


def _judge(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "scripts/check_l1.py"],
                          cwd=target, capture_output=True, text=True, timeout=900)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "app.py").write_text(
        "import os  # 存量：冻结进 baseline\n"
        "def unused_stock():\n"
        "    return 1\n"
        "def used(n: int) -> int:\n"
        "    return n + 1\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("httpx>=0.28\n", encoding="utf-8")
    return tmp_path


def test_writes_full_set_and_is_idempotent(repo: Path) -> None:
    r = _day0(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    for rel in ("ruff.toml", "mypy.ini", "scripts/check_l1.py",
                "scripts/requirements-l1.txt", "gates.txt", ".gitignore"):
        assert (repo / rel).is_file(), f"缺 {rel}"
    r2 = _day0(repo)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "已存在，跳过" in r2.stdout, "重跑必须跳过而不是覆盖"


@pytest.mark.skipif(not TOOLS, reason="L1 工具链不在 PATH")
def test_installed_judge_freezes_then_ratchets(repo: Path) -> None:
    assert _day0(repo).returncode == 0
    ok = _judge(repo)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    (repo / "new_bad.py").write_text(
        "import json\n"
        "def bad(a, b):\n"
        "    return a\n", encoding="utf-8")
    red = _judge(repo)
    assert red.returncode == 1, "新增违规必须红"
    assert "new_bad.py" in red.stdout
