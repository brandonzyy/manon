"""Embedding HTTP client for TEI / OpenAI-compatible endpoints."""

from __future__ import annotations

import httpx


class EmbeddingClient:
    """Async HTTP client that calls a text-embedding endpoint.

    Supports both Jina TEI format (returns list[list[float]]) and
    OpenAI-compatible format (returns {data: [{embedding: [...]}]}).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        batch_size: int = 32,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        all_vecs: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            vecs = await self._post_batch(batch)
            all_vecs.extend(vecs)
        return all_vecs

    async def embed_single(self, text: str) -> list[float]:
        vecs = await self._post_batch([text])
        return vecs[0]

    async def _post_batch(self, texts: list[str]) -> list[list[float]]:
        client = await self._get_client()
        resp = await client.post(
            f"{self.base_url}/embed",
            json={"inputs": texts},
        )
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, list):
            return body
        if isinstance(body, dict) and "data" in body:
            return [item["embedding"] for item in body["data"]]
        raise ValueError(f"Unexpected embedding response format: {type(body)}")

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
