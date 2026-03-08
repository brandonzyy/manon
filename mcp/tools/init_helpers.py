"""Helper functions for manon_init tool."""
from __future__ import annotations

import concurrent.futures
import logging
import time
from pathlib import Path

from shared.ast_sync import (
    ensure_parsers, detect_test_patterns, collect_directory_signals,
    set_custom_excludes, set_project, analyze_index_coverage,
)

log = logging.getLogger("manon-mcp")

# Will be injected by parent module
_client = None
_sync = None
_hooks = None


def init(client, sync, hooks):
    """Inject dependencies."""
    global _client, _sync, _hooks
    _client = client
    _sync = sync
    _hooks = hooks


# ── Formatting helpers ────────────────────────────────

def _fmt_stats(s: dict) -> str:
    """Format index stats into a single line."""
    fil = s.get("total_files", s.get("files_indexed", 0))
    ent = s.get("total_entities", s.get("entities_added", 0))
    rel = s.get("total_relations", s.get("relations_added", 0))
    chk = s.get("total_chunks", s.get("chunks_added", 0))
    return f"  📊 文件 {fil}  ·  实体 {ent}  ·  关系 {rel}  ·  块 {chk}"


# ── Smart analysis ────────────────────────────────────

def _run_smart_analysis(project_path: str, rid: str, proj: dict) -> list[str]:
    """Run LLM-based directory relevance analysis. Returns display lines.

    Only runs once per project (skipped if custom_excludes already set or
    if smart_analysis_done flag is set). Auto-applies skip recommendations
    and installs missing parsers for directories worth indexing.
    """
    # Skip if already analyzed
    if proj.get("smart_analysis_done"):
        return []
    # Skip if user already set custom excludes manually
    if proj.get("custom_excludes"):
        return []

    lines: list[str] = []
    try:
        signals = collect_directory_signals(project_path)
        dirs = signals.get("directories", {})
        if not dirs:
            return []

        result = _client._post(
            f"/api/v1/repos/{rid}/analyze-structure",
            {"signals": signals},
            timeout=30,
        )
        analyzed = result.get("directories", [])
        if not analyzed:
            return []

        skip_dirs = [d for d in analyzed if d["action"] == "skip"]
        index_dirs = [d for d in analyzed if d["action"] == "index"]

        # Auto-apply exclusions for skip directories
        if skip_dirs:
            exclude_patterns = [f"**/{d['name']}/**" for d in skip_dirs]
            set_custom_excludes(project_path, exclude_patterns)

        # For index directories, check if any need additional language parsers.
        # Uses codeindex's combined extension map (specialized + generic).
        supported = set(signals.get("supported_languages", []))
        try:
            from codeindex.parser import get_all_extensions
            _EXT_TO_LANG = get_all_extensions()
        except ImportError:
            _EXT_TO_LANG = {
                ".py": "python", ".js": "javascript", ".ts": "typescript",
                ".tsx": "tsx", ".php": "php", ".java": "java",
            }
        needed_langs: set[str] = set()
        for d in index_dirs:
            dir_info = dirs.get(d["name"], {})
            exts = dir_info.get("extensions", {})
            for ext, count in exts.items():
                lang = _EXT_TO_LANG.get(ext)
                if lang and lang not in supported and count >= 5:
                    needed_langs.add(lang)

        if needed_langs:
            try:
                from codeindex.parser_installer import install_parsers
                install_result = install_parsers(needed_langs)
                installed = [l for l, s in install_result.items() if s == "installed"]
                if installed:
                    lines.append(f"  🗂️ 新增语言解析器: {', '.join(installed)}")
                    log.info("Installed parsers for smart-analysis: %s", install_result)
            except Exception as e:
                log.warning("Failed to install additional parsers: %s", e)

        # Build display lines
        lines.append("  🧠 智能分析")
        for d in analyzed:
            icon = "✅" if d["action"] == "index" else "⊘ "
            lines.append(f"    {icon} {d['name']}/  {d.get('reason', '')}")

        if skip_dirs:
            names = ", ".join(d["name"] for d in skip_dirs)
            lines.append(f"  💡 已自动排除: {names}")

        # Mark as done so we don't re-run
        proj["smart_analysis_done"] = True
        set_project(project_path, proj)

    except Exception as e:
        log.warning("Smart analysis failed (non-blocking): %s", e)

    return lines


# ── Common init steps ─────────────────────────────────

def _detect_languages(project_path: str, lines: list[str]) -> None:
    """Detect project languages and install missing parsers."""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ensure_parsers, project_path)
            parser_status = future.result(timeout=30)
        log.info("Parser status: %s", parser_status)
        if parser_status:
            all_langs = sorted(parser_status.keys())
            installed = [l for l, s in parser_status.items() if s == "installed"]
            if installed:
                lines.append(f"  🗂️ 语言: {', '.join(all_langs)} (新安装: {', '.join(installed)})")
            else:
                lines.append(f"  🗂️ 语言: {', '.join(all_langs)}")
    except concurrent.futures.TimeoutError:
        log.warning("Parser detection timed out after 30s")
        lines.append("  ⚠️ 语言检测超时，跳过")
    except Exception as e:
        log.warning("Parser detection failed: %s", e)


