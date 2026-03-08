"""Observed token usage recording for upstream Copilot requests."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

from copilotx.auth.accounts import AccountRepository


@dataclass(slots=True)
class UsageSample:
    """Normalized token usage for one completed upstream request."""

    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int
    observed_at: float


class UsageRecorder:
    """Persist normalized usage samples into the accounts database."""

    def __init__(self, repository: AccountRepository) -> None:
        self.repository = repository

    def record_payload(
        self,
        account_id: str,
        payload: Any,
        *,
        model_hint: str | None = None,
    ) -> None:
        sample = extract_usage_sample(payload, model_hint=model_hint)
        if sample is None:
            return
        self.repository.record_usage(
            account_id,
            model=sample.model,
            input_tokens=sample.input_tokens,
            cached_input_tokens=sample.cached_input_tokens,
            output_tokens=sample.output_tokens,
            total_tokens=sample.total_tokens,
            observed_at=sample.observed_at,
        )

    async def wrap_stream(
        self,
        account_id: str,
        stream: AsyncIterator[bytes],
        *,
        model_hint: str | None = None,
    ) -> AsyncIterator[bytes]:
        observer = _SseUsageObserver(model_hint=model_hint)
        async for chunk in stream:
            observer.feed(chunk)
            yield chunk
        observer.finish()
        if observer.sample is not None:
            self.repository.record_usage(
                account_id,
                model=observer.sample.model,
                input_tokens=observer.sample.input_tokens,
                cached_input_tokens=observer.sample.cached_input_tokens,
                output_tokens=observer.sample.output_tokens,
                total_tokens=observer.sample.total_tokens,
                observed_at=observer.sample.observed_at,
            )


def extract_usage_sample(
    payload: Any,
    *,
    model_hint: str | None = None,
) -> UsageSample | None:
    """Normalize upstream chat/responses payloads into one usage sample."""
    if not isinstance(payload, dict):
        return None

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = _coerce_int(usage.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _coerce_int(usage.get("prompt_tokens"))

    output_tokens = _coerce_int(usage.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _coerce_int(usage.get("completion_tokens"))

    total_tokens = _coerce_int(usage.get("total_tokens"))
    cached_tokens = 0
    details = usage.get("input_tokens_details")
    if isinstance(details, dict):
        cached_tokens = _coerce_int(details.get("cached_tokens")) or 0

    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None

    input_tokens = max(input_tokens or 0, 0)
    output_tokens = max(output_tokens or 0, 0)
    total_tokens = max(total_tokens or (input_tokens + output_tokens), 0)
    model = str(payload.get("model") or model_hint or "unknown").strip() or "unknown"

    return UsageSample(
        model=model,
        input_tokens=input_tokens,
        cached_input_tokens=max(cached_tokens, 0),
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        observed_at=time.time(),
    )


class _SseUsageObserver:
    """Parse SSE payloads and keep the last completed usage sample."""

    def __init__(self, *, model_hint: str | None = None) -> None:
        self.model_hint = model_hint
        self.sample: UsageSample | None = None
        self._partial = ""
        self._event_type = ""
        self._data_lines: list[str] = []

    def feed(self, chunk: bytes) -> None:
        self._partial += chunk.decode("utf-8", errors="replace")
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            self._handle_line(line.rstrip("\r"))

    def finish(self) -> None:
        if self._partial:
            self._handle_line(self._partial.rstrip("\r"))
            self._partial = ""
        self._finalize_event()

    def _handle_line(self, line: str) -> None:
        if not line:
            self._finalize_event()
            return
        if line.startswith("event:"):
            self._event_type = line[6:].strip()
            return
        if line.startswith("data:"):
            self._data_lines.append(line[5:].lstrip())

    def _finalize_event(self) -> None:
        if not self._data_lines:
            self._event_type = ""
            return

        data = "\n".join(self._data_lines).strip()
        self._data_lines = []
        event_type = self._event_type
        self._event_type = ""

        if not data or data == "[DONE]":
            return

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return

        target = payload
        if event_type == "response.completed" and isinstance(payload, dict):
            target = payload.get("response", payload)

        sample = extract_usage_sample(target, model_hint=self.model_hint)
        if sample is not None:
            self.sample = sample


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
