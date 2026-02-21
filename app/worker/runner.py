"""Worker task execution pipeline — run agent loop, capture diffs, self-test, return result.

Flow: baseline diff → graph context → build prompt → load scoped files → agent loop →
      git diff → run tests → retry on failure → revert on final failure → return result.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger("manon.worker.runner")

_TEST_TIMEOUT = 120  # seconds
_MAX_SCOPED_CHARS = 60_000


def _resolve_worker_model() -> tuple[str, str, str, str]:
    """Return (model_name, api_url, api_key, api_format) for the worker primary model."""
    from ..routers.settings import get_runtime_config, get_custom_model
    from ..services.llm import _resolve_model
    cfg = get_runtime_config()
    model_id = cfg.get("worker_model", "")
    if model_id:
        return _resolve_model(model_id)
    return _resolve_model("glm-4.7-fp8")


def _resolve_worker_fallback() -> tuple[str, str, str, str] | None:
    """Return fallback model config, or None if same as primary."""
    from ..routers.settings import get_runtime_config
    from ..services.llm import _resolve_model
    cfg = get_runtime_config()
    fb = cfg.get("worker_model_fallback", "")
    primary = cfg.get("worker_model", "")
    if fb and fb != primary:
        return _resolve_model(fb)
    return None


async def _run_cmd(cmd: str, cwd: str, timeout: int = 60) -> str:
    """Run a shell command, return stdout. Raises on non-zero exit."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"Exit {proc.returncode}: {(err or out)[:500]}")
    return out


async def _revert_files(repo_root: str, files: list[str]) -> None:
    """Revert changed files: git checkout tracked files, delete untracked."""
    if not files:
        return
    tracked = []
    for f in files:
        try:
            await _run_cmd(f'git ls-files --error-unmatch "{f}"', cwd=repo_root, timeout=10)
            tracked.append(f)
        except Exception:
            # Untracked — delete
            abs_path = Path(repo_root) / f
            try:
                abs_path.unlink(missing_ok=True)
            except Exception:
                pass
    if tracked:
        quoted = " ".join(f'"{f}"' for f in tracked)
        try:
            await _run_cmd(f"git checkout -- {quoted}", cwd=repo_root, timeout=15)
        except Exception as exc:
            log.warning("git checkout revert failed: %s", exc)


def _read_file_truncated(abs_path: str, max_lines: int = 600) -> str | None:
    """Read a file, truncate if too long."""
    p = Path(abs_path)
    if not p.is_file():
        return None
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    lines = content.split("\n")
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n// ... truncated ({len(lines)} lines total)"
    return content