def _detect_tests(project_path: str, lines: list[str]) -> None:
    """Auto-detect test frameworks and append exclusion report."""
    try:
        _test_pats, test_report = detect_test_patterns(Path(project_path).resolve())
        if test_report:
            lines.append(f"  🧪 测试排除: {', '.join(test_report)}")
    except Exception as e:
        log.warning("Test framework detection failed: %s", e)


def _run_post_register(project_path: str, rid: str, proj: dict,
                       lines: list[str], graph_lines: list[str],
                       progress_cb=None) -> None:
    """Common post-registration steps: smart analysis, bg sync, index coverage.

    smart_analysis must run first (it may update exclude rules that affect
    scanning). After that, bg_sync and index_coverage are independent and
    run in parallel.

    Args:
        progress_cb: optional callable(pct, msg) for progress reporting (thread-safe)
    """
    # Phase 1: smart analysis (may change exclude rules)
    if progress_cb:
        progress_cb(35, "🧠 运行智能分析...")
    log.info("_run_post_register: smart_analysis starting")
    smart_lines = _run_smart_analysis(project_path, rid, proj)
    if smart_lines:
        lines.extend(smart_lines)

    # Phase 2: bg_sync + index_coverage in parallel
    if progress_cb:
        progress_cb(45, "📊 分析索引覆盖...")
    log.info("_run_post_register: bg_sync + coverage starting")
    bg_result = {"msg": ""}
    coverage_result = {"text": ""}

    def _do_bg_sync():
        if progress_cb:
            progress_cb(50, "🔄 启动文件同步...")
        bg_result["msg"] = _sync._start_bg_sync(
            project_path=project_path, repo_id=rid,
            old_hashes=proj.get("file_hashes", {}))

    def _do_coverage():
        try:
            coverage_result["text"] = analyze_index_coverage(
                project_path, proj.get("file_hashes", {}))
        except Exception as e:
            log.warning("Index coverage analysis failed: %s", e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_do_bg_sync), pool.submit(_do_coverage)]
        concurrent.futures.wait(futures, timeout=30)

    graph_lines.append(f"  🔄 {bg_result['msg']}")
    if coverage_result["text"]:
        graph_lines.append(f"\n{coverage_result['text']}")
    log.info("_run_post_register: done")


# ── Init workflows ────────────────────────────────────

def _init_existing_project(project_path: str, proj: dict,
                           progress_cb=None) -> tuple[str, list[str], list[str]]:
    """Handle init for an already-registered local project. Returns (rid, lines, graph_lines).

    Phase 1 runs _detect_languages, _detect_tests, and fetch repo status concurrently.
    Phase 2 (_run_post_register) runs after Phase 1 since it depends on proj/lines.

    Args:
        progress_cb: optional callable(pct, msg) for progress reporting (thread-safe)
    """
    rid = proj["repo_id"]
    log.info("Local project found: %s (repo_id=%s)", proj['name'], rid)
    lines = [f"  {proj['name']}  ({rid[:8]})"]
    graph_lines: list[str] = []
    sync = proj.get('last_sync', '') or '—'
    tracked = len(proj.get('file_hashes', {}))

    # Phase 1: run independent steps concurrently
    if progress_cb:
        progress_cb(20, "🔍 检测项目语言...")
    lang_lines: list[str] = []
    test_lines: list[str] = []
    repo_result = {"ok": False}

    def _do_detect_languages():
        _detect_languages(project_path, lang_lines)

    def _do_detect_tests():
        if progress_cb:
            progress_cb(25, "🧪 检测测试框架...")
        _detect_tests(project_path, test_lines)

    def _do_fetch_repo():
        if progress_cb:
            progress_cb(28, "📡 获取服务端状态...")
        try:
            t0 = time.time()
            repo = _client._get(f"/api/v1/repos/{rid}")
            log.info("Fetch repo status took %.1fs", time.time() - t0)
            repo_result["ok"] = True
            repo_result["repo"] = repo
        except Exception as e:
            repo_result["error"] = e
            log.warning("Failed to fetch repo %s status: %s", rid, e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_do_detect_languages),
            pool.submit(_do_detect_tests),
            pool.submit(_do_fetch_repo),
        ]
        concurrent.futures.wait(futures, timeout=30)

    if progress_cb:
        progress_cb(30, "📋 整理项目信息...")
    lines.extend(lang_lines)
    lines.extend(test_lines)

    if repo_result["ok"]:
        repo = repo_result["repo"]
        status = repo['index_status']
        status_icon = "🟢" if status == "done" else "🟡" if status == "indexing" else "⚪"
        graph_lines.append(f"  {status_icon} 索引 {status}  ·  🕐 同步 {sync}")
        if repo.get("index_stats"):
            graph_lines.append(_fmt_stats(repo["index_stats"]))
    else:
        graph_lines.append(f"  🕐 同步 {sync}  ·  📁 跟踪 {tracked} 文件")
        graph_lines.append(f"  ⚠️ 获取服务端状态失败: {repo_result.get('error', '?')}")

    # Phase 2: post-register depends on Phase 1 results
    _run_post_register(project_path, rid, proj, lines, graph_lines,
                       progress_cb=progress_cb)
    return rid, lines, graph_lines


