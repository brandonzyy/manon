"""Worker agent system prompt builder — generic, no project-specific hardcoding."""

from __future__ import annotations


def build_system_prompt(
    project_name: str,
    project_path: str,
    workspace: str | None = None,
    test_command: str | None = None,
    graph_context: str | None = None,
) -> str:
    sections = [
        "You are a coding worker for Manon. You receive development tasks and implement them by editing source code.",
        "",
        f"Project: {project_name}",
        f"Project root: {project_path}",
    ]

    if workspace:
        sections.append(f"Workspace (working directory for tests): {workspace}")

    sections.extend([
        "",
        "=== Code Change Rules ===",
        "- Keep changes minimal and focused on the task.",
        "- Do NOT add new package dependencies unless explicitly required.",
        "- When done, output a concise summary of what you changed.",
        "- Use search_code to find relevant code before making changes.",
        "",
        "=== Test Rules ===",
        "- When you modify a source file, update its test file if one exists.",
        "- Do NOT change test expectations — update source code to match existing tests.",
        "- If adding new states/branches, add corresponding test cases.",
    ])

    if test_command:
        sections.extend([
            "",
            f"=== Test Command: {test_command} ===",
            "Run this command to verify your changes pass tests.",
        ])

    if graph_context:
        sections.extend([
            "",
            "=== Code Knowledge Graph Context ===",
            graph_context,
        ])

    return "\n".join(sections)
