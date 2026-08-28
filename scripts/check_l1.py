#!/usr/bin/env python3
"""manon 自己的 L1 门禁 —— 五条棘轮 + 一条清单不变量。

    L1=~/.cache/manon-l1-venv/bin/python
    $L1 scripts/check_l1.py               # 全部判据，与 baseline 比对
    $L1 scripts/check_l1.py lint typing   # 只跑指定的
    $L1 scripts/check_l1.py --regenerate  # 重新生成全部 baseline（修好后收紧用）

**解释器是判据的一部分**，不是随便哪个 python3：产品依赖在解析路径上时 mypy
换一套结果，读出来的红与 CI 的红不是同一件事。裸跑当场拒（见 PRODUCT_ONLY）。
装一次：python3 scripts/install-hooks.py

工具链钉版本在 scripts/requirements-l1.txt。
**工具缺失时红，不跳过**：静默变绿等于谎报「这一类缺陷有人看着」。

五条棘轮共用一套语义（与 ~/.claude 工具仓的 ratchet 同构，那份不公开、这份自包含）：
存量冻结、新增即红、**变少而没重新生成也红**（不收紧的棘轮不是棘轮）。
key 不含行号——行号随任何编辑漂移，带行号的 baseline 撑不过三次提交。

判据只此一份：本文件是 manon 仓唯一的 L1 比对实现，CI（.github/workflows/ci.yml）
与本机都调它，不许在别处复制判断逻辑。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
BASELINES = ROOT / "scripts" / "l1-baselines"
GATES = ROOT / "gates.txt"
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache",
             ".ruff_cache", "repos", "indexes", "saas_repos", "saas_indexes",
             "saas_data", "dist", "build", "web/static/reports",
             "web/static/test-results"}
L1_PY = "~/.cache/manon-l1-venv/bin/python"
REGEN = f"{L1_PY} scripts/check_l1.py --regenerate"

# 判据的读数不许随解释器变——CI 那一步的名字就是这条不变量（ci.yml：
# 「此刻环境里不许有产品依赖」），这里把它写成可执行的一句。
# 产品依赖在解析路径上时 mypy 换一套解析结果：实测多报 6 条 import-untyped，
# 而 CI 上一条都没有。后果不是「多一条红」——照着这条红去 --regenerate，
# 幻影条目就进了 baseline，CI 随即以「变少了」再红一次，两步之后基线已经脏了。
# 2026-08-27 的 CI 首跑判例里这条只留下一句方法论教训，没有执行器；这就是。
#
# 哨兵按 (import 名, 发行名) 成对写：发行名必须在产品依赖表里、且不许出现在
# requirements-l1.txt 里，由 tests/test_check_l1_env.py 判——哨兵指错人时那份
# 用例红，而不是这道门禁开始乱拒。
PRODUCT_ONLY = (("yaml", "pyyaml"), ("numpy", "numpy"), ("fastapi", "fastapi"))
ALLOW_DIRTY = "MANON_L1_ALLOW_DIRTY"

_Find = Callable[[str], object | None]


def contaminated(find: _Find = importlib.util.find_spec) -> list[str]:
    """当前解释器里在场的产品依赖（发行名）。空列表 = 与 baseline 生成环境同构。"""
    found: list[str] = []
    for mod, dist in PRODUCT_ONLY:
        try:
            if find(mod) is not None:
                found.append(dist)
        except (ImportError, ValueError):
            pass
    return found


def _env_ok_or_die(regen: bool) -> None:
    dirty = contaminated()
    if not dirty:
        return
    names = "、".join(dirty)
    how = (f"换 L1 专用解释器：{L1_PY} scripts/check_l1.py …"
           f"（没装就先跑 python3 scripts/install-hooks.py）")
    if regen:
        _die(f"产品依赖在场（{names}），拒绝生成 baseline。{how} "
             f"逃生口对 --regenerate 无效：脏环境写出的基线带着幻影条目，"
             f"CI 随后会以「变少了」再红一次。")
    if os.environ.get(ALLOW_DIRTY):
        print(f"⚠️  {ALLOW_DIRTY}=1：产品依赖在场（{names}），"
              f"这一轮读数与 CI 不可比，不算判过。", file=sys.stderr)
        return
    _die(f"产品依赖在场（{names}），这一轮读数与 CI 不可比。{how} "
         f"只想看一眼：{ALLOW_DIRTY}=1 强跑——会在 stderr 留痕，且对 --regenerate 无效。")


def _die(msg: str) -> NoReturn:
    print(f"❌ {msg}", file=sys.stderr)
    raise SystemExit(2)


PINS_FILE = ROOT / "scripts" / "requirements-l1.txt"
_VER = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def pinned_versions(text: str) -> dict[str, str]:
    """requirements-l1.txt 里钉的版本。**这份表是唯一事实源**，不在代码里抄第二份。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, ver = line.split("==", 1)
            out[name.strip()] = ver.strip()
    return out


