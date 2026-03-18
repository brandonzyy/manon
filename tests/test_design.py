"""Tests for web/coach/design.py — design generation."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from web.coach.design import generate_design, SYSTEM_PROMPT
from web.coach.pipeline import Status, FeatureState


class TestGenerateDesign:
    """Tests for generate_design function."""

    @pytest.mark.asyncio
    async def test_generate_design_success(self, mock_hub):
        """Successful design generation should set design dict."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.USER_CONFIRMING,
            description="Test feature",
            spec={
                "title": "Test Feature",
                "scope": "Test scope",
                "requirements": [
                    {
                        "id": "R1",
                        "title": "Requirement 1",
                        "priority": "MUST",
                        "scenarios": [{"title": "Scenario", "condition": "When", "expected": "Then"}]
                    }
                ]
            },
        )

        mock_design = {
            "approach": "Use Flask with SQLAlchemy",
            "decisions": [{"title": "Use SQLite", "rationale": "Simple setup"}],
            "fileChanges": [{"file": "app.py", "action": "new", "description": "Main app"}]
        }

        with patch("web.coach.design.hub", mock_hub):
            with patch("web.coach.design.call_glm5", new_callable=AsyncMock) as mock_llm:
                with patch("web.coach.design.parse_json_from_llm") as mock_parse:
                    with patch("web.coach.design.decompose_to_tasks", new_callable=AsyncMock):
                        mock_llm.return_value = "JSON response"
                        mock_parse.return_value = mock_design
                        await generate_design(state)

        assert state.design == mock_design
        assert state.status == Status.DESIGNING

    @pytest.mark.asyncio
    async def test_generate_design_fallback_on_exception(self, mock_hub):
        """Design failure should set design=None and continue to decompose."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.USER_CONFIRMING,
            description="Test feature",
            spec={"title": "Test"},
        )

        with patch("web.coach.design.hub", mock_hub):
            with patch("web.coach.design.call_glm5", new_callable=AsyncMock) as mock_llm:
                with patch("web.coach.design.decompose_to_tasks", new_callable=AsyncMock) as mock_decompose:
                    mock_llm.side_effect = Exception("LLM error")
                    await generate_design(state)

        assert state.design is None
        mock_decompose.assert_called_once()

    @pytest.mark.asyncio
    async def test_design_format_output(self, mock_hub):
        """Design output should be formatted correctly."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.USER_CONFIRMING,
            description="Test",
            spec={
                "title": "Test",
                "scope": "",
                "requirements": [{"id": "R1", "title": "Req", "priority": "MUST", "scenarios": []}]
            },
        )

        mock_design = {
            "approach": "Test approach",
            "decisions": [{"title": "Decision 1", "rationale": "Reason 1"}],
            "fileChanges": [{"file": "main.py", "action": "new", "description": "Main file"}]
        }

        with patch("web.coach.design.hub", mock_hub):
            with patch("web.coach.design.call_glm5", new_callable=AsyncMock) as mock_llm:
                with patch("web.coach.design.parse_json_from_llm") as mock_parse:
                    with patch("web.coach.design.decompose_to_tasks", new_callable=AsyncMock):
                        mock_llm.return_value = "response"
                        mock_parse.return_value = mock_design
                        await generate_design(state)

        # Check that send_chat was called with formatted content
        found_chat = False
        for call in mock_hub.send_to_dev.call_args_list:
            args = call[0][0]
            if args.get("type") == "coach-chat":
                content = args.get("content", "")
                if "Test approach" in content:
                    found_chat = True
                    assert "Decision 1" in content
                    assert "main.py" in content
        assert found_chat

    @pytest.mark.asyncio
    async def test_design_calls_decompose(self, mock_hub):
        """generate_design should call decompose_to_tasks after design."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.USER_CONFIRMING,
            description="Test",
            spec={"title": "Test", "requirements": []},
        )

        mock_design = {"approach": "Test", "decisions": [], "fileChanges": []}

        with patch("web.coach.design.hub", mock_hub):
            with patch("web.coach.design.call_glm5", new_callable=AsyncMock) as mock_llm:
                with patch("web.coach.design.parse_json_from_llm") as mock_parse:
                    with patch("web.coach.design.decompose_to_tasks", new_callable=AsyncMock) as mock_decompose:
                        mock_llm.return_value = "response"
                        mock_parse.return_value = mock_design
                        await generate_design(state)

        mock_decompose.assert_called_once_with(state)

    @pytest.mark.asyncio
    async def test_design_status_change(self, mock_hub):
        """Status should change to DESIGNING during design."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.USER_CONFIRMING,
            description="Test",
            spec={"title": "Test", "requirements": []},
        )

        captured_status = []

        async def capture_status(*args, **kwargs):
            captured_status.append(state.status)

        mock_design = {"approach": "Test", "decisions": [], "fileChanges": []}

        with patch("web.coach.design.hub", mock_hub):
            with patch("web.coach.design.call_glm5", new_callable=AsyncMock) as mock_llm:
                with patch("web.coach.design.parse_json_from_llm") as mock_parse:
                    with patch("web.coach.design.decompose_to_tasks", new_callable=AsyncMock):
                        mock_llm.return_value = "response"
                        mock_parse.return_value = mock_design
                        mock_llm.side_effect = capture_status
                        mock_llm.return_value = "response"
                        await generate_design(state)

        assert state.status == Status.DESIGNING


class TestDesignConstants:
    """Tests for design module constants."""

    def test_system_prompt_exists(self):
        """SYSTEM_PROMPT should be defined."""
        assert len(SYSTEM_PROMPT) > 100

    def test_system_prompt_has_json_format(self):
        """SYSTEM_PROMPT should specify JSON format."""
        assert "JSON" in SYSTEM_PROMPT or "json" in SYSTEM_PROMPT

    def test_system_prompt_has_file_changes(self):
        """SYSTEM_PROMPT should mention fileChanges."""
        assert "fileChanges" in SYSTEM_PROMPT

    def test_system_prompt_has_decisions(self):
        """SYSTEM_PROMPT should mention decisions."""
        assert "decisions" in SYSTEM_PROMPT