async def execute_task(config: dict) -> dict:
    """Complete task execution pipeline. Returns result dict.

    config keys: featureId, taskId, instruction, scopedFiles, context,
                 repoPath, workspace, testCommand
    """
    feature_id = config.get("featureId", "")
    task_id = config.get("taskId", "")
    instruction = config.get("instruction") or "(no instruction)"
    scoped_files: list[str] = config.get("scopedFiles") or []
    context = config.get("context", "")
    pre_graph_context = config.get("graphContext", "")
    repo_root = config.get("repoPath", "")
    workspace = config.get("workspace") or ""
    test_command = config.get("testCommand", "")

    if not repo_root:
        return {"type": "feature-task-failed", "featureId": feature_id, "taskId": task_id,
                "reason": "No repoPath provided"}

    new_changes: list[str] = []
    succeeded = False

    try:
        # 1. Record baseline diff
        try:
            baseline_raw = await _run_cmd("git diff --name-only", cwd=repo_root)
            baseline = set(baseline_raw.strip().split("\n")) - {""}
        except Exception:
            baseline = set()

        # 2. Knowledge graph context (use pre-fetched from Manon + optional local query)
        graph_context = pre_graph_context
        queries_log: list[dict] = []
        if not graph_context:
            try:
                import time as _time
                from matrixone_graph import MatrixoneGraph
                mg = MatrixoneGraph.get(repo_root)
                t0 = _time.monotonic()
                result = await mg.query(instruction, top_k=5, depth=1)
                q_ms = int((_time.monotonic() - t0) * 1000)
                if result.context:
                    graph_context = result.context
                    log.info("Graph context: %d chars (%dms)", len(graph_context), q_ms)
                    queries_log.append({
                        "caller": "worker", "command": f"Worker 上下文检索 (Task #{task_id})",
                        "query": instruction[:120], "duration_ms": q_ms,
                        "context_tokens": len(graph_context) // 2,
                    })
            except Exception as exc:
                log.warning("Graph query failed: %s", exc)

        # 3. Build prompt
        from .prompts import build_system_prompt

        # Look up project name from DB
        project_name = Path(repo_root).name
        system_prompt = build_system_prompt(
            project_name=project_name,
            project_path=repo_root,
            workspace=workspace or None,
            test_command=test_command or None,
            graph_context=graph_context or None,
        )

        sections = [f"# Task #{task_id}", ""]
        sections.extend(["## Instruction", instruction, ""])
        if context:
            sections.extend(["## Context", context, ""])
        if graph_context:
            sections.extend(["## Code Intelligence", graph_context, ""])

        # 4. Load scoped files into prompt
        if scoped_files:
            sections.append("## Source Files (pre-loaded — do NOT waste turns reading these)")
            total_chars = 0
            for rel_path in scoped_files:
                if total_chars >= _MAX_SCOPED_CHARS:
                    break
                abs_path = str(Path(repo_root) / rel_path)
                content = _read_file_truncated(abs_path)
                if not content:
                    continue
                sections.extend([f"\n### {rel_path}", "```", content, "```"])
                total_chars += len(content)
            sections.append("")

        sections.extend(["## Instructions", "Implement the task. Keep changes focused on the task scope."])
        task_prompt = "\n".join(sections)
        log.info("Running agent for task %s (%d chars prompt)", task_id, len(task_prompt))

        # 5. Resolve models
        model_name, api_url, api_key, api_format = _resolve_worker_model()
        fb = _resolve_worker_fallback()
        fb_model = fb[0] if fb else None
        fb_url = fb[1] if fb else None
        fb_key = fb[2] if fb else None
        fb_fmt = fb[3] if fb else None

        # 6. Run agent loop
        from .agent import call_agent
        try:
            agent_result = await call_agent(
                task_prompt, system_prompt, repo_root,
                model_name, api_url, api_key, api_format,
                fb_model, fb_url, fb_key, fb_fmt,
            )
            log.info("Agent completed in %d turns", agent_result.get("turns", 0))
            # Broadcast active worker model (may have degraded to fallback)
            used_model = agent_result.get("active_model", model_name)
            try:
                from ..services.llm import _broadcast_model_status
                await _broadcast_model_status("worker", used_model)
            except Exception:
                pass
            # Collect queries from agent tool calls
            queries_log.extend(agent_result.get("queries", []))
            # Initialize token usage from agent result
            agent_tokens = agent_result.get("token_usage", {})
            token_usage = {
                "prompt": agent_tokens.get("prompt", 0),
                "completion": agent_tokens.get("completion", 0),
                "total": agent_tokens.get("total", 0),
            }
        except Exception as exc:
            log.error("Agent failed: %s", exc)
            return {"type": "feature-task-failed", "featureId": feature_id, "taskId": task_id,
                    "reason": f"Agent error: {exc}"}

        # 7. Detect changes
        try:
            all_diff_raw = await _run_cmd("git diff --name-only", cwd=repo_root)
            all_diff = all_diff_raw.strip().split("\n")
        except Exception:
            all_diff = []
        try:
            untracked_raw = await _run_cmd("git ls-files --others --exclude-standard", cwd=repo_root)
            untracked = untracked_raw.strip().split("\n")
        except Exception:
            untracked = []
        all_changes = list(set(f for f in all_diff + untracked if f))
        new_changes = [f for f in all_changes if f not in baseline]

        if not new_changes:
            log.info("No changes produced by agent for task %s", task_id)
            return {"type": "feature-task-failed", "featureId": feature_id, "taskId": task_id,
                    "reason": "No changes produced"}

        log.info("Task %s changed: %s", task_id, ", ".join(new_changes[:10]))

        # 7b. Capture per-file diffs
        diffs: dict[str, str] = {}
        total_diff_len = 0
        _MAX_FILE_DIFF = 3000
        _MAX_TOTAL_DIFF = 20000
        for f in new_changes:
            if total_diff_len >= _MAX_TOTAL_DIFF:
                break
            try:
                await _run_cmd(f'git ls-files --error-unmatch "{f}"', cwd=repo_root, timeout=10)
                diff_out = await _run_cmd(f'git diff -- "{f}"', cwd=repo_root, timeout=15)
                diffs[f] = diff_out[:_MAX_FILE_DIFF]
            except Exception:
                # Untracked new file — read content as diff
                abs_p = Path(repo_root) / f
                try:
                    content = abs_p.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_DIFF]
                    diffs[f] = f"+++ new file: {f}\n{content}"
                except Exception:
                    diffs[f] = f"+++ new file: {f} (unreadable)"
            total_diff_len += len(diffs.get(f, ""))

        # 7c. Task self-test — verify changes against task objectives
        self_test: dict = {"passed": True, "issues": []}
        try:
            diff_summary = "\n".join(f"--- {fp} ---\n{d[:500]}" for fp, d in list(diffs.items())[:8])
            self_test_prompt = (
                f"# Self-Test: Verify Task Completion\n\n"
                f"## Task Instruction\n{instruction}\n\n"
                f"## Changed Files\n{', '.join(new_changes)}\n\n"
                f"## Diffs\n{diff_summary}\n\n"
                f"## Verification\n"
                f"Check if the changes fully satisfy the task instruction. "
                f"Return JSON: {{\"passed\": true/false, \"issues\": [\"issue1\", ...], \"fix_needed\": true/false}}"
            )
            st_result = await call_agent(
                self_test_prompt, system_prompt, repo_root,
                model_name, api_url, api_key, api_format,
                fb_model, fb_url, fb_key, fb_fmt,
            )
            # Accumulate self-test token usage
            st_tokens = st_result.get("token_usage", {})
            token_usage["prompt"] += st_tokens.get("prompt", 0)
            token_usage["completion"] += st_tokens.get("completion", 0)
            token_usage["total"] += st_tokens.get("total", 0)
            # Parse self-test result
            import json as _json
            st_text = st_result.get("output", "")
            start = st_text.find("{")
            end = st_text.rfind("}") + 1
            if start >= 0 and end > start:
                st_parsed = _json.loads(st_text[start:end])
                self_test = {
                    "passed": st_parsed.get("passed", True),
                    "issues": st_parsed.get("issues", []),
                }
                if st_parsed.get("fix_needed"):
                    log.info("Self-test requests fix, giving agent one chance...")
                    fix_result = await call_agent(
                        f"Self-test found issues: {st_parsed.get('issues', [])}. Fix them.\n\nOriginal task: {instruction}",
                        system_prompt, repo_root,
                        model_name, api_url, api_key, api_format,
                        fb_model, fb_url, fb_key, fb_fmt,
                    )
                    fix_tokens = fix_result.get("token_usage", {})
                    token_usage["prompt"] += fix_tokens.get("prompt", 0)
                    token_usage["completion"] += fix_tokens.get("completion", 0)
                    token_usage["total"] += fix_tokens.get("total", 0)
            log.info("Self-test result: passed=%s, issues=%s", self_test["passed"], self_test.get("issues", []))
        except Exception as st_exc:
            log.warning("Self-test failed: %s", st_exc)
            self_test = {"passed": True, "issues": [f"Self-test error: {st_exc}"]}

        # 8. Run tests (if test_command provided)
        if test_command:
            test_cwd = workspace or repo_root
            log.info("Running tests: %s (cwd=%s)", test_command, test_cwd)
            try:
                test_output = await _run_cmd(test_command, cwd=test_cwd, timeout=_TEST_TIMEOUT)
                log.info("Tests passed")
            except Exception as test_err:
                test_error = str(test_err)[:3000]
                log.warning("Tests failed, giving agent a second chance...")
                # Retry: ask agent to fix test failures
                try:
                    await call_agent(
                        f"Tests failed after your changes. Fix these errors. Do NOT change test expectations.\n\n{test_error}",
                        system_prompt, repo_root,
                        model_name, api_url, api_key, api_format,
                        fb_model, fb_url, fb_key, fb_fmt,
                    )
                except Exception:
                    log.error("Agent retry failed")
                    await _revert_files(repo_root, new_changes)
                    return {"type": "feature-task-failed", "featureId": feature_id, "taskId": task_id,
                            "reason": "Tests failed after retry"}

                try:
                    await _run_cmd(test_command, cwd=test_cwd, timeout=_TEST_TIMEOUT)
                    log.info("Tests passed on second attempt")
                except Exception:
                    log.error("Tests still failing after retry")
                    await _revert_files(repo_root, new_changes)
                    return {"type": "feature-task-failed", "featureId": feature_id, "taskId": task_id,
                            "reason": "Tests failed after retry"}

        # 9. Success
        succeeded = True
        changes_preview = "\n".join(new_changes[:10])
        return {
            "type": "feature-task-done",
            "featureId": feature_id,
            "taskId": task_id,
            "changes": changes_preview,
            "output": agent_result.get("output", ""),
            "diffs": diffs,
            "selfTest": self_test,
            "tokenUsage": token_usage,
            "queries": queries_log,
        }

    except Exception as exc:
        log.error("Task pipeline error: %s", exc)
        return {"type": "feature-task-failed", "featureId": feature_id, "taskId": task_id,
                "reason": str(exc)}
    finally:
        if not succeeded and new_changes:
            await _revert_files(repo_root, new_changes)
