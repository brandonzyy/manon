"""Manon MCP — all MCP tool definitions."""
from __future__ import annotations

import concurrent.futures
import datetime
import json
import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import httpx

from shared.ast_sync import (
    load_projects, save_projects, get_project, set_project,
    find_project_by_repo_id, count_scannable_files,
    ensure_parsers, preview_project_structure, set_custom_excludes,
    SYNC_BATCH_SIZE,
)

log = logging.getLogger("manon-mcp")

# ── Injected dependencies ────────────────────────────
_client = None   # _client module
_sync = None     # _sync module
_hooks = None    # _hooks module
_config = None   # _config module
INLINE_SCAN_LIMIT = 50


def init(client, sync, hooks, config, constants):
    """Inject dependencies from server.py."""
    global _client, _sync, _hooks, _config, INLINE_SCAN_LIMIT
    _client = client
    _sync = sync
    _hooks = hooks
    _config = config
    INLINE_SCAN_LIMIT = constants["INLINE_SCAN_LIMIT"]


# ── Update helpers ───────────────────────────────────
_UPDATE_STATUS_FILE = Path.home() / ".manon" / "update_status.json"


def _write_update_status(ok: bool, lines: list[str]) -> None:
    """Persist update result so next manon_update/init can report it."""
    try:
        _UPDATE_STATUS_FILE.write_text(json.dumps({
            "ok": ok,
            "message": "\n".join(lines),
            "timestamp": datetime.datetime.now().isoformat(),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _read_update_status() -> str | None:
    """Read and clear previous background update result."""
    try:
        if not _UPDATE_STATUS_FILE.exists():
            return None
        data = json.loads(_UPDATE_STATUS_FILE.read_text(encoding="utf-8"))
        _UPDATE_STATUS_FILE.unlink(missing_ok=True)
        tag = "✓" if data.get("ok") else "✗"
        return f"[上次后台更新 {tag}] {data.get('message', '')}"
    except Exception:
        return None


def _do_update() -> list[str]:
    """Execute git pull + pip install. Writes result to status file."""
    install_dir = Path(__file__).resolve().parent.parent
    lines: list[str] = []
    ok = False
    branch = _config._git_branch()

    try:
        result = subprocess.run(
            ["git", "pull", "--quiet", "origin", branch],
            cwd=str(install_dir),
            capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=15,
        )
        git_out = result.stdout.strip()
        if "Already up to date" in git_out or "Already up-to-date" in git_out or not git_out:
            lines.append("代码已是最新，无需更新。")
            ok = True
            _write_update_status(ok, lines)
            return lines
        lines.append(f"代码已更新:\n{git_out}")
    except subprocess.TimeoutExpired:
        lines.append("git pull 超时（15s），请手动执行: cd manon && git pull")
        _write_update_status(False, lines)
        return lines
    except Exception as e:
        lines.append(f"git pull 失败: {e}")
        _write_update_status(False, lines)
        return lines

    req_file = install_dir / "mcp" / "requirements.txt"
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file)],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
        )
        lines.append("依赖已更新。")
        ok = True
    except subprocess.TimeoutExpired:
        lines.append("pip install 超时，请手动执行: pip install -r mcp/requirements.txt")
    except Exception as e:
        lines.append(f"依赖安装失败: {e}")

    lines.append("请重启 Claude Code 使新版本生效。")
    _write_update_status(ok, lines)
    return lines


