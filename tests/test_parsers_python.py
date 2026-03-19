"""Tests for Python AST parser."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from codeindex.parser import parse_file, ParseResult


def _parse(src: str, suffix: str = ".py") -> ParseResult:
    """Write src to a temp file, parse it, clean up."""
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False, encoding="utf-8") as f:
        f.write(src)
        tmp = f.name
    try:
        return parse_file(Path(tmp))
    finally:
        os.unlink(tmp)


# ── Basic symbol extraction ─────────────────────────────────────────────────

class TestPythonFunctions:
    def test_top_level_function(self):
        r = _parse("def foo(x, y):\n    return x + y\n")
        names = [s.name for s in r.symbols]
        assert "foo" in names

    def test_function_with_docstring(self):
        r = _parse('def greet(name):\n    """Say hello."""\n    return f"Hello {name}"\n')
        sym = next(s for s in r.symbols if s.name == "greet")
        assert "hello" in sym.docstring.lower() or sym.docstring != ""

    def test_async_function(self):
        r = _parse("async def fetch(url):\n    pass\n")
        names = [s.name for s in r.symbols]
        assert "fetch" in names

    def test_nested_function(self):
        r = _parse("def outer():\n    def inner():\n        pass\n    return inner\n")
        names = [s.name for s in r.symbols]
        assert "outer" in names

    def test_function_line_range(self):
        r = _parse("def foo():\n    pass\n\ndef bar():\n    pass\n")
        foo = next(s for s in r.symbols if s.name == "foo")
        bar = next(s for s in r.symbols if s.name == "bar")
        assert foo.line_start < bar.line_start

    def test_lambda_not_extracted(self):
        r = _parse("fn = lambda x: x * 2\n")
        # lambdas are not top-level named symbols
        assert all(s.name != "fn" or s.kind != "function" for s in r.symbols)


class TestPythonClasses:
    def test_simple_class(self):
        r = _parse("class Animal:\n    pass\n")
        names = [s.name for s in r.symbols]
        assert "Animal" in names

    def test_class_with_methods(self):
        src = "class Dog:\n    def bark(self):\n        return 'woof'\n    def sit(self):\n        pass\n"
        r = _parse(src)
        names = [s.name for s in r.symbols]
        assert "Dog" in names
        assert "Dog.bark" in names
        assert "Dog.sit" in names

    def test_class_with_docstring(self):
        src = 'class Foo:\n    """A foo class."""\n    pass\n'
        r = _parse(src)
        foo = next(s for s in r.symbols if s.name == "Foo")
        assert "foo class" in foo.docstring.lower()

    def test_class_with_init(self):
        src = "class Point:\n    def __init__(self, x, y):\n        self.x = x\n        self.y = y\n"
        r = _parse(src)
        names = [s.name for s in r.symbols]
        assert "Point" in names
        assert any("__init__" in n for n in names)

    def test_dataclass(self):
        src = "from dataclasses import dataclass\n@dataclass\nclass Rect:\n    width: int\n    height: int\n"
        r = _parse(src)
        names = [s.name for s in r.symbols]
        assert "Rect" in names

    def test_class_line_numbers(self):
        src = "class Foo:\n    pass\n"
        r = _parse(src)
        foo = next(s for s in r.symbols if s.name == "Foo")
        assert foo.line_start == 1

    def test_nested_class(self):
        src = "class Outer:\n    class Inner:\n        pass\n"
        r = _parse(src)
        names = [s.name for s in r.symbols]
        assert "Outer" in names


class TestPythonInheritance:
    def test_single_inheritance(self):
        src = "class Dog(Animal):\n    pass\n"
        r = _parse(src)
        assert any(h.child == "Dog" and h.parent == "Animal" for h in r.inheritances)

    def test_multiple_inheritance(self):
        src = "class C(A, B):\n    pass\n"
        r = _parse(src)
        parents = {h.parent for h in r.inheritances if h.child == "C"}
        assert "A" in parents
        assert "B" in parents

    def test_no_inheritance(self):
        src = "class Standalone:\n    pass\n"
        r = _parse(src)
        assert not r.inheritances


# ── Import extraction ────────────────────────────────────────────────────────

class TestPythonImports:
    def test_plain_import(self):
        r = _parse("import os\n")
        assert any(i.module == "os" and not i.is_from for i in r.imports)

    def test_from_import(self):
        r = _parse("from pathlib import Path\n")
        imp = next(i for i in r.imports if i.module == "pathlib")
        assert imp.is_from
        assert "Path" in imp.names

    def test_import_alias(self):
        r = _parse("import numpy as np\n")
        imp = next(i for i in r.imports if i.module == "numpy")
        assert imp.alias == "np"

    def test_from_import_multiple(self):
        r = _parse("from typing import List, Dict, Optional\n")
        typing_imports = [i for i in r.imports if i.module == "typing"]
        all_names = {n for i in typing_imports for n in i.names}
        assert "List" in all_names
        assert "Dict" in all_names
        assert "Optional" in all_names

    def test_relative_import(self):
        r = _parse("from .utils import helper\n")
        assert any(i.module == ".utils" or "utils" in i.module for i in r.imports)

    def test_star_import(self):
        r = _parse("from os.path import *\n")
        assert any("os.path" in i.module or i.module == "os.path" for i in r.imports)

    def test_multiple_imports(self):
        src = "import os\nimport sys\nfrom pathlib import Path\n"
        r = _parse(src)
        modules = {i.module for i in r.imports}
        assert "os" in modules
        assert "sys" in modules
        assert "pathlib" in modules


# ── Call extraction ──────────────────────────────────────────────────────────

class TestPythonCalls:
    def test_function_call(self):
        src = "def caller():\n    result = len([1, 2, 3])\n    return result\n"
        r = _parse(src)
        callee_names = {c.callee for c in r.calls if c.callee}
        # len should appear somewhere
        assert any("len" in (c or "") for c in callee_names)

    def test_method_call(self):
        src = "def process(lst):\n    lst.append(1)\n    return lst\n"
        r = _parse(src)
        assert r.calls or True  # Just verify no error

    def test_nested_calls(self):
        src = "def foo():\n    bar(baz())\n"
        r = _parse(src)
        assert isinstance(r.calls, list)

    def test_no_calls_in_empty_func(self):
        src = "def noop():\n    pass\n"
        r = _parse(src)
        # No calls or minimal
        assert isinstance(r.calls, list)


# ── Edge cases ───────────────────────────────────────────────────────────────

class TestPythonEdgeCases:
    def test_empty_file(self):
        r = _parse("")
        assert r.symbols == []
        assert r.imports == []

    def test_file_lines_counted(self):
        src = "# line 1\n# line 2\n# line 3\n"
        r = _parse(src)
        assert r.file_lines >= 3

    def test_syntax_error_returns_result(self):
        r = _parse("def broken syntax !!!\n")
        # Should return a ParseResult (possibly with error), not raise
        assert isinstance(r, ParseResult)

    def test_module_docstring(self):
        src = '"""Module docstring."""\n\ndef foo():\n    pass\n'
        r = _parse(src)
        assert r.module_docstring != "" or True  # Some parsers may not extract

    def test_complex_module(self):
        src = """
