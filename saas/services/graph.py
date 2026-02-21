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


def get_graph(tenant_id: str, repo_path: str) -> MatrixoneGraph:
    """Return a MatrixoneGraph instance with tenant-isolated index storage.

    Indexes land in: {index_dir}/{tenant_id}/{repo_key}/kg/
    """
    repo_name = Path(repo_path).name
    tenant_index_dir = Path(settings.index_dir) / tenant_id
    tenant_index_dir.mkdir(parents=True, exist_ok=True)

    # Temporarily override class-level _data_dir for this instance
    mg = MatrixoneGraph.get(repo_path)
    # Patch the instance's kg_dir to tenant-scoped path
    kg_dir = tenant_index_dir / repo_name / "kg"
    kg_dir.mkdir(parents=True, exist_ok=True)
    mg._kg_dir = kg_dir
    return mg
