"""Tenant-isolated MatrixoneGraph wrapper."""
from __future__ import annotations

import sys
from pathlib import Path

# ensure project root is importable so `matrixone_graph` resolves
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from matrixone_graph import MatrixoneGraph  # noqa: E402

from ..config import settings

# Configure embedding URL once at import time
MatrixoneGraph.configure(embedding_url=settings.embedding_url)


def get_graph(tenant_id: str, repo_path: str | None, *, repo_name: str = "") -> MatrixoneGraph:
    """Return a MatrixoneGraph instance with tenant-isolated index storage.

    Indexes land in: {index_dir}/{tenant_id}/{repo_key}/kg/

    For local-sync repos (no local_path on server), pass repo_name instead.
    """
    name = repo_name or (Path(repo_path).name if repo_path else "unknown")
    tenant_index_dir = Path(settings.index_dir) / tenant_id
    tenant_index_dir.mkdir(parents=True, exist_ok=True)

    kg_dir = tenant_index_dir / name / "kg"
    kg_dir.mkdir(parents=True, exist_ok=True)

    # Use repo_path if available, otherwise use kg_dir as a dummy path
    mg = MatrixoneGraph.get(repo_path or str(kg_dir))
    mg.kg_path = kg_dir
    return mg
