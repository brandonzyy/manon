"""HTTP client wrapper for Manon SaaS API."""
from __future__ import annotations

import json
import httpx


class ManonClient:
    def __init__(self, base_url: str, api_key: str):
        self.base = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120,
        )

    # ── Repos ──────────────────────────────────────────
    def create_repo(self, name: str, *, git_url: str = "", branch: str = "main", local_path: str | None = None) -> dict:
        body: dict = {"name": name, "branch": branch}
        if git_url:
            body["git_url"] = git_url
        if local_path:
            body["local_path"] = local_path
        return self._post("/api/v1/repos", body)

    def list_repos(self) -> list[dict]:
        return self._get("/api/v1/repos")

    def get_repo(self, repo_id: str) -> dict:
        return self._get(f"/api/v1/repos/{repo_id}")

    def delete_repo(self, repo_id: str) -> None:
        self._delete(f"/api/v1/repos/{repo_id}")

    # ── Indexing ───────────────────────────────────────
    def trigger_index(self, repo_id: str, *, incremental: bool = True) -> dict:
        return self._post(f"/api/v1/repos/{repo_id}/index", {"incremental": incremental})

    def index_status(self, repo_id: str) -> dict:
        return self._get(f"/api/v1/repos/{repo_id}/index-status")

    def push_update(self, repo_id: str) -> dict:
        return self._post(f"/api/v1/repos/{repo_id}/push-update", {})

    # ── Query ──────────────────────────────────────────
    def search(self, repo_id: str, query: str, *, top_k: int = 10, depth: int = 1) -> dict:
        return self._get(f"/api/v1/repos/{repo_id}/search", q=query, top_k=top_k, depth=depth)

    def graph(self, repo_id: str, symbol: str, *, depth: int = 1) -> dict:
        return self._get(f"/api/v1/repos/{repo_id}/graph", symbol=symbol, depth=depth)

    def impact(self, repo_id: str, *, commit: str = "HEAD", max_depth: int = 2) -> dict:
        return self._get(f"/api/v1/repos/{repo_id}/impact", commit=commit, max_depth=max_depth)

    # ── Usage ──────────────────────────────────────────
    def usage(self, *, days: int = 30) -> dict:
        return self._get("/api/v1/usage", days=days)

    # ── HTTP helpers ───────────────────────────────────
    def _get(self, path: str, **params) -> dict | list:
        r = self._client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = self._client.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> None:
        r = self._client.delete(path)
        r.raise_for_status()

    def close(self):
        self._client.close()
