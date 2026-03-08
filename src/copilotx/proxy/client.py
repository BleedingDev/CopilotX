"""Async HTTP client for the GitHub Copilot backend API."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from copilotx.config import (
    COPILOT_API_BASE_FALLBACK,
    COPILOT_CHAT_COMPLETIONS_PATH,
    COPILOT_HEADERS,
    COPILOT_MODELS_PATH,
    COPILOT_RESPONSES_PATH,
    MODELS_CACHE_TTL,
    REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RateLimitSnapshot:
    """Best-effort upstream request quota visibility."""

    limit: int | None = None
    remaining: int | None = None
    reset_at: float | None = None
    retry_after_seconds: float | None = None
    source: str = ""
    observed_at: float = 0.0

    @property
    def known(self) -> bool:
        return any(
            value is not None for value in (self.limit, self.remaining, self.reset_at)
        )


class CopilotClient:
    """Async client that talks to the Copilot API (dynamic base URL)."""

    def __init__(self, copilot_token: str, api_base_url: str = "") -> None:
        self._token = copilot_token
        self._api_base = (api_base_url or COPILOT_API_BASE_FALLBACK).rstrip("/")
        self._client: httpx.AsyncClient | None = None
        # Model cache
        self._models_cache: list[dict] | None = None
        self._models_cache_time: float = 0
        self._last_rate_limit: RateLimitSnapshot | None = None

    async def __aenter__(self) -> "CopilotClient":
        self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client:
            await self._client.aclose()

    def update_token(self, token: str) -> None:
        """Update the Copilot JWT (called after token refresh)."""
        self._token = token

    def update_api_base(self, api_base_url: str) -> None:
        """Update the API base URL (called after token refresh if changed)."""
        if api_base_url:
            self._api_base = api_base_url.rstrip("/")

    @property
    def last_rate_limit(self) -> RateLimitSnapshot | None:
        return self._last_rate_limit

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            **COPILOT_HEADERS,
        }
        if extra:
            h.update(extra)
        return h

    # ── Models ──────────────────────────────────────────────────────

    async def list_models(self) -> list[dict]:
        """GET /models — returns list of available models (cached)."""
        now = time.time()
        if self._models_cache and (now - self._models_cache_time) < MODELS_CACHE_TTL:
            return self._models_cache

        assert self._client is not None
        url = f"{self._api_base}{COPILOT_MODELS_PATH}"
        resp = await self._client.get(url, headers=self._headers())
        self._record_rate_limit(resp.headers)
        resp.raise_for_status()
        data = resp.json()

        models = [
            m
            for m in data.get("data", data.get("models", []))
            if m.get("model_picker_enabled", True)
        ]
        self._models_cache = models
        self._models_cache_time = now
        return models

    # ── Chat Completions (non-streaming) ────────────────────────────

    async def chat_completions(self, payload: dict) -> dict:
        """POST /chat/completions — non-streaming."""
        assert self._client is not None
        url = f"{self._api_base}{COPILOT_CHAT_COMPLETIONS_PATH}"
        resp = await self._client.post(url, json=payload, headers=self._headers())
        self._record_rate_limit(resp.headers)
        if resp.status_code >= 400:
            error_body = resp.text
            logger.error(
                "Chat completions error: status=%d body=%s",
                resp.status_code, error_body[:1000],
            )
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}: {error_body[:500]}",
                request=resp.request,
                response=resp,
            )
        return resp.json()

    # ── Chat Completions (streaming) ────────────────────────────────

    async def chat_completions_stream(self, payload: dict) -> AsyncIterator[bytes]:
        """POST /chat/completions with stream=true — yields raw SSE lines."""
        assert self._client is not None
        payload["stream"] = True
        url = f"{self._api_base}{COPILOT_CHAT_COMPLETIONS_PATH}"

        async with self._client.stream(
            "POST", url, json=payload, headers=self._headers(),
        ) as resp:
            self._record_rate_limit(resp.headers)
            if resp.status_code >= 400:
                error_body = await resp.aread()
                logger.error(
                    "Chat completions stream error: status=%d body=%s",
                    resp.status_code, error_body[:1000],
                )
                error_text = error_body.decode("utf-8", errors="replace")[:500]
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}: {error_text}",
                    request=resp.request,
                    response=resp,
                )
            async for line in resp.aiter_lines():
                # Yield ALL lines including empty ones — empty lines are
                # SSE event delimiters and MUST be preserved for clients
                # (e.g. OpenAI Python SDK) that rely on them to separate
                # JSON chunks.
                yield (line + "\n").encode("utf-8")

    # ── Responses API (non-streaming) ───────────────────────────────

    async def responses(
        self,
        payload: dict,
        *,
        vision: bool = False,
        initiator: str = "user",
    ) -> dict:
        """POST /responses — OpenAI Responses API (non-streaming)."""
        assert self._client is not None
        url = f"{self._api_base}{COPILOT_RESPONSES_PATH}"
        extra_headers = self._responses_extra_headers(vision, initiator)
        # Strip service_tier — not supported by GitHub Copilot
        payload.pop("service_tier", None)

        logger.debug("Responses API request: url=%s payload_keys=%s", url, list(payload.keys()))

        resp = await self._client.post(
            url, json=payload, headers=self._headers(extra_headers),
        )
        self._record_rate_limit(resp.headers)
        if resp.status_code >= 400:
            error_body = resp.text
            logger.error(
                "Responses API error: status=%d url=%s body=%s",
                resp.status_code, url, error_body[:1000],
            )
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}: {error_body[:500]}",
                request=resp.request,
                response=resp,
            )
        return resp.json()

    # ── Responses API (streaming) ───────────────────────────────────

    async def responses_stream(
        self,
        payload: dict,
        *,
        vision: bool = False,
        initiator: str = "user",
    ) -> AsyncIterator[bytes]:
        """POST /responses with stream=true — yields raw SSE lines."""
        assert self._client is not None
        payload["stream"] = True
        # Strip service_tier — not supported by GitHub Copilot
        payload.pop("service_tier", None)
        url = f"{self._api_base}{COPILOT_RESPONSES_PATH}"
        extra_headers = self._responses_extra_headers(vision, initiator)

        async with self._client.stream(
            "POST", url, json=payload, headers=self._headers(extra_headers),
        ) as resp:
            self._record_rate_limit(resp.headers)
            if resp.status_code >= 400:
                error_body = await resp.aread()
                logger.error(
                    "Responses stream error: status=%d url=%s body=%s",
                    resp.status_code, url, error_body[:1000],
                )
                error_text = error_body.decode("utf-8", errors="replace")[:500]
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}: {error_text}",
                    request=resp.request,
                    response=resp,
                )
            # Preserve empty lines as SSE event delimiters. Some clients (Codex CLI)
            # require proper event framing and can treat streams as incomplete if
            # delimiters are stripped.
            async for line in resp.aiter_lines():
                yield (line + "\n").encode("utf-8")

    # ── Private helpers ─────────────────────────────────────────────

    @staticmethod
    def _responses_extra_headers(vision: bool, initiator: str) -> dict[str, str]:
        """Build extra headers for Responses API requests."""
        h: dict[str, str] = {"X-Initiator": initiator}
        if vision:
            h["copilot-vision-request"] = "true"
        return h

    def _record_rate_limit(self, headers: httpx.Headers) -> None:
        snapshot = self._parse_rate_limit(headers)
        if snapshot is None:
            return

        previous = self._last_rate_limit
        if previous is None:
            self._last_rate_limit = snapshot
            return

        self._last_rate_limit = RateLimitSnapshot(
            limit=previous.limit if snapshot.limit is None else snapshot.limit,
            remaining=(
                previous.remaining if snapshot.remaining is None else snapshot.remaining
            ),
            reset_at=previous.reset_at if snapshot.reset_at is None else snapshot.reset_at,
            retry_after_seconds=(
                previous.retry_after_seconds
                if snapshot.retry_after_seconds is None
                else snapshot.retry_after_seconds
            ),
            source=snapshot.source or previous.source,
            observed_at=snapshot.observed_at or previous.observed_at,
        )

    def _parse_rate_limit(self, headers: httpx.Headers) -> RateLimitSnapshot | None:
        now = time.time()
        limit, limit_source = self._parse_header_number(
            headers,
            ("x-ratelimit-limit", "ratelimit-limit"),
        )
        remaining, remaining_source = self._parse_header_number(
            headers,
            ("x-ratelimit-remaining", "ratelimit-remaining"),
        )
        reset_value, reset_source = self._parse_header_number(
            headers,
            ("x-ratelimit-reset", "ratelimit-reset"),
        )
        retry_after, retry_source = self._parse_header_number(headers, ("retry-after",))

        if (
            limit is None
            and remaining is None
            and reset_value is None
            and retry_after is None
        ):
            return None

        reset_at: float | None = None
        if reset_value is not None:
            reset_at = reset_value if reset_value >= 1_000_000_000 else now + reset_value
        elif retry_after is not None:
            reset_at = now + retry_after

        source = next(
            (
                header_source
                for header_source in (
                    limit_source,
                    remaining_source,
                    reset_source,
                    retry_source,
                )
                if header_source
            ),
            "",
        )

        return RateLimitSnapshot(
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
            retry_after_seconds=retry_after,
            source=source,
            observed_at=now,
        )

    @staticmethod
    def _parse_header_number(
        headers: httpx.Headers,
        names: tuple[str, ...],
    ) -> tuple[int | None, str]:
        for name in names:
            raw_value = headers.get(name)
            if not raw_value:
                continue
            match = re.search(r"-?\d+(?:\.\d+)?", raw_value)
            if not match:
                continue
            value = float(match.group(0))
            if value < 0:
                continue
            return int(value), name
        return None, ""
