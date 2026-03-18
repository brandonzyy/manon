"""LiveCodeBench benchmark — tests Manon's actual agent pipeline (tool-calling loop).

Dataset: livecodebench/code_generation (400 problems from AtCoder, Codeforces, LeetCode)
Two test types:
  - stdin:      complete Python program reading from stdin, writing to stdout
  - functional: LeetCode-style Solution class

Usage:
    python tests/eval_livecodebench_agent.py --limit 20
    python tests/eval_livecodebench_agent.py --difficulty easy --limit 50
    python tests/eval_livecodebench_agent.py --platform atcoder --limit 30
    python tests/eval_livecodebench_agent.py           # full 400 problems
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
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

OUTPUT_FILE = Path(__file__).parent / "livecodebench_agent_samples.jsonl"

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("eval_lcb")

SYSTEM_PROMPT = """\
You are an expert competitive programmer.
You have tools to read, edit, and write files in the repository.
Solve the coding problem by writing your solution to `solution.py`.
Do not explain — just implement.
"""

# ── Functional test harness template ─────────────────────────────────────────

# Wraps a LeetCode Solution class to call it with parsed inputs and compare output
FUNCTIONAL_HARNESS = """\

# ── test harness ──────────────────────────────────────────────────────────────
import sys as _sys, json as _json, ast as _ast

def _parse(s):
    try:
        return _ast.literal_eval(s.strip())
    except Exception:
        return s.strip()

def _run_test(input_str, expected_str):
    parts = [p.strip() for p in input_str.strip().split("\\n") if p.strip()]
    args = [_parse(p) for p in parts]
    sol = Solution()
    # find the method (first non-dunder method)
    method = next(
        m for m in dir(sol)
        if not m.startswith("_") and callable(getattr(sol, m))
    )
    result = getattr(sol, method)(*args)
    expected = _parse(expected_str.strip())
    assert result == expected, f"got {result!r}, expected {expected!r}"

if __name__ == "__main__":
    cases = _json.loads(_sys.argv[1])
    for tc in cases:
        _run_test(tc["input"], tc["output"])
    print("OK")
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _git(cmd: str, cwd: str) -> str:
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip()


def create_task_repo(starter_code: str) -> str:
    """Create a temp git repo with solution.py (starter or empty)."""
    tmpdir = tempfile.mkdtemp(prefix="lcb_")
    _git("git init", tmpdir)
    _git('git config user.email "bench@manon"', tmpdir)
    _git('git config user.name "bench"', tmpdir)

    content = starter_code if starter_code.strip() else "# Write your solution here\n"
    (Path(tmpdir) / "solution.py").write_text(content, encoding="utf-8")
    _git("git add solution.py", tmpdir)
    _git('git commit -m "stub"', tmpdir)
    return tmpdir


def extract_code(text: str) -> str:
    """Extract Python code block from agent response (fallback)."""
    m = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"```\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    return text


