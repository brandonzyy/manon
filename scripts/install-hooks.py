#!/usr/bin/env python3
"""装本机 git 钩子与它需要的 L1 工具链。

本机这一层是**快反馈**，不是强制：强制在机外（GitHub Actions + 分支保护，
master 与 dev 都要求 l1-and-tests 与 secrets 两个检查通过）。`--no-verify`
绕得过本机钩子，绕不过分支保护——两层各司其职，别把本机这层当成拦得住。

跑法：python3 scripts/install-hooks.py          # 装
      python3 scripts/install-hooks.py --check  # 只看装没装
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "hooks"
# 仓外：放仓内会被 vulture 当源码扫进去。
VENV = Path(os.environ.get("MANON_L1_VENV", Path.home() / ".cache" / "manon-l1-venv"))
REQ = ROOT / "scripts" / "requirements-l1.txt"


def hooks_dir() -> Path:
    out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
                         capture_output=True, text=True, check=True).stdout.strip()
    q = Path(out)
    return (q if q.is_absolute() else ROOT / q) / "hooks"


def check() -> int:
    bad = []
    if not (VENV / "bin" / "python").exists():
        bad.append(f"L1 工具链不在 {VENV}")
    for src in sorted(SRC.iterdir()):
        dst = hooks_dir() / src.name
        if not dst.exists():
            bad.append(f"{src.name} 没装")
        elif dst.read_bytes() != src.read_bytes():
            bad.append(f"{src.name} 与仓里这一版不一致 —— 装的和判的不是同一个")
        elif not os.access(dst, os.X_OK):
            bad.append(f"{src.name} 没有执行位 —— git 会安静地跳过它")
    for line in bad:
        print(f"  ❌ {line}")
    if not bad:
        print(f"  ✅ 钩子已装且与仓内同源；L1 工具链在 {VENV}")
    return 1 if bad else 0


def main() -> int:
    if "--check" in sys.argv[1:]:
        return check()

    if not (VENV / "bin" / "python").exists():
        print(f"装 L1 工具链 → {VENV}")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    subprocess.run([str(VENV / "bin" / "pip"), "install", "-q", "-r", str(REQ)], check=True)

    dst_dir = hooks_dir()
    dst_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(SRC.iterdir()):
        dst = dst_dir / src.name
        shutil.copyfile(src, dst)
        dst.chmod(0o755)
        print(f"装上 {src.name} → {dst}")
    return check()


if __name__ == "__main__":
    sys.exit(main())
