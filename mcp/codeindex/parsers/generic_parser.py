"""Generic language parser — universal tree-sitter AST extraction.

Supports any language with a tree-sitter grammar by matching common AST node
type patterns that are shared across most tree-sitter grammars:
  - function_definition / function_declaration / method_declaration → functions
  - class_definition / class_declaration / struct_declaration → classes
  - call_expression → call relationships
  - import_statement / use_declaration / include_expression → imports

Not as precise as language-specific parsers, but good enough for knowledge
graph construction (symbol names, line numbers, basic call chains).
"""

from __future__ import annotations

from tree_sitter import Node, Tree

from ..parser import Call, CallType, Import, Inheritance, Symbol
from .base import BaseLanguageParser
from .utils import get_node_text


# ── Node type patterns shared across tree-sitter grammars ─────────

# Nodes that represent function/method definitions
_FUNC_DEF_TYPES = {
    "function_definition",       # Python, C, C++
    "function_declaration",      # Go, C, C++, Rust (extern)
    "method_declaration",        # Java, C#, Kotlin
    "method_definition",         # Ruby, C++
    "function_item",             # Rust
    "func_literal",              # Go (anonymous)
    "arrow_function",            # JS/TS (caught by TS parser, but useful)
    "function",                  # Ruby (def ... end)
    "constructor_declaration",   # Java, C#, Kotlin
}

# Nodes that represent class/struct/interface definitions
_CLASS_DEF_TYPES = {
    "class_definition",          # Python
    "class_declaration",         # Java, C#, Kotlin, Ruby, Swift
    "struct_item",               # Rust
    "struct_declaration",        # C, C++
    "struct_specifier",          # C
    "interface_declaration",     # Java, C#, Go, Kotlin
    "enum_declaration",          # Java, C#, Kotlin, Rust, Swift
    "enum_item",                 # Rust
    "trait_item",                # Rust
    "protocol_declaration",      # Swift
    "module_declaration",        # Ruby
    "object_declaration",        # Kotlin
}

# Nodes that represent import statements
_IMPORT_TYPES = {
    "import_statement",          # Python, Java, Kotlin
    "import_declaration",        # Java, Go, Kotlin
    "use_declaration",           # Rust
    "include_expression",        # C/C++ (#include)
    "preproc_include",           # C/C++ (#include)
    "using_directive",           # C#
    "require_call",              # Ruby
    "import_from_statement",     # Python (from X import Y)
}

# Nodes whose child may contain a name identifier
_NAME_FIELDS = {"name", "identifier", "declarator"}


