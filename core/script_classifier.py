"""Script classifier — distinguish tool scripts from source code.

Signals (in priority order):
1. is_imported_by_project  → source_code (definitive)
2. imports_project_modules → source_code (definitive)
3. directory heuristic      → source_code or tool_script (definitive)
4. tool name + single_main → tool_script (definitive)
5. uncertain               → LLM tiebreaker
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

# Directory-based heuristics (language-agnostic)
_SOURCE_DIRS = {"src", "lib", "core", "app", "pkg", "internal", "extensions", "packages", "ui",
                 "cmd", "api", "web", "server", "client", "service", "services", "modules"}
_TOOL_DIRS = {"scripts", "tools", "bin", "examples", "demo", "fixtures"}

# All code extensions supported by codeindex (must stay in sync with codeindex/parser.py)
_ALL_CODE_EXTS = {
    # Specialized parsers
    ".py", ".php", ".phtml", ".java",
    ".ts", ".tsx", ".js", ".jsx", ".mjs",
    # Generic parsers (tree-sitter)
    ".go", ".rs",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx",
    ".cs", ".rb", ".swift",
    ".kt", ".kts", ".scala", ".lua",
    ".r", ".R",
    ".ex", ".exs", ".dart", ".hs",
    ".ml", ".mli",
    ".sh", ".bash", ".zig",
}

# Extensions with relative-path import semantics (./foo, ../bar)
_RELATIVE_IMPORT_EXTS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".dart",
}


def _is_tool_name(stem: str) -> bool:
    return bool(_TOOL_RE.search(stem))


def _top_dir(rel_path: str) -> str:
    """Return the first directory component of a relative path."""
    parts = Path(rel_path).parts
    return parts[0] if len(parts) > 1 else ""


def _is_non_python(rel_path: str) -> bool:
    """True if the file is a non-Python source file supported by codeindex."""
    suffix = Path(rel_path).suffix
    return suffix in _ALL_CODE_EXTS and suffix != ".py"


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
        # scan_and_parse uses "module" key; older formats may use "name"
        self.imports = [i.get("module", "") or i.get("name", "") for i in pr.get("imports", [])]
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
    """Classify scripts as source_code or tool_script (Python + TS/JS)."""

    def __init__(self, project_packages: list[str]):
        """
        Args:
            project_packages: Top-level package names in this project.
                Python: dirs with __init__.py (e.g. ['core', 'manon_mcp']).
                TS/JS:  dirs with package.json or standard source dirs.
        """
        self.project_packages = set(project_packages)

    def _imports_project(self, signals: ScriptSignals) -> bool:
        for imp in signals.imports:
            # Python: "core.ast.scanner" → root = "core"
            # TS/JS:  "./utils" or "../config" → relative, handled elsewhere
            #         "openclaw/plugin-sdk" → root = "openclaw"
            root = imp.split(".")[0].split("/")[0]
            if root in self.project_packages:
                return True
        return False

    def _is_single_main(self, signals: ScriptSignals) -> bool:
        """Only entry point is __main__ guard, few or no exported API."""
        public = [n for n in signals.exports if n != "main"]
        return signals.has_main_guard and len(public) <= 2

    def _is_non_python_standalone(self, signals: ScriptSignals) -> bool:
        """Non-Python file with no exported symbols — likely a standalone script."""
        if not _is_non_python(signals.rel_path):
            return False
        exported = [n for n in signals.exports if n and not n.startswith("_")]
        return len(exported) == 0

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

        # Definitive: directory heuristic (language-agnostic)
        top = _top_dir(signals.rel_path)
        if top in _SOURCE_DIRS:
            return "source_code", True
        if top in _TOOL_DIRS:
            return "tool_script", True

        # Definitive: Python tool name + standalone entry point
        if _is_tool_name(signals.stem) and self._is_single_main(signals):
            return "tool_script", True

        # Definitive: non-Python tool name + no exports
        if _is_tool_name(signals.stem) and self._is_non_python_standalone(signals):
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
    Supports Python (.py) and all non-Python languages with path-based imports.
    """
    # Map module path → rel_path (Python: dotted module names)
    module_to_path: dict[str, str] = {}
    # Map bare path (no ext) → rel_path (non-Python: file paths without extension)
    bare_to_path: dict[str, str] = {}

    for f in file_results:
        rel = f["rel_path"]
        if rel.endswith(".py"):
            mod = rel[:-3].replace("/", ".").replace("\\", ".")
            module_to_path[mod] = rel
            module_to_path[Path(rel).stem] = rel
        elif _is_non_python(rel):
            # "src/browser/chrome.ts" → "src/browser/chrome"
            bare = str(Path(rel).with_suffix("")).replace("\\", "/")
            bare_to_path[bare] = rel
            # Also index without /index suffix: "src/foo/index.ts" → "src/foo"
            if bare.endswith("/index"):
                bare_to_path[bare[:-6]] = rel

    def _resolve_py_relative(imp_module: str, imp_names: list[str], importer_rel: str) -> list[str]:
        """Resolve Python relative imports to absolute module paths."""
        parts = importer_rel.replace("\\", "/").split("/")
        pkg_parts = parts[:-1]
        dots = len(imp_module) - len(imp_module.lstrip("."))
        if dots > 1:
            pkg_parts = pkg_parts[:max(0, len(pkg_parts) - (dots - 1))]
        suffix = imp_module.lstrip(".")
        base_parts = pkg_parts + (suffix.split(".") if suffix else [])
        resolved = []
        base = ".".join(base_parts)
        if base:
            resolved.append(base)
        for name in imp_names:
            if isinstance(name, str):
                resolved.append(f"{base}.{name}" if base else name)
            elif isinstance(name, dict):
                n = name.get("name", "")
                if n:
                    resolved.append(f"{base}.{n}" if base else n)
        return resolved

    def _resolve_relative_path(specifier: str, importer_rel: str) -> str:
        """Resolve relative import to bare path (e.g. './utils' from 'src/foo.ts' → 'src/utils').

        Works for any language with relative path imports (TS/JS, Go, Rust, Dart, etc.).
        """
        if not specifier.startswith("."):
            return ""
        importer_dir = str(Path(importer_rel).parent).replace("\\", "/")
        combined = importer_dir + "/" + specifier
        parts = []
        for p in combined.split("/"):
            if p == "..":
                if parts:
                    parts.pop()
            elif p and p != ".":
                parts.append(p)
        return "/".join(parts)

    imported: set[str] = set()

    for f in file_results:
        pr = f.get("parse_result") or {}
        is_py = f["rel_path"].endswith(".py")

        for imp in pr.get("imports", []):
            name = imp.get("module", "") or imp.get("name", "")
            names = imp.get("names", [])

            if is_py:
                # Python imports (dotted module paths)
                if name.startswith("."):
                    candidates = _resolve_py_relative(name, names, f["rel_path"])
                else:
                    candidates = [name]
                    for n in names:
                        n_str = n.get("name", "") if isinstance(n, dict) else str(n)
                        if n_str:
                            candidates.append(f"{name}.{n_str}")

                for cand in candidates:
                    if cand in module_to_path:
                        imported.add(module_to_path[cand])
                    for mod, path in module_to_path.items():
                        if cand == mod or cand.startswith(mod + ".") or mod.startswith(cand + "."):
                            imported.add(path)
            else:
                # Non-Python: relative path imports (./foo, ../bar)
                if name.startswith("."):
                    resolved = _resolve_relative_path(name, f["rel_path"])
                    if resolved and resolved in bare_to_path:
                        imported.add(bare_to_path[resolved])
                # Absolute/package imports: match against bare_to_path
                elif name in bare_to_path:
                    imported.add(bare_to_path[name])

    return imported
