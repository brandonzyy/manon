#!/usr/bin/env python3
"""manon 自己的 L1 门禁 —— 五条棘轮 + 一条清单不变量。

    python3 scripts/check_l1.py               # 全部判据，与 baseline 比对
    python3 scripts/check_l1.py lint typing   # 只跑指定的
    python3 scripts/check_l1.py --regenerate  # 重新生成全部 baseline（修好后收紧用）

工具链钉版本在 scripts/requirements-l1.txt；本机没有就先装：
    pip install -r scripts/requirements-l1.txt
**工具缺失时红，不跳过**：静默变绿等于谎报「这一类缺陷有人看着」。

五条棘轮共用一套语义（与 ~/.claude 工具仓的 ratchet 同构，那份不公开、这份自包含）：
存量冻结、新增即红、**变少而没重新生成也红**（不收紧的棘轮不是棘轮）。
key 不含行号——行号随任何编辑漂移，带行号的 baseline 撑不过三次提交。

判据只此一份：本文件是 manon 仓唯一的 L1 比对实现，CI（.github/workflows/ci.yml）
与本机都调它，不许在别处复制判断逻辑。
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
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache",
             ".ruff_cache", "repos", "indexes", "saas_repos", "saas_indexes",
             "saas_data", "dist", "build", "web/static/reports",
             "web/static/test-results"}
REGEN = "python3 scripts/check_l1.py --regenerate"


def _die(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    raise SystemExit(2)


def _need(tool: str) -> None:
    if shutil.which(tool) is None:
        _die(f"L1 工具缺失：{tool} 不在 PATH。装一次："
             f"pip install -r scripts/requirements-l1.txt。"
             f"本门禁刻意不跳过——工具缺失时静默变绿，等于谎报「有人看着」。")


def _pyfiles() -> list[Path]:
    out = []
    for p in ROOT.rglob("*.py"):
        if not any(d in p.parts for d in SKIP_DIRS) and "__pycache__" not in str(p):
            out.append(p)
    return sorted(out)


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    return r


# ── 各判据只回答「当前存量是什么」 ──────────────────────────────────────

def collect_lint() -> list[str]:
    _need("ruff")
    r = _run(["ruff", "check", ".", "--output-format=concise"])
    if r.returncode not in (0, 1):
        _die(f"ruff 退出码 {r.returncode}：\n{r.stderr[:500]}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in r.stdout.splitlines():
        m = re.match(r"^(.+?):\d+:\d+: ([A-Z]+\d+)", line)
        if m:
            counts[(m.group(1), m.group(2))] += 1
    return [f"{f}|{code}|{n}" for (f, code), n in sorted(counts.items())]


def collect_typing() -> list[str]:
    _need("mypy")
    r = _run(["mypy", ".", "--no-incremental", "--no-error-summary"])
    if r.returncode not in (0, 1):
        _die(f"mypy 退出码 {r.returncode}：\n{r.stderr[:500]}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in r.stdout.splitlines():
        m = re.match(r"^(.+?):\d+: error: .*?\[([a-z-]+)\]$", line)
        if m:
            counts[(m.group(1), m.group(2))] += 1
    return [f"{f}|{code}|{n}" for (f, code), n in sorted(counts.items())]


def collect_dead() -> list[str]:
    _need("vulture")
    files = _pyfiles()
    if not files:
        _die("扫描面为空——这不是「没有发现」，是「没看」。")
    r = _run(["vulture", "--min-confidence", "80",
              *[str(f.relative_to(ROOT)) for f in files]])
    if r.returncode not in (0, 3):        # 0 = 干净，3 = 有发现
        _die(f"vulture 退出码 {r.returncode}：\n{r.stderr[:500]}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in r.stdout.splitlines():
        m = re.match(r"^(.+?):\d+: (.+?) \(\d+% confidence\)$", line)
        if m:
            counts[(m.group(1), m.group(2))] += 1
    return [f"{f}|{msg}|{n}" for (f, msg), n in sorted(counts.items())]


def collect_contract() -> list[str]:
    """契约对账的死面 id。suspect 不进棘轮——它只决定审计投向，不是缺陷清单。"""
    auditor = ROOT / "scripts" / "manon-contract-audit.py"
    r = _run([sys.executable, str(auditor), ".", "--json"], timeout=900)
    if r.returncode != 0:
        _die(f"契约对账退出码 {r.returncode}：\n{(r.stderr or r.stdout)[:500]}")
    # JSON 是扁平结构：findings 一行一条，带 table/id/verdict。
    out: list[str] = []
    for row in json.loads(r.stdout).get("findings", []):
        if row.get("verdict") == "dead":
            out.append(f"{row.get('table')}:{row.get('id', '?')}")
    return sorted(out)


def collect_deps() -> list[str]:
    _need("pip-audit")
    manifests = [ROOT / "requirements.txt", ROOT / "manon_mcp" / "requirements.txt"]
    manifests = [m for m in manifests if m.exists()]
    if not manifests:
        _die("找不到依赖清单——判据没有对象可看。")
    r = _run(["pip-audit", "--no-deps", "-f", "json",
              *[f"-r={m}" for m in manifests]], timeout=300)
    if r.returncode not in (0, 1):
        _die(f"pip-audit 退出码 {r.returncode}（网络？）——判据没跑成不算零发现：\n"
             f"{r.stderr[:500]}")
    return _deps_entries(r.stdout, manifests)


def _deps_entries(stdout: str, manifests: list[Path]) -> list[str]:
    names: dict[str, str] = {}
    for m in manifests:
        for line in m.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.split("#", 1)[0].strip()
            if s and not s.startswith("-"):
                names.setdefault(s.split("=")[0].split(">")[0].split("<")[0]
                                 .split("~")[0].split("!")[0].strip().lower(),
                                 m.relative_to(ROOT).as_posix())
    counts: Counter[tuple[str, str]] = Counter()
    for entry in (json.loads(stdout or "{}").get("dependencies") or []):
        if entry.get("skip_reason"):
            continue
        rel = names.get((entry.get("name") or "").lower(), "<未归属>")
        for v in entry.get("vulns") or []:
            counts[(rel, v.get("id") or "?")] += 1
    return [f"{rel}|{vid}|{n}" for (rel, vid), n in sorted(counts.items())]


# ── 棘轮（只此一份） ────────────────────────────────────────────────────

def _load(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l.rstrip("\n") for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def compare(name: str, current: list[str], baseline_path: Path) -> int:
    baseline = _load(baseline_path)
    cur, old = set(current), set(baseline)
    added, gone = sorted(cur - old), sorted(old - cur)
    if not added and not gone:
        print(f"  ✅ {name}：存量 {len(cur)} 条，与 baseline 一致")
        return 0
    if added:
        print(f"  ❌ {name}：新增 {len(added)} 条 —— 新代码不许再欠")
        for a in added[:15]:
            print(f"       + {a}")
    if gone:
        print(f"  ❌ {name}：{len(gone)} 条存量已消失，但 baseline 没跟着收紧 ——"
              f" 不收紧的棘轮不是棘轮")
        for g in gone[:15]:
            print(f"       - {g}")
    print(f"       重新生成：{REGEN}")
    return 1


# ── L2：清单不变量（磁盘上的 check_* == 登记 + 豁免） ───────────────────

def gate_registry_ok() -> int:
    if not GATES.exists():
        print("❌ gates.txt 不在——门禁即清单，清单不在等于每条门禁都没登记", file=sys.stderr)
        return 1
    registered, exempt = set(), set()
    for line in GATES.read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        if not s or "|" not in s:
            continue
        path = s.split("|", 1)[0].strip()
        (exempt if path.startswith("exempt:") else registered).add(
            path.removeprefix("exempt:"))
    on_disk = {f"scripts/{p.name}" for p in (ROOT / "scripts").glob("check_*.py")}
    orphan = on_disk - registered - exempt
    stale = {q for q in registered
             if q.startswith("scripts/") and not (ROOT / q).exists()}
    rc = 0
    if orphan:
        print(f"❌ {len(orphan)} 个检查器没登记进 gates.txt：{sorted(orphan)}",
             file=sys.stderr)
        rc = 1
    if stale:
        print(f"❌ gates.txt 登记了不存在的路径：{sorted(stale)}", file=sys.stderr)
        rc = 1
    if not rc:
        print(f"  ✅ 清单闭合：磁盘 {len(on_disk)} 个 check_* == 登记 "
              f"{len(registered & on_disk)} + 豁免 {len(exempt & on_disk)}")
    return rc


ALL = {"lint": collect_lint, "typing": collect_typing, "dead": collect_dead,
       "contract": collect_contract, "deps": collect_deps}


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
                + "".join(f"{e}\n" for e in sorted(set(current))), encoding="utf-8")
            print(f"✅ {g} baseline 已重新生成：{len(set(current))} 条")
        else:
            rc |= compare(g, current, path)
    if not regen:
        rc |= gate_registry_ok()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
