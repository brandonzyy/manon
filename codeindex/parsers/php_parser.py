"""PHP language parser.

This module provides PHP-specific parsing functionality using tree-sitter.
"""

from typing import Optional

from tree_sitter import Node, Tree

from ..parser import Call, CallType, Import, Inheritance, Symbol
from .base import BaseLanguageParser
from .utils import get_node_text


class PhpParser(BaseLanguageParser):
    """PHP language parser.

    Extracts symbols, imports, calls, and inheritances from PHP source code.
    Supports PHP classes, methods, properties, functions, and namespace handling.
    """

    def _build_php_parse_result(self, tree, path, source_bytes: bytes, file_lines: int):
        """Extract all PHP parse content and return a ParseResult."""
        from ..parser import ParseResult
        try:
            symbols = self.extract_symbols(tree, source_bytes)
            imports = self.extract_imports(tree, source_bytes)
            inheritances = self.extract_inheritances(tree, source_bytes)
            calls = self.extract_calls(tree, source_bytes, symbols, imports)
            namespace = ""
            for child in tree.root_node.children:
                if child.type == "namespace_definition":
                    namespace = self._parse_php_namespace(child, source_bytes)
                    break
            return ParseResult(
                path=path, symbols=symbols, imports=imports, inheritances=inheritances,
                calls=calls, file_lines=file_lines, namespace=namespace,
            )
        except Exception as e:
            return ParseResult(path=path, error=f"Parse error: {str(e)}", file_lines=file_lines)

    def parse(self, path):
        """Parse a PHP source file."""
        from pathlib import Path
        from ..parser import ParseResult
        try:
            source_bytes = Path(path).read_bytes()
        except Exception as e:
            return ParseResult(path=path, error=str(e), file_lines=0)
        file_lines = source_bytes.count(b"\n") + (
            1 if source_bytes and not source_bytes.endswith(b"\n") else 0
        )
        tree = self.parser.parse(source_bytes)
        if tree.root_node.has_error:
            return ParseResult(path=path, error="Syntax error in source file", file_lines=file_lines)
        return self._build_php_parse_result(tree, path, source_bytes, file_lines)

    def extract_symbols(self, tree: Tree, source_bytes: bytes) -> list:
        """Extract symbols (classes, functions, methods, properties) from the parse tree.

        Args:
            tree: The tree-sitter parse tree
            source_bytes: The source code as bytes

        Returns:
            List of Symbol objects
        """
        symbols = []
        namespace = ""
        use_map = {}  # For tracking use statements
        inheritances = []  # For tracking inheritance relationships

        root = tree.root_node

        # First pass: Extract namespace and use statements
        for child in root.children:
            if child.type == "namespace_definition":
                namespace = self._parse_php_namespace(child, source_bytes)
            elif child.type == "namespace_use_declaration":
                imports = self._parse_php_use(child, source_bytes)
                # Build use map for class resolution
                for imp in imports:
                    if imp.alias:
                        use_map[imp.alias] = imp.module
                    else:
                        # Extract short name from module
                        short_name = imp.module.split("\\")[-1]
                        use_map[short_name] = imp.module

        # Second pass: Extract functions and classes
        for child in root.children:
            if child.type == "function_definition":
                symbol = self._parse_php_function(child, source_bytes)
                symbols.append(symbol)
            elif child.type == "class_declaration":
                # Pass namespace, use_map, and inheritances for inheritance extraction
                class_symbols = self._parse_php_class(
                    child, source_bytes, namespace, use_map, inheritances
                )
                symbols.extend(class_symbols)

        return symbols

    def extract_imports(self, tree: Tree, source_bytes: bytes) -> list:
        """Extract import/use statements from the parse tree.

        Args:
            tree: The tree-sitter parse tree
            source_bytes: The source code as bytes

        Returns:
            List of Import objects
        """
        imports = []
        root = tree.root_node

        for child in root.children:
            if child.type == "namespace_use_declaration":
                php_imports = self._parse_php_use(child, source_bytes)
                imports.extend(php_imports)
            elif child.type in ("include_expression", "require_expression"):
                php_import = self._parse_php_include(child, source_bytes)
                if php_import:
                    imports.append(php_import)

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
        # First extract namespace and use statements
        namespace = ""
        use_map = {}
        inheritances = []

        root = tree.root_node

        for child in root.children:
            if child.type == "namespace_definition":
                namespace = self._parse_php_namespace(child, source_bytes)
            elif child.type == "namespace_use_declaration":
                php_imports = self._parse_php_use(child, source_bytes)
                for imp in php_imports:
                    if imp.alias:
                        use_map[imp.alias] = imp.module
                    else:
                        short_name = imp.module.split("\\")[-1]
                        use_map[short_name] = imp.module

        # Extract inheritances for parent:: resolution
        for child in root.children:
            if child.type == "class_declaration":
                self._parse_php_class(child, source_bytes, namespace, use_map, inheritances)

        # Extract calls
        return self._extract_php_calls_from_tree(
            tree, source_bytes, imports, inheritances, namespace, use_map
        )

    def extract_inheritances(self, tree: Tree, source_bytes: bytes) -> list:
        """Extract class inheritance relationships from the parse tree.

        Args:
            tree: The tree-sitter parse tree
            source_bytes: The source code as bytes

        Returns:
            List of Inheritance objects
        """
        inheritances = []
        namespace = ""
        use_map = {}

        root = tree.root_node

        # First pass: Extract namespace and use statements
        for child in root.children:
            if child.type == "namespace_definition":
                namespace = self._parse_php_namespace(child, source_bytes)
            elif child.type == "namespace_use_declaration":
                php_imports = self._parse_php_use(child, source_bytes)
                for imp in php_imports:
                    if imp.alias:
                        use_map[imp.alias] = imp.module
                    else:
                        short_name = imp.module.split("\\")[-1]
                        use_map[short_name] = imp.module

        # Second pass: Extract inheritances from classes
        for child in root.children:
            if child.type == "class_declaration":
                self._parse_php_class(child, source_bytes, namespace, use_map, inheritances)

        return inheritances

    # ==================== Private Helper Methods ====================

    def _extract_php_docstring(self, node, source_bytes: bytes) -> str:
        """Extract docstring from PHPDoc/DocComment or inline comments.

        For PHP, the comment is often a sibling node (previous sibling)
        rather than a child node.

        Supports:
        - PHPDoc blocks: /** ... */
        - Inline comments: // ...
        """
        # First check children (for class-level comments)
        for child in node.children:
            if child.type == "comment":
                text = get_node_text(child, source_bytes)
                if text.startswith("/**"):
                    return self._parse_phpdoc_text(text)
                elif text.startswith("//"):
                    # Inline comment: remove // and strip
                    return text[2:].strip()

        # Check previous sibling (for method-level comments)
        if node.prev_sibling and node.prev_sibling.type == "comment":
            text = get_node_text(node.prev_sibling, source_bytes)
            if text.startswith("/**"):
                return self._parse_phpdoc_text(text)
            elif text.startswith("//"):
                # Inline comment: remove // and strip
                return text[2:].strip()

        return ""

    def _parse_phpdoc_text(self, text: str) -> str:
        """Parse PHPDoc comment text and extract description.

        Extracts the first non-annotation line(s) from PHPDoc.
        Skips @param, @return, @throws, etc.

        Args:
            text: Raw PHPDoc comment text (/** ... */)

        Returns:
            Cleaned description text
        """
        # Handle single-line PHPDoc: /** Description */
        if "\n" not in text:
            # Remove /** and */
            content = text.strip()
            if content.startswith("/**"):
                content = content[3:]
            if content.endswith("*/"):
                content = content[:-2]
            content = content.strip()
            # Skip if it's only annotations
            if content.startswith("@"):
                return ""
            return content

        # Handle multi-line PHPDoc
        lines = text.split("\n")
        description_lines = []

        for line in lines[1:-1]:  # Skip first (/**) and last (*/) lines
            line = line.strip()
            # Remove leading * and whitespace
            if line.startswith("*"):
                line = line[1:].strip()

            # Skip empty lines
            if not line:
                continue

            # Skip annotation lines (@param, @return, etc.)
            if line.startswith("@"):
                break  # Stop at first annotation

            description_lines.append(line)

        return " ".join(description_lines)

    def _parse_php_function(self, node, source_bytes: bytes, class_name: str = "") -> Symbol:
        """Parse a PHP function definition node (standalone function, not method)."""
        name = ""
        params = ""
        return_type = ""

        for child in node.children:
            if child.type == "name":
                name = get_node_text(child, source_bytes)
            elif child.type == "formal_parameters":
                params = get_node_text(child, source_bytes)
            elif child.type in ("named_type", "primitive_type", "optional_type"):
                return_type = get_node_text(child, source_bytes)

        signature = f"function {name}{params}"
        if return_type:
            signature += f": {return_type}"

        docstring = self._extract_php_docstring(node, source_bytes)

        return Symbol(
            name=name,
            kind="function",
            signature=signature,
            docstring=docstring,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
        )

    def _parse_php_method(self, node, source_bytes: bytes, class_name: str) -> Symbol:
        """Parse a PHP method declaration node with visibility, static, and return type."""
        name = ""
        params = ""
        return_type = ""
        visibility = ""
        is_static = False

        for child in node.children:
            if child.type == "visibility_modifier":
                visibility = get_node_text(child, source_bytes)
            elif child.type == "static_modifier":
                is_static = True
            elif child.type == "name":
                name = get_node_text(child, source_bytes)
            elif child.type == "formal_parameters":
                params = get_node_text(child, source_bytes)
            elif child.type in ("named_type", "primitive_type", "optional_type"):
                return_type = get_node_text(child, source_bytes)

        # Build signature: [visibility] [static] function name(params)[: return_type]
        sig_parts = []
        if visibility:
            sig_parts.append(visibility)
        if is_static:
            sig_parts.append("static")
        sig_parts.append(f"function {name}{params}")
        signature = " ".join(sig_parts)
        if return_type:
            signature += f": {return_type}"

        docstring = self._extract_php_docstring(node, source_bytes)
        full_name = f"{class_name}::{name}"

        return Symbol(
            name=full_name,
            kind="method",
            signature=signature,
            docstring=docstring,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
        )

    def _parse_php_property(self, node, source_bytes: bytes, class_name: str) -> Symbol:
        """Parse a PHP property declaration node."""
        prop_name = ""
        visibility = ""
        is_static = False
        prop_type = ""

        for child in node.children:
            if child.type == "visibility_modifier":
                visibility = get_node_text(child, source_bytes)
            elif child.type == "static_modifier":
                is_static = True
            elif child.type in ("named_type", "primitive_type", "optional_type"):
                prop_type = get_node_text(child, source_bytes)
            elif child.type == "property_element":
                for prop_child in child.children:
                    if prop_child.type == "variable_name":
                        prop_name = get_node_text(prop_child, source_bytes)

        # Build signature: [visibility] [static] [type] $name
        sig_parts = []
        if visibility:
            sig_parts.append(visibility)
        if is_static:
            sig_parts.append("static")
        if prop_type:
            sig_parts.append(prop_type)
        sig_parts.append(prop_name)
        signature = " ".join(sig_parts)

        full_name = f"{class_name}::{prop_name}"

        return Symbol(
            name=full_name,
            kind="property",
            signature=signature,
            docstring="",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
        )

    def _parse_php_class_header(
        self, node, source_bytes: bytes,
    ) -> tuple[str, str, list[str], bool, bool]:
        """Extract class name, extends, implements, is_abstract, is_final from a class node."""
        class_name, extends, implements, is_abstract, is_final = "", "", [], False, False
        for child in node.children:
            if child.type == "name":
                class_name = get_node_text(child, source_bytes)
            elif child.type == "abstract_modifier":
                is_abstract = True
            elif child.type == "final_modifier":
                is_final = True
            elif child.type == "base_clause":
                for bc_child in child.children:
                    if bc_child.type == "name":
                        extends = get_node_text(bc_child, source_bytes)
            elif child.type == "class_interface_clause":
                for ic_child in child.children:
                    if ic_child.type == "name":
                        implements.append(get_node_text(ic_child, source_bytes))
        return class_name, extends, implements, is_abstract, is_final

    @staticmethod
    def _build_php_class_signature(
        class_name: str, extends: str, implements: list[str], is_abstract: bool, is_final: bool,
    ) -> str:
        """Build PHP class signature string."""
        parts = []
        if is_abstract:
            parts.append("abstract")
        elif is_final:
            parts.append("final")
        parts.append(f"class {class_name}")
        if extends:
            parts.append(f"extends {extends}")
        if implements:
            parts.append(f"implements {', '.join(implements)}")
        return " ".join(parts)

    def _parse_php_class(
        self,
        node,
        source_bytes: bytes,
        namespace: str = "",
        use_map: dict[str, str] | None = None,
        inheritances: list[Inheritance] | None = None
    ) -> list[Symbol]:
        """Parse a PHP class definition node with extends, implements, properties and methods."""
        if use_map is None:
            use_map = {}
        if inheritances is None:
            inheritances = []

        class_name, extends, implements, is_abstract, is_final = self._parse_php_class_header(node, source_bytes)
        full_class_name = f"{namespace}\\{class_name}" if namespace else class_name

        if extends:
            parent_full = use_map.get(extends, f"{namespace}\\{extends}" if namespace else extends)
            inheritances.append(Inheritance(child=full_class_name, parent=parent_full))
        for iface in implements:
            iface_full = use_map.get(iface, f"{namespace}\\{iface}" if namespace else iface)
            inheritances.append(Inheritance(child=full_class_name, parent=iface_full))

        signature = self._build_php_class_signature(class_name, extends, implements, is_abstract, is_final)
        symbols = [Symbol(
            name=class_name, kind="class", signature=signature,
            docstring=self._extract_php_docstring(node, source_bytes),
            line_start=node.start_point[0] + 1, line_end=node.end_point[0] + 1,
        )]

        for child in node.children:
            if child.type == "declaration_list":
                for decl in child.children:
                    if decl.type == "property_declaration":
                        symbols.append(self._parse_php_property(decl, source_bytes, class_name))
                    elif decl.type == "method_declaration":
                        symbols.append(self._parse_php_method(decl, source_bytes, class_name))

        return symbols

    def _parse_php_include(self, node, source_bytes: bytes) -> Import | None:
        """Parse PHP include/require statements."""
        if node.type == "include_expression" or node.type == "require_expression":
            for child in node.children:
                if child.type == "string":
                    module = get_node_text(child, source_bytes)
                    # Remove quotes
                    module = module.strip('\'"')
                    return Import(module=module, names=[], is_from=False)
        return None

    def _parse_php_namespace(self, node, source_bytes: bytes) -> str:
        """Parse PHP namespace definition."""
        for child in node.children:
            if child.type == "namespace_name":
                return get_node_text(child, source_bytes)
        return ""

    def _parse_php_use_clause(self, clause_node, source_bytes: bytes, base_namespace: str) -> "Import | None":
        """Parse a single namespace_use_clause node into an Import."""
        module, alias = "", ""
        for child in clause_node.children:
            if child.type == "qualified_name":
                module = get_node_text(child, source_bytes)
            elif child.type == "name" and module:
                alias = get_node_text(child, source_bytes)
        if not module:
            return None
        if base_namespace:
            module = f"{base_namespace}\\{module}"
        return Import(module=module, names=[], is_from=True, alias=alias or None)

    def _parse_php_use_group(self, group_node, source_bytes: bytes, base_namespace: str) -> list["Import"]:
        """Parse a namespace_use_group node into a list of Imports."""
        imports: list = []
        for child in group_node.children:
            if child.type == "namespace_use_clause":
                name, alias = "", ""
                for cc in child.children:
                    if cc.type == "qualified_name":
                        name = get_node_text(cc, source_bytes)
                    elif cc.type == "name":
                        if not name:
                            name = get_node_text(cc, source_bytes)
                        else:
                            alias = get_node_text(cc, source_bytes)
                if name:
                    full_module = f"{base_namespace}\\{name}" if base_namespace else name
                    imports.append(Import(module=full_module, names=[], is_from=True, alias=alias or None))
        return imports

    def _parse_php_use(self, node, source_bytes: bytes) -> list[Import]:
        """Parse PHP use statement into Import objects."""
        imports: list[Import] = []
        base_namespace = ""
        for child in node.children:
            if child.type == "namespace_name":
                base_namespace = get_node_text(child, source_bytes)
            elif child.type == "namespace_use_clause":
                imp = self._parse_php_use_clause(child, source_bytes, base_namespace)
                if imp:
                    imports.append(imp)
            elif child.type == "namespace_use_group":
                imports.extend(self._parse_php_use_group(child, source_bytes, base_namespace))
        return imports

    # ==================== Call Extraction Methods ====================

    def _extract_php_class_body_calls(
        self, body_node, full_class_name: str, source_bytes: bytes,
        use_map: dict[str, str], namespace: str, parent_map: dict[str, str],
    ) -> list[Call]:
        """Extract calls from all method declarations in a PHP class body."""
        calls: list[Call] = []
        for method_node in body_node.children:
            if method_node.type == "method_declaration":
                method_name = next(
                    (get_node_text(n, source_bytes) for n in method_node.children if n.type == "name"), ""
                )
                if method_name:
                    calls.extend(self._extract_php_calls(
                        method_node, source_bytes, f"{full_class_name}::{method_name}",
                        use_map, namespace, parent_map, full_class_name,
                    ))
        return calls

    def _extract_php_calls_from_tree(
        self,
        tree,
        source_bytes: bytes,
        imports: list[Import],
        inheritances: list[Inheritance],
        namespace: str,
        use_map: dict[str, str]
    ) -> list[Call]:
        """Extract all PHP call relationships from parse tree."""
        parent_map = {inh.child: inh.parent for inh in inheritances}
        calls: list[Call] = []

        for child in tree.root_node.children:
            if child.type == "function_definition":
                func_name = next(
                    (get_node_text(n, source_bytes) for n in child.children if n.type == "name"), ""
                )
                if func_name:
                    caller = f"{namespace}\\{func_name}" if namespace else func_name
                    calls.extend(self._extract_php_calls(child, source_bytes, caller, use_map, namespace, parent_map, current_class=""))

            elif child.type == "class_declaration":
                class_name = next(
                    (get_node_text(n, source_bytes) for n in child.children if n.type == "name"), ""
                )
                if not class_name:
                    continue
                full_class_name = f"{namespace}\\{class_name}" if namespace else class_name
                body_node = next((n for n in child.children if n.type == "declaration_list"), None)
                if body_node:
                    calls.extend(self._extract_php_class_body_calls(body_node, full_class_name, source_bytes, use_map, namespace, parent_map))

        return calls

    def _dispatch_php_call_node(
        self, n: Node, source_bytes: bytes, caller: str,
        use_map: dict[str, str], namespace: str,
        parent_map: dict[str, str], current_class: str,
    ) -> "Call | None":
        """Dispatch a PHP AST node to its call parser. Returns Call or None."""
        t = n.type
        if t == "function_call_expression":
            return self._parse_php_function_call(n, source_bytes, caller, use_map, namespace)
        if t == "member_call_expression":
            return self._parse_php_member_call(n, source_bytes, caller, use_map, namespace, current_class)
        if t == "scoped_call_expression":
            return self._parse_php_scoped_call(n, source_bytes, caller, use_map, namespace, parent_map, current_class)
        if t == "object_creation_expression":
            return self._parse_php_object_creation(n, source_bytes, caller, use_map, namespace)
        return None

    def _extract_php_calls(
        self, node: Node, source_bytes: bytes, caller: str,
        use_map: dict[str, str], namespace: str,
        parent_map: dict[str, str], current_class: str,
    ) -> list[Call]:
        """Extract PHP calls from a function/method body."""
        calls = []
        stack = list(node.children)
        while stack:
            n = stack.pop()
            call = self._dispatch_php_call_node(n, source_bytes, caller, use_map, namespace, parent_map, current_class)
            if call:
                calls.append(call)
            stack.extend(n.children)
        return calls

    def _extract_php_name_and_args(self, node: Node, source_bytes: bytes) -> tuple:
        """Extract (name, args_count) from a PHP call/creation node."""
        name = None
        args_count = None
        for child in node.children:
            if child.type in ("name", "qualified_name"):
                name = get_node_text(child, source_bytes)
            elif child.type == "arguments":
                args_count = sum(1 for c in child.children if c.type not in (",", "(", ")"))
        return name, args_count

    def _parse_php_function_call(
        self, node: Node, source_bytes: bytes, caller: str,
        use_map: dict[str, str], namespace: str,
    ) -> Optional[Call]:
        """Parse PHP function call expression."""
        func_name, args_count = self._extract_php_name_and_args(node, source_bytes)
        if not func_name:
            return None
        if "\\" in func_name:
            callee = func_name.lstrip("\\")
        elif func_name in use_map:
            callee = use_map[func_name]
        else:
            callee = func_name
        return Call(caller=caller, callee=callee, line_number=node.start_point[0] + 1,
                    call_type=CallType.FUNCTION, arguments_count=args_count)

    def _resolve_php_member_callee(
        self, object_name: str | None, method_name: str, current_class: str, use_map: dict[str, str],
    ) -> tuple[str | None, "CallType"]:
        """Resolve PHP member call callee from object_name and method. Returns (callee, call_type)."""
        if object_name == "$this" and current_class:
            return f"{current_class}::{method_name}", CallType.METHOD
        if object_name and object_name.startswith("$"):
            class_name = object_name[1:].capitalize()
            full_class = use_map.get(class_name, class_name)
            return f"{full_class}::{method_name}", CallType.METHOD
        return None, CallType.DYNAMIC

    def _resolve_php_scope_callee(
        self, scope_name: str, method_name: str, current_class: str,
        use_map: dict[str, str], namespace: str, parent_map: dict[str, str],
    ) -> str | None:
        """Resolve PHP scoped call callee from scope name."""
        if scope_name in ("self", "static") and current_class:
            return f"{current_class}::{method_name}"
        if scope_name == "parent" and current_class:
            parent = parent_map.get(current_class, "parent")
            return f"{parent}::{method_name}"
        if not scope_name:
            return None
        if scope_name.startswith("\\"):
            return f"{scope_name.lstrip(chr(92))}::{method_name}"
        if scope_name in use_map:
            return f"{use_map[scope_name]}::{method_name}"
        if namespace:
            return f"{namespace}\\{scope_name}::{method_name}"
        return f"{scope_name}::{method_name}"

    def _parse_php_member_call(
        self, node: Node, source_bytes: bytes, caller: str,
        use_map: dict[str, str], namespace: str, current_class: str,
    ) -> Optional[Call]:
        """Parse PHP member call expression ($obj->method())."""
        object_name, method_name, args_count = None, None, None
        for child in node.children:
            if child.type == "variable_name" and not object_name:
                object_name = get_node_text(child, source_bytes)
            elif child.type == "name":
                method_name = get_node_text(child, source_bytes)
            elif child.type == "arguments":
                args_count = sum(1 for c in child.children if c.type not in (",", "(", ")"))

        if not method_name:
            return None

        callee, call_type = self._resolve_php_member_callee(object_name, method_name, current_class, use_map)
        return Call(caller=caller, callee=callee, line_number=node.start_point[0] + 1,
                    call_type=call_type, arguments_count=args_count)

    def _parse_php_scoped_call(
        self, node: Node, source_bytes: bytes, caller: str,
        use_map: dict[str, str], namespace: str, parent_map: dict[str, str], current_class: str,
    ) -> Optional[Call]:
        """Parse PHP scoped call expression (Class::method() or parent::method())."""
        scope_name, method_name, args_count = None, None, None
        for child in node.children:
            if child.type in ("name", "qualified_name", "relative_scope") and not scope_name:
                scope_name = get_node_text(child, source_bytes)
            elif child.type == "name" and scope_name:
                method_name = get_node_text(child, source_bytes)
            elif child.type == "arguments":
                args_count = sum(1 for c in child.children if c.type not in (",", "(", ")"))

        if not method_name:
            return None

        callee = self._resolve_php_scope_callee(scope_name, method_name, current_class, use_map, namespace, parent_map)
        if callee is None:
            return None
        return Call(caller=caller, callee=callee, line_number=node.start_point[0] + 1,
                    call_type=CallType.STATIC_METHOD, arguments_count=args_count)

    def _parse_php_object_creation(
        self, node: Node, source_bytes: bytes, caller: str,
        use_map: dict[str, str], namespace: str,
    ) -> Optional[Call]:
        """Parse PHP object creation expression (new Class())."""
        class_name, args_count = self._extract_php_name_and_args(node, source_bytes)

        if not class_name:
            return None

        # Skip anonymous classes
        if class_name == "class":
            return None

        # Resolve class name
        if class_name.startswith("\\"):
            # Fully qualified name
            full_class = class_name.lstrip("\\")
        elif class_name in use_map:
            # Resolve via use_map
            full_class = use_map[class_name]
        elif namespace:
            # Assume it's in current namespace
            full_class = f"{namespace}\\{class_name}"
        else:
            full_class = class_name

        # Constructor call
        callee = f"{full_class}::__construct"

        return Call(
            caller=caller,
            callee=callee,
            line_number=node.start_point[0] + 1,
            call_type=CallType.CONSTRUCTOR,
            arguments_count=args_count
        )
