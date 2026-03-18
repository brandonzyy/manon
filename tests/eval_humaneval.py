"""HumanEval benchmark runner — uses Manon's LLM API directly.

Usage:
    python tests/eval_humaneval.py                  # run all 164 problems
    python tests/eval_humaneval.py --limit 20       # quick smoke test (20 problems)
    python tests/eval_humaneval.py --model GLM-5    # override model
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx

# ── Config (mirrors web/config.py defaults) ──────────────────────────────────

API_URL = "https://api.matrixone.online/v1/chat/completions"
API_KEY = "sk-f05sj8cb25syBlnH3pUFN9TuczxgwtEtIEwQ5PEtD22sxeH1"
DEFAULT_MODEL = "glm-4.7-fp8"

OUTPUT_FILE = Path(__file__).parent / "humaneval_samples.jsonl"

# ── LLM call ─────────────────────────────────────────────────────────────────

async def complete_function(client: httpx.AsyncClient, prompt: str, model: str) -> str:
    """Ask the LLM to complete a Python function. Returns only the completion."""
    system = (
        "You are an expert Python programmer. "
        "Complete the function body. "
        "Return ONLY the Python code — no markdown fences, no explanation. "
        "The code must be syntactically valid and directly follow the given signature."
    )
    user = f"Complete this Python function:\n\n{prompt}"

    for attempt in range(3):
        try:
            resp = await client.post(
                API_URL,
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={"model": model, "max_tokens": 4096, "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]},
                timeout=120.0,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _extract_completion(prompt, content)
        except (httpx.RemoteProtocolError, httpx.ReadTimeout) as exc:
            if attempt == 2:
                raise
            await asyncio.sleep(3)
    raise RuntimeError("unreachable")


def _extract_completion(prompt: str, raw: str) -> str:
    """Strip markdown fences and return only the function body lines."""
    # Remove ```python ... ``` or ``` ... ```
    raw = re.sub(r"```(?:python)?\s*", "", raw)
    raw = re.sub(r"```", "", raw).strip()

    # If the model echoed the full function, strip the prompt prefix
    if raw.startswith(prompt.strip()):
        raw = raw[len(prompt.strip()):].strip()

    # human-eval expects the completion to start right after the prompt
    # so we just return what the model added
    return "\n" + raw if raw and not raw.startswith("\n") else raw


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

    print(f"Model : {model}")
    print(f"Tasks : {len(task_ids)} / {len(problems)}")
    print(f"Output: {OUTPUT_FILE}\n")

    samples = []
    async with httpx.AsyncClient() as client:
        for i, task_id in enumerate(task_ids, 1):
            prompt = problems[task_id]["prompt"]
            t0 = time.monotonic()
            try:
                completion = await complete_function(client, prompt, model)
                elapsed = time.monotonic() - t0
                samples.append({"task_id": task_id, "completion": completion})
                print(f"[{i:3}/{len(task_ids)}] {task_id}  ({elapsed:.1f}s)  OK")
            except Exception as exc:
                elapsed = time.monotonic() - t0
                print(f"[{i:3}/{len(task_ids)}] {task_id}  ({elapsed:.1f}s)  FAIL: {exc}")
                samples.append({"task_id": task_id, "completion": "    pass"})

    # Write samples
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")

    print(f"\nSaved {len(samples)} samples → {OUTPUT_FILE}")

    if limit and limit < len(problems):
        print(f"\n(Partial run — {limit} problems. Run without --limit for full score.)")
        print("Skipping evaluate_functional_correctness for partial runs.")
        return

    # Evaluate
    print("\nRunning evaluation harness...")
    results = evaluate_functional_correctness(str(OUTPUT_FILE))
    print("\n── Results ──────────────────────────────")
    for k, v in results.items():
        print(f"  {k}: {v:.4f}")
    print("─────────────────────────────────────────")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Run only first N problems")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model ID to use")
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.model))
