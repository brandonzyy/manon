#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证资产审计 —— /retire-checks skill 的固定动作层（纯 stdlib，全部子命令输出 JSON）。

这个脚本只做「用眼睛看会看错」的那部分：清点、算重复执行次数、算影响面、
对比前后快照。**判断哪条该退役、退役后由谁承接，是 LLM 和人的活，不在这里。**

三条来自实战的默认行为，别改掉：
  1. 统计测试语料一律排除 node_modules / vendor / .venv —— 不排除会把 TypeScript
     打包源码算成「测试代码」，实测虚报到 19 万行。
  2. 影响面按「谁在文本里引用这个路径」算，不按目录猜 —— 它是「不跑全量」的
     唯一正当理由。
  3. 空转门禁只报候选，不下结论 —— 判据是「它选中了零条测试」或「它断言的是
     一个每次变更都要手改的常量」，最终要人看一眼。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path.home() / ".retire-checks"
SKIP_DIRS = {"node_modules", "vendor", ".venv", ".git", "dist", "build",
             "__pycache__", ".mypy_cache", ".pytest_cache", "coverage"}
TEST_FILE = re.compile(r"^(test_.*|.*_test|.*\.spec|.*\.test)\.(py|ts|tsx|js|mjs|go|rb)$")
MILESTONE = re.compile(r"^test_(m\d|mvp\d|s\d|g\d)", re.IGNORECASE)


class Fail(Exception):
    pass


