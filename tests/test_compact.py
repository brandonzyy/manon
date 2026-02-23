"""Tests for web.coach.compact — history compaction helpers."""
import pytest

from web.coach.compact import _history_chars, _microcompact, _MICRO_TRUNCATE_AT, _MICRO_KEEP_CHARS


class TestHistoryChars:
    def test_empty(self):
        assert _history_chars([]) == 0

    def test_single(self):
        assert _history_chars([{"role": "user", "content": "hello"}]) == 5

    def test_multiple(self):
        history = [
            {"role": "user", "content": "abc"},
            {"role": "assistant", "content": "defgh"},
        ]
        assert _history_chars(history) == 8

    def test_missing_content(self):
        assert _history_chars([{"role": "user"}]) == 0


class TestMicrocompact:
    def test_no_truncation_short_messages(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        count = _microcompact(history)
        assert count == 0
        assert history[1]["content"] == "hello"

    def test_truncates_old_long_assistant(self):
        long_text = "x" * (_MICRO_TRUNCATE_AT + 500)
        history = [
            {"role": "assistant", "content": long_text},  # old, will be truncated
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "short1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "short2"},
            {"role": "user", "content": "q3"},
            {"role": "assistant", "content": "short3"},
            {"role": "user", "content": "q4"},
            {"role": "assistant", "content": "short4"},
            {"role": "user", "content": "q5"},
            {"role": "assistant", "content": "short5"},
        ]
        count = _microcompact(history)
        assert count == 1
        assert len(history[0]["content"]) < len(long_text)
        assert "已压缩" in history[0]["content"]

    def test_keeps_recent_assistant_messages(self):
        long_text = "y" * (_MICRO_TRUNCATE_AT + 100)
        # Last 5 assistant messages should be kept intact
        history = []
        for i in range(10):
            history.append({"role": "user", "content": f"q{i}"})
            history.append({"role": "assistant", "content": long_text})
        count = _microcompact(history)
        # First 5 assistant messages truncated, last 5 kept
        assert count == 5
        # Last assistant message should be intact
        assert history[-1]["content"] == long_text

    def test_skips_user_messages(self):
        long_user = "z" * (_MICRO_TRUNCATE_AT + 100)
        history = [
            {"role": "user", "content": long_user},
            {"role": "assistant", "content": "ok"},
        ]
        count = _microcompact(history)
        assert count == 0
        assert history[0]["content"] == long_user
