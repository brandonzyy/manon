"""Tests for codeindex/scanner.py language→extension mapping.

LANGUAGE_EXTENSIONS is inverted from parser.py rather than hand-maintained.
The hand-maintained version drifted: ".mjs" mapped to javascript in parser.py
but was absent here, so .mjs files were detected and then never collected.
"""
from __future__ import annotations

from codeindex.parser import FILE_EXTENSIONS, get_all_extensions
from codeindex.scanner import (
    LANGUAGE_EXTENSIONS,
    _build_language_extensions,
    get_language_extensions,
)


class TestInversion:
    def test_every_extension_is_reachable(self):
        """Anything parser.py can parse must be collectable by some language."""
        collectable = {e for exts in LANGUAGE_EXTENSIONS.values() for e in exts}
        assert set(get_all_extensions()) - collectable == set()

    def test_no_invented_extensions(self):
        """The map must not claim extensions parser.py cannot parse."""
        collectable = {e for exts in LANGUAGE_EXTENSIONS.values() for e in exts}
        assert collectable - set(get_all_extensions()) == set()

    def test_language_set_matches_parser(self):
        assert set(LANGUAGE_EXTENSIONS) == set(get_all_extensions().values())

    def test_mjs_is_collected(self):
        """Regression: .mjs was the extension the parallel list lost."""
        assert ".mjs" in LANGUAGE_EXTENSIONS["javascript"]
        assert FILE_EXTENSIONS[".mjs"] == "javascript"

    def test_generic_languages_present(self):
        for lang, ext in [("go", ".go"), ("rust", ".rs"), ("bash", ".sh")]:
            assert ext in LANGUAGE_EXTENSIONS[lang]

    def test_multi_extension_language_keeps_all(self):
        assert set(LANGUAGE_EXTENSIONS["cpp"]) >= {".cpp", ".cc", ".cxx", ".hpp"}

    def test_deterministic(self):
        assert _build_language_extensions() == _build_language_extensions()


class TestTsxOverlap:
    """Detection samples at most 500 files, so a large repo can report
    "typescript" without the sample ever hitting a .tsx file. Dropping the
    overlap would silently skip every .tsx file in such a repo."""

    def test_typescript_reaches_tsx(self):
        assert ".tsx" in LANGUAGE_EXTENSIONS["typescript"]

    def test_tsx_language_still_standalone(self):
        assert LANGUAGE_EXTENSIONS["tsx"] == [".tsx"]

    def test_overlap_is_not_duplicated(self):
        assert LANGUAGE_EXTENSIONS["typescript"].count(".tsx") == 1


class TestGetLanguageExtensions:
    def test_single_language(self):
        assert get_language_extensions(["go"]) == {".go"}

    def test_union_of_languages(self):
        assert get_language_extensions(["python", "go"]) == {".py", ".go"}

    def test_typescript_project(self):
        assert get_language_extensions(["typescript", "tsx", "javascript"]) == {
            ".ts", ".tsx", ".js", ".jsx", ".mjs",
        }

    def test_unknown_language_contributes_nothing(self):
        assert get_language_extensions(["cobol"]) == set()

    def test_empty_input(self):
        assert get_language_extensions([]) == set()
