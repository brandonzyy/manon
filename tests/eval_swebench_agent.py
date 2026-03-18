"""SWE-bench Verified benchmark — tests Manon's agent pipeline on real GitHub issues.

Approach (no Docker):
  1. Clone repo at base_commit into a temp dir
  2. Apply test_patch (adds the failing tests)
  3. Run agent with problem_statement
  4. Run FAIL_TO_PASS tests — check if they now pass

Limitations vs official eval:
  - No isolated environment per repo (deps installed into current venv)
  - May fail on repos with complex/conflicting dependencies
  - Results are approximate; official score requires Docker

Usage:
    python tests/eval_swebench_agent.py --limit 5 --repo psf/requests
    python tests/eval_swebench_agent.py --limit 10 --difficulty "<15 min fix"
    python tests/eval_swebench_agent.py --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── Bootstrap ────────────────────────────────────────────────────────────────

MANON_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MANON_ROOT))

# ── Config ────────────────────────────────────────────────────────────────────

API_URL = "https://api.matrixone.online/v1/chat/completions"
API_KEY = "sk-f05sj8cb25syBlnH3pUFN9TuczxgwtEtIEwQ5PEtD22sxeH1"
DEFAULT_MODEL = "glm-4.7-fp8"

OUTPUT_FILE = Path(__file__).parent / "swebench_agent_samples.jsonl"
REPO_CACHE = Path(__file__).parent / ".repo_cache"

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("eval_swe")

SYSTEM_PROMPT = """\
You are an expert software engineer fixing a real GitHub issue.
You have tools to read, edit, write files, and run commands in the repository.
Analyze the issue, find the root cause, and fix it by modifying the source code.
Do not modify test files. Do not explain — just fix.
"""

# ── Git helpers ───────────────────────────────────────────────────────────────

def _run(cmd: str, cwd: str, timeout: int = 120) -> tuple[int, str, str]:
    r = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=True, timeout=timeout,
    )
    stdout = r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
    stderr = r.stderr.decode("utf-8", errors="replace") if r.stderr else ""
    return r.returncode, stdout, stderr


def clone_repo(repo: str, base_commit: str) -> str:
    """Clone repo at base_commit into a temp dir. Uses cache for the bare clone."""
    REPO_CACHE.mkdir(parents=True, exist_ok=True)
    cache_dir = REPO_CACHE / repo.replace("/", "__")

    # Bare clone into cache if not present
    if not cache_dir.exists():
        log.info(f"Cloning {repo} into cache...")
        rc, _, err = _run(
            f"git clone --bare https://github.com/{repo}.git {cache_dir}",
            cwd=str(REPO_CACHE), timeout=300,
        )
        if rc != 0:
            raise RuntimeError(f"Clone failed: {err}")
    else:
        # Fetch latest
        _run("git fetch --all", cwd=str(cache_dir), timeout=60)

    # Create working copy from cache
    tmpdir = tempfile.mkdtemp(prefix="swe_")
    rc, _, err = _run(
        f"git clone {cache_dir} {tmpdir}",
        cwd=str(REPO_CACHE), timeout=60,
    )
    if rc != 0:
        raise RuntimeError(f"Local clone failed: {err}")

    # Checkout base commit
    rc, _, err = _run(f"git checkout {base_commit}", cwd=tmpdir, timeout=30)
    if rc != 0:
        raise RuntimeError(f"Checkout {base_commit} failed: {err}")

    return tmpdir


def apply_patch(patch_text: str, cwd: str) -> bool:
    """Apply a unified diff patch. Returns True on success."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False, encoding="utf-8") as f:
        f.write(patch_text)
        patch_file = f.name
    try:
        rc, _, err = _run(f"git apply --whitespace=fix {patch_file}", cwd=cwd)
        if rc != 0:
            # Try with --reject as fallback
            rc, _, err = _run(f"patch -p1 < {patch_file}", cwd=cwd)
        return rc == 0
    finally:
        os.unlink(patch_file)


def install_deps(cwd: str) -> bool:
    """Try to install the package in editable mode."""
    rc, _, _ = _run(f"{sys.executable} -m pip install -e . -q --no-deps", cwd=cwd, timeout=120)
    return rc == 0


