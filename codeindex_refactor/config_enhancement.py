"""Enhanced Config with automatic language detection and parser installation.

Add these methods to your existing Config class:
"""
from pathlib import Path
from .detector import quick_detect_languages
from .parser_installer import install_parsers
from .parser import FILE_EXTENSIONS


class Config:
    # ... existing code ...

    @classmethod
    def load_with_auto_setup(cls, root: Path) -> "Config":
        """Load config with automatic language detection and parser installation.

        This is the recommended entry point for scanning projects.

        Workflow:
        1. Quick language detection (lightweight, no AST parsing)
        2. Install missing tree-sitter parsers
        3. Load or generate scan configuration

        Args:
            root: Project root directory

        Returns:
            Config instance ready for scanning
        """
        # Phase 1: Detect languages
        detected_langs = quick_detect_languages(root, FILE_EXTENSIONS)

        # Phase 2: Install parsers
        if detected_langs:
            install_parsers(detected_langs)

        # Phase 3: Load or generate config
        config_file = root / ".codeindex.yaml"
        if config_file.exists():
            config = cls.load(config_file)
        else:
            # No config file - use smart defaults
            config = cls._auto_config(root, detected_langs)

        return config

    @classmethod
    def _auto_config(cls, root: Path, languages: set[str]) -> "Config":
        """Generate smart default config based on detected languages.

        Args:
            root: Project root
            languages: Detected languages

        Returns:
            Config with smart defaults
        """
        # Default: scan everything, rely on exclude rules
        include = ["."]

        # Smart exclude based on detected languages
        exclude = [
            # Universal
            "**/.git/**", "**/node_modules/**", "**/__pycache__/**",
        ]

        if "python" in languages:
            exclude.extend([
                "**/.venv/**", "**/venv/**", "**/.tox/**",
                "**/*.egg-info/**", "**/dist/**", "**/build/**",
            ])

        if "javascript" in languages or "typescript" in languages:
            exclude.extend([
                "**/.next/**", "**/.nuxt/**", "**/dist/**",
                "**/build/**", "**/.turbo/**",
            ])

        if "java" in languages:
            exclude.extend([
                "**/target/**", "**/.gradle/**", "**/.m2/**",
            ])

        return cls(include=include, exclude=exclude)