# ── Local impact analysis helpers ─────────────────────

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
        if ext not in (".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".php"):
            continue
        try:
            git_file_path = (prefix_with_slash + cf) if prefix_with_slash else cf
            if base_commit == commit and commit == "HEAD":
                udiff_ref = f"{base_commit}~1"
            elif commit == "HEAD":
                udiff_ref = f"{base_commit}~1..{base_commit}"
            else:
                udiff_ref = f"{commit}~1..{commit}"
            udiff = subprocess.run(
                ["git", "diff", udiff_ref, "--unified=0", "--", git_file_path],
                cwd=str(git_root), capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=10,
            )
            # Parse hunks: track added/deleted line ranges
            added_ranges: list[tuple[int, int]] = []
            deleted_ranges: list[tuple[int, int]] = []
            for line in udiff.stdout.split("\n"):
                m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if m:
                    del_start, del_count = int(m.group(1)), int(m.group(2)) if m.group(2) else 1
                    add_start, add_count = int(m.group(3)), int(m.group(4)) if m.group(4) else 1
                    if add_count > 0:
                        added_ranges.append((add_start, add_start + add_count - 1))
                    if del_count > 0:
                        deleted_ranges.append((del_start, del_start + del_count - 1))
            changed_lines = set()
            for s, e in added_ranges:
                changed_lines.update(range(s, e + 1))
            for s, e in deleted_ranges:
                changed_lines.add(s)  # deleted lines map to approximate position
            if not changed_lines:
                continue
            full_path = root / cf
            if not full_path.exists():
                continue
            total_added = sum(e - s + 1 for s, e in added_ranges)
            total_deleted = sum(e - s + 1 for s, e in deleted_ranges)
            from codeindex.parser import parse_file
            pr = parse_file(full_path)
            for sym in pr.symbols:
                if hasattr(sym, "line_start") and hasattr(sym, "line_end"):
                    sym_lines = set(range(sym.line_start, sym.line_end + 1))
                    overlap = sym_lines & changed_lines
                    if overlap:
                        # Count added/deleted lines within this symbol's range
                        sym_added = sum(
                            min(e, sym.line_end) - max(s, sym.line_start) + 1
                            for s, e in added_ranges if s <= sym.line_end and e >= sym.line_start
                        )
                        sym_deleted = sum(
                            min(e, sym.line_end) - max(s, sym.line_start) + 1
                            for s, e in deleted_ranges if s <= sym.line_end and e >= sym.line_start
                        )
                        changed_symbols.append({
                            "name": sym.name, "file": cf,
                            "added": sym_added, "deleted": sym_deleted,
                        })
                elif hasattr(sym, "line_number"):
                    if sym.line_number in changed_lines:
                        changed_symbols.append({
                            "name": sym.name, "file": cf,
                            "added": total_added, "deleted": total_deleted,
                        })
        except Exception as e:
            log.debug("Failed to analyze %s: %s", cf, e)
    return changed_symbols


def _find_lazy_import_callers(
    changed_symbols: list[dict], git_root: Path, root: Path,
    prefix_with_slash: str, existing_callers: list[str],
) -> tuple[list[str], set[str], list[str]]:
    """Find callers via lazy (function-body) imports not captured by AST graph.

    Scans for indented 'from <module> import <symbol>' patterns which indicate
    imports inside function bodies — these are invisible to static AST analysis.
    """
    extra_callers: list[str] = []
    extra_modules: set[str] = set()
    extra_chains: list[str] = []

    # Build set of already-known caller keys for dedup
    known: set[str] = set()
    for c in existing_callers:
        # Format: "  src --calls--> tgt"
        parts = c.strip().split(" --calls--> ")
        if len(parts) == 2:
            known.add(parts[0].strip())

    # Group symbol names by their module path
    mod_syms: dict[str, list[str]] = {}
    for s in changed_symbols:
        f = s.get("file", "")
        if not f.endswith(".py"):
            continue
        mod = f[:-3].replace("/", ".").replace("\\", ".")
        name = s["name"]
        # Skip private, dunder, and class-internal names
        if name.startswith("_") or name.startswith("<") or "." in name:
            continue
        mod_syms.setdefault(mod, []).append(name)

    if not mod_syms:
        return extra_callers, extra_modules, extra_chains

    # Build combined grep pattern for all symbols per module
    for mod_path, sym_names in mod_syms.items():
        sym_alt = "|".join(re.escape(n) for n in sym_names)
        # Match indented "from <mod> import ... <sym>" (lazy import)
        pattern = f"^\\s+from\\s+{re.escape(mod_path)}\\s+import\\s+.*\\b({sym_alt})\\b"
        try:
            result = subprocess.run(
                ["git", "grep", "-n", "-E", pattern, "--", "*.py"],
                cwd=str(git_root), capture_output=True, text=True,
                encoding="utf-8", stdin=subprocess.DEVNULL, timeout=10,
            )
        except Exception:
            continue
        if result.returncode != 0 or not result.stdout.strip():
            continue

        for line in result.stdout.strip().split("\n"):
            # Format: "file:lineno:content"
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            grep_file, line_no_str = parts[0], parts[1]
            try:
                line_no = int(line_no_str)
            except ValueError:
                continue

            # Strip git prefix to get project-relative path
            if prefix_with_slash and grep_file.startswith(prefix_with_slash):
                rel_file = grep_file[len(prefix_with_slash):]
            else:
                rel_file = grep_file

            # Find containing function by scanning backwards
            full_path = root / rel_file
            if not full_path.exists():
                continue
            try:
                file_lines = full_path.read_text(encoding="utf-8", errors="replace").split("\n")
            except Exception:
                continue

            import_indent = len(file_lines[line_no - 1]) - len(file_lines[line_no - 1].lstrip())
            func_name = ""
            for i in range(line_no - 2, -1, -1):
                stripped = file_lines[i].lstrip()
                if not stripped.startswith("def ") and not stripped.startswith("async def "):
                    continue
                func_indent = len(file_lines[i]) - len(file_lines[i].lstrip())
                if func_indent < import_indent:
                    m = re.match(r"(?:async\s+)?def\s+(\w+)", stripped)
                    if m:
                        func_name = m.group(1)
                    break

            if not func_name:
                continue

            # Build entity-style ID for the caller
            caller_mod = rel_file[:-3].replace("/", ".").replace("\\", ".") if rel_file.endswith(".py") else rel_file
            caller_id = f"{caller_mod}.{func_name}"

            if caller_id in known:
                continue
            known.add(caller_id)

            # Match which symbol was imported
            for sym_name in sym_names:
                if sym_name in parts[2]:
                    tgt_id = f"{mod_path}.{sym_name}"
                    extra_callers.append(f"  {caller_id} --calls--> {tgt_id}")
                    extra_modules.add(caller_mod)
                    extra_chains.append(f"{sym_name} → {func_name}")
                    break

    return extra_callers, extra_modules, extra_chains


def _query_symbol_callers(
    repo_id: str, changed_symbols: list[str], max_depth: int,
) -> tuple[list[str], set[str], list[str]]:
    """Query graph for callers of changed symbols. Returns (all_callers, affected_modules, chains)."""
    all_callers: list[str] = []
    affected_modules: set[str] = set()
    chains: list[str] = []
    syms_to_query = changed_symbols[:8]

    def _query_sym(sym):
        try:
            return sym, _client._get(f"/api/v1/repos/{repo_id}/graph", symbol=sym, depth=max_depth, timeout=8)
        except Exception:
            return sym, None

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


# ── Local impact (orchestrator) ───────────────────────

def _local_impact(repo_id: str, local_path: str, commit: str, max_depth: int) -> str:
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

    # --- Fix 3: Show diff stats per symbol ---
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

    # Supplement with lazy import callers (function-body imports missed by AST graph)
    lazy_callers, lazy_modules, lazy_chains = _find_lazy_import_callers(
        changed_symbols, git_root, root, prefix_with_slash, all_callers,
    )
    all_callers.extend(lazy_callers)
    affected_modules.update(lazy_modules)
    chains.extend(lazy_chains)

    callers_dedup = list(dict.fromkeys(all_callers)) if all_callers else []
    callers_limit = 10 if summary_mode else 20
    if callers_dedup:
        parts.append(f"\n调用者 ({len(callers_dedup)}):")
        parts.extend(callers_dedup[:callers_limit])

    non_test_modules = sorted(m for m in affected_modules if "test" not in m.lower())
    test_modules = sorted(m for m in affected_modules if "test" in m.lower())

    # Derive directly changed modules from changed_files
    direct_modules: set[str] = set()
    for cf in changed_files:
        m = cf.rsplit(".", 1)[0].replace("/", ".").replace("\\", ".") if "." in cf else cf
        if m:
            direct_modules.add(m)

    direct_non_test = sorted(m for m in non_test_modules if m in direct_modules)
    indirect_non_test = sorted(m for m in non_test_modules if m not in direct_modules)

    if direct_non_test:
        parts.append(f"\n直接变更模块 ({len(direct_non_test)}):")
        for m in direct_non_test:
            parts.append(f"  {m}")

    if indirect_non_test:
        parts.append(f"\n间接波及模块 ({len(indirect_non_test)}):")
        for m in indirect_non_test:
            parts.append(f"  {m}")

    if test_modules:
        parts.append(f"\n受影响测试 ({len(test_modules)}):")
        for t in test_modules[:10]:
            parts.append(f"  {t}")

    # Propagation chains
    if chains:
        chains_dedup = list(dict.fromkeys(chains))
        chains_limit = 5 if summary_mode else 15
        parts.append(f"\n传播链路 ({len(chains_dedup)}):")
        for c in chains_dedup[:chains_limit]:
            parts.append(f"  {c}")

    # --- Fix 4: Depth boundary warning with next-hop estimate ---
    has_deep_callers = len(chains) > 0 and max_depth <= 2
    if has_deep_callers:
        # Count unique callers at the boundary (last node in each chain)
        boundary_nodes: set[str] = set()
        chains_dedup_all = list(dict.fromkeys(chains)) if chains else []
        for c in chains_dedup_all:
            parts_chain = c.split(" → ")
            if len(parts_chain) >= max_depth + 1:
                boundary_nodes.add(parts_chain[-1])
        boundary_count = len(boundary_nodes) if boundary_nodes else len(chains_dedup_all)
        parts.append(f"\n⚠ 当前追踪深度 {max_depth} 跳，边界有 {boundary_count} 个调用者。可用 max_depth={max_depth + 1} 扩大范围。")

    # --- Fix 2: Quantitative risk assessment ---
    total_callers = len(callers_dedup)
    total_modules = len(affected_modules)
    total_tests = len(test_modules)

    # Detect test-only commits
    def _is_test_path(fp: str) -> bool:
        fp_l = fp.replace("\\", "/").lower()
        return (
            fp_l.startswith("tests/") or fp_l.startswith("test/")
            or "/tests/" in fp_l or "/test/" in fp_l
            or fp_l.endswith("_test.py") or "test_" in fp_l.split("/")[-1]
        )

    test_only = bool(changed_files) and all(_is_test_path(f) for f in changed_files)

    # For mixed commits, exclude test-file symbols from public_changed
    if test_only:
        public_changed = []
    else:
        public_changed = [
            s for s in changed_symbols
            if not s["name"].startswith("_") and not _is_test_path(s.get("file", ""))
        ]
    total_added = sum(s.get("added", 0) for s in changed_symbols)
    total_deleted = sum(s.get("deleted", 0) for s in changed_symbols)
    is_core = any(
        any(c in f.lower() for c in ("auth", "security", "payment", "database", "db", "core", "config"))
        for f in changed_files
    )

    # Score-based risk (0-100)
    score = 0
    reasons: list[str] = []
    suggestions: list[str] = []

    if is_core:
        score += 30
        reasons.append("涉及核心模块")
        suggestions.append("核心模块变更需 code review")
    if total_callers >= 10:
        score += 25
        reasons.append(f"{total_callers} 个调用者受影响")
    elif total_callers >= 3:
        score += 15
        reasons.append(f"{total_callers} 个调用者受影响")
    if total_modules >= 5:
        score += 20
        reasons.append(f"波及 {total_modules} 个模块")
    elif total_modules >= 3:
        score += 10
        reasons.append(f"{total_modules} 个模块受影响")
    if len(public_changed) > 0:
        score += min(len(public_changed) * 5, 15)
        reasons.append(f"{len(public_changed)} 个公共 API 变更")
    if total_added + total_deleted > 50:
        score += 10
        reasons.append(f"改动量大 (+{total_added}/-{total_deleted})")

    if score >= 50:
        risk = "high"
    elif score >= 25:
        risk = "medium"
    else:
        risk = "low"

    if not reasons:
        reasons.append("变更范围有限")

    # Test-only commit: cap score and level
    if test_only:
        score = min(score, 20)
        risk = "low"
        reasons = ["仅测试文件变更"]

    # Test coverage assessment
    if total_tests > 0:
        suggestions.append(f"运行受影响测试: {', '.join(test_modules[:3])}")
    elif total_callers > 0:
        suggestions.append("未发现关联测试，建议补充测试覆盖")

    if len(public_changed) > 0:
        suggestions.append("检查公共 API 向后兼容性")

    parts.append(f"\n风险评估: {risk} (score {score}/100)")
    parts.append(f"  指标: 调用者 {total_callers} | 模块 {total_modules} | 测试 {total_tests} | 公共API {len(public_changed)} | 改动 +{total_added}/-{total_deleted}")
    parts.append(f"  原因: {'; '.join(reasons)}")
    if suggestions:
        parts.append(f"  建议: {'; '.join(suggestions)}")

    return _client._truncate("\n".join(parts))


# ── manon_init helpers ────────────────────────────────

def _fmt_stats(s: dict) -> str:
    """Format index stats into a single line."""
    fil = s.get("total_files", s.get("files_indexed", 0))
    ent = s.get("total_entities", s.get("entities_added", 0))
    rel = s.get("total_relations", s.get("relations_added", 0))
    chk = s.get("total_chunks", s.get("chunks_added", 0))
    return f"  📊 文件 {fil}  ·  实体 {ent}  ·  关系 {rel}  ·  块 {chk}"


def _init_existing_project(project_path: str, proj: dict) -> tuple[str, list[str], list[str]]:
    """Handle init for an already-registered local project. Returns (rid, lines, graph_lines)."""
    import time as _time
    rid = proj["repo_id"]
    log.info("Local project found: %s (repo_id=%s)", proj['name'], rid)
    lines = [f"  {proj['name']}  ({rid[:8]})"]
    graph_lines: list[str] = []
    sync = proj.get('last_sync', '') or '—'
    tracked = len(proj.get('file_hashes', {}))

    # Detect languages and ensure parsers before fetching status
    try:
        parser_status = ensure_parsers(project_path)
        log.info("Parser status: %s", parser_status)
        if parser_status:
            all_langs = sorted(parser_status.keys())
            log.info("All langs: %s", all_langs)
            installed = [l for l, s in parser_status.items() if s == "installed"]
            if installed:
                lines.append(f"  🗂️ 语言: {', '.join(all_langs)} (新安装: {', '.join(installed)})")
            else:
                lines.append(f"  🗂️ 语言: {', '.join(all_langs)}")
    except Exception as e:
        log.warning("Parser detection failed: %s", e)

    try:
        t0 = _time.time()
        repo = _client._get(f"/api/v1/repos/{rid}")
        log.info("Fetch repo status took %.1fs", _time.time() - t0)
        status = repo['index_status']
        status_icon = "🟢" if status == "done" else "🟡" if status == "indexing" else "⚪"
        graph_lines.append(f"  {status_icon} 索引 {status}  ·  🕐 同步 {sync}")
        if repo.get("index_stats"):
            graph_lines.append(_fmt_stats(repo["index_stats"]))
    except Exception as e:
        graph_lines.append(f"  🕐 同步 {sync}  ·  📁 跟踪 {tracked} 文件")
        graph_lines.append(f"  ⚠️ 获取服务端状态失败: {e}")
        log.warning("Failed to fetch repo %s status: %s", rid, e)
    bg_msg = _sync._start_bg_sync(project_path=project_path, repo_id=rid,
                                   old_hashes=proj.get("file_hashes", {}))
    graph_lines.append(f"  🔄 {bg_msg}")
    return rid, lines, graph_lines


def _init_match_or_create(
    project_path: str, project_name: str, header_lines: list[str],
) -> tuple[str | None, list[str], list[str]] | str:
    """Match existing repo or create new one. Returns (rid, lines, graph_lines) or error string."""
    try:
        repos = _client._get("/api/v1/repos")
    except Exception as e:
        return "\n".join(header_lines) + f"\n\n  ❌ 获取仓库列表失败: {e}"

    norm = project_path.replace("\\", "/").rstrip("/")
    name = project_name or norm.split("/")[-1]
    matched = None
    for r in repos:
        if r.get("name") == name:
            matched = r
            break

    lines: list[str] = []
    graph_lines: list[str] = []

    if matched:
        rid = matched["id"]
        lines.append(f"  {matched['name']}  ({rid[:8]})")
        status = matched['index_status']
        status_icon = "🟢" if status == "done" else "🟡" if status == "indexing" else "⚪"
        graph_lines.append(f"  {status_icon} 索引 {status}")
        try:
            repo = _client._get(f"/api/v1/repos/{rid}")
            if repo.get("index_stats"):
                graph_lines.append(_fmt_stats(repo["index_stats"]))
        except Exception:
            pass
        if matched.get("source_type") == "local":
            info = {"repo_id": rid, "name": matched["name"], "last_sync": "", "file_hashes": {}}
            set_project(project_path, info)
            lines.append("  ✅ 已注册到本地项目表")
            # Detect languages and ensure parsers before background sync
            try:
                parser_status = ensure_parsers(project_path)
                if parser_status:
                    all_langs = sorted(parser_status.keys())
                    installed = [l for l, s in parser_status.items() if s == "installed"]
                    if installed:
                        lines.append(f"  🗂️ 语言: {', '.join(all_langs)} (新安装: {', '.join(installed)})")
                    else:
                        lines.append(f"  🗂️ 语言: {', '.join(all_langs)}")
            except Exception as e:
                log.warning("Parser detection failed: %s", e)
            bg_msg = _sync._start_bg_sync(project_path=project_path, repo_id=rid,
                                           old_hashes=info.get("file_hashes", {}))
            graph_lines.append(f"  🔄 {bg_msg}")
        return rid, lines, graph_lines

    # No match — create new repo
    try:
        result = _client._post("/api/v1/repos", {"name": name, "source_type": "local"})
        rid = result["id"]
        info = {"repo_id": rid, "name": name, "last_sync": "", "file_hashes": {}}
        set_project(project_path, info)
        lines.append(f"  🆕 {name}  ({rid[:8]})")
        try:
            parser_status = ensure_parsers(project_path)
            if parser_status:
                all_langs = sorted(parser_status.keys())
                lines.append(f"  🗂️ 语言: {', '.join(all_langs)}")
        except Exception:
            pass
        try:
            preview = preview_project_structure(project_path)
            lines.append(f"\n  📂 目录结构预览:\n{preview}")
            lines.append("  💡 如有目录不应被扫描，请调用 manon_configure_excludes 排除")
        except Exception:
            pass
        bg_msg = _sync._start_bg_sync(project_path=project_path, repo_id=rid,
                                       old_hashes=info.get("file_hashes", {}))
        graph_lines.append(f"  🔄 {bg_msg}")
    except Exception as e:
        lines.append(f"\n  ❌ 创建仓库失败: {e}")
        rid = None
    return rid, lines, graph_lines


def _build_health_lines(rid: str) -> list[str]:
    """Build code health section lines."""
    import time as _time
    lines = ["\n💊 代码健康"]
    try:
        t0 = _time.time()
        health_result = _client._get(f"/api/v1/repos/{rid}/code-health", timeout=10)
        log.info("Fetch code-health took %.1fs", _time.time() - t0)
        score = health_result.get("score", 0)
        grade = health_result.get("grade", "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D")
        if not health_result.get("reliable", True):
            lines.append("  ⚠️ 图谱数据不足，评分待索引完成后可用")
        else:
            dims = health_result.get("dimensions", [])
            dim_summary = "  ".join(f"{d['abbr']}{d['value']}" for d in dims)
            lines.append(f"  {grade} {score}/100  {dim_summary}")
    except Exception:
        lines.append("  ⏳ 待索引完成后可用")
    return lines


def _build_hooks_lines(project_path: str) -> list[str]:
    """Build hooks section lines with timeout protection."""
    import concurrent.futures
    import time as _time
    lines = ["\n🔗 钩子"]

    def _do_hooks():
        t0 = _time.time()
        hook_msg = _hooks._install_hook(project_path)
        log.info("Install git hook took %.1fs", _time.time() - t0)
        t1 = _time.time()
        claude_hook_msg = _hooks._install_claude_hooks()
        log.info("Install claude hooks took %.1fs", _time.time() - t1)
        return hook_msg, claude_hook_msg

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_hooks)
            hook_msg, claude_hook_msg = future.result(timeout=10)
        lines.append(f"  {hook_msg}" if hook_msg else "  ✅ Push hook 已就绪")
        lines.append(f"  {claude_hook_msg}" if claude_hook_msg else "  ✅ Claude Code hooks 已就绪")
    except concurrent.futures.TimeoutError:
        log.warning("Hooks installation timed out (10s), skipping")
        lines.append("  ⚠️ 钩子安装超时，已跳过（下次 init 会重试）")
    except Exception as e:
        log.warning("Hooks installation failed: %s", e)
        lines.append(f"  ⚠️ 钩子安装失败: {e}")
    return lines


