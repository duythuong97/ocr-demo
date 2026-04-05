"""Proxy chat provider.

Sends requests to a local proxy at PROXY_CHAT_URL that accepts:

    POST /api/lmstudio/chat
    {
        "model": {"id": "...", "name": "..."},
        "prompt": "<system prompt>",
        "messages": [
            {"role": "user"|"assistant", "content": [{"type": "text", "text": "..."}]}
        ]
    }

The proxy returns plain text with Transfer-Encoding: chunked, so we stream
it by reading HTTP chunks as they arrive.
"""

from __future__ import annotations

import logging
from typing import Generator

import requests

logger = logging.getLogger("app")


class ProxyChatError(Exception):
    """Raised when the proxy returns an error or is unreachable."""


class ProxyChatClient:
    """Thin streaming client for the local LLM proxy."""

    def __init__(self, url: str, timeout: int = 120) -> None:
        self.url = url
        self.timeout = timeout

    def is_available(self) -> bool:
        """Lightweight reachability check (HEAD on base host)."""
        try:
            from urllib.parse import urlparse

            parsed = urlparse(self.url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            requests.head(base, timeout=5)
            return True
        except Exception:
            return False

    def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict],
        model_id: str = "qwen/qwen3-14b",
        model_name: str = "qwen/qwen3-14b",
    ) -> Generator[str, None, None]:
        """Stream the assistant reply as text chunks.

        `messages` should be a list of ``{"role": ..., "content": ...}`` dicts
        where content is already a plain string (this method converts to the
        proxy array format internally).
        """
        proxy_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            proxy_messages.append({"role": role, "content": content})

        payload = {
            "model": {"id": model_id, "name": model_name},
            "prompt": system_prompt,
            "messages": proxy_messages,
        }

        logger.debug(
            "ProxyChatClient: POST %s model=%r messages=%d",
            self.url,
            model_id,
            len(proxy_messages),
        )

        try:
            yielded_any = False
            with requests.post(
                self.url,
                json=payload,
                stream=True,
                timeout=self.timeout,
            ) as res:
                logger.debug("Proxy response status: %s", res.status_code)
                res.raise_for_status()
                for chunk in res.iter_content(chunk_size=None):
                    if not chunk:
                        continue
                    text = chunk.decode("utf-8", errors="replace")
                    if not text:
                        continue
                    yielded_any = True
                    yield text

            if not yielded_any:
                # Some proxy implementations occasionally close a chunked
                # response with HTTP 200 but no body. Retry once in non-stream
                # mode to avoid silent empty answers.
                logger.warning(
                    "Proxy returned empty streamed body; retrying once in non-stream mode"
                )
                retry = requests.post(self.url, json=payload, timeout=self.timeout)
                retry.raise_for_status()
                text = retry.text or ""
                if text.strip():
                    yield text
                else:
                    raise ProxyChatError(
                        "Proxy returned HTTP 200 but an empty response body."
                    )
        except requests.exceptions.ConnectionError as exc:
            logger.error("Proxy connection failed: url=%s error=%s", self.url, exc)
            raise ProxyChatError(
                f"Cannot connect to proxy at {self.url}. Make sure the proxy is running."
            ) from exc
        except requests.exceptions.Timeout as exc:
            logger.error(
                "Proxy request timed out: url=%s timeout=%ss", self.url, self.timeout
            )
            raise ProxyChatError(
                f"Proxy request timed out after {self.timeout}s."
            ) from exc
        except requests.exceptions.HTTPError as exc:
            body = ""
            try:
                body = exc.response.text[:500]
            except Exception:
                pass
            logger.error(
                "Proxy HTTP error: status=%s body=%r", exc.response.status_code, body
            )
            raise ProxyChatError(
                f"Proxy returned HTTP {exc.response.status_code}: {body}"
            ) from exc