def run_stdin_test(code: str, test_cases: list[dict], timeout: int = 5) -> tuple[int, int]:
    """Run code against stdin test cases. Returns (passed, total)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp = f.name
    passed = 0
    try:
        for tc in test_cases:
            inp = tc.get("input", "")
            expected = tc.get("output", "").strip()
            try:
                r = subprocess.run(
                    [sys.executable, tmp],
                    input=inp, capture_output=True, text=True, timeout=timeout,
                )
                actual = r.stdout.strip()
                if actual == expected:
                    passed += 1
            except subprocess.TimeoutExpired:
                pass
    finally:
        os.unlink(tmp)
    return passed, len(test_cases)


def run_functional_test(code: str, test_cases: list[dict], timeout: int = 5) -> tuple[int, int]:
    """Run LeetCode-style Solution class against functional test cases."""
    full_code = code + FUNCTIONAL_HARNESS
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(full_code)
        tmp = f.name
    passed = 0
    try:
        # Run all test cases at once
        cases_json = json.dumps(test_cases)
        try:
            r = subprocess.run(
                [sys.executable, tmp, cases_json],
                capture_output=True, text=True, timeout=timeout * len(test_cases),
            )
            if r.returncode == 0 and "OK" in r.stdout:
                passed = len(test_cases)
        except subprocess.TimeoutExpired:
            pass
    finally:
        os.unlink(tmp)
    return passed, len(test_cases)


# ── Single problem runner ─────────────────────────────────────────────────────

async def run_one(row: dict, model: str) -> dict:
    """Run the agent on one LiveCodeBench problem."""
    from web.worker.agent import call_agent

    title = row["question_title"]
    content = row["question_content"]
    starter = row.get("starter_code", "") or ""
    testtype = row.get("_testtype", "stdin")
    test_cases = row.get("_test_cases", [])

    tmpdir = create_task_repo(starter)
    try:
        if testtype == "functional":
            task_prompt = (
                f"Problem: {title}\n\n"
                f"{content}\n\n"
                f"Write a Python `Solution` class in `solution.py` with the required method.\n"
                f"Starter code:\n```python\n{starter}\n```"
            )
        else:
            task_prompt = (
                f"Problem: {title}\n\n"
                f"{content}\n\n"
                f"Write a complete Python program in `solution.py` that reads from stdin "
                f"and writes the answer to stdout."
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

        sol_path = Path(tmpdir) / "solution.py"
        code = sol_path.read_text(encoding="utf-8") if sol_path.exists() else ""

        if testtype == "functional":
            passed, total = run_functional_test(code, test_cases)
        else:
            passed, total = run_stdin_test(code, test_cases)

        return {
            "title": title,
            "platform": row["platform"],
            "difficulty": row["difficulty"],
            "testtype": testtype,
            "passed": passed,
            "total": total,
            "turns": result.get("turns", 0),
            "model": result.get("active_model", model),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(limit: int | None, model: str, difficulty: str | None, platform: str | None) -> None:
    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: datasets not installed. Run: pip install datasets")
        sys.exit(1)

    print("Loading LiveCodeBench dataset...")
    ds = load_dataset("livecodebench/code_generation", split="test")

    # Flatten test cases and testtype into each row
    rows = []
    for r in ds:
        tc_raw = r["public_test_cases"]
        if isinstance(tc_raw, str):
            try:
                tc_raw = json.loads(tc_raw)
            except Exception:
                tc_raw = []
        if not tc_raw:
            continue
        testtype = tc_raw[0].get("testtype", "stdin") if tc_raw else "stdin"
        rows.append({**r, "_test_cases": tc_raw, "_testtype": testtype})

    # Filters
    if difficulty:
        rows = [r for r in rows if r["difficulty"] == difficulty]
    if platform:
        rows = [r for r in rows if r["platform"] == platform]
    if limit:
        rows = rows[:limit]

    total_problems = len(rows)
    stdin_count = sum(1 for r in rows if r["_testtype"] == "stdin")
    func_count = total_problems - stdin_count

    print(f"Model     : {model}")
    print(f"Problems  : {total_problems} (stdin={stdin_count}, functional={func_count})")
    if difficulty:
        print(f"Difficulty: {difficulty}")
    if platform:
        print(f"Platform  : {platform}")
    print(f"Output    : {OUTPUT_FILE}\n")

    results = []
    total_passed = 0
    total_tests = 0

    for i, row in enumerate(rows, 1):
        t0 = time.monotonic()
        try:
            res = await run_one(row, model)
            elapsed = time.monotonic() - t0
            p, t = res["passed"], res["total"]
            total_passed += p
            total_tests += t
            status = "PASS" if p == t else f"{p}/{t}"
            print(
                f"[{i:3}/{total_problems}] {res['platform']:10} {res['difficulty']:6} "
                f"{res['title'][:40]:40} ({elapsed:.1f}s, {res['turns']}t)  {status}"
            )
            results.append(res)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f"[{i:3}/{total_problems}] {row['question_title'][:40]:40} ({elapsed:.1f}s)  ERROR: {exc}")
            results.append({
                "title": row["question_title"],
                "platform": row["platform"],
                "difficulty": row["difficulty"],
                "testtype": row.get("_testtype", "?"),
                "passed": 0,
                "total": len(row.get("_test_cases", [])),
                "turns": 0,
                "error": str(exc),
            })

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nSaved {len(results)} results → {OUTPUT_FILE}")

    # Summary
    solved = sum(1 for r in results if r.get("passed", 0) == r.get("total", 1) and r.get("total", 0) > 0)
    print(f"\n── Results ──────────────────────────────────────────")
    print(f"  Solved (all tests pass): {solved}/{total_problems} = {solved/total_problems:.4f}")
    if total_tests > 0:
        print(f"  Test case pass rate    : {total_passed}/{total_tests} = {total_passed/total_tests:.4f}")

    # By difficulty
    for diff in ["easy", "medium", "hard"]:
        sub = [r for r in results if r.get("difficulty") == diff]
        if sub:
            s = sum(1 for r in sub if r.get("passed", 0) == r.get("total", 1) and r.get("total", 0) > 0)
            print(f"  {diff:6}: {s}/{len(sub)} = {s/len(sub):.4f}")
    print(f"─────────────────────────────────────────────────────")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"], default=None)
    parser.add_argument("--platform", choices=["atcoder", "codeforces", "leetcode"], default=None)
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.model, args.difficulty, args.platform))

