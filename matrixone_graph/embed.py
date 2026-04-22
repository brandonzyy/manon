"""Embedding HTTP client for OpenAI-compatible / TEI endpoints."""

from __future__ import annotations

import httpx


class EmbeddingClient:
    """Async HTTP client that calls a text-embedding endpoint.

    Supports two modes:
    - OpenAI-compatible (default): POST /embeddings with model + input
    - Legacy TEI: POST /embed with inputs (when model is not set)
    """

    def __init__(
        self,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        *,
        model: str = "",
        api_key: str = "",
        batch_size: int = 32,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
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
        if self.model:
            return await self._post_openai(client, texts)
        return await self._post_tei(client, texts)

    async def _post_openai(self, client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
        """OpenAI-compatible /embeddings endpoint (GLM, OpenAI, etc.)."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = await client.post(
            f"{self.base_url}/embeddings",
            headers=headers,
            json={"model": self.model, "input": texts},
        )
        resp.raise_for_status()
        body = resp.json()
        items = sorted(body["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]

    async def _post_tei(self, client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
        """Legacy TEI /embed endpoint."""
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
