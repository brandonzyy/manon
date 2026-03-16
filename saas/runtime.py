"""Runtime path helpers for local SaaS artifacts."""
from __future__ import annotations

import os
from pathlib import Path


def runtime_root() -> Path:
    """Return the default local runtime root for SaaS state."""
    explicit = os.environ.get("SAAS_RUNTIME_ROOT") or os.environ.get("MANON_RUNTIME_DIR")
    if explicit:
        return Path(explicit)
    return Path(".manon_runtime") / "saas"


RUNTIME_ROOT = runtime_root()

