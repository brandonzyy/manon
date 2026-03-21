"""Local AST chunking helpers shared by scan workflows."""
from __future__ import annotations

from pathlib import PurePosixPath

# Truncate individual chunk content beyond this limit (bytes).
# Prevents minified/bundled files from producing multi-MB chunks.
# Symbol metadata (name, kind, signature, lines) is always preserved.
_MAX_CHUNK_CONTENT = 8 * 1024  # 8 KB


def _make_entity_id(module: str, symbol_name: str) -> str:
    return f"{module}.{symbol_name}" if module else symbol_name


def _module_from_rel_path(rel_path: str) -> str:
    """Compute module prefix from a relative path string."""
    path = PurePosixPath(rel_path)
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def chunk_file_from_dict(source: str, parse_result_dict: dict, rel_path: str) -> list[dict]:
    """Build chunks from raw source text and parser output."""
    module = _module_from_rel_path(rel_path)
    symbols = parse_result_dict.get("symbols", [])

    lines = source.splitlines(keepends=True)
    chunks: list[dict] = []
    covered: set[int] = set()

    for sym in sorted(symbols, key=lambda s: s.get("line_start", 0)):
        start = max(sym.get("line_start", 1) - 1, 0)
        end = min(sym.get("line_end", 0), len(lines))
        if start >= end:
            continue
        content = "".join(lines[start:end])
        if not content.strip():
            continue
        if len(content) > _MAX_CHUNK_CONTENT:
            original_len = len(content)
            content = content[:_MAX_CHUNK_CONTENT] + f"\n# ... truncated ({original_len} chars)"
        sym_name = sym.get("name", "")
        cid = f"chunk:{_make_entity_id(module, sym_name)}"
        chunks.append({
            "id": cid,
            "content": content,
            "file_path": rel_path,
            "line_start": sym.get("line_start", 0),
            "line_end": sym.get("line_end", 0),
            "symbol_name": sym_name,
        })
        covered.update(range(start, end))

    uncovered = [i for i in range(len(lines)) if i not in covered]
    if uncovered:
        content = "".join(lines[i] for i in uncovered)
        if content.strip():
            chunks.append({
                "id": f"chunk:{module}.__file__",
                "content": content[:2000],
                "file_path": rel_path,
                "line_start": uncovered[0] + 1,
                "line_end": uncovered[-1] + 1,
                "symbol_name": "",
            })

    return chunks