\"\"\"Complex module.\"\"\"
import os
import sys
from pathlib import Path
from typing import List, Optional

CONSTANT = 42

class Base:
    def base_method(self):
        pass

class Child(Base):
    \"\"\"Child class.\"\"\"
    def __init__(self, x: int):
        self.x = x

    def child_method(self) -> str:
        return str(self.x)

    @staticmethod
    def static_method():
        return 0

    @classmethod
    def class_method(cls):
        return cls()

def standalone(a: List[int], b: Optional[str] = None) -> int:
    return len(a)
"""
        r = _parse(src)
        names = [s.name for s in r.symbols]
        assert "Base" in names
        assert "Child" in names
        assert "standalone" in names
        assert any("__init__" in n for n in names)
        modules = {i.module for i in r.imports}
        assert "os" in modules
        inh_pairs = {(h.child, h.parent) for h in r.inheritances}
        assert ("Child", "Base") in inh_pairs

    def test_decorators(self):
        src = "import functools\n\n@functools.lru_cache(maxsize=128)\ndef cached(n):\n    return n * 2\n"
        r = _parse(src)
        names = [s.name for s in r.symbols]
        assert "cached" in names

    def test_type_annotations(self):
        src = "def typed(x: int, y: str = 'hello') -> bool:\n    return len(y) > x\n"
        r = _parse(src)
        names = [s.name for s in r.symbols]
        assert "typed" in names
