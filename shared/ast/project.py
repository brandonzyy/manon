"""Project registry management - shared between MCP and web clients."""
from __future__ import annotations

import json
from pathlib import Path

PROJECTS_DIR = Path.home() / ".manon"
PROJECTS_FILE = PROJECTS_DIR / "projects.json"


def load_projects() -> dict:
    """Load projects registry from ~/.manon/projects.json."""
    if PROJECTS_FILE.exists():
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    return {"projects": {}}


def save_projects(data: dict) -> None:
    """Save projects registry to ~/.manon/projects.json."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_project(local_path: str) -> dict | None:
    """Get project info by local path."""
    norm = str(Path(local_path).resolve()).replace("\\", "/")
    return load_projects()["projects"].get(norm)


def set_project(local_path: str, info: dict) -> None:
    """Set project info for local path."""
    norm = str(Path(local_path).resolve()).replace("\\", "/")
    data = load_projects()
    data["projects"][norm] = info
    save_projects(data)


def find_project_by_repo_id(repo_id: str) -> tuple[str, dict] | None:
    """Find project by repo_id. Returns (local_path, info) or None."""
    for path, info in load_projects()["projects"].items():
        if info.get("repo_id") == repo_id:
            return path, info
    return None
