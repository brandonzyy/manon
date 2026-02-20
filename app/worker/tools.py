"""Manus agent tools — OpenAI function-calling format definitions + execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("manon.worker.tools")

# ── Safety constants ──

_DANGEROUS_CMD_RE = re.compile(
    r"rm\s+-rf|rmdir\s+/|del\s+/|format\s+|mkfs\.|dd\s+if=|git\s+clean|git\s+reset\s+--hard",
    re.IGNORECASE,
)
_PKG_MGR_RE = re.compile(
    r"\b(npm|yarn|pnpm|pip|pip3)\s+(install|add|remove|uninstall|ci)\b",
    re.IGNORECASE,
)
_MAX_FILE_LINES = 600
_MAX_CMD_OUTPUT = 8000
_CMD_TIMEOUT = 30  # seconds


# ── Tool definitions (OpenAI function-calling format) ──

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file. Returns file content or error.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file. old_text must match exactly (including whitespace).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to find and replace",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full file content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command in the repo root. Max 30s. NEVER run package manager install commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search the project code knowledge graph for relevant code entities, functions, and relationships.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (function name, class name, concept, etc.)",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


# ── Path safety ──

def _safe_resolve(repo_root: str, rel_path: str) -> str | None:
    """Resolve *rel_path* under *repo_root*, rejecting traversal."""
    root = Path(repo_root).resolve()
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        return None
    return str(target)


# ── Tool execution functions ──

def exec_read_file(repo_root: str, args: dict) -> dict:
    rel = args.get("path", "")
    abs_path = _safe_resolve(repo_root, rel)
    if abs_path is None:
        return {"error": "Path outside repo"}
    try:
        content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"error": f"File not found: {rel}"}
    except Exception as exc:
        return {"error": str(exc)}
    lines = content.split("\n")
    if len(lines) > _MAX_FILE_LINES:
        content = "\n".join(lines[:_MAX_FILE_LINES]) + f"\n// ... truncated ({len(lines)} lines total)"
    return {"content": content}


def exec_edit_file(repo_root: str, args: dict) -> dict:
    rel = args.get("path", "")
    abs_path = _safe_resolve(repo_root, rel)
    if abs_path is None:
        return {"error": "Path outside repo"}
    try:
        content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"error": f"File not found: {rel}"}
    old_text = args.get("old_text", "")
    new_text = args.get("new_text", "")
    if not content.__contains__(old_text):
        return {"error": "old_text not found in file"}
    count = content.count(old_text)
    if count > 1:
        return {"error": f"old_text matches {count} times, must be unique. Add more context."}
    Path(abs_path).write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
    return {"success": True, "message": f"Edited {rel}"}


def exec_write_file(repo_root: str, args: dict) -> dict:
    rel = args.get("path", "")
    abs_path = _safe_resolve(repo_root, rel)
    if abs_path is None:
        return {"error": "Path outside repo"}
    p = Path(abs_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    file_content = args.get("content", "")
    p.write_text(file_content, encoding="utf-8")
    line_count = file_content.count("\n") + 1
    return {"success": True, "message": f"Created {rel} ({line_count} lines)"}


async def exec_run_command(repo_root: str, args: dict) -> dict:
    cmd = args.get("command", "")
    if _DANGEROUS_CMD_RE.search(cmd):
        return {"error": "Dangerous command blocked"}
    if _PKG_MGR_RE.search(cmd):
        return {"error": "Package manager commands blocked — do not modify dependencies"}
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=repo_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_CMD_TIMEOUT)
        out = (stdout.decode(errors="replace") + stderr.decode(errors="replace"))[:_MAX_CMD_OUTPUT]
        return {"exit_code": proc.returncode, "output": out}
    except asyncio.TimeoutError:
        return {"error": f"Command timed out after {_CMD_TIMEOUT}s"}
    except Exception as exc:
        return {"error": str(exc)}


async def exec_search_code(repo_root: str, args: dict) -> dict:
    query = args.get("query", "")
    if not query:
        return {"error": "Empty query"}
    try:
        from matrixone_graph import MatrixoneGraph
        mg = MatrixoneGraph.get(repo_root)
        result = await mg.query(query, top_k=5, depth=1)
        if result.context:
            return {"context": result.context}
        return {"context": "(no results)"}
    except Exception as exc:
        log.warning("search_code failed: %s", exc)
        return {"error": f"Graph query failed: {exc}"}


# ── Dispatcher ──

async def execute_tool(name: str, args: dict, repo_root: str) -> dict:
    """Execute a tool by name, return result dict."""
    if name == "read_file":
        return exec_read_file(repo_root, args)
    if name == "edit_file":
        return exec_edit_file(repo_root, args)
    if name == "write_file":
        return exec_write_file(repo_root, args)
    if name == "run_command":
        return await exec_run_command(repo_root, args)
    if name == "search_code":
        return await exec_search_code(repo_root, args)
    return {"error": f"Unknown tool: {name}"}
