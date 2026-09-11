"""两个入口，一份实现 —— 同一个仓不许读出两套数。

判例（2026-08-27、2026-09-11 各一次）：`~/.claude/bin/assurance-check.py` 与 skill
里的那份曾各自演化，同一个仓、同一天、两条命令读出两套数（一边 `OK 2 / 缺 11`，
另一边 `OK 8 / 缺 6`）。修的人只改了其中一份，而没有任何机器会发现。

入口现在收成了薄壳（`bin/` 那份 runpy 到 skill 这份）。这三条判据钉的就是「收住了」：
落到同一份文件、对造出来的仓逐字节同读数、对真实仓逐字节同读数。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
IMPLEMENTATION = SKILL_SCRIPTS / "assurance_check.py"
BIN_ENTRY = Path.home() / ".claude/bin/assurance-check.py"
REAL_PROJECTS = ("Agents-verispring", "CaseOS", "verispring-ops")


def _fixture_repo() -> Path:
    root = Path(tempfile.mkdtemp(prefix="two-entries-"))
    (root / "ruff.toml").write_text('select = ["F", "B"]\n', encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "gates.txt").write_text("ruff|静态门禁\n", encoding="utf-8")
    (root / "scripts" / "run_gates.sh").write_text("ruff check .\n", encoding="utf-8")
    for i in range(3):
        (root / f"mod_{i}.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _run(entry: Path, project: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CASEOS_")}
    return subprocess.run([sys.executable, str(entry), str(project)],
                          capture_output=True, text=True, env=env, timeout=120)


@unittest.skipUnless(BIN_ENTRY.is_file(), f"本机没装薄入口：{BIN_ENTRY}")
class TwoEntriesAgree(unittest.TestCase):
    def test_bin_entry_is_a_thin_shell_over_the_single_implementation(self):
        text = BIN_ENTRY.read_text(encoding="utf-8")
        # 薄壳的标志：它不自己判，只把 argv 交给那份实现。
        self.assertIn("runpy.run_path", text)
        self.assertLess(len(text.splitlines()), 60, "入口又长出了自己的判据")

    def test_both_entries_agree_on_a_fixture_repo(self):
        project = _fixture_repo()
        self.addCleanup(lambda: __import__("shutil").rmtree(project, ignore_errors=True))
        a, b = _run(IMPLEMENTATION, project), _run(BIN_ENTRY, project)
        self.assertEqual(a.stdout, b.stdout)
        self.assertEqual(a.returncode, b.returncode)

    def test_both_entries_agree_on_the_real_projects(self):
        missing = [n for n in REAL_PROJECTS if not (Path.home() / n).is_dir()]
        if missing:
            self.skipTest(f"本机没有这些仓：{missing}（三仓读数由装了它们的机器复核）")
        for name in REAL_PROJECTS:
            with self.subTest(project=name):
                project = Path.home() / name
                a, b = _run(IMPLEMENTATION, project), _run(BIN_ENTRY, project)
                self.assertEqual(a.stdout, b.stdout)
                self.assertEqual(a.returncode, b.returncode)


if __name__ == "__main__":
    unittest.main()
