#!/usr/bin/env python
"""Launch the Manon MCP server."""
import os

# Clear proxy env vars so Manon connects directly to the API.
# Claude Code's MCP env block may not reliably override inherited vars.
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
           "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

from manon_mcp.server import mcp

if __name__ == "__main__":
    mcp.run()
