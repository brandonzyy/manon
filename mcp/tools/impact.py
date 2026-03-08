"""Local impact analysis helpers for manon_impact tool."""
from __future__ import annotations

import concurrent.futures
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("manon-mcp")

# Will be injected by parent module
_client = None


def init(client):
    """Inject dependencies."""
    global _client
    _client = client


# ── Git helpers ───────────────────────────────────────

def _detect_git_root(root: Path) -> tuple[Path, str]:
    """Detect git root and compute project prefix. Returns (git_root, prefix_with_slash)."""
    try:
        git_root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(root), capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=5,
        )
        git_root = Path(git_root_result.stdout.strip()).resolve() if git_root_result.returncode == 0 else root
    except Exception:
        git_root = root
    try:
        rel_prefix = root.relative_to(git_root).as_posix()
        if rel_prefix == ".":
            rel_prefix = ""
    except ValueError:
        rel_prefix = ""
    prefix_with_slash = (rel_prefix + "/") if rel_prefix else ""
    return git_root, prefix_with_slash


def _get_changed_files(
    git_root: Path, root: Path, prefix_with_slash: str, commit: str,
) -> tuple[list[str], list[str], str, str] | str:
    """Get changed files from git diff. Returns (changed_files, raw_files, base_commit, commit_info) or error string."""
    base_commit = commit
    try:
        if commit == "HEAD":
            if prefix_with_slash:
                log_cmd = ["git", "log", "-1", "--format=%H", "--", prefix_with_slash]
            else:
                log_cmd = ["git", "log", "-1", "--format=%H"]
            log_result = subprocess.run(log_cmd, cwd=str(git_root), capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=10)
            last_project_commit = log_result.stdout.strip() if log_result.returncode == 0 else ""
            if last_project_commit:
                base_commit = last_project_commit
                diff_cmd = ["git", "diff", f"{last_project_commit}~1", last_project_commit, "--name-only"]
                commit_msg_cmd = ["git", "log", "-1", "--format=%h %s", last_project_commit]
            else:
                diff_cmd = ["git", "diff", "HEAD~1", "--name-only"]
                commit_msg_cmd = ["git", "log", "-1", "--format=%h %s"]
        else:
            base_commit = commit
            diff_cmd = ["git", "diff", f"{commit}~1", commit, "--name-only"]
            commit_msg_cmd = ["git", "log", "-1", "--format=%h %s", commit]

        msg_result = subprocess.run(commit_msg_cmd, cwd=str(git_root), capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=10)
        commit_info = msg_result.stdout.strip() if msg_result.returncode == 0 else commit
        diff_result = subprocess.run(diff_cmd, cwd=str(git_root), capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=10)
        if diff_result.returncode != 0:
            return f"git diff 失败: {diff_result.stderr.strip()}"

        raw_files = [f for f in diff_result.stdout.strip().split("\n") if f]
        # Strict commit isolation: only include files from the specified commit.
        # Working tree changes are NOT merged — they belong to a different scope.

        changed_files = []
        for f in raw_files:
            if prefix_with_slash and f.startswith(prefix_with_slash):
                changed_files.append(f[len(prefix_with_slash):])
            elif not prefix_with_slash:
                changed_files.append(f)
    except Exception as e:
        return f"git 操作失败: {e}"
    return changed_files, raw_files, base_commit, commit_info


def _find_changed_symbols(
    changed_files: list[str], root: Path, git_root: Path,
    prefix_with_slash: str, base_commit: str, commit: str,
) -> list[dict]:
    """Identify symbols affected by changed lines in each file.

    Returns list of dicts: {name, file, added, deleted} with per-symbol diff stats.
    """
    changed_symbols: list[dict] = []
    for cf in changed_files[:15]:
        ext = Path(cf).suffix.lower()
        if ext not in (".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".php"):
            continue
        full_path = root / cf
        if not full_path.exists():
            continue

        # Get changed line ranges
        git_path = (prefix_with_slash + cf) if prefix_with_slash else cf
        try:
            diff_result = subprocess.run(
                ["git", "diff", f"{base_commit}~1", base_commit, "--unified=0", "--", git_path],
                cwd=str(git_root), capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=10,
            )
            if diff_result.returncode != 0:
                continue
            diff_text = diff_result.stdout
        except Exception:
            continue

        # Parse @@ -a,b +c,d @@ to get changed line ranges
        changed_ranges: list[tuple[int, int]] = []
        for line in diff_text.split("\n"):
            if line.startswith("@@"):
                match = re.search(r'\+(\d+)(?:,(\d+))?', line)
                if match:
                    start = int(match.group(1))
                    count = int(match.group(2)) if match.group(2) else 1
                    changed_ranges.append((start, start + count - 1))

        if not changed_ranges:
            continue

        # Parse file to find symbols
        try:
            from codeindex.parser import parse_file
            pr = parse_file(full_path)
            if pr.error:
                continue
        except Exception:
            continue

        # Compute diff stats per symbol
        added_lines = sum(1 for line in diff_text.split("\n") if line.startswith("+") and not line.startswith("+++"))
        deleted_lines = sum(1 for line in diff_text.split("\n") if line.startswith("-") and not line.startswith("---"))

        # Match symbols to changed ranges
        for sym in pr.symbols:
            sym_start = sym.line
            sym_end = sym.end_line if sym.end_line else sym_start
            for r_start, r_end in changed_ranges:
                if not (r_end < sym_start or r_start > sym_end):
                    changed_symbols.append({
                        "name": sym.name,
                        "file": cf,
                        "added": added_lines,
                        "deleted": deleted_lines,
                    })
                    break

    return changed_symbols


# ── Symbol caller query ───────────────────────────────

def _query_symbol_callers(repo_id: str, sym_names: list[str], max_depth: int) -> tuple[list[str], set[str], list[str]]:
    """Query callers for changed symbols. Returns (all_callers, affected_modules, chains)."""
    all_callers: list[str] = []
    affected_modules: set[str] = set()
    chains: list[str] = []

    syms_to_query = sym_names[:30]

    def _query_sym(sym: str) -> tuple[str, dict]:
        try:
            result = _client._get(f"/api/v1/repos/{repo_id}/graph", symbol=sym, depth=1, direction="callers")
            return sym, result
        except Exception:
            return sym, {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_query_sym, s): s for s in syms_to_query}
        for fut in concurrent.futures.as_completed(futures, timeout=15):
            try:
                sym, result = fut.result()
            except Exception:
                continue
            if not result:
                continue
            for r in result.get("relations", []):
                src = r.get("src_id", "")
                tgt = r.get("tgt_id", "")
                kind = r.get("kind", "")
                if kind == "calls" and tgt and sym in tgt:
                    all_callers.append(f"  {src} --calls--> {tgt}")
                    mod = ".".join(src.split(".")[:-1]) if "." in src else src
                    affected_modules.add(mod)
                    # Build propagation chain: sym → direct_caller
                    caller_name = src.split(".")[-1] if "." in src else src
                    chains.append(f"{sym} → {caller_name}")
    return all_callers, affected_modules, chains


