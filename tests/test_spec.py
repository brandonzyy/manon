"""Tests for web/coach/spec.py — spec generation and presentation."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from web.coach.spec import (
    finalize_spec, _present_plan, SYSTEM_PROMPT,
)
from web.coach.pipeline import Status, FeatureState


class TestFinalizeSpec:
    """Tests for finalize_spec function."""

    @pytest.mark.asyncio
    async def test_finalize_spec_success(self, mock_hub):
        """Successful spec generation should set spec and call _present_plan."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.CLARIFYING,
            description="Test feature",
            conversation_history=[{"question": "Q1?", "answer": "A1"}],
        )

        mock_spec = {
            "title": "Test Feature",
            "scope": "Test scope",
            "requirements": [{"id": "R1", "title": "Req 1", "priority": "MUST"}]
        }

        with patch("web.coach.spec.hub", mock_hub):
            with patch("web.coach.spec.call_glm5", new_callable=AsyncMock) as mock_llm:
                with patch("web.coach.spec.parse_json_from_llm") as mock_parse:
                    mock_llm.return_value = "JSON response"
                    mock_parse.return_value = mock_spec
                    await finalize_spec(state)

        assert state.spec == mock_spec
        assert state.status == Status.USER_CONFIRMING

    @pytest.mark.asyncio
    async def test_finalize_spec_parse_json(self, mock_hub):
        """Should extract JSON from LLM response."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.CLARIFYING,
            description="Test",
        )

        mock_spec = {"title": "Test", "scope": "", "requirements": []}

        with patch("web.coach.spec.hub", mock_hub):
            with patch("web.coach.spec.call_glm5", new_callable=AsyncMock) as mock_llm:
                with patch("web.coach.spec.parse_json_from_llm") as mock_parse:
                    mock_llm.return_value = 'Here is the JSON: {"title": "Test"}'
                    mock_parse.return_value = mock_spec
                    await finalize_spec(state)

        mock_parse.assert_called_once()

    @pytest.mark.asyncio
    async def test_finalize_spec_failure_sets_phase(self, mock_hub):
        """Spec failure should set _failed_phase."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.CLARIFYING,
            description="Test",
        )

        with patch("web.coach.spec.hub", mock_hub):
            with patch("web.coach.spec.call_glm5", new_callable=AsyncMock) as mock_llm:
                mock_llm.side_effect = Exception("LLM error")
                await finalize_spec(state)

        assert state._failed_phase == "spec"
        assert state.status == Status.EXECUTING

    @pytest.mark.asyncio
    async def test_finalize_spec_uses_conversation_history(self, mock_hub):
        """Should include conversation history in prompt."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.CLARIFYING,
            description="Test feature",
            conversation_history=[
                {"question": "What scope?", "answer": "User management"},
                {"question": "Priority?", "answer": "High"},
            ],
        )

        mock_spec = {"title": "Test", "scope": "", "requirements": []}

        with patch("web.coach.spec.hub", mock_hub):
            with patch("web.coach.spec.call_glm5", new_callable=AsyncMock) as mock_llm:
                with patch("web.coach.spec.parse_json_from_llm") as mock_parse:
                    mock_llm.return_value = "response"
                    mock_parse.return_value = mock_spec
                    await finalize_spec(state)

        # Check that conversation history was included
        call_args = mock_llm.call_args
        user_prompt = call_args[0][1]  # second positional arg is user_prompt
        assert "User management" in user_prompt or "High" in user_prompt


class TestPresentPlan:
    """Tests for _present_plan function."""

    @pytest.mark.asyncio
    async def test_present_plan_format(self, mock_hub):
        """Should format spec as markdown."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.SPEC_READY,
            spec={
                "title": "User Authentication",
                "scope": "Implement login/logout",
                "requirements": [
                    {
                        "id": "R1",
                        "title": "Login",
                        "priority": "MUST",
                        "scenarios": [
                            {"title": "Valid login", "condition": "User enters correct creds", "expected": "User is logged in"}
                        ]
                    }
                ]
            },
        )

        with patch("web.coach.spec.hub", mock_hub):
            await _present_plan(state)

        # Check that send_chat was called with formatted content
        mock_hub.send_to_dev.assert_called()
        # Find the chat message
        for call in mock_hub.send_to_dev.call_args_list:
            args = call[0][0]
            if args.get("type") == "coach-chat":
                content = args.get("content", "")
                assert "User Authentication" in content
                assert "Login" in content

    @pytest.mark.asyncio
    async def test_present_plan_status_change(self, mock_hub):
        """Should change status to USER_CONFIRMING."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.SPEC_READY,
            spec={"title": "Test", "scope": "", "requirements": []},
        )

        with patch("web.coach.spec.hub", mock_hub):
            await _present_plan(state)

        assert state.status == Status.USER_CONFIRMING

    @pytest.mark.asyncio
    async def test_present_plan_priority_labels(self, mock_hub):
        """Should show priority labels correctly."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.SPEC_READY,
            spec={
                "title": "Test",
                "scope": "",
                "requirements": [
                    {"id": "R1", "title": "Must have", "priority": "MUST"},
                    {"id": "R2", "title": "Should have", "priority": "SHOULD"},
                    {"id": "R3", "title": "May have", "priority": "MAY"},
                ]
            },
        )

        with patch("web.coach.spec.hub", mock_hub):
            await _present_plan(state)

        # Find chat message content
        content = ""
        for call in mock_hub.send_to_dev.call_args_list:
            args = call[0][0]
            if args.get("type") == "coach-chat":
                content = args.get("content", "")

        assert "必须" in content or "MUST" in content
        assert "建议" in content or "SHOULD" in content
        assert "可选" in content or "MAY" in content

    @pytest.mark.asyncio
    async def test_present_plan_sends_spec_ready_event(self, mock_hub):
        """Should send feature-spec-ready event."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.SPEC_READY,
            spec={"title": "Test", "scope": "", "requirements": []},
        )

        with patch("web.coach.spec.hub", mock_hub):
            await _present_plan(state)

        # Check for spec-ready event
        found = False
        for call in mock_hub.send_to_dev.call_args_list:
            args = call[0][0]
            if args.get("type") == "feature-spec-ready":
                found = True
                assert args.get("featureId") == "test-123"
                assert args.get("spec") is not None
        assert found


class TestSpecConstants:
    """Tests for spec module constants."""

    def test_system_prompt_exists(self):
        """SYSTEM_PROMPT should be defined."""
        assert len(SYSTEM_PROMPT) > 100

    def test_system_prompt_has_json_format(self):
        """SYSTEM_PROMPT should specify JSON format."""
        assert "JSON" in SYSTEM_PROMPT or "json" in SYSTEM_PROMPT

    def test_system_prompt_has_priority_values(self):
        """SYSTEM_PROMPT should mention priority values."""
        assert "MUST" in SYSTEM_PROMPT
        assert "SHOULD" in SYSTEM_PROMPT
        assert "MAY" in SYSTEM_PROMPT
