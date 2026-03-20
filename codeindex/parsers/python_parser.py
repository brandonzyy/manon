"""Python language parser.

This module provides the PythonParser class that implements Python-specific
symbol extraction, import resolution, and call relationship analysis using tree-sitter.
"""

import logging
from pathlib import Path
from typing import Optional

from tree_sitter import Node, Tree

_log = logging.getLogger(__name__)

from ..parser import Call, CallType, Import, Inheritance, Symbol
from .base import BaseLanguageParser
from .utils import count_arguments, get_node_text


class PythonParser(BaseLanguageParser):
    """Python language parser.

    Extracts symbols (classes, functions, methods), imports, inheritances,
    and call relationships from Python source files.
    """

    def extract_symbols(self, tree: Tree, source_bytes: bytes) -> list:
        """Extract symbols (classes, functions, methods) from the parse tree.

        Args:
            tree: The tree-sitter parse tree
            source_bytes: The source code as bytes

        Returns:
            List of Symbol objects
        """
        symbols: list[Symbol] = []
        inheritances: list[Inheritance] = []  # For nested class support
        root = tree.root_node

        for child in root.children:
            if child.type == "function_definition":
                symbols.append(self._parse_function(child, source_bytes))
            elif child.type == "class_definition":
                symbols.extend(self._parse_class(child, source_bytes, "", inheritances))
            elif child.type == "decorated_definition":
                for dec_child in child.children:
                    if dec_child.type == "function_definition":
                        symbols.append(self._parse_function(dec_child, source_bytes))
                    elif dec_child.type == "class_definition":
                        symbols.extend(
                            self._parse_class(dec_child, source_bytes, "", inheritances)
                        )

        return symbols

    def extract_imports(self, tree: Tree, source_bytes: bytes) -> list:
        """Extract import statements from the parse tree.

        Args:
            tree: The tree-sitter parse tree
            source_bytes: The source code as bytes

        Returns:
            List of Import objects
        """
        imports: list[Import] = []
        root = tree.root_node

        for child in root.children:
            if child.type in ("import_statement", "import_from_statement"):
                import_list = self._parse_import(child, source_bytes)
                imports.extend(import_list)

        return imports

    def extract_calls(
        self, tree: Tree, source_bytes: bytes, symbols: list, imports: list
    ) -> list:
        """Extract function/method call relationships from the parse tree.

        Args:
            tree: The tree-sitter parse tree
            source_bytes: The source code as bytes
            symbols: Previously extracted symbols
            imports: Previously extracted imports

        Returns:
            List of Call objects
        """
        # Extract inheritances first (needed for super() resolution)
        inheritances = self.extract_inheritances(tree, source_bytes)

        # Extract call relationships
        calls = self._extract_python_calls_from_tree(
            tree, source_bytes, imports, inheritances
        )

        return calls

    def extract_inheritances(self, tree: Tree, source_bytes: bytes) -> list:
        """Extract class inheritance relationships from the parse tree.

        Args:
            tree: The tree-sitter parse tree
            source_bytes: The source code as bytes

        Returns:
            List of Inheritance objects
        """
        inheritances: list[Inheritance] = []
        root = tree.root_node

        for child in root.children:
            if child.type == "class_definition":
                self._extract_class_inheritances(child, source_bytes, "", inheritances)
            elif child.type == "decorated_definition":
                for dec_child in child.children:
                    if dec_child.type == "class_definition":
                        self._extract_class_inheritances(
                            dec_child, source_bytes, "", inheritances
                        )

        return inheritances

    def _build_python_parse_result(self, tree, path, source_bytes: bytes, file_lines: int):
        """Extract all Python parse content and return a ParseResult."""
        from ..parser import ParseResult
        try:
            module_docstring = self._extract_module_docstring(tree, source_bytes)
            symbols = self.extract_symbols(tree, source_bytes)
            imports = self.extract_imports(tree, source_bytes)
            inheritances = self.extract_inheritances(tree, source_bytes)
            calls = self.extract_calls(tree, source_bytes, symbols, imports)
            return ParseResult(path=path, symbols=symbols, imports=imports, inheritances=inheritances,
                               calls=calls, file_lines=file_lines, module_docstring=module_docstring)
        except Exception as e:
            return ParseResult(path=path, error=f"Parse error: {str(e)}", file_lines=file_lines)

    def parse(self, path: Path):
        """Parse a Python source file."""
        from ..parser import ParseResult
        try:
            source_bytes = path.read_bytes()
        except Exception as e:
            return ParseResult(path=path, error=str(e), file_lines=0)
        file_lines = source_bytes.count(b"\n") + (
            1 if source_bytes and not source_bytes.endswith(b"\n") else 0
        )
        tree = self.parser.parse(source_bytes)
        if tree.root_node.has_error:
            _log.debug("Partial parse (syntax errors) in %s — extracting available symbols", path.name)
        return self._build_python_parse_result(tree, path, source_bytes, file_lines)

    # ============================================================================
    # Symbol Extraction Helpers
    # ============================================================================

    def _extract_docstring(self, node: Node, source_bytes: bytes) -> str:
        """Extract docstring from first child if it's a string."""
        if node.child_count == 0:
            return ""

        # Look for expression_statement containing string
        for child in node.children:
            if child.type == "block":
                for block_child in child.children:
                    if block_child.type == "expression_statement":
                        for expr_child in block_child.children:
                            if expr_child.type == "string":
                                text = get_node_text(expr_child, source_bytes)
                                # Remove quotes
                                if text.startswith('"""') or text.startswith("'''"):
                                    return text[3:-3].strip()
                                elif text.startswith('"') or text.startswith("'"):
                                    return text[1:-1].strip()
                        break
                break

        return ""

    def _parse_function(
        self,
        node: Node,
        source_bytes: bytes,
        class_name: str = "",
        decorators: list[str] | None = None,
    ) -> Symbol:
        """Parse a function definition node."""
        name = ""
        signature_parts = []

        for child in node.children:
            if child.type == "identifier":  # function name is 'identifier', not 'name'
                name = get_node_text(child, source_bytes)
            elif child.type == "parameters":
                signature_parts.append(get_node_text(child, source_bytes))
            elif child.type == "type":
                signature_parts.append(f" -> {get_node_text(child, source_bytes)}")

        kind = "method" if class_name else "function"
        full_name = f"{class_name}.{name}" if class_name else name
        signature = f"def {name}{''.join(signature_parts)}"
        docstring = self._extract_docstring(node, source_bytes)

        return Symbol(
            name=full_name,
            kind=kind,
            signature=signature,
            docstring=docstring,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
        )

    def _parse_python_class_header(
        self, node: Node, source_bytes: bytes, parent_class: str,
    ) -> tuple[str, list[str], str]:
        """Extract (full_class_name, bases, signature) from a class_definition node."""
        class_name, bases = "", []
        for child in node.children:
            if child.type == "identifier":
                class_name = get_node_text(child, source_bytes)
            elif child.type == "argument_list":
                for arg_child in child.children:
                    if arg_child.type in ("identifier", "attribute", "subscript"):
                        base_text = get_node_text(arg_child, source_bytes)
                        bases.append(base_text.split("[")[0] if "[" in base_text else base_text)
        full_class_name = f"{parent_class}.{class_name}" if parent_class else class_name
        signature = f"class {class_name}" + (f"({', '.join(bases)})" if bases else "")
        return full_class_name, bases, signature

    def _parse_class(
        self, node: Node, source_bytes: bytes,
        parent_class: str = "", inheritances: list[Inheritance] | None = None,
    ) -> list[Symbol]:
        """Parse a class definition node and its methods."""
        if inheritances is None:
            inheritances = []

        full_class_name, bases, signature = self._parse_python_class_header(node, source_bytes, parent_class)
        symbols = [Symbol(
            name=full_class_name, kind="class", signature=signature,
            docstring=self._extract_docstring(node, source_bytes),
            line_start=node.start_point[0] + 1, line_end=node.end_point[0] + 1,
        )]
        for base in bases:
            inheritances.append(Inheritance(child=full_class_name, parent=base))

        for child in node.children:
            if child.type == "block":
                for block_child in child.children:
                    if block_child.type == "function_definition":
                        symbols.append(self._parse_function(block_child, source_bytes, full_class_name))
                    elif block_child.type == "class_definition":
                        symbols.extend(self._parse_class(block_child, source_bytes, full_class_name, inheritances))

        return symbols

    def _parse_import_statement(self, node: Node, source_bytes: bytes) -> list[Import]:
        """Parse `import foo` and `import foo as bar` statements."""
        imports: list[Import] = []
        for child in node.children:
            if child.type == "dotted_name":
                imports.append(Import(module=get_node_text(child, source_bytes), names=[], is_from=False, alias=None))
            elif child.type == "aliased_import":
                module_name, alias = "", None
                for ac in child.children:
                    if ac.type == "dotted_name":
                        module_name = get_node_text(ac, source_bytes)
                    elif ac.type == "identifier" and module_name:
                        alias = get_node_text(ac, source_bytes)
                if module_name:
                    imports.append(Import(module=module_name, names=[], is_from=False, alias=alias))
        return imports

    def _parse_from_import_statement(self, node: Node, source_bytes: bytes) -> list[Import]:
        """Parse `from foo import bar, baz as qux` statements."""
        module, imported_items, found_module = "", [], False
        for child in node.children:
            if child.type in ("dotted_name", "relative_import") and not found_module:
                module = get_node_text(child, source_bytes)
                found_module = True
            elif found_module:
                if child.type == "dotted_name":
                    name = get_node_text(child, source_bytes)
                    if name != module:
                        imported_items.append((name, None))
                elif child.type == "identifier":
                    name = get_node_text(child, source_bytes)
                    if name not in ("import", "from", "*") and name != module:
                        imported_items.append((name, None))
                elif child.type == "aliased_import":
                    name, alias = "", None
                    for ac in child.children:
                        if ac.type in ("dotted_name", "identifier") and not name:
                            name = get_node_text(ac, source_bytes)
                        elif ac.type == "identifier" and name:
                            alias = get_node_text(ac, source_bytes)
                    if name:
                        imported_items.append((name, alias))
                elif child.type == "wildcard_import":
                    imported_items.append(("*", None))
        return [Import(module=module, names=[name], is_from=True, alias=alias) for name, alias in imported_items] if module and imported_items else []

    def _parse_import(self, node: Node, source_bytes: bytes) -> list[Import]:
        """Parse an import or from-import statement into Import objects."""
        if node.type == "import_statement":
            return self._parse_import_statement(node, source_bytes)
        if node.type == "import_from_statement":
            return self._parse_from_import_statement(node, source_bytes)
        return []

    def _extract_module_docstring(self, tree: Tree, source_bytes: bytes) -> str:
        """Extract module-level docstring."""
        root = tree.root_node
        for child in root.children:
            if child.type == "expression_statement":
                for expr_child in child.children:
                    if expr_child.type == "string":
                        text = get_node_text(expr_child, source_bytes)
                        if text.startswith('"""') or text.startswith("'''"):
                            return text[3:-3].strip()
                        elif text.startswith('"') or text.startswith("'"):
                            return text[1:-1].strip()
                break
            elif child.type not in ("comment",):
                break
        return ""

    def _extract_class_inheritances(
        self,
        node: Node,
        source_bytes: bytes,
        parent_class: str,
        inheritances: list[Inheritance],
    ) -> None:
        """Extract inheritances from a class definition (helper for extract_inheritances).

        Args:
            node: Tree-sitter class_definition node
            source_bytes: Source code bytes
            parent_class: Parent class name for nested classes
            inheritances: List to append Inheritance objects to
        """
        class_name = ""
        bases = []

        for child in node.children:
            if child.type == "identifier":
                class_name = get_node_text(child, source_bytes)
            elif child.type == "argument_list":
                for arg_child in child.children:
                    if arg_child.type in ("identifier", "attribute", "subscript"):
                        base_text = get_node_text(arg_child, source_bytes)
                        base_name = base_text.split("[")[0] if "[" in base_text else base_text
                        bases.append(base_name)

        full_class_name = f"{parent_class}.{class_name}" if parent_class else class_name

        # Add inheritances
        for base in bases:
            inheritances.append(Inheritance(child=full_class_name, parent=base))

        # Process nested classes
        for child in node.children:
            if child.type == "block":
                for block_child in child.children:
                    if block_child.type == "class_definition":
                        self._extract_class_inheritances(
                            block_child, source_bytes, full_class_name, inheritances
                        )

    # ============================================================================
    # Call Relationship Extraction (Epic 11, Story 11.1)
    # ============================================================================

    def _build_alias_map(self, imports: list[Import]) -> dict[str, str]:
        """Build alias-to-module mapping from imports (Epic 11, Story 11.1).

        Examples:
            >>> imports = [
            ...     Import(module="pandas", alias="pd"),
            ...     Import(module="numpy", alias="np"),
            ...     Import(module="numpy", names=["array"], is_from=True, alias="np_array"),
            ... ]
            >>> _build_alias_map(imports)
            {'pd': 'pandas', 'np': 'numpy', 'np_array': 'numpy.array'}
        """
        alias_map = {}
        for imp in imports:
            if imp.alias:
                # Handle from-import with alias: from numpy import array as np_array
                if imp.is_from and imp.names:
                    # Map alias to module.name: np_array → numpy.array
                    alias_map[imp.alias] = f"{imp.module}.{imp.names[0]}"
                else:
                    # Regular import with alias: import pandas as pd
                    alias_map[imp.alias] = imp.module
        return alias_map

    def _resolve_alias(self, callee: str, alias_map: dict[str, str]) -> str:
        """Resolve import alias in callee name (Epic 11, Story 11.1).

        Examples:
            >>> _resolve_alias("pd.read_csv", {"pd": "pandas"})
            'pandas.read_csv'

            >>> _resolve_alias("np_array", {"np_array": "numpy.array"})
            'numpy.array'

            >>> _resolve_alias("helper", {})
            'helper'
        """
        if not callee:
            return callee

        # Direct match (for from-import aliases like np_array)
        if callee in alias_map:
            return alias_map[callee]

        # No dot, not in alias_map
        if "." not in callee:
            return callee

        # Split and check prefix (for module aliases like pd.read_csv)
        parts = callee.split(".", 1)
        prefix = parts[0]
        suffix = parts[1] if len(parts) > 1 else ""

        if prefix in alias_map:
            real_prefix = alias_map[prefix]
            return f"{real_prefix}.{suffix}" if suffix else real_prefix

        return callee

    def _determine_python_call_type(
        self, func_node: Node, source_bytes: bytes
    ) -> CallType:
        """Determine Python call type from function node.

        Rules:
            - Constructor: Identifier starts with uppercase → CONSTRUCTOR
            - Dynamic: getattr/setattr/eval/exec/__import__ → DYNAMIC
            - Static method: ClassName.method() → STATIC_METHOD
            - Instance method: obj.method() → METHOD
            - Simple call: func() → FUNCTION
        """
        if func_node.type == "identifier":
            name = get_node_text(func_node, source_bytes)
            # Constructor: starts with uppercase
            if name and name[0].isupper():
                return CallType.CONSTRUCTOR
            # Dynamic calls
            if name in ("getattr", "setattr", "eval", "exec", "__import__"):
                return CallType.DYNAMIC
            return CallType.FUNCTION

        elif func_node.type == "attribute":
            # obj.method() or Class.method() or Outer.Inner() (nested class)
            # Check the attribute part (method/class name)
            attr_node = func_node.child_by_field_name("attribute")
            if attr_node:
                attr_name = get_node_text(attr_node, source_bytes)
                # Constructor: Outer.Inner() → Inner starts with uppercase
                if attr_name and attr_name[0].isupper():
                    return CallType.CONSTRUCTOR

            # Check the object part
            obj_node = func_node.child_by_field_name("object")
            if obj_node and obj_node.type == "identifier":
                obj_name = get_node_text(obj_node, source_bytes)
                # Static method: ClassName.method()
                if obj_name and obj_name[0].isupper():
                    return CallType.STATIC_METHOD

            return CallType.METHOD

        return CallType.FUNCTION

    def _extract_call_name(self, func_node: Node, source_bytes: bytes) -> str:
        """Extract callee name from function node.

        Examples:
            - identifier: "helper" → "helper"
            - attribute: obj.method → "obj.method"
            - attribute: pd.read_csv → "pd.read_csv"
            - attribute: super().method → "super.method"
        """
        if func_node.type == "identifier":
            return get_node_text(func_node, source_bytes)

        elif func_node.type == "attribute":
            # Build full attribute path: obj.attr1.attr2...
            parts = []
            current = func_node

            while current:
                if current.type == "attribute":
                    # Get the attribute name
                    attr_node = current.child_by_field_name("attribute")
                    if attr_node:
                        parts.insert(0, get_node_text(attr_node, source_bytes))
                    # Move to object
                    current = current.child_by_field_name("object")
                elif current.type == "identifier":
                    parts.insert(0, get_node_text(current, source_bytes))
                    break
                elif current.type == "call":
                    # Special case: super().method() → extract as "super.method"
                    func_child = current.child_by_field_name("function")
                    if func_child and func_child.type == "identifier":
                        func_name = get_node_text(func_child, source_bytes)
                        if func_name == "super":
                            parts.insert(0, "super")
                            break
                    # For other calls, just use generic name
                    parts.insert(0, "<call>")
                    break
                else:
                    break

            return ".".join(parts)

        return ""

    def _build_local_type_map(self, func_node: Node, source_bytes: bytes) -> dict[str, str]:
        """Build var→ClassName map from simple assignments in a function body.

        Patterns recognized:
            var = ClassName(...)   →  {var: "ClassName"}
            var: ClassName = ...   →  {var: "ClassName"}
        Only handles direct identifier assignments; no complex generics or chained calls.
        """
        type_map: dict[str, str] = {}
        body = next((n for n in func_node.children if n.type == "block"), None)
        if body is None:
            return type_map
        for stmt in body.children:
            # assignments are often wrapped: expression_statement → assignment
            node = stmt
            if node.type == "expression_statement" and node.child_count == 1:
                node = node.children[0]
            if node.type != "assignment":
                continue
            left = node.child_by_field_name("left")
            if not left or left.type != "identifier":
                continue
            var_name = get_node_text(left, source_bytes)
            # Pattern 1: var: ClassName = ...
            type_ann = node.child_by_field_name("type")
            if type_ann:
                ann_node = type_ann
                if ann_node.type == "type" and ann_node.child_count == 1:
                    ann_node = ann_node.children[0]
                if ann_node.type == "identifier":
                    type_name = get_node_text(ann_node, source_bytes)
                    if type_name and type_name[0].isupper():
                        type_map[var_name] = type_name
                continue
            # Pattern 2: var = ClassName(...)
            right = node.child_by_field_name("right")
            if right and right.type == "call":
                func_child = right.child_by_field_name("function")
                if func_child and func_child.type == "identifier":
                    cls_name = get_node_text(func_child, source_bytes)
                    if cls_name and cls_name[0].isupper():
                        type_map[var_name] = cls_name
        return type_map

    def _resolve_python_callee(
        self, callee_raw: str, caller: str, parent_map: dict[str, str],
        type_map: dict[str, str] | None = None,
    ) -> str:
        """Resolve self/super references and instance variable types in a callee."""
        if callee_raw.startswith("self.") and "." in caller:
            class_name = caller.rsplit(".", 1)[0]
            callee_raw = callee_raw.replace("self.", f"{class_name}.", 1)
        if callee_raw.startswith("super.") and "." in caller:
            class_name = caller.rsplit(".", 1)[0]
            if class_name in parent_map:
                callee_raw = callee_raw.replace("super.", f"{parent_map[class_name]}.", 1)
        # Instance variable type inference: graph.add_relation() → CodeGraph.add_relation()
        if type_map and "." in callee_raw:
            prefix = callee_raw.split(".", 1)[0]
            if prefix in type_map:
                callee_raw = type_map[prefix] + callee_raw[len(prefix):]
        return callee_raw

    def _parse_python_call(
        self,
        node: Node,
        source_bytes: bytes,
        caller: str,
        alias_map: dict[str, str],
        parent_map: dict[str, str],
        type_map: dict[str, str] | None = None,
    ) -> Optional[Call]:
        """Parse a single Python call node."""
        func_node = node.child_by_field_name("function")
        if not func_node:
            return None

        callee_raw = self._extract_call_name(func_node, source_bytes)
        if not callee_raw:
            return None

        callee_raw = self._resolve_python_callee(callee_raw, caller, parent_map, type_map)
        callee = self._resolve_alias(callee_raw, alias_map)

        call_type = self._determine_python_call_type(func_node, source_bytes)
        if call_type == CallType.CONSTRUCTOR:
            callee = f"{callee}.__init__"

        args_node = node.child_by_field_name("arguments")
        args_count = count_arguments(args_node) if args_node else None

        return Call(
            caller=caller,
            callee=callee,
            line_number=node.start_point[0] + 1,
            call_type=call_type,
            arguments_count=args_count,
        )

    def _extract_python_calls(
        self,
        node: Node,
        source_bytes: bytes,
        context: str,
        alias_map: dict[str, str],
        parent_map: dict[str, str],
        type_map: dict[str, str] | None = None,
    ) -> list[Call]:
        """Extract Python call relationships (Epic 11, Story 11.1).

        Recursively traverses AST to extract function/method calls.

        Args:
            node: Tree-sitter AST node
            source_bytes: Source code bytes
            context: Current context (function/method/class name, e.g., "Child.method")
            alias_map: Import alias mapping
            parent_map: Parent class mapping (for super() resolution)
            type_map: Local variable type map for instance method inference

        Returns:
            List of Call objects
        """
        calls = []

        # Process call nodes
        if node.type == "call":
            call = self._parse_python_call(
                node, source_bytes, context, alias_map, parent_map, type_map
            )
            if call:
                calls.append(call)

        # Recurse into children
        for child in node.children:
            calls.extend(
                self._extract_python_calls(
                    child, source_bytes, context, alias_map, parent_map, type_map
                )
            )

        return calls

    def _is_simple_decorator(self, decorator_node: Node) -> bool:
        """Check if decorator is simple (Phase 1 support).

        Simple decorators (Phase 1):
            @decorator         ✅
            @module.decorator  ✅

        Complex decorators (Phase 2, not supported yet):
            @decorator(arg1, arg2)  ❌
            @decorator()            ❌
            @outer(@inner)          ❌
        """
        # Decorator node should only have identifier or attribute children (no call)
        for child in decorator_node.children:
            if child.type == "call":  # Has call means it's complex
                return False
        return True

    def _extract_decorator_name(self, decorator_node: Node, source_bytes: bytes) -> str:
        """Extract decorator name from decorator node.

        Examples:
            @decorator → "decorator"
            @module.decorator → "module.decorator"
        """
        for child in decorator_node.children:
            if child.type == "identifier":
                return get_node_text(child, source_bytes)
            elif child.type == "attribute":
                return self._extract_call_name(child, source_bytes)
        return ""

    def _extract_decorator_calls(
        self, node: Node, source_bytes: bytes, context: str
    ) -> list[Call]:
        """Extract decorator calls from function/class definition (Phase 1).

        Args:
            node: function_definition or class_definition node
            source_bytes: Source code bytes
            context: Context (function/class name or module)

        Returns:
            List of Call objects for decorators
        """
        calls = []

        # Only handle function_definition and class_definition
        if node.type not in ("function_definition", "class_definition"):
            return calls

        # Find decorator nodes
        for child in node.children:
            if child.type == "decorator":
                # Phase 1: Only simple decorators
                if self._is_simple_decorator(child):
                    decorator_name = self._extract_decorator_name(child, source_bytes)

                    if decorator_name:
                        calls.append(
                            Call(
                                caller=context,
                                callee=decorator_name,
                                line_number=child.start_point[0] + 1,
                                call_type=CallType.FUNCTION,
                                arguments_count=1,  # Decorator receives 1 arg (decorated object)
                            )
                        )

        return calls

    def _extract_class_body_method_calls(
        self, body_node, class_name: str, source_bytes: bytes, alias_map: dict, parent_map: dict,
    ) -> list[Call]:
        """Extract calls from all methods inside a class body node."""
        calls: list[Call] = []
        for method_node in body_node.children:
            if method_node.type == "function_definition":
                method_name = ""
                for node in method_node.children:
                    if node.type == "identifier":
                        method_name = get_node_text(node, source_bytes)
                        break
                if method_name:
                    context = f"{class_name}.{method_name}"
                    type_map = self._build_local_type_map(method_node, source_bytes)
                    calls.extend(self._extract_decorator_calls(method_node, source_bytes, class_name))
                    calls.extend(self._extract_python_calls(method_node, source_bytes, context, alias_map, parent_map, type_map))
            elif method_node.type == "decorated_definition":
                for dec_node in method_node.children:
                    if dec_node.type == "decorator" and self._is_simple_decorator(dec_node):
                        decorator_name = self._extract_decorator_name(dec_node, source_bytes)
                        if decorator_name:
                            calls.append(Call(
                                caller=class_name, callee=decorator_name,
                                line_number=dec_node.start_point[0] + 1,
                                call_type=CallType.FUNCTION, arguments_count=1,
                            ))
                for dec_child in method_node.children:
                    if dec_child.type == "function_definition":
                        method_name = ""
                        for node in dec_child.children:
                            if node.type == "identifier":
                                method_name = get_node_text(node, source_bytes)
                                break
                        if method_name:
                            type_map = self._build_local_type_map(dec_child, source_bytes)
                            calls.extend(self._extract_python_calls(
                                dec_child, source_bytes, f"{class_name}.{method_name}", alias_map, parent_map, type_map,
                            ))
        return calls

    def _extract_class_node_calls(
        self, class_node, source_bytes: bytes, caller: str, alias_map: dict, parent_map: dict,
    ) -> list[Call]:
        """Extract calls from a class definition node (decorators + methods)."""
        calls: list[Call] = []
        class_name = ""
        for node in class_node.children:
            if node.type == "identifier":
                class_name = get_node_text(node, source_bytes)
                break
        if not class_name:
            return calls
        calls.extend(self._extract_decorator_calls(class_node, source_bytes, caller))
        body_node = next((n for n in class_node.children if n.type == "block"), None)
        if body_node:
            calls.extend(self._extract_class_body_method_calls(body_node, class_name, source_bytes, alias_map, parent_map))
        return calls

    def _extract_python_calls_from_tree(
        self,
        tree: Tree,
        source_bytes: bytes,
        imports: list[Import],
        inheritances: list[Inheritance],
    ) -> list[Call]:
        """Extract all Python calls from parse tree."""
        alias_map = self._build_alias_map(imports)
        parent_map = {inh.child: inh.parent for inh in inheritances}
        calls: list[Call] = []

        for child in tree.root_node.children:
            if child.type == "function_definition":
                func_name = next(
                    (get_node_text(n, source_bytes) for n in child.children if n.type == "identifier"), ""
                )
                if func_name:
                    type_map = self._build_local_type_map(child, source_bytes)
                    calls.extend(self._extract_python_calls(child, source_bytes, func_name, alias_map, parent_map, type_map))

            elif child.type == "class_definition":
                calls.extend(self._extract_class_node_calls(child, source_bytes, "<module>", alias_map, parent_map))

            elif child.type == "decorated_definition":
                for dec_node in child.children:
                    if dec_node.type == "decorator" and self._is_simple_decorator(dec_node):
                        decorator_name = self._extract_decorator_name(dec_node, source_bytes)
                        if decorator_name:
                            calls.append(Call(
                                caller="<module>", callee=decorator_name,
                                line_number=dec_node.start_point[0] + 1,
                                call_type=CallType.FUNCTION, arguments_count=1,
                            ))
                for dec_child in child.children:
                    if dec_child.type == "function_definition":
                        func_name = next(
                            (get_node_text(n, source_bytes) for n in dec_child.children if n.type == "identifier"), ""
                        )
                        if func_name:
                            type_map = self._build_local_type_map(dec_child, source_bytes)
                            calls.extend(self._extract_python_calls(dec_child, source_bytes, func_name, alias_map, parent_map, type_map))
                    elif dec_child.type == "class_definition":
                        calls.extend(self._extract_class_node_calls(dec_child, source_bytes, "<module>", alias_map, parent_map))

        return calls
