"""OpenTelemetry helpers.

Uses only the OTel API, so spans are no-ops until the host installs an SDK tracer
provider (e.g. via `opentelemetry-instrument` or OTEL_* exporter configuration).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

tracer = trace.get_tracer("agentic_review")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """Start a span, set scalar attributes, and record exceptions as errors."""
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def record_token_usage(current: Span, usage: dict[str, Any] | None) -> None:
    """Attach LangChain `usage_metadata` token counts to a span."""
    if not usage:
        return
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if usage.get(key) is not None:
            current.set_attribute(f"llm.usage.{key}", int(usage[key]))
