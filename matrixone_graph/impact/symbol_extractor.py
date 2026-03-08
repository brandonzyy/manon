"""Changed symbol extractor using codeindex parser."""
from __future__ import annotations

from pathlib import Path

from codeindex.parser import parse_file

from .models import ChangedFile, ChangedSymbol, ChangeType


class ChangedSymbolExtractor:
    """Extract code symbols from changed files using codeindex parser."""

    def __init__(self, repo_path: Path = Path(".")) -> None:
        self.repo_path = repo_path

    def extract(self, files: list[ChangedFile]) -> list[ChangedSymbol]:
        symbols: list[ChangedSymbol] = []
        for f in files:
            symbols.extend(self._from_file(f))
        return symbols

    def _from_file(self, cf: ChangedFile) -> list[ChangedSymbol]:
        if cf.change_type == ChangeType.DELETED:
            return [ChangedSymbol(
                name=f"<deleted:{Path(cf.path).stem}>",
                file=cf.path, change_type=ChangeType.DELETED,
            )]
        fp = self.repo_path / cf.path
        if not fp.exists():
            return []
        try:
            pr = parse_file(fp)
        except Exception:
            return []
        if pr.error:
            return []
        if cf.change_type == ChangeType.ADDED:
            return [
                ChangedSymbol(
                    name=s.name, file=cf.path, change_type=ChangeType.ADDED,
                    line_start=s.line_start, line_end=s.line_end,
                    lines_changed=s.line_end - s.line_start + 1,
                )
                for s in pr.symbols
            ]
        # MODIFIED — find symbols overlapping changed lines
        ranges = cf.added_lines + cf.deleted_lines
        result: list[ChangedSymbol] = []
        for s in pr.symbols:
            for rs, re_ in ranges:
                if s.line_start <= re_ and rs <= s.line_end:
                    changed = sum(
                        min(s.line_end, e) - max(s.line_start, st) + 1
                        for st, e in ranges if s.line_start <= e and st <= s.line_end
                    )
                    result.append(ChangedSymbol(
                        name=s.name, file=cf.path, change_type=ChangeType.MODIFIED,
                        line_start=s.line_start, line_end=s.line_end,
                        lines_changed=changed,
                    ))
                    break
        return result