# ── Local impact orchestrator ─────────────────────────

def local_impact(repo_id: str, local_path: str, commit: str, max_depth: int) -> str:
    """Client-side impact analysis for local repos using git diff + server graph."""
    root = Path(local_path).resolve()
    parts: list[str] = []

    git_root, prefix_with_slash = _detect_git_root(root)

    result = _get_changed_files(git_root, root, prefix_with_slash, commit)
    if isinstance(result, str):
        return result  # error message
    changed_files, raw_files, base_commit, commit_info = result

    if not changed_files:
        diag_parts = [
            f"commit={commit}", f"git_root={git_root}", f"project={root}",
            f"prefix={prefix_with_slash!r}", f"raw_files={raw_files[:5]}",
        ]
        return f"没有检测到文件变更。\n诊断: {', '.join(diag_parts)}"

    parts.append(f"影响分析: {commit_info}")
    parts.append(f"变更文件 ({len(changed_files)}):")
    for f in changed_files:
        parts.append(f"  {f}")

    changed_symbols_raw = _find_changed_symbols(
        changed_files, root, git_root, prefix_with_slash, base_commit, commit,
    )

    if not changed_symbols_raw:
        parts.append("\n未能精确定位变更符号，按文件级别分析。")
        for cf in changed_files:
            module = cf.rsplit(".", 1)[0].replace("/", ".").replace("\\", ".")
            try:
                result = _client._get(f"/api/v1/repos/{repo_id}/graph", symbol=module, depth=1)
                for r in result.get("relations", [])[:5]:
                    parts.append(f"  {r.get('src_id', '?')} --{r.get('kind', '?')}--> {r.get('tgt_id', '?')}")
            except Exception:
                pass
        return _client._truncate("\n".join(parts))

    # Deduplicate by name, keep first occurrence (with diff stats)
    seen_names: set[str] = set()
    changed_symbols: list[dict] = []
    for s in changed_symbols_raw:
        if s["name"] not in seen_names:
            seen_names.add(s["name"])
            changed_symbols.append(s)

    # Summary mode for large commits
    summary_mode = len(changed_symbols) > 30
    if summary_mode:
        parts.append(f"\n[摘要模式 — 符号过多，按文件聚合]")
        # Aggregate symbols by file
        file_agg: dict[str, dict] = {}
        for s in changed_symbols:
            f = s.get("file", "?")
            if f not in file_agg:
                file_agg[f] = {"count": 0, "added": 0, "deleted": 0}
            file_agg[f]["count"] += 1
            file_agg[f]["added"] += s.get("added", 0)
            file_agg[f]["deleted"] += s.get("deleted", 0)
        parts.append(f"\n变更符号 ({len(changed_symbols)}, 按文件聚合):")
        for f, agg in sorted(file_agg.items()):
            parts.append(f"  {f}: {agg['count']} 个符号 (+{agg['added']}/-{agg['deleted']})")
    else:
        parts.append(f"\n变更符号 ({len(changed_symbols)}):")
        for s in changed_symbols:
            added, deleted = s.get("added", 0), s.get("deleted", 0)
            diff_stat = f"+{added}/-{deleted}" if (added or deleted) else ""
            loc = f" ({s['file']})" if s.get("file") else ""
            parts.append(f"  {s['name']} {diff_stat}{loc}")

    sym_names = [s["name"] for s in changed_symbols]
    all_callers, affected_modules, chains = _query_symbol_callers(repo_id, sym_names, max_depth)

    if all_callers:
        parts.append(f"\n直接调用者 ({len(all_callers)}):")
        for c in all_callers[:20]:
            parts.append(c)

    if affected_modules:
        parts.append(f"\n受影响模块 ({len(affected_modules)}):")
        for m in sorted(affected_modules)[:20]:
            parts.append(f"  {m}")

    if chains:
        parts.append(f"\n传播链路 ({len(chains)}):")
        for c in chains[:15]:
            parts.append(f"  {c}")

    return _client._truncate("\n".join(parts))
