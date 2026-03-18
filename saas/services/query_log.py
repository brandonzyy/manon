"""Deep query iteration log — JSONL storage for embedding training data.

Each deep_query call produces one record containing:
- The original question and LLM-decomposed sub-questions
- Per-round: query text, search hits (entities + chunks with full content), LLM judgment
- Coverage assessment (which sub-questions were covered)

Training signal extraction:
- Positive pairs: (query, chunk_content) where chunk appears in covered results
- Hard negatives: (query, chunk_content) where chunk was retrieved but LLM judged as not covering
- Query expansion: (original_question, llm_generated_query) pairs
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ..config import settings

log = logging.getLogger("saas.query_log")

_LOG_DIR: Path | None = None


def _ensure_log_dir() -> Path:
    global _LOG_DIR
    if _LOG_DIR is None:
        _LOG_DIR = Path(settings.data_dir) / "query_logs"
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def save_deep_query_log(record: dict) -> None:
    """Append a deep_query record to the monthly JSONL log file.

    Record contains: timestamp, tenant_id, repo_id, question, rounds (query/entities/chunks),
    llm_analysis (sub_questions/covered/missing), and final_coverage.
    """
    try:
        log_dir = _ensure_log_dir()
        month = time.strftime("%Y-%m")
        log_file = log_dir / f"deep_query_{month}.jsonl"
        record["timestamp"] = time.time()
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        log.warning("Failed to save query log: %s", e)
