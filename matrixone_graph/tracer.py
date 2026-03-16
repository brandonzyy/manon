"""Runtime call tracer — captures dynamic call edges via sys.setprofile.

Usage:
    tracer = CallTracer(project_root="/path/to/project")
    tracer.start()
    # ... run tests or application code ...
    tracer.stop()
    tracer.save("dynamic-deps.json")

The output JSON maps "caller->callee" to call count, e.g.:
    {"manon_mcp._tools.register->manon_mcp._client._get": 3, ...}
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any


class CallTracer:
    """Capture call events using sys.setprofile and record (caller, callee) pairs."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self._project_root = str(Path(project_root).resolve()) if project_root else None
        self._edges: dict[str, int] = {}  # "caller->callee" -> count
        self._lock = threading.Lock()
        self._old_profile = None
        self._active = False
        # stdlib / site-packages prefixes to filter out
        self._stdlib_prefixes = self._build_stdlib_prefixes()

    @staticmethod
    def _build_stdlib_prefixes() -> tuple[str, ...]:
        prefixes = []
        for p in sys.path:
            norm = os.path.normcase(os.path.abspath(p))
            if "site-packages" in norm or "dist-packages" in norm:
                prefixes.append(norm)
        # stdlib location
        stdlib = os.path.normcase(os.path.dirname(os.__file__))
        prefixes.append(stdlib)
        return tuple(prefixes)

    def _is_project_file(self, filename: str | None) -> bool:
        if not filename:
            return False
        norm = os.path.normcase(os.path.abspath(filename))
        if any(norm.startswith(p) for p in self._stdlib_prefixes):
            return False
        if self._project_root:
            root = os.path.normcase(self._project_root)
            return norm.startswith(root)
        return True

    def _frame_to_id(self, frame) -> str | None:
        """Convert a frame to a 'module.function' entity ID."""
        filename = frame.f_code.co_filename
        if not self._is_project_file(filename):
            return None
        func_name = frame.f_code.co_name
        if func_name in ("<module>", "<lambda>", "<listcomp>", "<dictcomp>", "<setcomp>", "<genexpr>"):
            return None
        # Derive module from file path
        root = self._project_root or os.getcwd()
        try:
            rel = os.path.relpath(filename, root)
        except ValueError:
            return None
        rel = rel.replace("\\", "/")
        if rel.startswith(".."):
            return None
        # Strip extension, convert to dotted module
        base = rel.rsplit(".", 1)[0]
        parts = base.split("/")
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        module = ".".join(parts)
        return f"{module}.{func_name}" if module else func_name

    def _profile_callback(self, frame, event: str, arg: Any) -> None:
        if event != "call":
            return
        callee_id = self._frame_to_id(frame)
        if not callee_id:
            return
        caller_frame = frame.f_back
        if not caller_frame:
            return
        caller_id = self._frame_to_id(caller_frame)
        if not caller_id:
            return
        if caller_id == callee_id:
            return
        key = f"{caller_id}->{callee_id}"
        with self._lock:
            self._edges[key] = self._edges.get(key, 0) + 1

    def start(self) -> None:
        """Start tracing calls."""
        if self._active:
            return
        self._old_profile = sys.getprofile()
        sys.setprofile(self._profile_callback)
        threading.setprofile(self._profile_callback)
        self._active = True

    def stop(self) -> None:
        """Stop tracing calls."""
        if not self._active:
            return
        sys.setprofile(self._old_profile)
        threading.setprofile(None)
        self._active = False

    @property
    def edges(self) -> dict[str, int]:
        """Return captured edges as {\"caller->callee\": count}."""
        with self._lock:
            return dict(self._edges)

    def save(self, path: str | Path) -> None:
        """Save captured edges to a JSON file."""
        data = self.edges
        Path(path).write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def load(path: str | Path) -> dict[str, int]:
        """Load edges from a JSON file."""
        return json.loads(Path(path).read_text(encoding="utf-8"))
