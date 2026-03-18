"""HumanEval benchmark — tests Manon's actual agent pipeline (tool-calling loop).

Each problem:
  1. Creates a temp git repo with the function stub in solution.py
  2. Runs call_agent (the real worker agent loop with read/edit/write/run_command tools)
  3. Extracts the completion from the modified file
  4. Evaluates with human-eval harness

Usage:
    python tests/eval_humaneval_agent.py --limit 10
    python tests/eval_humaneval_agent.py --model GLM-5
    python tests/eval_humaneval_agent.py           # full 164 problems
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

# ── Bootstrap: add manon root to sys.path ────────────────────────────────────

MANON_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MANON_ROOT))

# ── Config (mirrors web/config.py defaults) ──────────────────────────────────

API_URL = "https://api.matrixone.online/v1/chat/completions"
API_KEY = "sk-f05sj8cb25syBlnH3pUFN9TuczxgwtEtIEwQ5PEtD22sxeH1"
DEFAULT_MODEL = "glm-4.7-fp8"

OUTPUT_FILE = Path(__file__).parent / "humaneval_agent_samples.jsonl"

logging.basicConfig(level=logging.WARNING)  # suppress agent debug noise
log = logging.getLogger("eval_agent")

SYSTEM_PROMPT = """\
You are an expert Python programmer working on a coding task.
You have tools to read, edit, and write files in the repository.
Complete the task by modifying the file directly using your tools.
Do not explain — just implement.
"""

# ── Temp repo setup ───────────────────────────────────────────────────────────

def _git(cmd: str, cwd: str) -> str:
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def create_task_repo(prompt: str) -> str:
    """Create a temp git repo with solution.py containing the function stub."""
    tmpdir = tempfile.mkdtemp(prefix="humaneval_")
    _git("git init", tmpdir)
    _git('git config user.email "bench@manon"', tmpdir)
    _git('git config user.name "bench"', tmpdir)

    # Write stub: prompt ends with the docstring, body is just `pass`
    stub = prompt.rstrip() + "\n    pass\n"
    (Path(tmpdir) / "solution.py").write_text(stub, encoding="utf-8")

    _git("git add solution.py", tmpdir)
    _git('git commit -m "stub"', tmpdir)
    return tmpdir


def extract_completion(original_prompt: str, modified_file: str) -> str:
    """Return only what the agent added after the original prompt."""
    # Normalize line endings
    prompt = original_prompt.rstrip()
    modified = modified_file.strip()

    # If agent rewrote the whole file, strip the prompt prefix
    if modified.startswith(prompt):
        return "\n" + modified[len(prompt):].lstrip("\n")

    # Otherwise return the whole file content as completion
    # (evaluator will handle it)
    return "\n" + modified


# ── Single problem runner ─────────────────────────────────────────────────────

async def run_one(task_id: str, prompt: str, model: str) -> dict:
    """Run the agent on one HumanEval problem. Returns {task_id, completion, meta}."""
    from web.worker.agent import call_agent

    tmpdir = create_task_repo(prompt)
    try:
        task_prompt = (
            f"Complete the Python function in `solution.py`.\n\n"
            f"The file currently contains a stub with `pass`. "
            f"Replace `pass` with a correct implementation.\n\n"
            f"Function to implement:\n```python\n{prompt}\n```"
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

        # Read the modified file
        sol_path = Path(tmpdir) / "solution.py"
        modified = sol_path.read_text(encoding="utf-8") if sol_path.exists() else ""

        completion = extract_completion(prompt, modified)

        return {
            "task_id": task_id,
            "completion": completion,
            "_meta": {
                "turns": result.get("turns", 0),
                "model": result.get("active_model", model),
                "tokens": result.get("token_usage", {}),
            },
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(limit: int | None, model: str) -> None:
    try:
        from human_eval.data import read_problems
        from human_eval.evaluation import evaluate_functional_correctness
    except ImportError:
        print("ERROR: human-eval not installed. Run: pip install human-eval")
        sys.exit(1)

    problems = read_problems()
    task_ids = list(problems.keys())
    if limit:
        task_ids = task_ids[:limit]

    print(f"Model  : {model}")
    print(f"Tasks  : {len(task_ids)} / {len(problems)}")
    print(f"Output : {OUTPUT_FILE}")
    print(f"Mode   : Manon agent pipeline (tool-calling loop)\n")

    samples = []
    for i, task_id in enumerate(task_ids, 1):
        prompt = problems[task_id]["prompt"]
        t0 = time.monotonic()
        try:
            result = await run_one(task_id, prompt, model)
            elapsed = time.monotonic() - t0
            turns = result["_meta"]["turns"]
            samples.append({"task_id": result["task_id"], "completion": result["completion"]})
            print(f"[{i:3}/{len(task_ids)}] {task_id}  ({elapsed:.1f}s, {turns} turns)  OK")
        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f"[{i:3}/{len(task_ids)}] {task_id}  ({elapsed:.1f}s)  FAIL: {exc}")
            samples.append({"task_id": task_id, "completion": "\n    pass"})

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")

    print(f"\nSaved {len(samples)} samples → {OUTPUT_FILE}")

    if limit and limit < len(problems):
        print(f"\n(Partial run — skipping evaluation. Remove --limit for full score.)")
        return

    # Evaluate using subprocess (Windows-compatible, avoids signal.setitimer)
    print("\nEvaluating...")
    passed = 0
    for s in samples:
        task_id = s["task_id"]
        prob = problems[task_id]
        code = (
            prob["prompt"] + s["completion"] + "\n"
            + prob["test"] + "\n"
            + f"check({prob['entry_point']})\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
            tmp.write(code)
            tmp_path = tmp.name
        try:
            r = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                passed += 1
        except subprocess.TimeoutExpired:
            pass
        finally:
            os.unlink(tmp_path)

    total = len(samples)
    print(f"\n── Results ──────────────────────────────")
    print(f"  pass@1: {passed}/{total} = {passed/total:.4f}")
    print(f"─────────────────────────────────────────")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.model))