class GenericParser(BaseLanguageParser):
    """Universal parser for any tree-sitter supported language.

    Walks the AST looking for common node type patterns. Extracts:
    - Symbols: functions, methods, classes, structs, interfaces
    - Imports: import/use/include statements
    - Calls: call_expression nodes
    - Inheritances: superclass/interface lists

    Quality is "good enough" — symbol names and line numbers are reliable,
    call targets are best-effort (may miss complex patterns).
    """

    def __init__(self, parser, *, language: str = "unknown"):
        super().__init__(parser)
        self.language = language

    # ── Symbols ──────────────────────────────────────────

    def extract_symbols(self, tree: Tree, source_bytes: bytes) -> list:
        symbols: list[Symbol] = []
        self._walk_for_symbols(tree.root_node, source_bytes, symbols, prefix="")
        return symbols

    def _walk_for_symbols(self, node: Node, source_bytes: bytes,
                          symbols: list[Symbol], prefix: str) -> None:
        """Recursively walk AST, collecting function and class symbols."""
        for child in node.children:
            if child.type in _FUNC_DEF_TYPES:
                sym = self._parse_func_symbol(child, source_bytes, prefix)
                if sym:
                    symbols.append(sym)
            elif child.type in _CLASS_DEF_TYPES:
                sym = self._parse_class_symbol(child, source_bytes, prefix)
                if sym:
                    symbols.append(sym)
                    # Recurse into class body for methods
                    body = self._find_body(child)
                    if body:
                        new_prefix = f"{prefix}{sym.name}." if prefix else f"{sym.name}."
                        self._walk_for_symbols(body, source_bytes, symbols, new_prefix)
            elif child.type in ("decorated_definition", "annotation"):
                # Python/Kotlin decorated definitions — look inside
                self._walk_for_symbols(child, source_bytes, symbols, prefix)
            elif child.type == "program" or child.type == "source_file":
                self._walk_for_symbols(child, source_bytes, symbols, prefix)

    def _parse_func_symbol(self, node: Node, source_bytes: bytes,
                           prefix: str) -> Symbol | None:
        name = self._extract_name(node, source_bytes)
        if not name:
            return None
        full_name = f"{prefix}{name}" if prefix else name
        # Try to build signature
        params = node.child_by_field_name("parameters")
        sig = f"def {full_name}({get_node_text(params, source_bytes)})" if params else f"def {full_name}()"
        kind = "method" if prefix else "function"
        return Symbol(
            name=full_name, kind=kind, signature=sig,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
        )

    def _parse_class_symbol(self, node: Node, source_bytes: bytes,
                            prefix: str) -> Symbol | None:
        name = self._extract_name(node, source_bytes)
        if not name:
            return None
        full_name = f"{prefix}{name}" if prefix else name
        kind = "class"
        if "interface" in node.type:
            kind = "interface"
        elif "struct" in node.type:
            kind = "struct"
        elif "enum" in node.type:
            kind = "enum"
        elif "trait" in node.type or "protocol" in node.type:
            kind = "interface"
        return Symbol(
            name=full_name, kind=kind, signature=f"{kind} {full_name}",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
        )

    # ── Imports ──────────────────────────────────────────

    def extract_imports(self, tree: Tree, source_bytes: bytes) -> list:
        imports: list[Import] = []
        self._walk_for_imports(tree.root_node, source_bytes, imports)
        return imports

    def _walk_for_imports(self, node: Node, source_bytes: bytes,
                          imports: list[Import]) -> None:
        for child in node.children:
            if child.type in _IMPORT_TYPES:
                text = get_node_text(child, source_bytes).strip()
                # Extract module name: take the main path/identifier
                module = self._extract_import_module(child, source_bytes, text)
                if module:
                    is_from = "from" in child.type or text.startswith("from ")
                    imports.append(Import(module=module, is_from=is_from))

    def _extract_import_module(self, node: Node, source_bytes: bytes,
                               text: str) -> str:
        """Best-effort extraction of the imported module path."""
        # Try field-based extraction first
        path_node = (node.child_by_field_name("path")
                     or node.child_by_field_name("module_name")
                     or node.child_by_field_name("source")
                     or node.child_by_field_name("name"))
        if path_node:
            return get_node_text(path_node, source_bytes).strip().strip('"\'<>')

        # Fallback: find the first scoped_identifier / dotted_name / identifier
        for child in node.children:
            if child.type in ("dotted_name", "scoped_identifier",
                              "identifier", "qualified_name",
                              "string_literal", "string"):
                raw = get_node_text(child, source_bytes).strip().strip('"\'<>')
                if raw and raw not in ("import", "from", "use", "require", "include"):
                    return raw

        # Last resort: strip keywords from the text
        for kw in ("import ", "from ", "use ", "require ", "#include ", "using "):
            if text.startswith(kw):
                rest = text[len(kw):].split()[0] if text[len(kw):].split() else ""
                return rest.strip('"\'<>;')
        return ""

    # ── Calls ────────────────────────────────────────────

    def extract_calls(self, tree: Tree, source_bytes: bytes,
                      symbols: list, imports: list) -> list:
        calls: list[Call] = []
        # Build a set of known symbol names for caller detection
        sym_ranges: list[tuple[str, int, int]] = [
            (s.name, s.line_start, s.line_end) for s in symbols
        ]
        self._walk_for_calls(tree.root_node, source_bytes, calls, sym_ranges)
        return calls

    def _walk_for_calls(self, node: Node, source_bytes: bytes,
                        calls: list[Call],
                        sym_ranges: list[tuple[str, int, int]]) -> None:
        for child in node.children:
            if child.type == "call_expression":
                call = self._parse_call(child, source_bytes, sym_ranges)
                if call:
                    calls.append(call)
            # Recurse into all children
            self._walk_for_calls(child, source_bytes, calls, sym_ranges)

    def _parse_call(self, node: Node, source_bytes: bytes,
                    sym_ranges: list[tuple[str, int, int]]) -> Call | None:
        """Extract a Call from a call_expression node."""
        func_node = node.child_by_field_name("function")
        if not func_node:
            # Some grammars use first child as the function
            if node.children:
                func_node = node.children[0]
            else:
                return None

        callee = get_node_text(func_node, source_bytes).strip()
        if not callee or len(callee) > 200:
            return None

        line = node.start_point[0] + 1
        # Determine caller from enclosing symbol
        caller = self._find_enclosing_symbol(line, sym_ranges) or "<module>"

        # Determine call type
        call_type = CallType.METHOD if "." in callee else CallType.FUNCTION

        return Call(
            caller=caller, callee=callee,
            line_number=line, call_type=call_type,
        )

    # ── Inheritances ─────────────────────────────────────

    def extract_inheritances(self, tree: Tree, source_bytes: bytes) -> list:
        inheritances: list[Inheritance] = []
        self._walk_for_inheritances(tree.root_node, source_bytes, inheritances)
        return inheritances

    def _walk_for_inheritances(self, node: Node, source_bytes: bytes,
                               inheritances: list[Inheritance]) -> None:
        for child in node.children:
            if child.type in _CLASS_DEF_TYPES:
                name = self._extract_name(child, source_bytes)
                if not name:
                    continue
                # Look for superclass/interface lists
                for field_name in ("superclass", "superclasses", "super_class",
                                   "interfaces", "type_parameters"):
                    sup = child.child_by_field_name(field_name)
                    if sup:
                        parent = get_node_text(sup, source_bytes).strip("() ")
                        if parent and parent != name:
                            for p in parent.split(","):
                                p = p.strip()
                                if p:
                                    inheritances.append(Inheritance(child=name, parent=p))

                # Also check argument_list (Python style: class Foo(Bar))
                for sub in child.children:
                    if sub.type in ("argument_list", "superclasses",
                                    "class_heritage", "super_class_clause"):
                        parents_text = get_node_text(sub, source_bytes).strip("() ")
                        for p in parents_text.split(","):
                            p = p.strip()
                            if p and p != name:
                                inheritances.append(Inheritance(child=name, parent=p))

            self._walk_for_inheritances(child, source_bytes, inheritances)

    # ── Helpers ──────────────────────────────────────────

    def _extract_name(self, node: Node, source_bytes: bytes) -> str:
        """Extract the name of a definition node."""
        # Try standard field names
        for field in ("name", "declarator"):
            name_node = node.child_by_field_name(field)
            if name_node:
                # Declarator may be wrapped (e.g., C function_declarator)
                if name_node.type in ("function_declarator", "pointer_declarator"):
                    inner = name_node.child_by_field_name("declarator")
                    if inner:
                        name_node = inner
                text = get_node_text(name_node, source_bytes).strip()
                # Remove generic params like Foo<T>
                if "<" in text:
                    text = text[:text.index("<")]
                if text:
                    return text

        # Fallback: find first identifier child
        for child in node.children:
            if child.type == "identifier" or child.type == "type_identifier":
                return get_node_text(child, source_bytes).strip()
        return ""

    def _find_body(self, node: Node) -> Node | None:
        """Find the body/block child of a class/struct node."""
        for field in ("body", "block"):
            body = node.child_by_field_name(field)
            if body:
                return body
        # Fallback: look for block-like children
        for child in node.children:
            if child.type in ("block", "class_body", "declaration_list",
                              "field_declaration_list", "enum_body",
                              "interface_body", "struct_body"):
                return child
        return None

    def _find_enclosing_symbol(self, line: int,
                               sym_ranges: list[tuple[str, int, int]]) -> str:
        """Find the innermost symbol that contains the given line."""
        best = ""
        best_span = float("inf")
        for name, start, end in sym_ranges:
            if start <= line <= end:
                span = end - start
                if span < best_span:
                    best = name
                    best_span = span
        return best