def emit(payload, code=0):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(code)


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def line_count(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def resolve_root(args) -> Path:
    if args.root:
        return Path(args.root).resolve()
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    base = Path(out.stdout.strip()) if out.returncode == 0 else Path.cwd()
    # 子项目优先：cwd 若在 monorepo 的某个子目录里，就以那一层为准
    cwd = Path.cwd().resolve()
    return cwd if cwd != base and (cwd / "scripts").is_dir() else base.resolve()


# ---------------------------------------------------------------- inventory

def find_runners(root: Path):
    """门禁执行器：既跑检查器又跑测试、还自带相位/报告的那种脚本。"""
    hits = []
    for path in walk(root):
        name = path.name
        if not (name.startswith("run_") and path.suffix in {".sh", ".py"}):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        signals = {
            "phases": len(re.findall(r"if selected |--phases|PHASES", text)),
            "pytest": len(re.findall(r"\bpytest\b", text)),
            "checkers": len(set(re.findall(r"check_[a-z0-9_]+\.(?:sh|py)", text))),
            # 清单驱动的执行器不硬编码任何检查器名字——只认 pytest/check_ 会把它漏掉，
            # 而它恰恰是最该保留的那种（唯一实现、单一事实源）。
            "manifest_driven": len(re.findall(r"[a-z0-9_]*gates?[a-z0-9_]*\.txt", text)),
        }
        if any(signals.values()):
            hits.append({"path": str(path.relative_to(root)),
                         "lines": line_count(path), **signals})
    return sorted(hits, key=lambda h: h["path"])


def find_checkers(root: Path):
    return sorted(
        {str(p.relative_to(root)) for p in walk(root)
         if p.name.startswith("check_") and p.suffix in {".sh", ".py"}})


def find_manifests(root: Path):
    """门禁清单：每行 `<路径>|<说明>` 的登记文件。"""
    out = []
    for path in walk(root):
        if path.suffix not in {".txt", ".cfg", ".list"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        entries = [l.strip() for l in text.splitlines()
                   if l.strip() and not l.strip().startswith("#") and "|" in l]
        exempt = [l.strip() for l in text.splitlines() if l.strip().startswith("# exempt:")]
        if entries and all("/" in e.split("|", 1)[0] for e in entries):
            out.append({"path": str(path.relative_to(root)),
                        "registered": len(entries), "exempt": len(exempt),
                        "entries": [e.split("|", 1)[0].strip() for e in entries],
                        "exempt_entries": [e[len("# exempt:"):].split("|", 1)[0].strip()
                                           for e in exempt if "|" in e]})
    return out


def test_corpus(root: Path):
    buckets = {}
    for path in walk(root):
        if not TEST_FILE.match(path.name):
            continue
        rel = path.relative_to(root)
        top = "/".join(rel.parts[:-1]) or "."
        b = buckets.setdefault(top, {"files": 0, "lines": 0, "milestone_files": 0,
                                     "milestone_lines": 0})
        n = line_count(path)
        b["files"] += 1
        b["lines"] += n
        if MILESTONE.match(path.stem):
            b["milestone_files"] += 1
            b["milestone_lines"] += n
    return dict(sorted(buckets.items(), key=lambda kv: -kv[1]["lines"]))


def execution_counts(root: Path, checkers, runners, manifests):
    """每个检查器在一次完整门禁里被执行几次 —— 重复执行是最容易被忽略的浪费。"""
    registered = {e for m in manifests for e in m["entries"]}
    counts = {}
    runner_sources = {
        r["path"]: (root / r["path"]).read_text(encoding="utf-8", errors="replace")
        for r in runners}
    for checker in checkers:
        name = Path(checker).name
        n = 1 if checker in registered else 0
        where = ["manifest"] if n else []
        for rpath, text in runner_sources.items():
            # 只数**看起来像调用**的行：注释里提一嘴不是执行。不加这道过滤，
            # 一个在注释里解释自己的执行器会被误报成重复执行三次。
            hits = 0
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or name not in stripped:
                    continue
                if re.search(rf"(bash|sh|python3?|node|\./|\$PY|\$\{{PY\}}|run\()"
                             rf"[^\n]*{re.escape(name)}", stripped):
                    hits += 1
            if hits:
                n += hits
                where.append(f"{rpath}×{hits}")
        counts[checker] = {"executions": n, "where": where}
    return counts


def cmd_inventory(args):
    root = resolve_root(args)
    runners = find_runners(root)
    checkers = find_checkers(root)
    manifests = find_manifests(root)
    corpus = test_corpus(root)
    counts = execution_counts(root, checkers, runners, manifests)
    registered = {e for m in manifests for e in m["entries"]}
    exempted = {e for m in manifests for e in m["exempt_entries"]}
    totals = {
        "test_files": sum(b["files"] for b in corpus.values()),
        "test_lines": sum(b["lines"] for b in corpus.values()),
        "milestone_files": sum(b["milestone_files"] for b in corpus.values()),
        "milestone_lines": sum(b["milestone_lines"] for b in corpus.values()),
    }
    totals["milestone_share"] = (
        round(totals["milestone_lines"] / totals["test_lines"], 3) if totals["test_lines"] else 0)
    emit({
        "root": str(root), "measured_at": now_iso(),
        "runners": runners,
        "runner_count": len(runners),
        "checkers": len(checkers),
        "manifests": manifests,
        "unregistered_checkers": sorted(set(checkers) - registered - exempted),
        "duplicated_execution": {k: v for k, v in counts.items() if v["executions"] > 1},
        # 零执行 ≠ 违规：合法豁免（被在册门禁内部调用、只能人工跑）也是零。
        # 分开列，别让人对着「合法的零」返工。
        "never_executed_unexempted": sorted(
            k for k, v in counts.items() if v["executions"] == 0 and k not in exempted),
        "never_executed_but_exempt": sorted(
            k for k, v in counts.items() if v["executions"] == 0 and k in exempted),
        "test_corpus": corpus,
        "totals": totals,
        "note": "统计已排除 " + "/".join(sorted(SKIP_DIRS)),
    })


# ---------------------------------------------------------------- blast radius

def cmd_blast_radius(args):
    """哪些测试/门禁在文本里引用了这些路径——「不跑全量」的唯一正当理由。"""
    root = resolve_root(args)
    targets = [t.strip() for t in args.paths]
    # 裸文件名只在全仓唯一时才当匹配键。否则 `scripts/README.md` 会用 "README.md"
    # 去匹配，满仓提到 README 的文件全被算成引用者——实测把一个毫不相干的
    # Node 测试算进了影响面。影响面宁可窄一点也不能虚。
    basename_freq = {}
    for path in walk(root):
        basename_freq[path.name] = basename_freq.get(path.name, 0) + 1
    names = {}
    for t in targets:
        keys = {t}
        base = Path(t).name
        if basename_freq.get(base, 0) <= 1:
            keys.add(base)
        names[t] = keys
    readers = {t: [] for t in targets}
    scanned = 0
    for path in walk(root):
        if path.suffix not in {".py", ".sh", ".ts", ".tsx", ".js", ".mjs", ".yaml", ".yml", ".toml", ".json", ".md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        rel = str(path.relative_to(root))
        for target, keys in names.items():
            if rel == target:
                continue
            if any(k in text for k in keys):
                readers[target].append(rel)
    tests = sorted({r for lst in readers.values() for r in lst
                    if TEST_FILE.match(Path(r).name)})
    others = sorted({r for lst in readers.values() for r in lst
                     if not TEST_FILE.match(Path(r).name)})
    emit({
        "root": str(root), "targets": targets, "files_scanned": scanned,
        "per_target": {t: sorted(v) for t, v in readers.items()},
        "test_readers": tests,
        "non_test_readers": others,
        "verification_set": tests,
        "hint": ("跑 verification_set 即覆盖本次改动的测试影响面。"
                 "non_test_readers 里的文档/配置要人工核一遍有没有悬空引用。"
                 "注意：这只覆盖**文本引用**；改了运行时代码就老老实实跑全量。"),
    })


# ---------------------------------------------------------------- vacuous

# 只认 `len(X) != N`：这是「断言集合恰好这么大」，每次正常增删都要回来改门禁。
# `len(X) == N` 几乎都是解析守卫（len(cells) == 4），不是门禁断言，不报。
HARDCODED = re.compile(r"len\([^)]*\)\s*!=\s*(\d+)")


def cmd_vacuous(args):
    """空转候选：靠「什么都不测」或「一个每次都要手改的常量」来通过的门禁。"""
    root = resolve_root(args)
    findings = []

    for runner in find_runners(root):
        path = root / runner["path"]
        text = path.read_text(encoding="utf-8", errors="replace")
        # 逐行跟踪相位块：一条 pytest 调用可能跨多行续行，跨行大正则不可靠。
        phase, buffer = None, ""
        for raw in text.splitlines():
            line = raw.strip()
            started = re.match(r"if selected ([a-z0-9-]+);", line)
            if started:
                phase, buffer = started.group(1), ""
                continue
            if line == "fi":
                phase, buffer = None, ""
                continue
            if phase is None:
                continue
            buffer = (buffer + " " + line) if buffer else line
            if buffer.endswith("\\"):
                buffer = buffer[:-1]
                continue
            if "pytest" not in buffer:
                buffer = ""
                continue
            targets = re.findall(r"[\w/.$\-{}]*tests?/[\w/.\-]+\.py", buffer)
            selector = re.search(r"-k\s+'([^']+)'", buffer)
            if targets and selector:
                findings.append({
                    "kind": "pytest_selector",
                    "runner": runner["path"], "phase": phase,
                    "targets": [t.split("/")[-1] for t in targets],
                    "selector": selector.group(1),
                    "check": (f"跑 `pytest <目标> -k '{selector.group(1)}' --collect-only`；"
                              f"收集到 0 条就是空转相——选中零条测试然后打印 PASS。"
                              f"另查这些目标是否已被某个全量相整体覆盖：若是，本相纯属重复执行"),
                })
            buffer = ""

    for checker in find_checkers(root):
        path = root / checker
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            if HARDCODED.search(line) and not line.strip().startswith("#"):
                findings.append({
                    "kind": "hardcoded_constant",
                    "file": checker, "line": line_no, "source": line.strip()[:140],
                    "check": ("这类断言把「集合的当前规模/末位名字」写死，"
                              "每次正常变更都要回来改门禁，却拦不住内容改错、编号重复、"
                              "缺失回滚。换成真不变量（唯一性/命名规范/引用完整性）"),
                })
        # 扫描型门禁：grep 一个可能不存在的文件 → 文件没了就静默 PASS
        for match in re.finditer(r"(?:rg|grep)\b[^\n|]*?\$\{?ROOT\}?/([\w/.\-]+)", text):
            referenced = match.group(1)
            if not (root / referenced).exists():
                findings.append({
                    "kind": "vacuous_scan",
                    "file": checker, "scans": referenced,
                    "check": ("被扫描的文件不存在——grep 找不到即返回非零，"
                              "断言直接走 PASS 分支。删除扫描对象时必须同时删掉这段断言"),
                })
    emit({"root": str(root), "candidates": findings, "count": len(findings),
          "note": "这些是**候选**，不是结论。每条都按 check 字段实际验一遍再动。"})


# ---------------------------------------------------------------- snapshot

def slug_for(root: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(root).lower()).strip("-")[-60:]


def cmd_snapshot(args):
    root = resolve_root(args)
    saved = STATE_DIR / slug_for(root)
    saved.mkdir(parents=True, exist_ok=True)
    runners = find_runners(root)
    checkers = find_checkers(root)
    manifests = find_manifests(root)
    corpus = test_corpus(root)
    counts = execution_counts(root, checkers, runners, manifests)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    payload = {
        "label": args.label, "at": now_iso(), "head": head, "root": str(root),
        "runner_count": len(runners),
        "runner_lines": sum(r["lines"] for r in runners),
        "checker_count": len(checkers),
        "registered": sum(m["registered"] for m in manifests),
        "exempt": sum(m["exempt"] for m in manifests),
        "pytest_invocations": sum(r["pytest"] for r in runners),
        "max_duplicate_execution": max([v["executions"] for v in counts.values()] or [0]),
        "test_files": sum(b["files"] for b in corpus.values()),
        "test_lines": sum(b["lines"] for b in corpus.values()),
        "milestone_lines": sum(b["milestone_lines"] for b in corpus.values()),
        "extra": args.extra or {},
    }
    (saved / f"{args.label}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit({"ok": True, "saved": str(saved / f"{args.label}.json"), "snapshot": payload})


def cmd_diff(args):
    root = resolve_root(args)
    saved = STATE_DIR / slug_for(root)
    def load(label):
        path = saved / f"{label}.json"
        if not path.is_file():
            raise Fail(f"没有名为 {label} 的快照：{path}")
        return json.loads(path.read_text(encoding="utf-8"))
    before, after = load(args.before), load(args.after)
    rows = {}
    for key, value in before.items():
        if isinstance(value, (int, float)) and key in after:
            delta = after[key] - value
            rows[key] = {"before": value, "after": after[key], "delta": delta,
                         "pct": (round(delta / value * 100, 1) if value else None)}
    emit({"root": str(root), "before": args.before, "after": args.after,
          "before_head": before.get("head"), "after_head": after.get("head"),
          "metrics": rows,
          "hint": "同一把尺量前后。任何一行变差都要能说出理由，说不出就是回归。"})


def cmd_list(args):
    root = resolve_root(args)
    saved = STATE_DIR / slug_for(root)
    items = []
    if saved.is_dir():
        for path in sorted(saved.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append({k: data.get(k) for k in ("label", "at", "head")})
    emit({"root": str(root), "dir": str(saved), "snapshots": items})


# ---------------------------------------------------------------- CLI

def main():
    parser = argparse.ArgumentParser(description="验证资产审计")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inventory", help="清点：执行器/检查器/清单/测试语料/重复执行")
    p.add_argument("--root"); p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("blast-radius", help="影响面：谁在文本里引用这些路径")
    p.add_argument("paths", nargs="+"); p.add_argument("--root")
    p.set_defaults(func=cmd_blast_radius)

    p = sub.add_parser("vacuous", help="空转候选：靠什么都不测来通过的门禁")
    p.add_argument("--root"); p.set_defaults(func=cmd_vacuous)

    p = sub.add_parser("snapshot", help="记一次度量快照（同一把尺）")
    p.add_argument("--label", required=True); p.add_argument("--root")
    p.add_argument("--extra", type=json.loads)
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("diff", help="对比两次快照")
    p.add_argument("before"); p.add_argument("after"); p.add_argument("--root")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("list", help="列出本仓的快照")
    p.add_argument("--root"); p.set_defaults(func=cmd_list)

    args = parser.parse_args()
    try:
        args.func(args)
    except Fail as exc:
        emit({"ok": False, "error": str(exc)}, code=1)


if __name__ == "__main__":
    main()
