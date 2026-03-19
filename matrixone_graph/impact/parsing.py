"""Git diff parsing and symbol extraction for impact analysis."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from codeindex.parser import parse_file

from .models import ChangedFile, ChangedSymbol, ChangeType

_FILE_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GitDiffParser:
    """Parse git diff output into ChangedFile objects."""

    def __init__(self, repo_path: Path = Path(".")) -> None:
        self.repo_path = repo_path

    def _git(self, *args: str) -> str:
        r = subprocess.run(
            ["git", "-C", str(self.repo_path), *args],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout

    def for_commit(self, commit: str = "HEAD") -> list[ChangedFile]:
        ref = f"{commit}~1..{commit}"
        return self._parse(self._git("diff", ref, "--unified=0"))

    def staged(self) -> list[ChangedFile]:
        return self._parse(self._git("diff", "--cached", "--unified=0"))

    def branch_diff(self, base: str, head: str = "HEAD") -> list[ChangedFile]:
        return self._parse(self._git("diff", f"{base}..{head}", "--unified=0"))

    def current_commit(self) -> str:
        return self._git("rev-parse", "--short", "HEAD").strip()

    def _parse(self, diff: str) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        cur: ChangedFile | None = None
        is_new = is_del = False
        for line in diff.split("\n"):
            m = _FILE_RE.match(line)
            if m:
                if cur:
                    files.append(cur)
                cur = ChangedFile(path=m.group(2), change_type=ChangeType.MODIFIED)
                is_new = is_del = False
                continue
            if cur:
                if line.startswith("new file"):
                    is_new = True
                    cur.change_type = ChangeType.ADDED
                    continue
                if line.startswith("deleted file"):
                    is_del = True
                    cur.change_type = ChangeType.DELETED
                    continue
            hm = _HUNK_RE.match(line)
            if hm and cur:
                os, oc = int(hm.group(1)), int(hm.group(2) or 1)
                ns, nc = int(hm.group(3)), int(hm.group(4) or 1)
                if oc > 0 and not is_new:
                    cur.deleted_lines.append((os, os + oc - 1))
                if nc > 0 and not is_del:
                    cur.added_lines.append((ns, ns + nc - 1))
        if cur:
            files.append(cur)
        return files


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