# ── Tool registration ────────────────────────────────

def register(mcp):
    """Register all MCP tools on the given FastMCP instance."""
    _register_repo_tools(mcp)
    _register_search_tools(mcp)
    _register_index_tools(mcp)
    _register_repo_crud_tools(mcp)
    _register_init_tools(mcp)
    _register_config_tools(mcp)
    _register_query_tools(mcp)
    _register_utility_tools(mcp)
    _register_health_tools(mcp)
    _register_dynamic_tools(mcp)


def _register_repo_tools(mcp):
    """Repo CRUD tools."""

    @mcp.tool()
    def manon_repos_list() -> str:
        """列出当前租户的所有代码仓库及其索引状态。"""
        repos = _client._get("/api/v1/repos")
        if not repos:
            return "没有仓库。用 manon_repos_create 添加。"
        lines = []
        for r in repos:
            icon = {"done": "+", "indexing": "~", "error": "x"}.get(r["index_status"], "-")
            src = " [local]" if r.get("source_type") == "local" else ""
            lines.append(f"  {icon} {r['id']}  {r['name']:<20s}  {r['index_status']}{src}")
        return "\n".join(lines)


def _register_search_tools(mcp):
    """Search, graph, and impact tools."""

    @mcp.tool()
    def manon_search(repo_id: str, query: str, top_k: int = 10, depth: int = 1) -> str:
        """语义搜索代码库。用自然语言描述你要找的内容，返回相关的代码实体、关系和上下文。

        Args:
            repo_id: 仓库 ID（从 manon_repos_list 获取）
            query: 搜索内容，如 "用户认证流程"、"数据库连接池"
            top_k: 返回结果数量（默认 10）
            depth: 图遍历深度（默认 1）
        """
        result = _client._get(f"/api/v1/repos/{repo_id}/search", q=query, top_k=top_k, depth=depth)
        if result.get("context"):
            return _client._truncate(result["context"])
        if not result.get("entities") and not result.get("chunks"):
            return f"未找到与 '{query}' 相关的结果。"
        return _client._format_search(result)

    @mcp.tool()
    def manon_graph(repo_id: str, symbol: str, depth: int = 1, direction: str = "both") -> str:
        """查询代码符号的调用关系和依赖图。

        Args:
            repo_id: 仓库 ID
            symbol: 代码符号名，如 "UserService"、"authenticate"
            depth: 遍历深度（默认 1，最大 3）
            direction: 遍历方向 - "both"(双向), "callers"(只查上游调用者), "callees"(只查下游被调用者)
        """
        result = _client._get(f"/api/v1/repos/{repo_id}/graph", symbol=symbol, depth=depth, direction=direction)
        if result.get("context"):
            return _client._truncate(result["context"])
        return _client._format_graph(result)

    @mcp.tool()
    def manon_impact(repo_id: str, commit: str = "HEAD", max_depth: int = 2) -> str:
        """分析某次 commit 的影响范围。返回变更的符号、直接/间接调用者、受影响模块和风险评估。

        Args:
            repo_id: 仓库 ID
            commit: commit hash（默认 HEAD）
            max_depth: 影响传播深度（默认 2）
        """
        found = find_project_by_repo_id(repo_id)
        if found:
            return _local_impact(repo_id, found[0], commit, max_depth)
        result = _client._get(f"/api/v1/repos/{repo_id}/impact", commit=commit, max_depth=max_depth)
        return _client._format_impact(result)


