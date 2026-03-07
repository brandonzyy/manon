"""Usage examples for the refactored codeindex.

Example 1: Basic usage with auto-setup
--------------------------------------
from pathlib import Path
from codeindex.config import Config
from codeindex.scanner import scan_directory

# Automatic language detection + parser installation + smart config
root = Path(".")
config = Config.load_with_auto_setup(root)

# Now scan with the configured settings
result = scan_directory(root, config, root)
print(f"Found {len(result.files)} files")


Example 2: Manual language detection
------------------------------------
from codeindex.detector import quick_detect_languages
from codeindex.parser import FILE_EXTENSIONS

root = Path(".")
langs = quick_detect_languages(root, FILE_EXTENSIONS)
print(f"Detected languages: {langs}")


Example 3: Manual parser installation
-------------------------------------
from codeindex.parser_installer import install_parsers

langs = {"python", "javascript", "typescript"}
results = install_parsers(langs)
for lang, status in results.items():
    print(f"{lang}: {status}")


Migration Guide for Existing Code
---------------------------------

Before (old way):
    from codeindex.config import Config
    config = Config.load(Path(".codeindex.yaml"))
    # Problem: parsers might not be installed, languages not detected

After (new way):
    from codeindex.config import Config
    config = Config.load_with_auto_setup(Path("."))
    # Automatically detects languages, installs parsers, loads config
"""
