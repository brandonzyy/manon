"""Tests for web/ coach and services — compact, pipeline helpers, settings, llm."""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from web.coach.compact import (
    _history_chars, _microcompact,
    _COMPACT_TRIGGER, _COMPACT_TARGET, _MICRO_KEEP_RECENT,
    _MICRO_TRUNCATE_AT, _MICRO_KEEP_CHARS, _AUTO_KEEP_RECENT,
    _COMPACT_MODEL, _COMPACT_PROMPT,
)
from web.coach.pipeline import (
    Status, FeatureState, get_session, _ensure_session, _sessions,
    _send_dev, _send_thinking, _send_chat, handle_dev_message,
    handle_feature_approved, handle_feature_rejected,
    _handle_user_response, _start_feature, _handle_plan_approved,
    _handle_plan_rejected, _cancel_feature, _retry_failed_phase,
    _skip_failed_tasks, _retry_failed_tasks, _retry_with_guidance,
)
from web.routers.settings import BUILTIN_MODELS, _CONFIG_FILE


# ── State Machine Tests ───────────────────────────────────

class TestFeaturePipelineStateMachine:
    """Tests for feature pipeline state transitions."""

    # ── Session Management ──

    def test_get_session_returns_none(self, clean_sessions):
        """Unknown dev_id should return None."""
        result = get_session("unknown-dev")
        assert result is None

    def test_ensure_session_creates_new(self, clean_sessions):
        """New dev_id should get a new session."""
        session = _ensure_session("new-dev-001")
        assert session is not None
        assert session.dev_id == "new-dev-001"
        assert session.status == Status.IDLE

    def test_ensure_session_returns_existing(self, clean_sessions):
        """Same dev_id should reuse existing session."""
        session1 = _ensure_session("existing-dev")
        session1.description = "test description"

        session2 = _ensure_session("existing-dev")
        assert session2.description == "test description"

    def test_session_state_isolation(self, clean_sessions):
        """Multiple dev_ids should not interfere with each other."""
        session1 = _ensure_session("dev-1")
        session1.status = Status.CLARIFYING

        session2 = _ensure_session("dev-2")
        session2.status = Status.EXECUTING

        assert get_session("dev-1").status == Status.CLARIFYING
        assert get_session("dev-2").status == Status.EXECUTING

    # ── FeatureState Dataclass ──

    def test_feature_state_defaults(self):
        """FeatureState should have sensible defaults."""
        state = FeatureState()
        assert state.feature_id == ""
        assert state.status == Status.IDLE
        assert state.conversation_history == []
        assert state.spec is None
        assert state.tasks == []

    def test_feature_state_custom_values(self):
        """FeatureState should accept custom values."""
        state = FeatureState(
            feature_id="feat-123",
            dev_id="dev-001",
            status=Status.CLARIFYING,
            description="Test feature",
        )
        assert state.feature_id == "feat-123"
        assert state.status == Status.CLARIFYING

    # ── State Transitions (via handle_dev_message) ──

    @pytest.mark.asyncio
    async def test_idle_to_clarifying(self, clean_sessions, mock_hub):
        """feature-request message should start pipeline."""
        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.clarify_intent", new_callable=AsyncMock) as mock_clarify:
                await handle_dev_message("dev-001", {
                    "type": "feature-request",
                    "description": "Test feature",
                    "projectId": "proj-001"
                })

        session = get_session("dev-001")
        assert session.status == Status.CLARIFYING
        assert session.description == "Test feature"
        mock_clarify.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_feature_blocked(self, clean_sessions, mock_hub):
        """Busy session should reject new request."""
        # Set up a busy session
        session = _ensure_session("busy-dev")
        session.status = Status.EXECUTING

        with patch("web.coach.pipeline.hub", mock_hub):
            await handle_dev_message("busy-dev", {
                "type": "feature-request",
                "description": "Another feature"
            })

        # Should have sent a rejection message
        mock_hub.send_to_dev.assert_called()
        call_args = mock_hub.send_to_dev.call_args[0][0]
        assert "进行中" in str(call_args) or "coach-chat" in call_args.get("type", "")

    # ── Message Routing ──

    @pytest.mark.asyncio
    async def test_route_manon_chat(self, clean_sessions, mock_hub):
        """manon-chat should be routed to chat handler."""
        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.chat._handle_manon_chat", new_callable=AsyncMock) as mock_chat:
                await handle_dev_message("dev-001", {
                    "type": "manon-chat",
                    "content": "Hello"
                })
                mock_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_user_response(self, clean_sessions, mock_hub):
        """user-response should be routed to _handle_user_response."""
        session = _ensure_session("dev-001")
        session.status = Status.CLARIFYING

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.clarify_intent", new_callable=AsyncMock):
                await handle_dev_message("dev-001", {
                    "type": "user-response",
                    "content": "Some answer"
                })

        # Conversation history should be updated
        assert len(session.conversation_history) == 1

    @pytest.mark.asyncio
    async def test_route_plan_approved(self, clean_sessions, mock_hub):
        """feature-plan-approved should trigger design."""
        session = _ensure_session("dev-001")
        session.status = Status.USER_CONFIRMING

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.generate_design", new_callable=AsyncMock) as mock_design:
                await handle_dev_message("dev-001", {"type": "feature-plan-approved"})
                mock_design.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_plan_rejected(self, clean_sessions, mock_hub):
        """feature-plan-rejected should regenerate spec."""
        session = _ensure_session("dev-001")
        session.status = Status.USER_CONFIRMING

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.finalize_spec", new_callable=AsyncMock) as mock_spec:
                await handle_dev_message("dev-001", {
                    "type": "feature-plan-rejected",
                    "reason": "Need changes"
                })
                mock_spec.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_feature_approved(self, clean_sessions, mock_hub):
        """feature-approved should complete pipeline."""
        session = _ensure_session("dev-001")
        session.status = Status.REVIEWING

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.handle_feature_approved", new_callable=AsyncMock) as mock_approved:
                await handle_dev_message("dev-001", {"type": "feature-approved"})
                mock_approved.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_feature_rejected(self, clean_sessions, mock_hub):
        """feature-rejected should loop back to executing."""
        session = _ensure_session("dev-001")
        session.status = Status.REVIEWING

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.handle_feature_rejected", new_callable=AsyncMock) as mock_rejected:
                await handle_dev_message("dev-001", {
                    "type": "feature-rejected",
                    "reason": "Not good"
                })
                mock_rejected.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_unknown_type_error(self, clean_sessions, mock_hub):
        """Unknown message type should send error."""
        with patch("web.coach.pipeline.hub", mock_hub):
            await handle_dev_message("dev-001", {"type": "unknown-type"})

        mock_hub.send_to_dev.assert_called()
        call_args = mock_hub.send_to_dev.call_args[0][0]
        assert "error" in call_args.get("type", "") or "Unknown" in str(call_args)

    # ── User Response Handling ──

    @pytest.mark.asyncio
    async def test_executing_retry_keyword(self, clean_sessions, mock_hub):
        """'重试' should trigger retry logic."""
        session = _ensure_session("dev-001")
        session.status = Status.EXECUTING
        session.tasks = [{"id": 1, "status": "failed"}]

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline._retry_failed_tasks", new_callable=AsyncMock) as mock_retry:
                await _handle_user_response("dev-001", {"content": "重试"})
                mock_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_executing_skip_keyword(self, clean_sessions, mock_hub):
        """'跳过' should skip failed tasks."""
        session = _ensure_session("dev-001")
        session.status = Status.EXECUTING
        session.tasks = [{"id": 1, "status": "failed"}]

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline._skip_failed_tasks", new_callable=AsyncMock) as mock_skip:
                await _handle_user_response("dev-001", {"content": "跳过"})
                mock_skip.assert_called_once()

    @pytest.mark.asyncio
    async def test_executing_cancel_keyword(self, clean_sessions, mock_hub):
        """'取消' should cancel feature."""
        session = _ensure_session("dev-001")
        session.status = Status.EXECUTING

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline._cancel_feature", new_callable=AsyncMock) as mock_cancel:
                await _handle_user_response("dev-001", {"content": "取消"})
                mock_cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_executing_guidance_text(self, clean_sessions, mock_hub):
        """Other text should trigger retry with guidance."""
        session = _ensure_session("dev-001")
        session.status = Status.EXECUTING
        session.tasks = [{"id": 1, "status": "failed"}]

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline._retry_with_guidance", new_callable=AsyncMock) as mock_guidance:
                await _handle_user_response("dev-001", {"content": "Please fix the bug"})
                mock_guidance.assert_called_once()

    @pytest.mark.asyncio
    async def test_reviewing_approve_keywords(self, clean_sessions, mock_hub):
        """'通过' should approve feature."""
        session = _ensure_session("dev-001")
        session.status = Status.REVIEWING

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.handle_feature_approved", new_callable=AsyncMock) as mock_approve:
                await _handle_user_response("dev-001", {"content": "通过"})
                mock_approve.assert_called_once()

    @pytest.mark.asyncio
    async def test_reviewing_approve_english_keyword(self, clean_sessions, mock_hub):
        """'approve' should approve feature."""
        session = _ensure_session("dev-001")
        session.status = Status.REVIEWING

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.handle_feature_approved", new_callable=AsyncMock) as mock_approve:
                await _handle_user_response("dev-001", {"content": "approve"})
                mock_approve.assert_called_once()

    # ── Error Recovery ──

    @pytest.mark.asyncio
    async def test_cancel_feature(self, clean_sessions, mock_hub):
        """_cancel_feature should set status to FAILED then IDLE."""
        session = _ensure_session("dev-001")
        session.status = Status.EXECUTING
        session._failed_phase = "spec"

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.generate_report", new_callable=AsyncMock):
                await _cancel_feature(session)

        assert session._failed_phase is None
        assert session.status == Status.IDLE

    @pytest.mark.asyncio
    async def test_skip_failed_tasks(self, clean_sessions, mock_hub):
        """_skip_failed_tasks should mark tasks as skipped."""
        session = _ensure_session("dev-001")
        session.status = Status.EXECUTING
        session.tasks = [
            {"id": 1, "status": "completed"},
            {"id": 2, "status": "failed"},
            {"id": 3, "status": "failed"},
        ]

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.execute_task_loop", new_callable=AsyncMock):
                await _skip_failed_tasks(session)

        assert session.tasks[1]["status"] == "skipped"
        assert session.tasks[2]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_retry_failed_phase_spec(self, clean_sessions, mock_hub):
        """_retry_failed_phase should retry spec generation."""
        session = _ensure_session("dev-001")
        session._failed_phase = "spec"

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.finalize_spec", new_callable=AsyncMock) as mock_spec:
                await _retry_failed_phase(session)
                mock_spec.assert_called_once()

        assert session._failed_phase is None

    @pytest.mark.asyncio
    async def test_retry_failed_tasks_resets_status(self, clean_sessions, mock_hub):
        """_retry_failed_tasks should reset failed tasks to pending."""
        session = _ensure_session("dev-001")
        session.tasks = [
            {"id": 1, "status": "completed"},
            {"id": 2, "status": "failed"},
        ]

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.execute_task_loop", new_callable=AsyncMock):
                await _retry_failed_tasks(session)

        assert session.tasks[1]["status"] == "pending"

    # ── Feature Approval/Rejection ──

    @pytest.mark.asyncio
    async def test_handle_feature_approved_wrong_status(self, clean_sessions, mock_hub):
        """handle_feature_approved should do nothing if not REVIEWING."""
        session = _ensure_session("dev-001")
        session.status = Status.EXECUTING

        await handle_feature_approved("dev-001")
        # Status should remain unchanged
        assert session.status == Status.EXECUTING

    @pytest.mark.asyncio
    async def test_handle_feature_rejected_loops_back(self, clean_sessions, mock_hub):
        """handle_feature_rejected should loop back to EXECUTING."""
        session = _ensure_session("dev-001")
        session.status = Status.REVIEWING
        session.tasks = [{"id": 1, "status": "completed"}]

        with patch("web.coach.pipeline.hub", mock_hub):
            with patch("web.coach.pipeline.execute_task_loop", new_callable=AsyncMock):
                await handle_feature_rejected("dev-001", "Not good enough")

        assert session.status == Status.EXECUTING
        assert "驳回意见" in session.description


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
