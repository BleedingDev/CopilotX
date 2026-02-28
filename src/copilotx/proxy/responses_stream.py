"""Stream ID synchronization for the Responses API.

GitHub Copilot's Responses API returns different IDs for the same item in
'response.output_item.added' vs 'response.output_item.done' events.
This breaks @ai-sdk/openai (Vercel AI SDK) and other clients that expect
consistent IDs across the stream lifecycle.

Ported from MarsIWE's responses_stream.rs.
"""

from __future__ import annotations

import json
import time
from typing import AsyncIterator


class ResponsesStreamIdTracker:
    """Tracks output item IDs across streaming events for consistency.

    Maintains a mapping of output_index → item_id from 'added' events,
    and patches 'done' events to use the original ID.
    """

    def __init__(self) -> None:
        self._output_items: dict[int, str] = {}  # output_index → item_id
        self._id_counter: int = 0

    def fix_stream_data(self, data_str: str, event_type: str | None) -> str:
        """Process a single SSE data payload and fix IDs if necessary."""
        if not data_str:
            return data_str

        if event_type == "response.output_item.added":
            return self._handle_added(data_str)
        elif event_type == "response.output_item.done":
            return self._handle_done(data_str)
        else:
            return self._handle_other(data_str)

    def _generate_id(self, output_index: int) -> str:
        self._id_counter += 1
        ts = int(time.time() * 1_000_000)  # microseconds
        return f"oi_{output_index}_{ts:x}{self._id_counter:04x}"

    def _handle_added(self, data_str: str) -> str:
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            return data_str

        output_index = payload.get("output_index")
        if output_index is None:
            return data_str

        item = payload.get("item", {})
        item_id = item.get("id")

        # Generate ID if missing
        if not item_id:
            item_id = self._generate_id(output_index)
            item["id"] = item_id
            payload["item"] = item

        # Record for later patching
        self._output_items[output_index] = item_id
        return json.dumps(payload, separators=(",", ":"))

    def _handle_done(self, data_str: str) -> str:
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            return data_str

        output_index = payload.get("output_index")
        if output_index is None:
            return data_str

        # Replace ID with the one from the 'added' event
        original_id = self._output_items.get(output_index)
        if original_id:
            item = payload.get("item", {})
            item["id"] = original_id
            payload["item"] = item

        return json.dumps(payload, separators=(",", ":"))

    def _handle_other(self, data_str: str) -> str:
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            return data_str

        output_index = payload.get("output_index")
        if output_index is not None:
            original_id = self._output_items.get(output_index)
            if original_id:
                payload["item_id"] = original_id
                return json.dumps(payload, separators=(",", ":"))

        return data_str


def _extract_event_type(data_str: str) -> str | None:
    """Quick extract of event type from a JSON data payload without full parsing."""
    # Check common event type patterns
    for etype in (
        "response.output_item.added",
        "response.output_item.done",
        "response.output_text.delta",
        "response.output_text.done",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.reasoning_summary_text.delta",
        "response.reasoning_summary_text.done",
        "response.created",
        "response.completed",
        "response.incomplete",
        "response.failed",
        "error",
    ):
        if f'"type":"{etype}"' in data_str:
            return etype
    return None


async def fix_responses_stream(
    raw_lines: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """Wrap a raw SSE line stream with ID synchronization.

    Consumes raw SSE lines from the Copilot backend, applies the ID tracker
    to fix inconsistent item IDs, and re-emits corrected SSE lines.
    """
    tracker = ResponsesStreamIdTracker()

    async for raw_line in raw_lines:
        line = raw_line.decode("utf-8").rstrip("\n")

        # Preserve SSE event delimiters between events.
        if not line:
            yield b"\n"
            continue

        # Pass through non-data lines (event: lines, comments, etc.)
        if not line.startswith("data: "):
            yield (line + "\n").encode("utf-8")
            continue

        data_str = line[6:]  # strip "data: "

        # Pass through [DONE] marker
        if data_str == "[DONE]":
            yield raw_line
            continue

        # Extract event type and apply ID fix
        event_type = _extract_event_type(data_str)
        fixed_data = tracker.fix_stream_data(data_str, event_type)
        yield (f"data: {fixed_data}\n").encode("utf-8")

    # Final newline
    yield b"\n"
