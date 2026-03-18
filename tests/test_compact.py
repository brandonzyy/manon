"""Tests for web.coach.compact — history compaction helpers."""
import pytest
from unittest.mock import AsyncMock, patch

from web.coach.compact import (
    _history_chars, _microcompact, _auto_compact,
    _MICRO_TRUNCATE_AT, _MICRO_KEEP_CHARS, _MICRO_KEEP_RECENT,
    _COMPACT_TRIGGER, _COMPACT_TARGET, _AUTO_KEEP_RECENT,
    _AUTO_FALLBACK_KEEP, _COMPACT_MODEL, _COMPACT_PROMPT,
)


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

    def test_unicode_characters(self):
        """Unicode characters should be counted correctly."""
        history = [
            {"role": "user", "content": "你好世界"},  # 4 Chinese chars
            {"role": "assistant", "content": "🎉🎊"},  # 2 emoji
        ]
        assert _history_chars(history) == 6

    def test_mixed_content(self):
        """Mixed ASCII and Unicode should work."""
        history = [{"role": "user", "content": "Hello 世界"}]
        assert _history_chars(history) == 8  # "Hello " (6) + "世界" (2)

    def test_empty_string_content(self):
        """Empty string content should count as 0."""
        history = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "hello"},
        ]
        assert _history_chars(history) == 5


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

    def test_exact_truncate_boundary(self):
        """Message at exactly _MICRO_TRUNCATE_AT should NOT be truncated."""
        exact_text = "x" * _MICRO_TRUNCATE_AT
        history = [
            {"role": "assistant", "content": exact_text},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "short"},
        ]
        count = _microcompact(history)
        # Should not truncate because it's exactly at the boundary (not >)
        assert count == 0

    def test_one_over_truncate_boundary(self):
        """Message just over _MICRO_TRUNCATE_AT should be truncated."""
        over_text = "x" * (_MICRO_TRUNCATE_AT + 1)
        history = [
            {"role": "assistant", "content": over_text},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "short"},
        ]
        count = _microcompact(history)
        assert count == 1
        assert len(history[0]["content"]) < len(over_text)

    def test_keeps_first_n_chars(self):
        """Truncated message should keep first _MICRO_KEEP_CHARS."""
        long_text = "abcdefghij" * 500  # 5000 chars
        history = [
            {"role": "assistant", "content": long_text},
        ] + [
            {"role": "user", "content": f"q{i}"},
            {"role": "assistant", "content": "short"},
        ] for i in range(_MICRO_KEEP_RECENT + 1)
        history = [{"role": "assistant", "content": long_text}]
        for i in range(_MICRO_KEEP_RECENT + 1):
            history.append({"role": "user", "content": f"q{i}"})
            history.append({"role": "assistant", "content": "short"})

        _microcompact(history)
        # First message should start with original prefix
        assert history[0]["content"].startswith("abcdefghij")

    def test_empty_history(self):
        """Empty history should return 0."""
        count = _microcompact([])
        assert count == 0

    def test_only_user_messages(self):
        """History with only user messages should not be modified."""
        history = [
            {"role": "user", "content": "x" * (_MICRO_TRUNCATE_AT + 100)},
        ]
        count = _microcompact(history)
        assert count == 0


class TestAutoCompact:
    """Tests for _auto_compact async function."""

    @pytest.mark.asyncio
    async def test_below_trigger_no_action(self):
        """History below trigger should not be compacted."""
        history = [{"role": "user", "content": "short"}]

        with patch("web.coach.compact._send_thinking", new_callable=AsyncMock):
            with patch("web.coach.compact.llm_chat", new_callable=AsyncMock):
                await _auto_compact("dev-001", history)

        assert len(history) == 1

    @pytest.mark.asyncio
    async def test_force_compact(self):
        """Force=True should compact even below threshold."""
        history = [{"role": "user", "content": "short"}] * 15

        with patch("web.coach.compact._send_thinking", new_callable=AsyncMock):
            with patch("web.coach.compact._save_chat_history", new_callable=AsyncMock):
                with patch("web.coach.compact.llm_chat", new_callable=AsyncMock) as mock_llm:
                    mock_llm.return_value = {"content": "Summary"}
                    await _auto_compact("dev-001", history, force=True)

        # History should be compacted to summary + recent
        assert len(history) <= _AUTO_KEEP_RECENT + 1  # +1 for summary

    @pytest.mark.asyncio
    async def test_llm_fallback_truncation(self):
        """LLM failure should fallback to truncation."""
        history = [{"role": "user", "content": "x" * 10000}] * 15
        original_len = len(history)

        with patch("web.coach.compact._send_thinking", new_callable=AsyncMock):
            with patch("web.coach.compact.llm_chat", new_callable=AsyncMock) as mock_llm:
                mock_llm.side_effect = Exception("LLM error")
                await _auto_compact("dev-001", history, force=True)

        # Should fallback to keeping last _AUTO_FALLBACK_KEEP messages
        assert len(history) == _AUTO_FALLBACK_KEEP

    @pytest.mark.asyncio
    async def test_focus_hint_added_to_prompt(self):
        """Focus hint should be included in prompt."""
        history = [{"role": "user", "content": "test"}] * 15

        with patch("web.coach.compact._send_thinking", new_callable=AsyncMock):
            with patch("web.coach.compact._save_chat_history", new_callable=AsyncMock):
                with patch("web.coach.compact.llm_chat", new_callable=AsyncMock) as mock_llm:
                    mock_llm.return_value = {"content": "Summary"}
                    await _auto_compact("dev-001", history, force=True, focus_hint="API设计")

                    # Check that focus hint was added to prompt
                    call_args = mock_llm.call_args
                    messages = call_args[0][0]
                    assert "API设计" in messages[0]["content"]


class TestCompactConstants:
    """Tests for compact threshold constants."""

    def test_trigger_greater_than_target(self):
        """Trigger should be greater than target."""
        assert _COMPACT_TRIGGER > _COMPACT_TARGET

    def test_truncate_at_greater_than_keep_chars(self):
        """Truncate threshold should be greater than keep chars."""
        assert _MICRO_TRUNCATE_AT > _MICRO_KEEP_CHARS

    def test_keep_recent_reasonable(self):
        """Keep recent should be reasonable (5-20)."""
        assert 5 <= _MICRO_KEEP_RECENT <= 20
        assert 5 <= _AUTO_KEEP_RECENT <= 30

    def test_compact_model_set(self):
        """Compact model should be set."""
        assert _COMPACT_MODEL == "glm-4.7-fp8"

    def test_compact_prompt_not_empty(self):
        """Compact prompt should not be empty."""
        assert len(_COMPACT_PROMPT) > 100
        assert "摘要" in _COMPACT_PROMPT or "压缩" in _COMPACT_PROMPT

