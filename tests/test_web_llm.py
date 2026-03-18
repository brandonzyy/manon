"""Tests for web/services/llm.py — pure helper functions."""
import pytest

from web.services.llm import _split_anthropic_messages


class TestSplitAnthropicMessages:
    def test_no_system(self):
        msgs = [{"role": "user", "content": "hi"}]
        system, filtered = _split_anthropic_messages(msgs)
        assert system == ""
        assert len(filtered) == 1

    def test_with_system(self):
        msgs = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "hi"},
        ]
        system, filtered = _split_anthropic_messages(msgs)
        assert system == "You are helpful"
        assert len(filtered) == 1
        assert filtered[0]["role"] == "user"

    def test_empty(self):
        system, filtered = _split_anthropic_messages([])
        assert system == ""
        assert filtered == []

    def test_multiple_messages(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        system, filtered = _split_anthropic_messages(msgs)
        assert system == "sys"
        assert len(filtered) == 3
