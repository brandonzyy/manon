"""Parser utilities - language detection, parser installation, annotation enrichment."""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("manon.ast_sync")

# Cache for language detection results (path -> languages)
_LANG_CACHE: dict[str, set[str]] = {}


def ensure_parsers(local_path: str, use_cache: bool = True) -> dict[str, str]:
    """Auto-detect project languages and install missing tree-sitter parsers.

    Now delegates to codeindex's built-in functionality with caching.

    Args:
        local_path: Project root path
        use_cache: Whether to use cached language detection results (default True)

    Returns dict mapping language → status ("already_installed" | "installed" | "failed").
    """
    from codeindex.detector import quick_detect_languages
    from codeindex.parser import get_all_extensions
    from codeindex.parser_installer import install_parsers

    root = Path(local_path).resolve()
    root_str = str(root)

    # Check cache first
    if use_cache and root_str in _LANG_CACHE:
        langs = _LANG_CACHE[root_str]
        log.debug("Using cached language detection for %s: %s", local_path, langs)
    else:
        langs = quick_detect_languages(root, get_all_extensions(), max_files=500)
        _LANG_CACHE[root_str] = langs

    if not langs:
        log.info("No supported languages detected in %s", local_path)
        return {}

    return install_parsers(langs, timeout=30)


# ── Decorator enrichment (fallback if parser doesn't extract) ─────

_PY_DECORATOR_RE = re.compile(r"^\s*@([\w.]+)")
_TS_DECORATOR_RE = re.compile(r"^\s*@(\w+)")
_PHP_ATTR_RE = re.compile(r"#\[(\w+)")
_JAVA_ANN_RE = re.compile(r"^\s*@(\w+)")


def _enrich_annotations(pr_dict: dict, source: str, file_path: str) -> dict:
    """Add decorator/annotation data to symbols if the parser didn't extract them.

    Uses regex-based extraction as a fallback when the tree-sitter parser
    doesn't support annotation extraction (e.g., older codeindex versions
    or cached module state).
    """
    symbols = pr_dict.get("symbols", [])
    if not symbols:
        return pr_dict

    # Check if any symbol already has annotations — if so, parser handled it
    if any(s.get("annotations") for s in symbols):
        return pr_dict

    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    if ext not in ("py", "ts", "tsx", "js", "jsx", "php", "phtml", "java"):
        return pr_dict

    lines = source.split("\n") if source else []
    if not lines:
        return pr_dict

    # Build line→symbol mapping
    sym_by_line: dict[int, dict] = {}
    for s in symbols:
        ls = s.get("line_start", 0)
        if ls > 0:
            sym_by_line[ls] = s

    if ext == "py":
        _enrich_python_decorators(lines, sym_by_line)
    elif ext in ("ts", "tsx", "js", "jsx"):
        _enrich_ts_decorators(lines, sym_by_line)
    elif ext in ("php", "phtml"):
        _enrich_php_attributes(lines, sym_by_line)
    elif ext == "java":
        _enrich_java_annotations(lines, sym_by_line)

    return pr_dict


def _enrich_python_decorators(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        decorators = []
        # Scan lines above the symbol definition for decorators
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i]
            m = _PY_DECORATOR_RE.match(line)
            if m:
                decorators.insert(0, {"name": m.group(1)})
            elif line.strip() and not line.strip().startswith("#"):
                break
        if decorators:
            sym["annotations"] = decorators


def _enrich_ts_decorators(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        decorators = []
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i]
            m = _TS_DECORATOR_RE.match(line)
            if m:
                decorators.insert(0, {"name": m.group(1)})
            elif line.strip() and not line.strip().startswith("//"):
                break
        if decorators:
            sym["annotations"] = decorators


def _enrich_php_attributes(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        attrs = []
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i]
            for m in _PHP_ATTR_RE.finditer(line):
                attrs.insert(0, {"name": m.group(1)})
            if line.strip() and not line.strip().startswith("//") and "#[" not in line:
                break
        if attrs:
            sym["annotations"] = attrs


def _enrich_java_annotations(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        annotations = []
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i]
            m = _JAVA_ANN_RE.match(line)
            if m:
                annotations.insert(0, {"name": m.group(1)})
            elif line.strip() and not line.strip().startswith("//"):
                break
        if annotations:
            sym["annotations"] = annotations


def _resolve_relative_callees(parse_dict: dict, rel_path: str) -> dict:
    """Resolve relative-path callees (./mod.func) to full module IDs.

    The TypeScript parser resolves import aliases to relative paths like
    ``./chat-helpers.streamLLMWithTools``.  The server pipeline expects
    full dot-separated module IDs like
    ``electron.orchestrator.chat-helpers.streamLLMWithTools``.
    """
    import posixpath

    calls = parse_dict.get("calls")
    if not calls:
        return parse_dict

    # e.g. "electron/orchestrator/skill-router.ts" → "electron/orchestrator"
    file_dir = posixpath.dirname(rel_path)

    changed = False
    for call in calls:
        callee = call.get("callee", "")
        if not (callee.startswith("./") or callee.startswith("../")):
            continue
        # Split first dot-segment (relative module) from the rest (symbol chain)
        # e.g. "./chat-helpers.streamLLMWithTools" → "./chat-helpers", "streamLLMWithTools"
        dot_idx = callee.find(".", 2 if callee.startswith("./") else 3)
        if dot_idx == -1:
            # No symbol part, just a module reference
            mod_rel = callee
            symbol = ""
        else:
            mod_rel = callee[:dot_idx]
            symbol = callee[dot_idx + 1:]

        # Resolve relative path: "./chat-helpers" relative to "electron/orchestrator"
        resolved = posixpath.normpath(posixpath.join(file_dir, mod_rel))
        # Convert slashes to dots: "electron/orchestrator/chat-helpers" → "electron.orchestrator.chat-helpers"
        module_id = resolved.replace("/", ".")

        call["callee"] = f"{module_id}.{symbol}" if symbol else module_id
        changed = True

    return parse_dict
