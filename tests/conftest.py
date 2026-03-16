"""Shared pytest fixtures for manon tests."""
import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_hub():
    """Mock WebSocket hub for testing pipeline without real connections."""
    hub = MagicMock()
    hub.send_to_dev = AsyncMock()
    hub.send_to_monitor = AsyncMock()
    hub.broadcast_to_monitors = AsyncMock()
    return hub


@pytest.fixture
def mock_llm():
    """Mock LLM responses for deterministic testing."""
    mock = AsyncMock()
    mock.return_value = {"content": "Mocked LLM response"}
    return mock


@pytest.fixture
def feature_state():
    """Pre-populated FeatureState for testing various states."""
    from web.coach.pipeline import FeatureState, Status
    return FeatureState(
        feature_id="test-123",
        dev_id="dev-001",
        project_id="proj-001",
        status=Status.IDLE,
        description="Test feature description",
    )


@pytest.fixture
def feature_state_clarifying(feature_state):
    """FeatureState in CLARIFYING status."""
    from web.coach.pipeline import Status
    feature_state.status = Status.CLARIFYING
    feature_state.conversation_history = [
        {"question": "What is the feature?", "answer": "A test feature"}
    ]
    return feature_state


@pytest.fixture
def feature_state_executing(feature_state):
    """FeatureState in EXECUTING status with tasks."""
    from web.coach.pipeline import Status
    feature_state.status = Status.EXECUTING
    feature_state.tasks = [
        {"id": 1, "title": "Task 1", "status": "pending"},
        {"id": 2, "title": "Task 2", "status": "completed"},
        {"id": 3, "title": "Task 3", "status": "failed"},
    ]
    feature_state.current_task_idx = 0
    return feature_state


@pytest.fixture
def feature_state_reviewing(feature_state):
    """FeatureState in REVIEWING status with completed tasks."""
    from web.coach.pipeline import Status
    feature_state.status = Status.REVIEWING
    feature_state.spec = {
        "title": "Test Feature",
        "scope": "Test scope",
        "requirements": [{"priority": "MUST", "title": "Req 1"}]
    }
    feature_state.design = {
        "approach": "Test approach",
        "fileChanges": [{"file": "test.py", "action": "create", "description": "Test file"}]
    }
    feature_state.tasks = [
        {"id": 1, "title": "Task 1", "status": "completed"},
        {"id": 2, "title": "Task 2", "status": "completed"},
    ]
    feature_state.evaluation = {"score": 8, "summary": "Good job"}
    return feature_state


@pytest.fixture
def temp_repo(tmp_path):
    """Temporary git repo for worker tool tests."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    # Initialize git repo
    subprocess.run(
        ["git", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo),
        check=True,
        capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(repo),
        check=True,
        capture_output=True
    )
    # Create a test file
    test_file = repo / "test.txt"
    test_file.write_text("hello world", encoding="utf-8")

    # Create nested directory structure
    nested_dir = repo / "src" / "components"
    nested_dir.mkdir(parents=True)
    (nested_dir / "index.ts").write_text("export const x = 1;", encoding="utf-8")

    return str(repo)


@pytest.fixture
def temp_project_registry(tmp_path, monkeypatch):
    """Create a temporary project registry for ast_sync tests."""
    registry_file = tmp_path / "projects.json"
    registry_file.write_text('{"projects": {}}', encoding="utf-8")

    import core.ast.project as ast_project
    monkeypatch.setattr(ast_project, "PROJECTS_FILE", registry_file)
    monkeypatch.setattr(ast_project, "PROJECTS_DIR", tmp_path)

    return registry_file


@pytest.fixture
def mock_httpx_response():
    """Factory fixture for creating mock httpx responses."""
    def _create(json_data=None, status_code=200, raise_error=False):
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = json_data or {}

        def raise_for_status():
            if status_code >= 400:
                import httpx
                raise httpx.HTTPStatusError(
                    f"HTTP {status_code}",
                    request=MagicMock(),
                    response=mock
                )
        mock.raise_for_status = raise_for_status
        return mock
    return _create


@pytest.fixture
def mock_aiohttpx_client():
    """Mock async httpx client for saas_client tests."""
    client = MagicMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.delete = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def clean_sessions():
    """Clear _sessions dict before and after each test."""
    from web.coach import pipeline
    pipeline._sessions.clear()
    yield
    pipeline._sessions.clear()


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