def _init_match_or_create(
    project_path: str, project_name: str, header_lines: list[str],
    progress_cb=None,
) -> tuple[str | None, list[str], list[str]] | str:
    """Match existing repo or create new one. Returns (rid, lines, graph_lines) or error string.

    Args:
        progress_cb: optional callable(pct, msg) for progress reporting (thread-safe)
    """
    try:
        repos = _client._get("/api/v1/repos")
    except Exception as e:
        return "\n".join(header_lines) + f"\n\n  ❌ 获取仓库列表失败: {e}"

    # Infer project name from path if not provided
    name = project_name or Path(project_path).resolve().name
    lines = []
    graph_lines: list[str] = []

    # Try to match existing local repo by name
    matched = None
    for r in repos:
        if r.get("source_type") == "local" and r["name"] == name:
            matched = r
            break

    if matched:
        rid = matched["id"]
        lines.append(f"  {name}  ({rid[:8]})")
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

            if progress_cb:
                progress_cb(20, "🔍 检测项目语言...")
            _detect_languages(project_path, lines)
            if progress_cb:
                progress_cb(25, "🧪 检测测试框架...")
            _detect_tests(project_path, lines)
            _run_post_register(project_path, rid, info, lines, graph_lines,
                               progress_cb=progress_cb)

        return rid, lines, graph_lines

    # No match — create new repo
    try:
        result = _client._post("/api/v1/repos", {"name": name, "source_type": "local"})
        rid = result["id"]
        info = {"repo_id": rid, "name": name, "last_sync": "", "file_hashes": {}}
        set_project(project_path, info)
        lines.append(f"  🆕 {name}  ({rid[:8]})")

        if progress_cb:
            progress_cb(20, "🔍 检测项目语言...")
        _detect_languages(project_path, lines)
        if progress_cb:
            progress_cb(25, "🧪 检测测试框架...")
        _detect_tests(project_path, lines)
        _run_post_register(project_path, rid, info, lines, graph_lines,
                           progress_cb=progress_cb)
    except Exception as e:
        lines.append(f"\n  ❌ 创建仓库失败: {e}")
        rid = None

    return rid, lines, graph_lines


# ── Health and hooks ──────────────────────────────────

def _build_health_lines(rid: str) -> list[str]:
    """Fetch and format code health score.

    Uses a short timeout (5s) since this is non-critical and the API
    returns 400 for newly created repos that haven't been indexed yet.
    """
    lines = []
    try:
        health = _client._get(f"/api/v1/repos/{rid}/code-health", timeout=5)
        score = health.get("score", 0)
        grade = health.get("grade", "?")
        dims = health.get("dimensions", {})
        dim_str = "  ".join(f"{k}{v}" for k, v in dims.items())
        lines.append(f"\n💊 代码健康")
        lines.append(f"  {grade} {score:.1f}/100  {dim_str}")
    except Exception as e:
        log.debug("Code health not available (expected for new repos): %s", e)
    return lines


def _build_hooks_lines(project_path: str) -> list[str]:
    """Install git hooks and Claude Code hooks.

    Runs synchronously — hook installation is fast (<1s) and using
    ThreadPoolExecutor caused 10-20s GIL contention delays on Windows
    when background sync threads were active.
    """
    log.info("_build_hooks_lines starting for %s", project_path)
    lines = ["\n🔗 钩子"]

    try:
        t0 = time.time()
        hook_msg = _hooks._install_hook(project_path)
        log.info("Install git hook took %.1fs", time.time() - t0)
        t1 = time.time()
        claude_hook_msg = _hooks._install_claude_hooks()
        log.info("Install claude hooks took %.1fs", time.time() - t1)
        lines.append(f"  {hook_msg}" if hook_msg else "  ✅ Push hook 已就绪")
        lines.append(f"  {claude_hook_msg}" if claude_hook_msg else "  ✅ Claude Code hooks 已就绪")
    except Exception as e:
        log.warning("Hooks installation failed: %s", e)
        lines.append(f"  ⚠️ 钩子安装失败: {e}")

    return lines