def _register_index_tools(mcp):
    """Index, status, push-update, repo CRUD tools."""

    @mcp.tool()
    def manon_index(repo_id: str, incremental: bool = True) -> str:
        """触发代码索引构建。索引完成后才能进行搜索和分析。

        Args:
            repo_id: 仓库 ID
            incremental: 增量索引（默认 True），设为 False 全量重建
        """
        found = find_project_by_repo_id(repo_id)
        if found:
            local_path, info = found
            old_hashes = {} if not incremental else info.get("file_hashes", {})
            limit = 0 if not incremental else INLINE_SCAN_LIMIT
            bg_msg = _sync._start_bg_sync(
                repo_id, local_path, old_hashes,
                max_files=limit, full_reindex=not incremental,
            )
            return f"本地索引已提交后台执行。{bg_msg}"
        result = _client._post(f"/api/v1/repos/{repo_id}/index", {"incremental": incremental})
        return f"索引已触发: {result['status']}。用 manon_index_status 查看进度。"

    @mcp.tool()
    def manon_index_status(repo_id: str) -> str:
        """查看仓库的索引状态和统计信息。

        IMPORTANT: 返回结果已格式化，请原样输出给用户，不要总结或改写。

        Args:
            repo_id: 仓库 ID
        """
        result = _client._get(f"/api/v1/repos/{repo_id}/index-status")
        s = result["status"]
        stats = result.get("stats")
        msg = f"状态: {s}"
        if stats:
            total_files = stats.get('total_files', stats.get('files_scanned', stats.get('files_synced', 0)))
            msg += f"\n文件: {total_files}"
            msg += f"\n实体: {stats.get('total_entities', stats.get('entities_added', 0))}"
            msg += f", 关系: {stats.get('total_relations', stats.get('relations_added', 0))}"
            msg += f", 块: {stats.get('total_chunks', stats.get('chunks_added', 0))}"
        prog = _sync._read_sync_progress(repo_id)
        if prog:
            ps = prog.get("status", "")
            pm = prog.get("message", "")
            ts = prog.get("updated_at", "")
            if ps == "syncing":
                msg += f"\n\n🔄 本地同步: {pm}"
                if _sync._is_syncing(repo_id):
                    msg += " (进行中)"
            elif ps == "done":
                msg += f"\n\n✅ 本地同步: {pm}"
            elif ps == "error":
                msg += f"\n\n❌ 本地同步失败: {pm}"
            if ts:
                msg += f"\n   更新于 {ts}"
        return msg


