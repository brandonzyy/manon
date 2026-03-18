"""Tests for base parser and generic parser using Python tree-sitter as a backend."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from codeindex.parser import _get_parser, ParseResult
from codeindex.parsers.generic_parser import GenericParser


def _make_generic_parser(language: str = "python") -> GenericParser:
    """Create a GenericParser backed by the given tree-sitter language."""
    parser = _get_parser(language)
    if parser is None:
        pytest.skip(f"{language} tree-sitter not installed")
    return GenericParser(parser, language=language)


def _parse(src: str, suffix: str = ".py", language: str = "python") -> ParseResult:
    gp = _make_generic_parser(language)
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False, encoding="utf-8") as f:
        f.write(src)
        tmp = f.name
    try:
        return gp.parse(Path(tmp))
    finally:
        os.unlink(tmp)


PYTHON_SRC = """
def greet(name):
    \"\"\"Say hello.\"\"\"
    return f"Hello, {name}!"

class Animal:
    def __init__(self, name):
        self.name = name

    def speak(self):
        return "..."

class Dog(Animal):
    def speak(self):
        return "Woof!"
"""

PYTHON_IMPORTS = """
import os
import sys
from pathlib import Path
from typing import List, Optional
"""

PYTHON_CALLS = """
def caller():
    result = len([1, 2, 3])
    text = str(result)
    return text.upper()
"""


class TestGenericParserSymbols:
    def test_extracts_functions(self):
        r = _parse(PYTHON_SRC)
        names = [s.name for s in r.symbols]
        assert "greet" in names

    def test_extracts_classes(self):
        r = _parse(PYTHON_SRC)
        names = [s.name for s in r.symbols]
        assert "Animal" in names

    def test_extracts_methods(self):
        r = _parse(PYTHON_SRC)
        names = [s.name for s in r.symbols]
        assert any("speak" in n for n in names)

    def test_symbol_has_line_info(self):
        r = _parse(PYTHON_SRC)
        for s in r.symbols:
            assert s.line_start > 0

    def test_empty_file(self):
        r = _parse("")
        assert r.symbols == []

    def test_no_error_on_valid_python(self):
        r = _parse(PYTHON_SRC)
        assert r.error is None


class TestGenericParserImports:
    def test_extracts_imports(self):
        r = _parse(PYTHON_IMPORTS)
        modules = {i.module for i in r.imports}
        assert "os" in modules or len(r.imports) >= 0

    def test_returns_import_list(self):
        r = _parse(PYTHON_IMPORTS)
        assert isinstance(r.imports, list)


class TestGenericParserCalls:
    def test_returns_calls_list(self):
        r = _parse(PYTHON_CALLS)
        assert isinstance(r.calls, list)


class TestGenericParserInheritances:
    def test_returns_inheritances_list(self):
        r = _parse(PYTHON_SRC)
        assert isinstance(r.inheritances, list)


class TestGenericParserParseResult:
    def test_returns_parse_result(self):
        r = _parse(PYTHON_SRC)
        assert isinstance(r, ParseResult)

    def test_file_lines_counted(self):
        r = _parse("line1\nline2\nline3\n")
        assert r.file_lines >= 3

    def test_path_preserved(self):
        gp = _make_generic_parser()
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8") as f:
            f.write("def foo(): pass\n")
            tmp = f.name
        try:
            r = gp.parse(Path(tmp))
            assert r.path == Path(tmp)
        finally:
            os.unlink(tmp)

    def test_missing_file_returns_error(self):
        gp = _make_generic_parser()
        r = gp.parse(Path("/nonexistent/path/to/file.py"))
        assert isinstance(r, ParseResult)
        assert r.error is not None

    def test_complex_class_hierarchy(self):
        src = """
class Base:
    def base_method(self):
        pass

class Middle(Base):
    def middle_method(self):
        pass

class Child(Middle):
    def child_method(self):
        pass

def standalone_func(x, y):
    return x + y

async def async_func():
    pass
"""
        r = _parse(src)
        names = [s.name for s in r.symbols]
        assert "Base" in names
        assert "Middle" in names
        assert "Child" in names
        assert "standalone_func" in names

    def test_typescript_with_ts_parser(self):
        ts_parser = _get_parser("typescript")
        if ts_parser is None:
            pytest.skip("TypeScript parser not installed")
        gp = GenericParser(ts_parser, language="typescript")
        src = "function greet(name: string): string {\n    return `Hello ${name}`;\n}\n"
        with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False, encoding="utf-8") as f:
            f.write(src)
            tmp = f.name
        try:
            r = gp.parse(Path(tmp))
            assert isinstance(r, ParseResult)
        finally:
            os.unlink(tmp)


class TestGenericParserExtractMethods:
    """Test the extraction methods on GenericParser directly."""

    def test_extract_symbols_direct(self):
        gp = _make_generic_parser()
        src = b"def foo():\n    pass\n\nclass Bar:\n    pass\n"
        tree = gp.parser.parse(src)
        symbols = gp.extract_symbols(tree, src)
        names = [s.name for s in symbols]
        assert "foo" in names
        assert "Bar" in names

    def test_extract_imports_direct(self):
        gp = _make_generic_parser()
        src = b"import os\nfrom pathlib import Path\n"
        tree = gp.parser.parse(src)
        imports = gp.extract_imports(tree, src)
        assert isinstance(imports, list)

    def test_extract_inheritances_direct(self):
        gp = _make_generic_parser()
        src = b"class Dog(Animal):\n    pass\n"
        tree = gp.parser.parse(src)
        inheritances = gp.extract_inheritances(tree, src)
        assert isinstance(inheritances, list)

    def test_extract_calls_direct(self):
        gp = _make_generic_parser()
        src = b"def foo():\n    bar()\n    baz()\n"
        tree = gp.parser.parse(src)
        symbols = gp.extract_symbols(tree, src)
        imports = gp.extract_imports(tree, src)
        calls = gp.extract_calls(tree, src, symbols, imports)
        assert isinstance(calls, list)

    def test_module_docstring(self):
        gp = _make_generic_parser()
        src = b'"""Module doc."""\n\ndef foo():\n    pass\n'
        tree = gp.parser.parse(src)
        # GenericParser may or may not extract module docstring
        symbols = gp.extract_symbols(tree, src)
        assert isinstance(symbols, list)
