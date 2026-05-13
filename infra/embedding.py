from __future__ import annotations

from typing import Protocol, runtime_checkable

import requests as _requests


@runtime_checkable
class EmbedderProtocol(Protocol):
    """Interface for any embedding backend.

    Implement this protocol to swap in a different embedding provider
    (e.g. SentenceTransformer, OpenAI, Azure) without changing any consumer.
    Wire the concrete instance in web/__init__.py.
    """

    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def get_config(self) -> dict: ...


class Embedder:
    """OpenAI-compatible embeddings client (/v1/embeddings).

    Works with any server that exposes the OpenAI embeddings API
    (e.g. Ollama ≥ 0.1.24, LM Studio, OpenAI, vLLM, …).
    Point ``base_url`` at whichever server is running.
    """

    def __init__(
        self, model_name: str, base_url: str = "http://localhost:11434"
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = _requests.post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model_name, "input": texts},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        # sort by index to guarantee order
        data.sort(key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    def get_config(self) -> dict:
        return {"model_name": self.model_name, "base_url": self.base_url}
