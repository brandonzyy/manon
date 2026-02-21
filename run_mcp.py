#!/usr/bin/env python
"""Launch Manon MCP server with correct module resolution.

The project has a local `mcp/` directory that shadows the installed `mcp`
package. This launcher pre-imports the installed package into sys.modules
cache before adding the project root to sys.path (needed for `shared`).
"""
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# Remove project root from sys.path — local mcp/ directory shadows the
# installed mcp package.  Python auto-adds the script's directory as
# sys.path[0]; we strip it, import the real package, then re-add it.
sys.path = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != os.path.normcase(ROOT)]

# Pre-import installed mcp package into sys.modules cache
import mcp                    # noqa: E402
import mcp.server             # noqa: E402
import mcp.server.fastmcp     # noqa: E402

# Re-add project root so `shared` is importable
sys.path.insert(0, ROOT)

# Import and run the server module directly via importlib
# (avoids `from mcp.server import ...` re-resolution issues)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "manon_mcp_server",
    os.path.join(ROOT, "mcp", "server.py"),
    submodule_search_locations=[],
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.mcp.run()
