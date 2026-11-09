"""Tracing, cost, and per-stage latency percentiles.

Tracing uses the OpenTelemetry API, which is a no-op until a tracer
provider is installed: without ASKDOCS_OTEL_ENDPOINT the service pays
nothing for it and needs no SDK. `setup_tracing` installs the SDK and an
OTLP/HTTP exporter (any collector: Jaeger, Tempo, the OTel Collector).

`Stats` keeps the last WINDOW answers per stage in memory and serves
P50/P95 at GET /stats. Traces answer "why was *this* request slow";
the window answers "how slow are we *now*" without a metrics backend.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from opentelemetry import trace

TRACER = trace.get_tracer("askdocs")
STAGES = ("retrieval_ms", "ttft_ms", "generation_ms", "total_ms")
TOKENS = ("prompt_tokens", "completion_tokens")
WINDOW = 1000            # answers kept per stage: recent enough to track a change
PERCENTILES = (50, 95)


@dataclass(frozen=True)
class Prices:
    """USD per million tokens. Zero for a local model: its cost is GPU
    time, reported as generation_ms, not money."""
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0


FREE = Prices()


def cost_usd(usage: dict | None, prices: Prices) -> float | None:
    if usage is None:
        return None
    return round((usage["prompt_tokens"] * prices.input_per_mtok
                  + usage["completion_tokens"] * prices.output_per_mtok) / 1e6, 8)


def percentile(xs: list[float], p: float) -> float:
    """Nearest rank: the smallest observed value with at least p% of the
    values at or below it (rank ceil(p/100 * n)). Always a value that was
    observed, never an interpolation between two requests."""
    if not xs:
        raise ValueError("percentile of no values")
    s = sorted(xs)
    return s[max(1, math.ceil(p / 100 * len(s))) - 1]


def summarize(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    return {"n": len(xs), **{f"p{p}": percentile(xs, p) for p in PERCENTILES}}


class Stats:
    """Rolling window over finished answers. Mutated only from the event
    loop thread, so it needs no lock."""

    def __init__(self, window: int = WINDOW):
        self._series = {k: deque(maxlen=window) for k in (*STAGES, *TOKENS, "cost_usd")}
        self.answers = 0
        self.refusals = 0
        self.errors: dict[str, int] = {}

    def record(self, event: str, data: dict) -> None:
        if event == "error":
            self.errors[data["stage"]] = self.errors.get(data["stage"], 0) + 1
            return
        if event != "done":
            return
        self.answers += 1
        self.refusals += bool(data["refused"])
        values = {**data["timings"], **(data.get("usage") or {})}
        if data.get("cost_usd") is not None:
            values["cost_usd"] = data["cost_usd"]
        for key, series in self._series.items():
            if key in values:
                series.append(values[key])

    def snapshot(self) -> dict:
        return {"answers": self.answers, "refusals": self.refusals, "errors": dict(self.errors),
                "window": self._series["total_ms"].maxlen,
                **{k: summarize(list(v)) for k, v in self._series.items()}}


def setup_tracing(endpoint: str | None, service_name: str) -> Callable[[], None] | None:
    """Install the SDK with an OTLP/HTTP exporter; returns its shutdown
    (flushes buffered spans), or None when tracing is off."""
    if not endpoint:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        raise RuntimeError("ASKDOCS_OTEL_ENDPOINT is set but the SDK is missing: "
                           "pip install 'askdocs[otel]'") from e
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")))
    trace.set_tracer_provider(provider)
    return provider.shutdown