def tool_version(path: str) -> str | None:
    """`<工具> --version` 里的版本号。读不出来返回 None（当作「量不到」，不是「对」）。"""
    try:
        r = subprocess.run([path, "--version"], capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    m = _VER.search(f"{r.stdout} {r.stderr}")
    return m.group(1) if m else None


def _tool(name: str) -> str:
    """L1 工具的绝对路径，**先找解释器自己那个 bin，再退回 PATH**，并核版本。

    钉解释器只挡住了「产品依赖换掉 mypy 的解析结果」那一种；出读数的是工具，
    而工具此前一律按裸名字走 PATH——解释器钉在 venv 上、工具却来自别处。
    实测 2026-08-28：本机 PATH 上是 mypy 1.19.0、requirements-l1.txt 钉的是
    2.3.1，同一份代码本机多报 6 条 import-untyped，vulture 干脆不在 PATH。
    照那个红去 --regenerate，baseline 就被写进了另一个版本的读数，CI 随即以
    「变少了」再红一次——正是钉解释器要防的那条链，只是下沉了一层。

    **版本不符一律拒，没有逃生口**：工具版本就是 baseline 的内容，读数与 CI
    不可比时给出的任何结论都是假的，而 ALLOW_DIRTY 那个口子放行的是「让我看一眼」，
    不是「让我按不可比的读数改 baseline」。
    """
    how = (f"装一次：python3 scripts/install-hooks.py"
           f"（或 pip install -r scripts/requirements-l1.txt），"
           f"再用 {L1_PY} 跑。")
    cand = Path(sys.executable).parent / name
    path = str(cand) if cand.exists() else shutil.which(name)
    if path is None:
        _die(f"L1 工具缺失：{name} 既不在 {cand.parent} 也不在 PATH。{how} "
             f"本门禁刻意不跳过——工具缺失时静默变绿，等于谎报「有人看着」。")
    want = pinned_versions(PINS_FILE.read_text(encoding="utf-8")).get(name)
    got = tool_version(path)
    if want and got != want:
        _die(f"{name} 版本不符：在跑的是 {got or '读不出版本'}（{path}），"
             f"requirements-l1.txt 钉的是 {want}。工具的输出就是 baseline 的内容，"
             f"版本一漂读数与 CI 就不可比。{how}")
    return path


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
    r = _run([_tool("ruff"), "check", ".", "--output-format=concise"])
    if r.returncode not in (0, 1):
        # ruff 的 fatal 走 stderr；mypy 的（如 stub 里的 [syntax]）走 stdout——
        # 判例（2026-08-27）：CI 上 mypy exit 2、stderr 为空，死因在 stdout 被吞。
        _die(f"ruff 退出码 {r.returncode}：\n{(r.stderr or r.stdout)[:500]}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in r.stdout.splitlines():
        m = re.match(r"^(.+?):\d+:\d+: ([A-Z]+\d+)", line)
        if m:
            counts[(m.group(1), m.group(2))] += 1
    return [f"{f}|{code}|{n}" for (f, code), n in sorted(counts.items())]


def collect_typing() -> list[str]:
    r = _run([_tool("mypy"), ".", "--no-incremental", "--no-error-summary"])
    if r.returncode not in (0, 1):
        _die(f"mypy 退出码 {r.returncode}：\n{(r.stderr or r.stdout)[:500]}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in r.stdout.splitlines():
        m = re.match(r"^(.+?):\d+: error: .*?\[([a-z-]+)\]$", line)
        if m:
            counts[(m.group(1), m.group(2))] += 1
    return [f"{f}|{code}|{n}" for (f, code), n in sorted(counts.items())]


def collect_dead() -> list[str]:
    files = _pyfiles()
    if not files:
        _die("扫描面为空——这不是「没有发现」，是「没看」。")
    r = _run([_tool("vulture"), "--min-confidence", "80",
              *[str(f.relative_to(ROOT)) for f in files]])
    if r.returncode not in (0, 3):        # 0 = 干净，3 = 有发现
        _die(f"vulture 退出码 {r.returncode}：\n{(r.stderr or r.stdout)[:500]}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in r.stdout.splitlines():
        m = re.match(r"^(.+?):\d+: (.+?) \(\d+% confidence\)$", line)
        if m:
            counts[(m.group(1), m.group(2))] += 1
    return [f"{f}|{msg}|{n}" for (f, msg), n in sorted(counts.items())]


def collect_contract() -> list[str]:
    """契约对账的死面 id。suspect 不进棘轮——它只决定审计投向，不是缺陷清单。"""
    auditor = ROOT / "scripts" / "manon-contract-audit.py"
    # 基线文件本身不许当证据：contract.txt 里写着 "endpoints:GET /tunnel-url"，
    # 留在扫描面里下一轮就把这行字读成弱引用，dead 升 suspect——棘轮自指，
    # 判定随基线内容翻转（判例 2026-08-27：CI「5 条存量消失」即此）。两种
    # pattern 各管一条枚举路径：走查分支按目录尾斜杠匹配，git 枚举分支按文件名。
    r = _run([sys.executable, str(auditor), ".", "--json",
              "--no-project-excludes",
              "--exclude", "scripts/l1-baselines/",
              "--exclude", "scripts/l1-baselines/*"], timeout=900)
    if r.returncode != 0:
        _die(f"契约对账退出码 {r.returncode}：\n{(r.stderr or r.stdout)[:500]}")
    # JSON 是扁平结构：findings 一行一条，带 table/id/verdict。
    out: list[str] = []
    for row in json.loads(r.stdout).get("findings", []):
        if row.get("verdict") == "dead":
            out.append(f"{row.get('table')}:{row.get('id', '?')}")
    return sorted(out)


def collect_deps() -> list[str]:
    manifests = [ROOT / "requirements.txt", ROOT / "manon_mcp" / "requirements.txt"]
    manifests = [m for m in manifests if m.exists()]
    if not manifests:
        _die("找不到依赖清单——判据没有对象可看。")
    r = _run([_tool("pip-audit"), "--no-deps", "-f", "json",
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
    _env_ok_or_die(regen)
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
