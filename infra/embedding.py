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
    """OpenAI-compatible embeddings client ({base_url}/embeddings).

    Works with any provider that speaks the OpenAI Embeddings API
    (Ollama /v1, OpenAI, Azure, LM Studio, vLLM, …).

    ``batch_size`` controls how many texts are sent per HTTP request.
    Smaller batches avoid timeouts on slow/weak machines.
    """

    def __init__(
        self,
        model_name: str,
        base_url: str,
        batch_size: int = 8,
        timeout: int = 120,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, max(len(texts), 1), self.batch_size):
            batch = texts[i : i + self.batch_size]
            if not batch:
                break
            resp = _requests.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model_name, "input": batch},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            results.extend(item["embedding"] for item in resp.json()["data"])
        return results

    def get_config(self) -> dict:
        return {
            "model_name": self.model_name,
            "base_url": self.base_url,
            "batch_size": self.batch_size,
            "timeout": self.timeout,
        }
