"""Script classifier — distinguish tool scripts from source code.

Signals (in priority order):
1. is_imported_by_project  → source_code (definitive)
2. imports_project_modules → source_code (definitive)
3. tool name + single_main → tool_script (definitive)
4. uncertain               → LLM tiebreaker
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Literal

# ── Naming heuristics ─────────────────────────────────────────────────────────

_TOOL_PATTERNS = [
    r"^deploy[_-]", r"[_-]deploy$",
    r"^setup[_-]",  r"[_-]setup$",
    r"^install[_-]",
    r"^migrate[_-]",
    r"^seed[_-]",
    r"^admin[_-]",  r"[_-]admin$",
    r"^run[_-]",    r"^start[_-]", r"^stop[_-]",
    r"^init[_-]",   r"^bootstrap[_-]",
    r"^cleanup[_-]",r"^reset[_-]",
    r"^update[_-]", r"[_-]update$",
    r"^helper[_-]", r"[_-]helper$",
]
_TOOL_RE = re.compile("|".join(_TOOL_PATTERNS), re.IGNORECASE)


def _is_tool_name(stem: str) -> bool:
    return bool(_TOOL_RE.search(stem))


# ── AST-based signal extraction ───────────────────────────────────────────────

class ScriptSignals:
    def __init__(self, rel_path: str, parse_result: dict | None = None,
                 source: str | None = None):
        self.rel_path = rel_path
        self.stem = Path(rel_path).stem

        # Parse result may come from existing scan cache (no re-parse needed)
        if parse_result is not None:
            self._from_parse_result(parse_result)
        elif source is not None:
            self._from_source(source)
        else:
            self._empty()

    def _empty(self):
        self.imports: list[str] = []
        self.exports: list[str] = []
        self.has_main_guard: bool = False
        self.line_count: int = 0
        self.docstring: str = ""

    def _from_parse_result(self, pr: dict):
        """Use already-parsed scan result (avoids double parsing)."""
        self.imports = [i.get("name", "") for i in pr.get("imports", [])]
        self.exports = [s.get("name", "") for s in pr.get("symbols", [])]
        self.line_count = pr.get("line_count", 0)
        self.docstring = pr.get("docstring", "") or ""
        self.has_main_guard = pr.get("has_main_guard", False)
        # Fallback: infer from exports
        if not self.has_main_guard:
            self.has_main_guard = "__main__" in str(pr)

    def _from_source(self, source: str):
        """Parse from raw source text."""
        self.imports = []
        self.exports = []
        self.has_main_guard = False
        self.docstring = ""
        self.line_count = source.count("\n") + 1

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return

        # Docstring
        first = ast.get_docstring(tree)
        self.docstring = first or ""

        for node in ast.walk(tree):
            # Imports
            if isinstance(node, ast.Import):
                self.imports += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self.imports.append(node.module)
            # Top-level public names
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    self.exports.append(node.name)
            # __main__ guard
            elif isinstance(node, ast.If):
                test = ast.unparse(node.test)
                if "__name__" in test and "__main__" in test:
                    self.has_main_guard = True


# ── Classifier ────────────────────────────────────────────────────────────────

class ScriptClassifier:
    """Classify Python scripts as source_code or tool_script."""

    def __init__(self, project_packages: list[str]):
        """
        Args:
            project_packages: Top-level package names in this project
                              (e.g. ['core', 'manon_mcp', 'saas', 'codeindex'])
        """
        self.project_packages = set(project_packages)

    def _imports_project(self, signals: ScriptSignals) -> bool:
        for imp in signals.imports:
            root = imp.split(".")[0]
            if root in self.project_packages:
                return True
        return False

    def _is_single_main(self, signals: ScriptSignals) -> bool:
        """Only entry point is __main__ guard, few or no exported API."""
        public = [n for n in signals.exports if n != "main"]
        return signals.has_main_guard and len(public) <= 2

    def classify(
        self,
        signals: ScriptSignals,
        imported_by_project: bool = False,
    ) -> tuple[Literal["source_code", "tool_script", "uncertain"], bool]:
        """
        Returns (classification, is_certain).
        is_certain=False → send to LLM.
        """
        # Definitive: called by other project code
        if imported_by_project:
            return "source_code", True

        # Definitive: imports project internals → part of the codebase
        if self._imports_project(signals):
            return "source_code", True

        # Definitive: tool name + standalone entry point
        if _is_tool_name(signals.stem) and self._is_single_main(signals):
            return "tool_script", True

        # Uncertain → LLM
        return "uncertain", False

    def make_summary(self, signals: ScriptSignals) -> dict:
        """Compact summary for LLM prompt (minimal tokens)."""
        return {
            "path": signals.rel_path,
            "imports": signals.imports[:10],
            "exports": signals.exports[:10],
            "docstring": signals.docstring[:150],
            "lines": signals.line_count,
            "has_main": signals.has_main_guard,
        }

    def classify_batch(
        self,
        file_results: list[dict],
        imported_paths: set[str],
    ) -> tuple[list[dict], list[dict]]:
        """
        Split file_results into (keep, uncertain).
        Tool scripts are silently dropped; source_code kept; uncertain returned for LLM.
        """
        keep: list[dict] = []
        uncertain: list[dict] = []

        for f in file_results:
            signals = ScriptSignals(
                f["rel_path"],
                parse_result=f.get("parse_result"),
            )
            is_imported = f["rel_path"] in imported_paths
            result, certain = self.classify(signals, is_imported)

            if not certain:
                uncertain.append(f)
            elif result == "source_code":
                keep.append(f)
            # tool_script → drop (don't add to keep)

        return keep, uncertain


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_scripts_like_path(rel_path: str) -> bool:
    """True if the file lives inside a directory named 'scripts'."""
    parts = Path(rel_path).parts
    return any(p == "scripts" for p in parts[:-1])


def build_imported_paths(file_results: list[dict], project_root: Path) -> set[str]:
    """
    Build set of rel_paths that are imported by other files.
    Uses parse_result imports to match against known file paths.
    """
    # Map module path → rel_path
    module_to_path: dict[str, str] = {}
    for f in file_results:
        rel = f["rel_path"]
        if rel.endswith(".py"):
            mod = rel[:-3].replace("/", ".").replace("\\", ".")
            module_to_path[mod] = rel
            # Also map last component for relative imports
            module_to_path[Path(rel).stem] = rel

    imported: set[str] = set()
    for f in file_results:
        pr = f.get("parse_result") or {}
        for imp in pr.get("imports", []):
            name = imp.get("name", "")
            if name in module_to_path:
                imported.add(module_to_path[name])
            # Check prefix match (e.g. "scripts.foo" → "scripts/foo.py")
            for mod, path in module_to_path.items():
                if name == mod or name.startswith(mod + "."):
                    imported.add(path)

    return imported