def run_tests(test_ids: list[str], cwd: str, timeout: int = 60) -> tuple[int, int, str]:
    """Run pytest on specific test IDs. Returns (passed, total, output)."""
    if not test_ids:
        return 0, 0, ""
    ids_str = " ".join(f'"{t}"' for t in test_ids)
    rc, stdout, stderr = _run(
        f"{sys.executable} -m pytest {ids_str} -x --tb=short -q",
        cwd=cwd, timeout=timeout,
    )
    output = stdout + stderr
    # Parse pytest summary line
    passed = 0
    for line in output.splitlines():
        if "passed" in line and ("failed" in line or "error" in line or line.strip().startswith("=")):
            try:
                passed = int(line.strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif line.strip().endswith("passed"):
            try:
                passed = int(line.strip().split()[0])
            except (ValueError, IndexError):
                pass
    return passed, len(test_ids), output


# ── Single problem runner ─────────────────────────────────────────────────────

async def run_one(row: dict, model: str) -> dict:
    """Run the agent on one SWE-bench problem."""
    from web.worker.agent import call_agent

    instance_id = row["instance_id"]
    repo = row["repo"]
    base_commit = row["base_commit"]
    problem = row["problem_statement"]
    test_patch = row["test_patch"]
    fail_to_pass = json.loads(row["FAIL_TO_PASS"]) if isinstance(row["FAIL_TO_PASS"], str) else row["FAIL_TO_PASS"]

    tmpdir = clone_repo(repo, base_commit)
    try:
        # Install package deps (best-effort)
        install_deps(tmpdir)

        # Apply test_patch so the failing tests exist
        apply_patch(test_patch, tmpdir)

        # Run agent
        task_prompt = (
            f"Repository: {repo}\n\n"
            f"Issue:\n{problem}\n\n"
            f"Fix the bug described above. "
            f"Do NOT modify any test files. "
            f"Only modify source files to fix the root cause."
        )

        result = await call_agent(
            task_prompt=task_prompt,
            system_prompt=SYSTEM_PROMPT,
            repo_root=tmpdir,
            model_name=model,
            api_url=API_URL,
            api_key=API_KEY,
            api_format="openai",
        )

        # Generate the patch the agent produced
        _, diff_out, _ = _run("git diff HEAD", cwd=tmpdir)

        # Run FAIL_TO_PASS tests
        passed, total, test_output = run_tests(fail_to_pass, tmpdir)

        return {
            "instance_id": instance_id,
            "repo": repo,
            "difficulty": row.get("difficulty", ""),
            "passed": passed,
            "total": total,
            "resolved": passed == total and total > 0,
            "turns": result.get("turns", 0),
            "model": result.get("active_model", model),
            "patch": diff_out,
            "_test_output": test_output[-500:] if test_output else "",
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(
    limit: int | None,
    model: str,
    repo_filter: str | None,
    difficulty_filter: str | None,
) -> None:
    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: datasets not installed. Run: pip install datasets")
        sys.exit(1)

    print("Loading SWE-bench Verified dataset...")
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    rows = list(ds)

    if repo_filter:
        rows = [r for r in rows if r["repo"] == repo_filter]
    if difficulty_filter:
        rows = [r for r in rows if r["difficulty"] == difficulty_filter]
    if limit:
        rows = rows[:limit]

    print(f"Model     : {model}")
    print(f"Problems  : {len(rows)}")
    if repo_filter:
        print(f"Repo      : {repo_filter}")
    if difficulty_filter:
        print(f"Difficulty: {difficulty_filter}")
    print(f"Output    : {OUTPUT_FILE}\n")

    results = []
    for i, row in enumerate(rows, 1):
        t0 = time.monotonic()
        try:
            res = await run_one(row, model)
            elapsed = time.monotonic() - t0
            status = "RESOLVED" if res["resolved"] else f"{res['passed']}/{res['total']} tests"
            print(
                f"[{i:3}/{len(rows)}] {res['instance_id']:50} "
                f"({elapsed:.1f}s, {res['turns']}t)  {status}"
            )
            results.append(res)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f"[{i:3}/{len(rows)}] {row['instance_id']:50} ({elapsed:.1f}s)  ERROR: {exc}")
            results.append({
                "instance_id": row["instance_id"],
                "repo": row["repo"],
                "difficulty": row.get("difficulty", ""),
                "passed": 0,
                "total": 0,
                "resolved": False,
                "turns": 0,
                "error": str(exc),
            })

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nSaved {len(results)} results → {OUTPUT_FILE}")

    resolved = sum(1 for r in results if r.get("resolved"))
    total = len(results)
    print(f"\n── Results ──────────────────────────────────────────")
    print(f"  Resolved: {resolved}/{total} = {resolved/total:.4f}")

    for diff in ["<15 min fix", "15 min - 1 hour", "1-4 hours", ">4 hours"]:
        sub = [r for r in results if r.get("difficulty") == diff]
        if sub:
            s = sum(1 for r in sub if r.get("resolved"))
            print(f"  {diff}: {s}/{len(sub)} = {s/len(sub):.4f}")
    print(f"─────────────────────────────────────────────────────")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--repo", default=None, help="e.g. psf/requests")
    parser.add_argument("--difficulty", default=None, help="e.g. '<15 min fix'")
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.model, args.repo, args.difficulty))