def _register_repo_crud_tools(mcp):
    """Repo create, get, delete, push-update tools."""

    @mcp.tool()
    def manon_repos_create(name: str, git_url: str = "", branch: str = "main", local_path: str = "") -> str:
        """创建新的代码仓库。支持 git URL（服务端 clone）或本地路径（客户端 AST 同步）。

        Args:
            name: 仓库名称
            git_url: Git 仓库地址（可选，服务端会自动 clone）
            branch: 分支名（默认 main）
            local_path: 本地项目路径（与 git_url 二选一，会在本地提取 AST 上传到云端）
        """
        if local_path and not git_url:
            resolved = str(Path(local_path).resolve())
            if not Path(resolved).is_dir():
                return f"路径不存在: {resolved}"
            result = _client._post("/api/v1/repos", {
                "name": name, "branch": branch, "source_type": "local",
            })
            repo_id = result["id"]
            set_project(resolved, {
                "repo_id": repo_id, "name": name,
                "last_sync": "", "file_hashes": {},
            })
            file_count = count_scannable_files(resolved)
            return (
                f"仓库已创建: id={repo_id}, name={name}\n"
                f"本地路径: {resolved}\n"
                f"检测到 {file_count} 个文件，请调用 manon_index {repo_id} 开始索引。"
            )
        body: dict = {"name": name, "branch": branch}
        if git_url:
            body["git_url"] = git_url
        if local_path:
            body["local_path"] = local_path
        result = _client._post("/api/v1/repos", body)
        return f"仓库已创建: id={result['id']}, name={result['name']}, status={result['index_status']}"

    @mcp.tool()
    def manon_repos_get(repo_id: str) -> str:
        """查看仓库详情。

        Args:
            repo_id: 仓库 ID
        """
        result = _client._get(f"/api/v1/repos/{repo_id}")
        return json.dumps(result, indent=2, ensure_ascii=False)

    @mcp.tool()
    def manon_repos_delete(repo_id: str) -> str:
        """删除仓库及其所有索引数据。

        Args:
            repo_id: 仓库 ID
        """
        found = find_project_by_repo_id(repo_id)
        if found:
            local_path, _ = found
            data = load_projects()
            data["projects"].pop(local_path, None)
            save_projects(data)
        _client._delete(f"/api/v1/repos/{repo_id}")
        return f"仓库 {repo_id} 已删除。"

    @mcp.tool()
    def manon_push_update(repo_id: str) -> str:
        """拉取最新代码并增量重建索引。本地仓库会扫描变更文件并上传 AST。

        Args:
            repo_id: 仓库 ID
        """
        found = find_project_by_repo_id(repo_id)
        if found:
            local_path, info = found
            old_hashes = info.get("file_hashes", {})
            bg_msg = _sync._start_bg_sync(repo_id, local_path, old_hashes)
            return f"增量同步已提交后台执行。{bg_msg}"
        try:
            repo = _client._get(f"/api/v1/repos/{repo_id}")
            if repo.get("source_type") == "local":
                return (
                    f"本地项目未注册（可能 manon_init 超时未完成）。\n"
                    f"请先在项目目录执行 manon_init 注册本地项目，再调用 push_update。"
                )
        except Exception:
            pass
        result = _client._post(f"/api/v1/repos/{repo_id}/push-update", {})
        return f"更新已触发: {result['status']}。用 manon_index_status 查看进度。"


