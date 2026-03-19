"""Local impact analysis helpers for manon_impact tool.

NOTE: git diff parsing and symbol extraction here intentionally duplicate
matrixone_graph/impact/{git_parser,symbol_extractor}.py. This module runs
in the local MCP process and must handle monorepo subpath stripping that
the server-side classes do not support. Merging would add an undesired
cross-module dependency and break the client/server boundary.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("manon-mcp")


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


_SUPPORTED_EXTS = frozenset((".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".php"))


def _parse_diff_ranges(diff_text: str) -> list[tuple[int, int]]:
    """Parse @@ hunk headers to extract changed line ranges."""
    ranges: list[tuple[int, int]] = []
    for line in diff_text.split("\n"):
        if line.startswith("@@"):
            m = re.search(r'\+(\d+)(?:,(\d+))?', line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                ranges.append((start, start + count - 1))
    return ranges


def _find_changed_symbols(
    changed_files: list[str], root: Path, git_root: Path,
    prefix_with_slash: str, base_commit: str, commit: str,
) -> list[dict]:
    """Identify symbols affected by changed lines in each file."""
    changed_symbols: list[dict] = []
    for cf in changed_files[:15]:
        if Path(cf).suffix.lower() not in _SUPPORTED_EXTS:
            continue
        full_path = root / cf
        if not full_path.exists():
            continue

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

        changed_ranges = _parse_diff_ranges(diff_text)
        if not changed_ranges:
            continue

        try:
            from codeindex.parser import parse_file
            pr = parse_file(full_path)
            if pr.error:
                continue
        except Exception:
            continue

        added_lines = sum(1 for line in diff_text.split("\n") if line.startswith("+") and not line.startswith("+++"))
        deleted_lines = sum(1 for line in diff_text.split("\n") if line.startswith("-") and not line.startswith("---"))

        for sym in pr.symbols:
            sym_start = getattr(sym, "line_start", getattr(sym, "line", 0))
            sym_end = getattr(sym, "line_end", getattr(sym, "end_line", 0)) or sym_start
            for r_start, r_end in changed_ranges:
                if not (r_end < sym_start or r_start > sym_end):
                    changed_symbols.append({"name": sym.name, "file": cf, "added": added_lines, "deleted": deleted_lines})
                    break

    return changed_symbols


# ── Local impact orchestrator ─────────────────────────

def _format_changed_symbols_section(changed_symbols: list[dict]) -> list[str]:
    """Format the changed symbols section. Returns lines."""
    if not changed_symbols:
        return ["\n未能精确定位变更符号，按文件级别分析。"]
    parts = []
    if len(changed_symbols) > 30:
        parts.append("\n[摘要模式 — 符号过多，按文件聚合]")
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
    return parts


def _format_local_impact_output(
    commit_info: str, changed_files: list[str], changed_symbols: list[dict], server_result: dict, client,
) -> str:
    """Format the local impact analysis output."""
    parts: list[str] = [f"影响分析: {commit_info}", f"变更文件 ({len(changed_files)}):"]
    parts.extend(f"  {f}" for f in changed_files)
    parts.extend(_format_changed_symbols_section(changed_symbols))

    for label, key, limit in [("直接调用者", "direct_callers", 20),
                                ("受影响模块", "affected_modules", 20),
                                ("传播链路", "propagation_chains", 15)]:
        items = server_result.get(key, [])
        if items:
            parts.append(f"\n{label} ({len(items)}):")
            parts.extend(f"  {c}" for c in items[:limit])

    return client._truncate("\n".join(parts))


def local_impact(repo_id: str, local_path: str, commit: str, max_depth: int, *, client) -> str:
    """Client-side impact analysis: local git diff + single compound API for caller resolution."""
    root = Path(local_path).resolve()
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

    changed_symbols_raw = _find_changed_symbols(
        changed_files, root, git_root, prefix_with_slash, base_commit, commit,
    )

    # Deduplicate by name
    seen_names: set[str] = set()
    changed_symbols: list[dict] = []
    for s in changed_symbols_raw:
        if s["name"] not in seen_names:
            seen_names.add(s["name"])
            changed_symbols.append(s)

    # Single compound API call — server resolves all callers in bulk
    try:
        server_result = client._post(
            f"/api/v1/repos/{repo_id}/impact-local",
            {
                "commit_info": commit_info,
                "changed_files": changed_files,
                "changed_symbols": changed_symbols,
                "max_depth": max_depth,
            },
            timeout=30,
        )
    except Exception as exc:
        log.warning("impact-local API failed: %s", exc)
        server_result = {}

    return _format_local_impact_output(commit_info, changed_files, changed_symbols, server_result, client)
