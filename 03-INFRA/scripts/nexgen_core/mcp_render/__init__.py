"""One module per CLI dialect for MCP configuration rendering.

`McpRenderer` (`nexgen_core.renderer`) stays the facade: manifest loading,
server resolution, timeouts plumbing and the `render_all` fan-out. Each
module here owns exactly one CLI's native schema -- the part that breaks
every time a vendor ships a v2. Import direction is one-way: these modules
never import the facade back; shared constants live in this `__init__`.
"""
from __future__ import annotations

import sys

#: Windows detection shared by every dialect module (shim names, junctions).
IS_WINDOWS = sys.platform == "win32"

#: Pinned bridge for CLIs without native remote support.
MCP_REMOTE_PACKAGE = "mcp-remote@0.1.38"