def _register_init_tools(mcp):
    """Init and configure tools."""

    @mcp.tool()
    def manon_init(project_path: str, project_name: str = "") -> str:
        """初始化当前项目的 Manon 连接。检查 API 可达性、匹配或创建仓库、展示图谱状态。

        IMPORTANT: 返回结果已格式化，请原样输出给用户，不要总结或改写。

        Args:
            project_path: 项目在本机的绝对路径（通常是当前工作目录）
            project_name: 项目名称（可选，默认从路径推断）
        """
        log.info("manon_init called: path=%s, name=%s", project_path, project_name)
        try:
            health = _client._get_no_auth("/health")
            log.info("Health check OK")
        except Exception as e:
            log.error("Health check failed: %s", e)
            return f"❌ Manon API 不可达 ({_config.API_URL}): {e}\n   请确认 saas 服务已启动。"

        lines = [f"─── 🧠 Manon v{_config.CLIENT_VERSION} {'─' * 28}"]
        lines.append("\n📦 项目状态")
        lines.append("  ✅ API 连接成功")
        prev = _read_update_status()
        if prev:
            lines.append(prev)

        rid = None
        graph_lines: list[str] = []

        proj = get_project(project_path)
        if proj:
            rid, proj_lines, graph_lines = _init_existing_project(project_path, proj)
            lines.extend(proj_lines)
        else:
            result = _init_match_or_create(project_path, project_name, lines)
            if isinstance(result, str):
                return result  # error message
            rid, proj_lines, graph_lines = result
            lines.extend(proj_lines)

        if graph_lines:
            lines.append("\n🕸️ 知识图谱")
            lines.extend(graph_lines)

        if rid:
            lines.extend(_build_health_lines(rid))

        lines.extend(_build_hooks_lines(project_path))

        return "\n".join(lines)

    @mcp.tool()
    def manon_configure_excludes(project_path: str, exclude_patterns: list[str]) -> str:
        """为项目设置自定义排除模式。在 manon_init 返回目录预览后，如果发现有非源码目录未被排除，调用此工具添加排除规则。

        排除模式使用 glob 语法，例如:
        - "**/data/**" 排除所有 data 目录
        - "**/logs/**" 排除所有 logs 目录
        - "scripts/_*" 排除 scripts 下以 _ 开头的文件

        Args:
            project_path: 项目在本机的绝对路径
            exclude_patterns: glob 排除模式列表
        """
        proj = get_project(project_path)
        if not proj:
            return "❌ 项目未注册，请先调用 manon_init"
        set_custom_excludes(project_path, exclude_patterns)
        preview = preview_project_structure(project_path)
        return f"✅ 已设置 {len(exclude_patterns)} 条自定义排除规则\n\n📂 更新后的目录结构:\n{preview}"


