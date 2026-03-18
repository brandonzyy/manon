"""Tests for web/coach/decompose.py — task decomposition and execution."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from web.coach.decompose import (
    decompose_to_tasks, execute_task_loop, assign_task,
    _group_tasks_by_order, SYSTEM_PROMPT, TASK_TIMEOUT, MAX_RETRIES,
)
from web.coach.pipeline import Status, FeatureState


class TestGroupTasksByOrder:
    """Tests for _group_tasks_by_order helper."""

    def test_group_single_order(self):
        """Tasks with same order should be grouped together."""
        tasks = [
            {"id": 1, "order": 1},
            {"id": 2, "order": 1},
            {"id": 3, "order": 1},
        ]
        groups = _group_tasks_by_order(tasks)
        assert len(groups) == 1
        assert len(groups[1]) == 3

    def test_group_multiple_orders(self):
        """Tasks with different orders should be in separate groups."""
        tasks = [
            {"id": 1, "order": 1},
            {"id": 2, "order": 2},
            {"id": 3, "order": 1},
            {"id": 4, "order": 3},
        ]
        groups = _group_tasks_by_order(tasks)
        assert len(groups) == 3
        assert len(groups[1]) == 2
        assert len(groups[2]) == 1
        assert len(groups[3]) == 1

    def test_group_default_order(self):
        """Tasks without order should use index + 1 as order."""
        tasks = [
            {"id": 1},
            {"id": 2},
            {"id": 3},
        ]
        groups = _group_tasks_by_order(tasks)
        assert len(groups) == 3
        assert len(groups[1]) == 1
        assert len(groups[2]) == 1
        assert len(groups[3]) == 1

    def test_group_empty_tasks(self):
        """Empty tasks list should return empty dict."""
        groups = _group_tasks_by_order([])
        assert groups == {}

    def test_group_preserves_task_data(self):
        """Task data should be preserved in groups."""
        tasks = [
            {"id": 1, "order": 1, "title": "Task 1"},
            {"id": 2, "order": 1, "title": "Task 2"},
        ]
        groups = _group_tasks_by_order(tasks)
        assert groups[1][0]["title"] == "Task 1"
        assert groups[1][1]["title"] == "Task 2"


class TestDecomposeToTasks:
    """Tests for decompose_to_tasks function."""

    @pytest.mark.asyncio
    async def test_decompose_success(self, mock_hub):
        """Successful decomposition should create tasks list."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.DESIGNING,
            description="Test feature",
            spec={
                "title": "Test Feature",
                "scope": "Test scope",
                "requirements": [
                    {"id": "R1", "title": "Requirement 1", "priority": "MUST", "scenarios": []}
                ]
            },
            design={"approach": "Test approach", "fileChanges": []},
        )

        mock_tasks = [
            {"id": 1, "title": "Task 1", "instruction": "Do task 1", "files": ["a.py"], "order": 1},
            {"id": 2, "title": "Task 2", "instruction": "Do task 2", "files": ["b.py"], "order": 2},
        ]

        with patch("web.coach.decompose.hub", mock_hub):
            with patch("web.coach.decompose.call_glm5", new_callable=AsyncMock) as mock_llm:
                with patch("web.coach.decompose.parse_json_from_llm") as mock_parse:
                    with patch("web.coach.decompose.execute_task_loop", new_callable=AsyncMock):
                        mock_llm.return_value = "JSON response"
                        mock_parse.return_value = mock_tasks
                        await decompose_to_tasks(state)

        assert len(state.tasks) == 2
        assert state.tasks[0]["status"] == "pending"
        assert state.status == Status.DECOMPOSING

    @pytest.mark.asyncio
    async def test_decompose_failure_sets_phase(self, mock_hub):
        """Decomposition failure should set _failed_phase."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.DESIGNING,
            description="Test",
            spec={"title": "Test"},
        )

        with patch("web.coach.decompose.hub", mock_hub):
            with patch("web.coach.decompose.call_glm5", new_callable=AsyncMock) as mock_llm:
                mock_llm.side_effect = Exception("LLM error")
                await decompose_to_tasks(state)

        assert state._failed_phase == "decompose"
        assert state.status == Status.EXECUTING

    @pytest.mark.asyncio
    async def test_decompose_calls_execute_task_loop(self, mock_hub):
        """Successful decomposition should call execute_task_loop."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.DESIGNING,
            description="Test",
            spec={"title": "Test", "requirements": []},
        )

        with patch("web.coach.decompose.hub", mock_hub):
            with patch("web.coach.decompose.call_glm5", new_callable=AsyncMock) as mock_llm:
                with patch("web.coach.decompose.parse_json_from_llm") as mock_parse:
                    with patch("web.coach.decompose.execute_task_loop", new_callable=AsyncMock) as mock_exec:
                        mock_llm.return_value = "response"
                        mock_parse.return_value = [{"id": 1, "title": "Task"}]
                        await decompose_to_tasks(state)

        mock_exec.assert_called_once_with(state)

    @pytest.mark.asyncio
    async def test_decompose_includes_design_context(self, mock_hub):
        """Decomposition should include design context in prompt."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.DESIGNING,
            description="Test",
            spec={"title": "Test", "requirements": []},
            design={
                "approach": "Use FastAPI",
                "fileChanges": [{"file": "main.py", "action": "new", "description": "Main app"}]
            },
        )

        captured_prompt = []

        async def capture_prompt(*args, **kwargs):
            captured_prompt.append(args[1])  # user_prompt is second arg
            return "response"

        with patch("web.coach.decompose.hub", mock_hub):
            with patch("web.coach.decompose.call_glm5", new_callable=AsyncMock) as mock_llm:
                with patch("web.coach.decompose.parse_json_from_llm") as mock_parse:
                    with patch("web.coach.decompose.execute_task_loop", new_callable=AsyncMock):
                        mock_llm.side_effect = capture_prompt
                        mock_llm.return_value = "response"
                        mock_parse.return_value = []
                        await decompose_to_tasks(state)

        # Design context should be in prompt
        prompt = captured_prompt[0] if captured_prompt else ""
        assert "FastAPI" in prompt or "技术设计" in prompt


class TestExecuteTaskLoop:
    """Tests for execute_task_loop function."""

    @pytest.mark.asyncio
    async def test_execute_single_task(self, mock_hub):
        """Single task should be executed directly."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.DECOMPOSING,
            tasks=[{"id": 1, "title": "Task 1", "status": "pending", "order": 1}],
        )

        with patch("web.coach.decompose.hub", mock_hub):
            with patch("web.coach.decompose.assign_task", new_callable=AsyncMock) as mock_assign:
                with patch("web.coach.decompose.generate_report", new_callable=AsyncMock):
                    with patch("web.coach.decompose.llm_chat", new_callable=AsyncMock) as mock_eval:
                        mock_assign.return_value = True
                        mock_eval.return_value = {"content": '{"score": 8, "summary": "Good", "issues": [], "passed": true}'}
                        await execute_task_loop(state)

        assert state.tasks[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_execute_parallel_tasks(self, mock_hub):
        """Same-order tasks should run in parallel."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.DECOMPOSING,
            tasks=[
                {"id": 1, "title": "Task 1", "status": "pending", "order": 1},
                {"id": 2, "title": "Task 2", "status": "pending", "order": 1},
            ],
        )

        call_count = 0

        async def count_assign(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return True

        with patch("web.coach.decompose.hub", mock_hub):
            with patch("web.coach.decompose.assign_task", new_callable=AsyncMock) as mock_assign:
                with patch("web.coach.decompose.generate_report", new_callable=AsyncMock):
                    with patch("web.coach.decompose.llm_chat", new_callable=AsyncMock) as mock_eval:
                        mock_assign.side_effect = count_assign
                        mock_eval.return_value = {"content": '{"score": 8, "summary": "Good", "issues": []}'}
                        await execute_task_loop(state)

        # Both tasks should be called
        assert call_count == 2
        assert state.tasks[0]["status"] == "completed"
        assert state.tasks[1]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_task_failure_prompts_user(self, mock_hub):
        """Task failure should send message to user."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.DECOMPOSING,
            tasks=[{"id": 1, "title": "Task 1", "status": "pending", "order": 1}],
        )

        with patch("web.coach.decompose.hub", mock_hub):
            with patch("web.coach.decompose.assign_task", new_callable=AsyncMock) as mock_assign:
                mock_assign.return_value = False
                await execute_task_loop(state)

        # Should have sent failure message
        assert state.tasks[0]["status"] == "failed"
        # Check that send_chat was called with failure message
        found = False
        for call in mock_hub.send_to_dev.call_args_list:
            args = call[0][0]
            if args.get("type") == "coach-chat":
                content = args.get("content", "")
                if "失败" in content:
                    found = True
        assert found

    @pytest.mark.asyncio
    async def test_all_tasks_complete_evaluation(self, mock_hub):
        """All tasks complete should trigger LLM evaluation."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            status=Status.DECOMPOSING,
            description="Test feature",
            spec={"title": "Test"},
            design={"approach": "Test approach"},
            tasks=[
                {"id": 1, "title": "Task 1", "status": "pending", "order": 1},
            ],
        )

        with patch("web.coach.decompose.hub", mock_hub):
            with patch("web.coach.decompose.assign_task", new_callable=AsyncMock) as mock_assign:
                with patch("web.coach.decompose.generate_report", new_callable=AsyncMock):
                    with patch("web.coach.decompose.llm_chat", new_callable=AsyncMock) as mock_eval:
                        mock_assign.return_value = True
                        mock_eval.return_value = {
                            "content": '{"score": 8, "summary": "Good job", "issues": [], "passed": true}'
                        }
                        await execute_task_loop(state)

        # Evaluation should have been called
        mock_eval.assert_called()
        assert state.evaluation is not None
        assert state.status == Status.REVIEWING


class TestAssignTask:
    """Tests for assign_task function."""

    @pytest.mark.asyncio
    async def test_assign_task_success(self, mock_hub):
        """Successful task assignment should return True."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            project_id="proj-001",
            spec={"title": "Test"},
            design={"approach": "Test"},
            tasks=[{"id": 1, "title": "Task", "status": "pending"}],
        )

        task = {"id": 1, "title": "Task", "instruction": "Do the task", "files": ["test.py"]}

        mock_pool = MagicMock()
        mock_pool.submit = AsyncMock(return_value={
            "type": "feature-task-done",
            "output": "Done",
            "diffs": {},
        })

        with patch("web.coach.decompose.worker_pool", mock_pool):
            with patch("web.coach.decompose.hub", mock_hub):
                result = await assign_task(state, task)

        assert result is True

    @pytest.mark.asyncio
    async def test_assign_task_retry_logic(self, mock_hub):
        """Task should retry up to MAX_RETRIES times."""
        state = FeatureState(
            feature_id="test-123",
            dev_id="dev-001",
            tasks=[],
        )

        task = {"id": 1, "title": "Task", "instruction": "Do the task"}

        call_count = 0

        async def failing_submit(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"type": "feature-task-failed", "reason": "Error"}

        mock_pool = MagicMock()
        mock_pool.submit = failing_submit

        with patch("web.coach.decompose.worker_pool", mock_pool):
            with patch("web.coach.decompose.hub", mock_hub):
                result = await assign_task(state, task)

        assert result is False
        assert call_count == MAX_RETRIES + 1  # Initial + retries


class TestDecomposeConstants:
    """Tests for decompose module constants."""

    def test_system_prompt_exists(self):
        """SYSTEM_PROMPT should be defined."""
        assert len(SYSTEM_PROMPT) > 100

    def test_system_prompt_has_json_format(self):
        """SYSTEM_PROMPT should specify JSON format."""
        assert "JSON" in SYSTEM_PROMPT

    def test_system_prompt_has_order_field(self):
        """SYSTEM_PROMPT should mention order field."""
        assert "order" in SYSTEM_PROMPT

    def test_task_timeout_reasonable(self):
        """TASK_TIMEOUT should be reasonable (5-30 minutes)."""
        assert 300 <= TASK_TIMEOUT <= 1800  # 5-30 min in seconds

    def test_max_retries_reasonable(self):
        """MAX_RETRIES should be reasonable (1-5)."""
        assert 1 <= MAX_RETRIES <= 5
