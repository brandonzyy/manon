"""Tests for web/ coach and services — compact, pipeline helpers, settings, llm."""
import pytest
from pathlib import Path

from web.coach.compact import (
    _history_chars, _microcompact,
    _COMPACT_TRIGGER, _COMPACT_TARGET, _MICRO_KEEP_RECENT,
    _MICRO_TRUNCATE_AT, _MICRO_KEEP_CHARS, _AUTO_KEEP_RECENT,
    _COMPACT_MODEL, _COMPACT_PROMPT,
)
from web.coach.pipeline import (
    Status, FeatureState, get_session, _ensure_session, _sessions,
    _send_dev, _send_thinking, _send_chat,
)
from web.routers.settings import BUILTIN_MODELS, _CONFIG_FILE


# ── Compact module ───────────────────────────────────

class TestHistoryChars:
    def test_empty(self):
        assert _history_chars([]) == 0

    def test_with_messages(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        assert _history_chars(history) == 5 + 8

    def test_missing_content(self):
        history = [{"role": "user"}]
        assert _history_chars(history) == 0


class TestMicrocompact:
    def test_no_truncation_needed(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        count = _microcompact(history)
        assert count == 0

    def test_truncates_old_long_messages(self):
        history = []
        # Add many old assistant messages
        for i in range(10):
            history.append({"role": "user", "content": f"q{i}"})
            history.append({"role": "assistant", "content": "x" * 3000})
        count = _microcompact(history)
        assert count > 0  # some messages should be truncated

    def test_keeps_recent_messages(self):
        history = []
        for i in range(3):
            history.append({"role": "user", "content": f"q{i}"})
            history.append({"role": "assistant", "content": "x" * 3000})
        original_last = history[-1]["content"]
        _microcompact(history)
        # Recent messages should be kept intact
        assert history[-1]["content"] == original_last


class TestCompactConstants:
    def test_thresholds(self):
        assert _COMPACT_TRIGGER == 100_000
        assert _COMPACT_TARGET == 80_000
        assert _MICRO_KEEP_RECENT == 5
        assert _MICRO_TRUNCATE_AT == 2000
        assert _MICRO_KEEP_CHARS == 1500

    def test_compact_model(self):
        assert _COMPACT_MODEL == "glm-4.7-fp8"

    def test_compact_prompt(self):
        assert len(_COMPACT_PROMPT) > 0


# ── Settings module ──────────────────────────────────

class TestBuiltinModels:
    def test_has_models(self):
        assert len(BUILTIN_MODELS) >= 1

    def test_model_structure(self):
        for m in BUILTIN_MODELS:
            assert "id" in m
            assert "name" in m
            assert "provider" in m

    def test_default_model(self):
        ids = [m["id"] for m in BUILTIN_MODELS]
        assert "glm-4.7-fp8" in ids

    def test_config_file_path(self):
        assert isinstance(_CONFIG_FILE, Path)
        assert _CONFIG_FILE.name == "settings.json"