def _register_config_tools(mcp):
    """Config and account tools."""

    @mcp.tool()
    def manon_config() -> str:
        """查看当前 Manon 配置和连接状态。

        IMPORTANT: 返回结果已格式化，请原样输出给用户，不要总结或改写。
        """
        log.info("manon_config called")
        lines = [f"─── ⚙️ Manon 配置 {'─' * 28}"]
        lines.append(f"  🏷️ 版本  {_config.CLIENT_VERSION}")
        lines.append(f"  🌐 区域  {_config.REGION}")
        lines.append(f"  🔗 API   {_config.API_URL}")
        import time as _time
        t0 = _time.monotonic()
        try:
            cfg = _client._get("/api/v1/config", timeout=3)
            elapsed = _time.monotonic() - t0
            log.info("manon_config /api/v1/config OK in %.1fs", elapsed)
            lines.append(f"  💎 套餐  {cfg['tier']}")
            lines.append(f"  ⚡ 限速  {cfg['rate_limit']} req/min")
        except Exception as e:
            elapsed = _time.monotonic() - t0
            log.warning("manon_config /api/v1/config failed in %.1fs: %s", elapsed, e)
            lines.append("  ⚠️ 服务  连接超时")
        if _config._update_notice:
            lines.append(_config._update_notice)
        elif not _config._version_checked:
            threading.Thread(target=_config._check_version, daemon=True).start()
        return "\n".join(lines)

    @mcp.tool()
    def manon_account() -> str:
        """查看账户信息：套餐、配额使用情况、近 30 天用量。"""
        try:
            acc = _client._get("/api/v1/account")
        except Exception as e:
            return f"获取账户信息失败: {e}"
        q = acc["quotas"]
        lines = [
            f"套餐: {acc['tier']}",
            f"速率限制: {acc['rate_limit']} req/min",
            f"仓库: {q['repos']['used']}/{q['repos']['limit']}",
            f"深度查询 (今日): {q['deep_query_daily']['used']}/{q['deep_query_daily']['limit']}",
            f"30 天总调用: {acc['usage_30d']}",
        ]
        return "\n".join(lines)


def _register_query_tools(mcp):
    """Deep query tools."""

    @mcp.tool()
    def manon_deep_query(repo_id: str, question: str, max_rounds: int = 3) -> str:
        """深度查询代码知识图谱。自动多轮迭代，确保覆盖问题的所有子方面。

        Args:
            repo_id: 仓库 ID
            question: 要查询的问题（自然语言）
            max_rounds: 最大迭代轮数（默认 3，最大 5）
        """
        try:
            result = _client._post(f"/api/v1/repos/{repo_id}/deep-query", {
                "question": question, "max_rounds": max_rounds,
            }, timeout=30 + max_rounds * 30)
        except httpx.TimeoutException:
            try:
                fallback = _client._get(f"/api/v1/repos/{repo_id}/search", q=question, top_k=10, depth=1)
                ctx = fallback.get("context", "")
                if ctx:
                    return _client._truncate(f"(deep-query 超时，回退到单轮搜索)\n\n{ctx}")
                return f"deep-query 超时且回退搜索无结果。建议拆分为更小的问题后用 manon_search 逐个查询。"
            except Exception as e2:
                log.warning("deep-query timeout, fallback search also failed: %s", e2)
                return "deep-query 超时。建议减少 max_rounds 或拆分为更小的问题用 manon_search 查询。"
        lines = [result["context"]]
        lines.append(f"\n---\n查询轮次: {len(result['rounds'])}")
        if result.get("sub_questions"):
            lines.append(f"子问题: {', '.join(result['sub_questions'])}")
        if result.get("covered"):
            lines.append(f"已覆盖: {', '.join(result['covered'])}")
        for r in result["rounds"]:
            if r.get("queries"):
                lines.append(f"  Round {r['round']}: 补充查询 {r['queries']}")
        return _client._truncate("\n".join(lines))


