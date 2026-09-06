#!/usr/bin/env python3
"""Day-0 施工器 —— 新仓第一次提交之前，把 L1 一次装齐。

assurance_check 只体检不施工；没有这个施工器，每个新仓的 L1 靠会话现场手搓，
质量方差完全取决于那一次会话（2026-08-27 体系盘点点名的缺口二）。

装什么（全部幂等：已存在的文件跳过并打印，--force 才覆盖）：

    ruff.toml / mypy.ini             L1 配置（含跨机可比的边界：钉 python_version、
                                     follow_imports=skip）
    scripts/requirements-l1.txt      工具链钉版本——工具输出是 baseline 的内容，
                                     版本一漂 baseline 全线报新增
    scripts/check_l1.py              自含判据：四条棘轮（lint/类型/死代码/依赖）
                                     + 清单闭合不变量
    gates.txt                        门禁清单，判据只此一份的登记处
    .github/workflows/gates.yml      机外执行器（仅 GitHub 远端；顺序即判据：
                                     L1 工具链先装、检查先跑、产品依赖后进）
    .gitignore += .venv-l1/

装完当场用 PATH 上的工具跑一次 --regenerate 冻结存量；工具不在就打印收尾
命令并标 ⚠（半成品状态要说出来，不静默）。

只管 Python 仓。TS/Go 的等价判据未覆盖——会说，不装样子货。
用法：python3 day0.py <项目路径> [--force] [--with-ci]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PINNED = """\
# L1 机器层工具链。**钉版本**：这几件工具的输出就是 baseline 的内容，
# 版本一漂 baseline 全线报新增，而漂移只在别人的机器上表现为红。
ruff==0.16.4
mypy==2.3.1
vulture==2.16
pip-audit==2.10.0
pytest-cov==7.0.0
pytest-asyncio==1.4.0
"""

RUFF_TOML = """\
# L1 之一：lint。规则集 F（未用/未定义）· B（陷阱）· ARG（未用参数）·
# ERA（注释掉的代码）· ASYNC（异步陷阱）。存量冻结在 scripts/l1-baselines/lint.txt，
# 比对逻辑在 scripts/check_l1.py（只此一份）。
target-version = "py310"
extend-exclude = ["dist", "build"]

[lint]
select = ["F", "B", "ARG", "ERA", "ASYNC"]
"""

MYPY_INI = """\
; L1 之二：类型检查。存量冻结在 scripts/l1-baselines/typing.txt，
; 比对逻辑在 scripts/check_l1.py（只此一份）。python_version 钉死，
; 两台机器看到同一套语义，baseline 才可比。
[mypy]
python_version = 3.10
ignore_missing_imports = True
; 三方库一律当 Any（follow_imports=skip）：本机生成 baseline 时依赖未必在
; 路径上、CI 装了依赖，两边若解析深度不同，同一份代码会读出两套错误
; （判例：manon docs/incidents/2026-08-27-ci-first-run-four-root-causes.md）。
follow_imports = skip
no_incremental = True
exclude = (?x)(^\\.venv/|^\\.venv-l1/|^node_modules/)
"""

GATES_TXT = """\
# 门禁清单 —— 每条都要答得出「谁执行它」。
# 执行器：CI（.github/workflows/gates.yml，从干净克隆跑）；本机手动：python3 scripts/check_l1.py。
# 格式：<路径>|<一句话说明>；豁免行写 exempt:<路径>|<reason>。

scripts/check_l1.py|L1 四条棘轮（lint/类型/死代码/依赖漏洞）+ 清单闭合不变量：存量冻结、新增即红、变少而没重新生成也红
"""

CI_YML = """\
# 机外门禁 —— 元规则二：执行器至少有一个不在你的机器上、从干净克隆跑。
name: gates
on:
  pull_request:
  push:
    branches: [main, master]
