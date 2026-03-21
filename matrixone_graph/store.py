"""Storage layer for MatrixoneGraph.

Provides CodeGraph (NetworkX DiGraph wrapper) and VectorIndex (numpy cosine similarity)
for persisting and querying code entities, relations, and chunks.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    """A code entity node in the knowledge graph."""
    id: str
    kind: str  # class, function, method, module
    name: str
    description: str = ""
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0
    decorators: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Entity:
        return Entity(**{k: v for k, v in d.items() if k in Entity.__dataclass_fields__})


@dataclass
class Relation:
    """A directed edge between two entities."""
    src_id: str
    tgt_id: str
    kind: str  # calls, imports, inherits
    description: str = ""
    file_path: str = ""
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Relation:
        return Relation(**{k: v for k, v in d.items() if k in Relation.__dataclass_fields__})

@dataclass
class Chunk:
    """A code chunk (symbol-level or block-level)."""
    id: str
    content: str
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0
    symbol_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Chunk:
        return Chunk(**{k: v for k, v in d.items() if k in Chunk.__dataclass_fields__})


# ---------------------------------------------------------------------------
# CodeGraph — NetworkX DiGraph wrapper
# ---------------------------------------------------------------------------

class CodeGraph:
    """Thin wrapper around NetworkX DiGraph for entity/relation storage."""

    def __init__(self) -> None:
        self._g = nx.DiGraph()

    def add_entity(self, entity: Entity) -> None:
        self._g.add_node(entity.id, **entity.to_dict())

    def get_entity(self, entity_id: str) -> Entity | None:
        if entity_id not in self._g:
            return None
        data = dict(self._g.nodes[entity_id])
        if not data.get("kind"):  # phantom node (auto-created by add_edge)
            return None
        data["id"] = entity_id  # NetworkX strips id after node_link_graph load
        return Entity.from_dict(data)

    def has_entity(self, entity_id: str) -> bool:
        return entity_id in self._g

    def add_relation(self, rel: Relation) -> None:
        self._g.add_edge(rel.src_id, rel.tgt_id, **rel.to_dict())
        # Track which file is responsible for each phantom node created by this edge.
        # When that file is later removed/updated, we can surgically clean up its phantoms.
        if rel.file_path:
            for node_id in (rel.src_id, rel.tgt_id):
                d = self._g.nodes[node_id]
                if not d.get("kind"):  # phantom (auto-created by add_edge, no real entity)
                    rf = d.get("responsible_files")
                    if rf is None:
                        self._g.nodes[node_id]["responsible_files"] = [rel.file_path]
                    elif rel.file_path not in rf:
                        rf.append(rel.file_path)

    def remove_by_file(self, file_path: str) -> None:
        to_remove = [
            n for n, d in self._g.nodes(data=True)
            if d.get("file_path") == file_path
        ]
        self._g.remove_nodes_from(to_remove)
        # Decrement responsible_files on surviving phantom nodes.
        # A phantom whose last responsible file was just removed is deleted.
        phantoms_to_remove = []
        for n, d in self._g.nodes(data=True):
            if d.get("kind"):
                continue
            rf = d.get("responsible_files", [])
            if file_path in rf:
                rf.remove(file_path)
                if not rf:
                    phantoms_to_remove.append(n)
        if phantoms_to_remove:
            self._g.remove_nodes_from(phantoms_to_remove)

    def neighbors(self, entity_id: str, depth: int = 1, direction: str = "both") -> list[tuple[Entity, list[Relation]]]:
        if entity_id not in self._g:
            return []
        visited: set[str] = {entity_id}
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])
        results: list[tuple[Entity, list[Relation]]] = []
        while queue:
            nid, d = queue.popleft()
            if d >= depth:
                continue
            if direction == "callers":
                neighbor_set = set(self._g.predecessors(nid))
            elif direction == "callees":
                neighbor_set = set(self._g.successors(nid))
            else:
                neighbor_set = set(self._g.successors(nid)) | set(self._g.predecessors(nid))
            for neighbor in neighbor_set:
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                ent = self.get_entity(neighbor)
                is_phantom = ent is None
                if is_phantom:
                    # External reference (e.g. path.join, fs.readFile) — no AST data
                    # but still has edges worth reporting
                    ent = Entity(id=neighbor, kind="external", name=neighbor.rsplit(".", 1)[-1])
                rels: list[Relation] = []
                for u, v, edata in self._g.edges([nid, neighbor], data=True):
                    if (u == nid and v == neighbor) or (u == neighbor and v == nid):
                        if "src_id" in edata:
                            rels.append(Relation.from_dict(edata))
                results.append((ent, rels))
                if not is_phantom:
                    queue.append((neighbor, d + 1))
        return results

    def files_indexed(self) -> set[str]:
        return {d.get("file_path", "") for _, d in self._g.nodes(data=True) if d.get("file_path")}

    @property
    def entity_count(self) -> int:
        """Count of real entities only (excludes phantom nodes)."""
        return sum(1 for _, d in self._g.nodes(data=True) if d.get("kind"))

    @property
    def phantom_count(self) -> int:
        """Count of phantom nodes (unresolved external references)."""
        return sum(1 for _, d in self._g.nodes(data=True) if not d.get("kind"))

    def prune_phantoms(self) -> int:
        """Remove phantom nodes not directly adjacent to any real entity.

        Phantoms that only connect to other phantoms are dead weight —
        no real entity references them, so they can never be resolved.
        Called as a safety net after final sync batch.
        """
        real_ids = {n for n, d in self._g.nodes(data=True) if d.get("kind")}
        dead = [
            n for n, d in self._g.nodes(data=True)
            if not d.get("kind")
            and not (set(self._g.predecessors(n)) | set(self._g.successors(n))) & real_ids
        ]
        if dead:
            self._g.remove_nodes_from(dead)
        return len(dead)

    @property
    def relation_count(self) -> int:
        return self._g.number_of_edges()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = nx.node_link_data(self._g)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        # NetworkX 3.2+ changed default key from "links" to "edges";
        # ensure both formats load correctly
        if "links" in data and "edges" not in data:
            data["edges"] = data.pop("links")
        try:
            self._g = nx.node_link_graph(data, directed=True)
        except Exception:
            # Fallback: try without directed kwarg (API changed in nx 3.4)
            try:
                self._g = nx.node_link_graph(data)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# VectorIndex — numpy cosine-similarity search
# ---------------------------------------------------------------------------

class VectorIndex:
    """In-memory vector index backed by numpy arrays (float32)."""

    def __init__(self) -> None:
        self._entity_ids: list[str] = []
        self._entity_vecs: np.ndarray | None = None
        self._chunk_ids: list[str] = []
        self._chunk_vecs: np.ndarray | None = None

    def add_entity_vectors(self, ids: list[str], vecs: list[list[float]]) -> None:
        if not ids:
            return
        new = np.array(vecs, dtype=np.float32)
        self._entity_ids.extend(ids)
        self._entity_vecs = (
            np.vstack([self._entity_vecs, new]) if self._entity_vecs is not None else new
        )

    def add_chunk_vectors(self, ids: list[str], vecs: list[list[float]]) -> None:
        if not ids:
            return
        new = np.array(vecs, dtype=np.float32)
        self._chunk_ids.extend(ids)
        self._chunk_vecs = (
            np.vstack([self._chunk_vecs, new]) if self._chunk_vecs is not None else new
        )

    def remove_by_ids(self, ids_to_remove: set[str]) -> None:
        self._entity_ids, self._entity_vecs = self._filter(self._entity_ids, self._entity_vecs, ids_to_remove)
        self._chunk_ids, self._chunk_vecs = self._filter(self._chunk_ids, self._chunk_vecs, ids_to_remove)

    @staticmethod
    def _filter(ids, vecs, remove):
        if vecs is None or not ids:
            return [], None
        keep = [i for i, eid in enumerate(ids) if eid not in remove]
        if not keep:
            return [], None
        return [ids[i] for i in keep], vecs[np.array(keep)]

    def _cosine_topk(self, query, matrix, ids, top_k):
        if matrix is None or len(ids) == 0:
            return []
        q = query.astype(np.float32).reshape(1, -1)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True).clip(min=1e-10)
        q_norm = np.linalg.norm(q).clip(min=1e-10)
        scores = (matrix @ q.T).squeeze() / (norms.squeeze() * q_norm)
        k = min(top_k, len(ids))
        if k >= len(ids):
            top_idx = np.argsort(-scores)[:k]
        else:
            top_idx = np.argpartition(-scores, k)[:k]
            top_idx = top_idx[np.argsort(-scores[top_idx])]
        return [(ids[i], float(scores[i])) for i in top_idx]

    def search_entities(self, query_vec, top_k=10):
        return self._cosine_topk(np.array(query_vec), self._entity_vecs, self._entity_ids, top_k)

    def search_chunks(self, query_vec, top_k=10):
        return self._cosine_topk(np.array(query_vec), self._chunk_vecs, self._chunk_ids, top_k)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        arrays = {}
        if self._entity_vecs is not None:
            arrays["entity_vecs"] = self._entity_vecs
        if self._chunk_vecs is not None:
            arrays["chunk_vecs"] = self._chunk_vecs
        np.savez_compressed(path, **arrays)
        meta_path = path.with_suffix(".ids.json")
        meta_path.write_text(json.dumps({
            "entity_ids": self._entity_ids, "chunk_ids": self._chunk_ids,
        }, ensure_ascii=False), encoding="utf-8")

    def load(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            data = np.load(path, allow_pickle=False)
        except Exception:
            # Corrupted vectors file — remove and rebuild from scratch
            path.unlink(missing_ok=True)
            path.with_suffix(".ids.json").unlink(missing_ok=True)
            return
        self._entity_vecs = data["entity_vecs"] if "entity_vecs" in data else None
        self._chunk_vecs = data["chunk_vecs"] if "chunk_vecs" in data else None
        meta_path = path.with_suffix(".ids.json")
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self._entity_ids = meta.get("entity_ids", [])
            self._chunk_ids = meta.get("chunk_ids", [])

    @property
    def entity_count(self):
        return len(self._entity_ids)

    @property
    def chunk_count(self):
        return len(self._chunk_ids)

