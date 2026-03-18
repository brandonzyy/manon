"""Base class for language parsers.

This module defines the abstract interface that all language-specific parsers must implement.
It provides a consistent API for parsing different programming languages using tree-sitter.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from tree_sitter import Parser, Tree


class BaseLanguageParser(ABC):
    """Abstract base class for language-specific parsers.

    All language parsers (Python, PHP, Java, etc.) should inherit from this class
    and implement the required abstract methods.

    Attributes:
        parser: The tree-sitter Parser instance for this language
    """

    def __init__(self, parser: Parser):
        """Initialize the language parser.

        Args:
            parser: A configured tree-sitter Parser for this language
        """
        self.parser = parser

    @abstractmethod
    def extract_symbols(self, tree: Tree, source_bytes: bytes) -> list:
        """Extract symbols (classes, functions, methods) from the parse tree.

        Args:
            tree: The tree-sitter parse tree
            source_bytes: The source code as bytes

        Returns:
            List of Symbol objects
        """
        pass

    @abstractmethod
    def extract_imports(self, tree: Tree, source_bytes: bytes) -> list:
        """Extract import statements from the parse tree.

        Args:
            tree: The tree-sitter parse tree
            source_bytes: The source code as bytes

        Returns:
            List of Import objects
        """
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    def extract_inheritances(self, tree: Tree, source_bytes: bytes) -> list:
        """Extract class inheritance relationships from the parse tree.

        Args:
            tree: The tree-sitter parse tree
            source_bytes: The source code as bytes

        Returns:
            List of Inheritance objects
        """
        pass

    def _build_parse_result(self, tree, path: Path, source_bytes: bytes, file_lines: int):
        """Extract all parse content and return a ParseResult."""
        from ..parser import ParseResult
        try:
            symbols = self.extract_symbols(tree, source_bytes)
            imports = self.extract_imports(tree, source_bytes)
            inheritances = self.extract_inheritances(tree, source_bytes)
            calls = self.extract_calls(tree, source_bytes, symbols, imports)
            return ParseResult(path=path, symbols=symbols, imports=imports,
                               inheritances=inheritances, calls=calls, file_lines=file_lines)
        except Exception as e:
            return ParseResult(path=path, error=f"Parse error: {str(e)}", file_lines=file_lines)

    def parse(self, path: Path):
        """Parse a source file using this language parser."""
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
            return ParseResult(path=path, error="Syntax error in source file", file_lines=file_lines)
        return self._build_parse_result(tree, path, source_bytes, file_lines)