jobs:
  l1:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      # 顺序是判据：L1 工具链先装、L1 检查先跑，产品依赖之后才进环境。
      # mypy 钉 python_version=3.10 时解析 numpy 这类内嵌 stub 的 PEP 695
      # 语法会直接 fatal（判例：manon docs/incidents/2026-08-27-ci-first-run-four-root-causes.md）。
      - run: pip install -r scripts/requirements-l1.txt
      - run: python3 scripts/check_l1.py
{PRODUCT_DEPS}{PYTEST}"""

CHECK_L1 = r'''#!/usr/bin/env python3
"""L1 门禁 —— 四条棘轮 + 清单闭合。由 day0.py 生成，此后本仓自维护。

    python3 scripts/check_l1.py               # 全部判据，与 baseline 比对
    python3 scripts/check_l1.py lint typing   # 只跑指定的
    python3 scripts/check_l1.py --regenerate  # 修好存量后收紧用

工具链钉版本在 scripts/requirements-l1.txt；本机没有就先装。
**工具缺失时红，不跳过**：静默变绿等于谎报「这一类缺陷有人看着」。

四条棘轮共用一套语义（与 manon 仓的 check_l1 同构）：存量冻结、新增即红、
**变少而没重新生成也红**（不收紧的棘轮不是棘轮）。key 不含行号——行号随
任何编辑漂移，带行号的 baseline 撑不过三次提交。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINES = ROOT / "scripts" / "l1-baselines"
GATES = ROOT / "gates.txt"
SKIP_DIRS = {".git", ".venv", ".venv-l1", "node_modules", "__pycache__",
             ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build"}
REGEN = "python3 scripts/check_l1.py --regenerate"


def _die(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    raise SystemExit(2)


def _need(tool: str) -> None:
    if shutil.which(tool) is None:
        _die(f"L1 工具缺失：{tool} 不在 PATH。装一次："
             f"pip install -r scripts/requirements-l1.txt。"
             f"本门禁刻意不跳过——工具缺失时静默变绿，等于谎报「有人看着」。")


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, cwd=ROOT)


def _pyfiles() -> list[Path]:
    out = []
    for p in ROOT.rglob("*.py"):
        if not any(d in p.parts for d in SKIP_DIRS):
            out.append(p)
    return sorted(out)


def collect_lint() -> list[str]:
    _need("ruff")
    r = _run(["ruff", "check", ".", "--output-format=concise"])
    if r.returncode not in (0, 1):
        # fatal 的输出流不固定：ruff 走 stderr，mypy 的（如 stub 里的 [syntax]）
        # 走 stdout——判例（2026-08-27，manon）：CI 上 mypy exit 2、stderr 为空，
        # 死因在 stdout 被吞。两边都回显。
        _die(f"ruff 退出码 {r.returncode}：\n{(r.stderr or r.stdout)[:500]}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in r.stdout.splitlines():
        m = re.match(r"^(.+?):\d+:\d+: ([A-Z]+\d+)", line)
        if m:
            counts[(m.group(1), m.group(2))] += 1
    return [f"{f}|{c}|{n}" for (f, c), n in sorted(counts.items())]


def collect_typing() -> list[str]:
    _need("mypy")
    r = _run(["mypy", ".", "--no-incremental", "--no-error-summary"])
    if r.returncode not in (0, 1):
        _die(f"mypy 退出码 {r.returncode}：\n{(r.stderr or r.stdout)[:500]}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in r.stdout.splitlines():
        m = re.match(r"^(.+?):\d+: error: .*?\[([a-z-]+)\]$", line)
        if m:
            counts[(m.group(1), m.group(2))] += 1
    return [f"{f}|{c}|{n}" for (f, c), n in sorted(counts.items())]


def collect_dead() -> list[str]:
    _need("vulture")
    files = _pyfiles()
    if not files:
        _die("扫描面为空——这不是「没有发现」，是「没看」。")
    r = _run(["vulture", "--min-confidence", "80",
              *[str(f.relative_to(ROOT)) for f in files]])
    if r.returncode not in (0, 3):        # 0 = 干净，3 = 有发现
        _die(f"vulture 退出码 {r.returncode}：\n{(r.stderr or r.stdout)[:500]}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in r.stdout.splitlines():
        m = re.match(r"^(.+?):\d+: (.+?) \(\d+% confidence\)$", line)
        if m:
            counts[(m.group(1), m.group(2))] += 1
    return [f"{f}|{msg}|{n}" for (f, msg), n in sorted(counts.items())]


def collect_deps() -> list[str]:
    _need("pip-audit")
    manifests = sorted(ROOT.glob("requirements*.txt"))
    if not manifests:
        print("⏭ deps：根目录没有 requirements*.txt，依赖判据对象缺席（不是零发现）")
        return []
    r = _run(["pip-audit", "--no-deps", "-f", "json",
              *[f"-r={m.name}" for m in manifests]], timeout=300)
    if r.returncode not in (0, 1):
        _die(f"pip-audit 退出码 {r.returncode}（网络？）——判据没跑成不算零发现：\n"
             f"{(r.stderr or r.stdout)[:500]}")
    out: list[str] = []
    payload = json.loads(r.stdout or "{}")
    # pip-audit 2.x 的 JSON 是 {"dependencies": [...]}；个别版本/模式是裸数组，两种都认
    rows = payload.get("dependencies", []) if isinstance(payload, dict) else payload
    for row in rows:
        out += [f"{row.get('name', '?')}|{v.get('id', '?')}"
                for v in (row.get("vulns") or [])]
    return sorted(out)


ALL = {"lint": collect_lint, "typing": collect_typing,
       "dead": collect_dead, "deps": collect_deps}


def compare(gate: str, current: list[str], path: Path) -> int:
    if not path.exists():
        print(f"❌ {gate}：没有 baseline（{path.name}）——先 {REGEN}")
        return 1
    base = {l.strip() for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")}
    cur = set(current)
    rc = 0
    for e in sorted(cur - base):
        print(f"❌ {gate}：新增 —— 新代码不许再欠\n       + {e}")
        rc = 1
    for e in sorted(base - cur):
        print(f"❌ {gate}：存量已消失，但 baseline 没跟着收紧 —— 不收紧的棘轮不是棘轮\n       - {e}")
        rc = 1
    if rc:
        print(f"       重新生成：{REGEN}")
    else:
        print(f"  ✅ {gate}：存量 {len(base)} 条，与 baseline 一致")
    return rc


def gate_registry_ok() -> int:
    if not GATES.exists():
        print("❌ 没有 gates.txt —— 每个检查都要答得出「谁执行它」")
        return 1
    registered, exempt = set(), set()
    for line in GATES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entry = line.split("|", 1)[0]
        (exempt if entry.startswith("exempt:") else registered).add(
            entry.removeprefix("exempt:"))
    on_disk = {str(p.relative_to(ROOT)) for p in ROOT.glob("scripts/check_*.py")}
    on_disk |= {str(p.relative_to(ROOT)) for p in ROOT.glob("check_*.py")}
    rc = 0
    for r in sorted(r for r in registered if not (ROOT / r).exists()):
        print(f"❌ 清单登记的路径不存在：{r}")
        rc = 1
    for d in sorted(on_disk - registered - exempt):
        print(f"❌ 磁盘上的检查未登记：{d} —— 登记进 gates.txt，或写 exempt:{d}|<reason>")
        rc = 1
    if not rc:
        print(f"  ✅ 清单闭合：磁盘 {len(on_disk)} 个 check_* == 登记 "
              f"{len(registered & on_disk)} + 豁免 {len(exempt & on_disk)}")
    return rc


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    regen = "--regenerate" in sys.argv
    gates = args or list(ALL)
    unknown = [g for g in gates if g not in ALL]
    if unknown:
        _die(f"不认识的判据：{unknown}（可选：{'/'.join(ALL)}）")
    BASELINES.mkdir(exist_ok=True)
    rc = 0
    for g in gates:
        current = ALL[g]()
        path = BASELINES / f"{g}.txt"
        if regen:
            path.write_text(
                f"# {g} 存量 baseline —— 由 check_l1.py --regenerate 生成，别手改。\n"
                f"# 格式每行一条，key 不含行号。只许变短；修好后必须重新生成。\n"
                + "".join(f"{e}\n" for e in sorted(set(current))),
                encoding="utf-8")
            print(f"✅ {g} baseline 已重新生成：{len(set(current))} 条")
        else:
            rc |= compare(g, current, path)
    if not regen:
        rc |= gate_registry_ok()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
'''

FILES = {
    "ruff.toml": RUFF_TOML,
    "mypy.ini": MYPY_INI,
    "scripts/requirements-l1.txt": PINNED,
    "scripts/check_l1.py": CHECK_L1,
    "gates.txt": GATES_TXT,
}

TOOLS = ("ruff", "mypy", "vulture", "pip-audit")


def _write(root: Path, rel: str, content: str, force: bool) -> bool:
    target = root / rel
    if target.exists() and not force:
        print(f"  ⏭ {rel} 已存在，跳过（--force 覆盖）")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"  ✅ {rel}")
    return True


def _remote_kind(root: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=15)
        url = (r.stdout or "").strip().lower()
    except Exception:
        return "none"
    if "github" in url:
        return "github"
    if "gitee" in url:
        return "gitee"
    return "other" if url else "none"


def main() -> int:
    ap = argparse.ArgumentParser(description="Day-0：给新仓一键装齐 L1")
    ap.add_argument("project")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    ap.add_argument("--with-ci", action="store_true",
                    help="无视远端类型，强制写 CI 模板")
    args = ap.parse_args()
    root = Path(args.project).expanduser().resolve()
    if not root.is_dir():
        print(f"❌ 不是目录：{root}")
        return 2

    py = list(root.glob("*.py")) or list(root.glob("*/*.py"))
    if not py:
        print("❌ 没找到 .py 文件——day0 v1 只管 Python 仓。"
              "TS/Go 的等价判据未覆盖：这是明说，不是装了样子货。")
        return 2

    print(f"Day-0 施工：{root}")
    for rel, content in FILES.items():
        _write(root, rel, content, args.force)

    kind = _remote_kind(root)
    ci = args.with_ci or kind == "github"
    if ci:
        deps_step = ("      - run: pip install -r requirements.txt\n"
                     if (root / "requirements.txt").exists() else "")
        pytest_step = ("      - run: python3 -m pytest tests/ -q\n"
                       if (root / "tests").is_dir()
                       and any((root / "tests").glob("test_*.py")) else "")
        _write(root, ".github/workflows/gates.yml",
               CI_YML.replace("{PRODUCT_DEPS}", deps_step)
                     .replace("{PYTEST}", pytest_step), args.force)
    elif kind == "gitee":
        print("  ⚠ 远端是 Gitee：Gitee Go 未启用，机外层走不了 CI——"
              "事前靠合并咽喉、事后靠每日点名（见工程保障体系规范）。CI 模板未写。")
    else:
        print("  ⚠ 远端未定：CI 模板未写（配了没跑比没有更危险）。"
              "定了 GitHub 远端后重跑一次 day0（幂等）补上。")

    gi = root / ".gitignore"
    line = ".venv-l1/\n"
    if gi.exists():
        if ".venv-l1" not in gi.read_text(encoding="utf-8"):
            with gi.open("a", encoding="utf-8") as f:
                f.write("\n" + line)
            print("  ✅ .gitignore += .venv-l1/")
        else:
            print("  ⏭ .gitignore 已有 .venv-l1/")
    else:
        gi.write_text(line, encoding="utf-8")
        print("  ✅ .gitignore（新建：.venv-l1/）")

    if all(shutil.which(t) for t in TOOLS):
        print("  … 工具链在 PATH，当场冻结存量：")
        r = subprocess.run([sys.executable, str(root / "scripts/check_l1.py"),
                            "--regenerate"], cwd=root, capture_output=True,
                           text=True, timeout=900)
        print("    " + r.stdout.strip().replace("\n", "\n    "))
        if r.returncode != 0:
            print(f"    ⚠ --regenerate 退出码 {r.returncode}：{r.stderr[:300]}")
            return 1
        print("  ✅ baseline 已冻结。**记得提交**（baseline 必须进远端，"
              "否则只有你这台机器认得存量）。")
    else:
        print("  ⚠ L1 工具链不在 PATH，baseline 未冻结——这不是完成态：")
        print("      python3 -m venv .venv-l1 && .venv-l1/bin/pip install -r scripts/requirements-l1.txt")
        print("      PATH=\"$PWD/.venv-l1/bin:$PATH\" python3 scripts/check_l1.py --regenerate")
    print("  下一步：提交全部产物 → 推远端 → 确认 CI 首跑绿 → "
          "（GitHub）开 branch protection：required checks 勾上 l1。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