def _register_utility_tools(mcp):
    """Usage, embedding, and update tools."""

    @mcp.tool()
    def manon_usage(days: int = 30) -> str:
        """查看 API 用量统计。

        Args:
            days: 统计天数（默认 30）
        """
        result = _client._get("/api/v1/usage", days=days)
        return json.dumps(result, indent=2, ensure_ascii=False)

    @mcp.tool()
    def manon_embedding(texts: list[str]) -> str:
        """将文本转换为向量嵌入。

        Args:
            texts: 要嵌入的文本列表（最多 128 条）
        """
        result = _client._post("/api/v1/embedding", {"inputs": texts})
        return f"生成了 {result['count']} 个向量（维度: {len(result['embeddings'][0])}）"

    @mcp.tool()
    def manon_update() -> str:
        """检查并更新 Manon 到最新版本。后台执行 git pull + 依赖安装。

        更新完成后需要重启 Claude Code 使新版本生效。
        """
        install_dir = Path(__file__).resolve().parent.parent
        parts: list[str] = []
        prev = _read_update_status()
        if prev:
            parts.append(prev)
        try:
            branch = _config._git_branch()
            subprocess.run(
                ["git", "fetch", "--quiet", "origin", branch],
                cwd=str(install_dir), capture_output=True, stdin=subprocess.DEVNULL, timeout=5,
            )
            behind = subprocess.run(
                ["git", "rev-list", "--count", f"HEAD..origin/{branch}"],
                cwd=str(install_dir), capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=3,
            ).stdout.strip()
            if behind and int(behind) > 0:
                threading.Thread(target=_do_update, daemon=True).start()
                parts.append(
                    f"发现 {behind} 个新提交（当前 {_config.CLIENT_VERSION}），"
                    f"更新已在后台启动。\n完成后请重启 Claude Code 使新版本生效。"
                )
            else:
                parts.append(f"当前版本 {_config.CLIENT_VERSION} 已是最新。")
        except Exception:
            threading.Thread(target=_do_update, daemon=True).start()
            parts.append(
                f"当前版本: {_config.CLIENT_VERSION}，更新已在后台启动。\n"
                f"完成后请重启 Claude Code 使新版本生效。"
            )
        return "\n".join(parts)


def _register_health_tools(mcp):
    """Health, hooks, and dynamic merge tools."""

    @mcp.tool()
    def manon_code_health(repo_id: str) -> str:
        """分析代码库的健康状况。基于知识图谱计算 8 个维度的健康评分。

        维度: 模块耦合度(MC)、循环依赖(CD)、扇入集中度(FI)、死代码(DC)、
              测试覆盖(TC)、函数规模(FS)、技术债务(TD)、继承深度(ID)

        IMPORTANT: 返回结果已格式化，请原样输出给用户，不要总结或改写。

        Args:
            repo_id: 仓库 ID（从 manon_repos_list 获取）
        """
        result = _client._get(f"/api/v1/repos/{repo_id}/code-health", timeout=60)
        score = result.get("score", 0)
        dims = result.get("dimensions", [])
        grade = result.get("grade", "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D")
        lines = [f"代码健康评分: {score}/100 ({grade})"]
        lines.append(f"实体: {result.get('entity_count', 0)}, 关系: {result.get('relation_count', 0)}")
        if not result.get("reliable", True):
            lines.append("⚠ 图谱数据为空，评分不可靠。请先运行 manon_index 重建索引。")
        lines.append("")
        for d in dims:
            bar = "█" * d["value"] + "░" * (10 - d["value"])
            lines.append(f"  {d['abbr']:>2s} {d['name']:<6s} {bar} {d['value']}/10 (权重{d['weight']})")
            detail = d.get("detail", {})
            if detail:
                info = ", ".join(f"{k}={v}" for k, v in detail.items() if not isinstance(v, list))
                if info:
                    lines.append(f"     {info}")
        return "\n".join(lines)

    @mcp.tool()
    def manon_setup_hooks(project_path: str) -> str:
        """为项目安装 git pre-push hook，push 后自动更新知识图谱并输出代码健康评分。

        Args:
            project_path: 项目在本机的绝对路径
        """
        resolved = Path(project_path).resolve()
        if not (resolved / ".git").is_dir():
            return f"不是 git 仓库: {resolved}"
        result = _hooks._install_hook(project_path)
        _hooks._persist_api_config()
        if result:
            return f"{result}\ngit push 后将自动更新知识图谱并输出代码健康评分。"
        return "pre-push hook 已存在，API 配置已更新。"


def _register_dynamic_tools(mcp):
    """Dynamic edge merge tools."""

    @mcp.tool()
    def manon_merge_dynamic(repo_id: str, deps_path: str = "dynamic-deps.json") -> str:
        """合并运行时追踪的动态调用边到知识图谱。

        支持两种格式（自动检测）：
        - Python 格式: {"caller->callee": count} — 由 pytest --trace-calls 生成
        - JS/TS 格式: [{"from": path, "to": path}] — 由 Module._load hook 生成

        动态边使用 file_path="__dynamic__" 标记，不会与静态 AST 边冲突。

        Args:
            repo_id: 仓库 ID
            deps_path: 动态依赖文件路径（默认 dynamic-deps.json，也支持 .manon-runtime-deps.json）
        """
        # Try multiple default paths
        p = Path(deps_path)
        if not p.exists() and deps_path == "dynamic-deps.json":
            alt = Path(".manon-runtime-deps.json")
            if alt.exists():
                p = alt
        if not p.exists():
            return f"文件不存在: {p.resolve()}\n请先运行 pytest --trace-calls 或 vitest 生成依赖文件"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return f"读取 {p.name} 失败: {e}"
        if not data:
            return f"{p.name} 为空，没有动态边可合并。"

        # Auto-detect format: list = raw file-path edges, dict = pre-resolved
        body: dict = {}
        if isinstance(data, list):
            # JS/TS raw format: [{"from": ..., "to": ...}]
            found = find_project_by_repo_id(repo_id)
            project_root = found[0] if found else str(Path.cwd())
            body = {"raw_edges": data, "project_root": project_root}
            fmt = "JS/TS 文件路径"
            count = len(data)
        elif isinstance(data, dict):
            body = {"edges": data}
            fmt = "Python 实体 ID"
            count = len(data)
        else:
            return f"不支持的格式: {type(data).__name__}，期望 dict 或 list"

        try:
            result = _client._post(
                f"/api/v1/repos/{repo_id}/merge-dynamic",
                body,
                timeout=30,
            )
            added = result.get("added", 0)
            removed = result.get("removed", 0)
            skipped = result.get("skipped", 0)
            resolved = result.get("resolved_from_raw", 0)
            lines = [
                "动态边合并完成。",
                f"  格式: {fmt}  来源: {p.name} ({count} 条)",
                f"  添加: {added}  移除旧边: {removed}  跳过: {skipped}",
            ]
            if resolved:
                lines.append(f"  路径解析: {resolved} 条边从文件路径转换为实体 ID")
            return "\n".join(lines)
        except Exception as e:
            return f"合并失败: {e}"
