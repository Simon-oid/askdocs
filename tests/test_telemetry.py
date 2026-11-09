import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from askdocs.app import create_app
from askdocs.config import Settings
from askdocs.pipeline import answer
from askdocs.telemetry import FREE, Prices, Stats, cost_usd, percentile, setup_tracing, summarize

from .fakes import FakeLLM, FakeRetriever

EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def provider():
    # The global provider can be set once per process; TRACER is a proxy
    # that starts recording as soon as it is.
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        p = TracerProvider()
        p.add_span_processor(SimpleSpanProcessor(EXPORTER))
        trace.set_tracer_provider(p)


@pytest.fixture(autouse=True)
def spans():
    EXPORTER.clear()
    yield lambda: {s.name: s for s in EXPORTER.get_finished_spans()}


async def run(retriever, llm, **kw):
    return [e async for e in answer("q?", retriever, llm, k=2, max_tokens=64, **kw)]


class TestSpans:
    async def test_answer_is_one_trace_with_two_stage_children(self, spans):
        events = await run(FakeRetriever(), FakeLLM())
        got = spans()
        root, ret, gen = got["askdocs.answer"], got["retrieval"], got["generation"]
        assert ret.parent.span_id == root.context.span_id == gen.parent.span_id
        assert len({s.context.trace_id for s in got.values()}) == 1
        assert root.attributes["askdocs.outcome"] == "answered"
        assert ret.attributes["askdocs.passages"] == 2
        assert gen.attributes["gen_ai.usage.input_tokens"] == 100
        assert gen.attributes["gen_ai.usage.output_tokens"] == 2
        assert gen.attributes["gen_ai.response.finish_reasons"] == ("stop",)
        assert [e.name for e in gen.events] == ["first_token"]
        assert events[-1][1]["trace_id"] == format(root.context.trace_id, "032x")

    async def test_question_text_is_not_recorded(self, spans):
        await run(FakeRetriever(), FakeLLM())
        attrs = [v for s in spans().values() for v in s.attributes.values()]
        assert "q?" not in attrs

    async def test_retrieval_failure_marks_both_spans_and_skips_generation(self, spans):
        await run(FakeRetriever(error="down"), FakeLLM())
        got = spans()
        assert "generation" not in got
        assert got["retrieval"].status.status_code == StatusCode.ERROR
        assert [e.name for e in got["retrieval"].events] == ["exception"]
        assert got["askdocs.answer"].status.status_code == StatusCode.ERROR
        assert got["askdocs.answer"].attributes["askdocs.outcome"] == "error"

    async def test_generation_failure_is_an_error_span(self, spans):
        await run(FakeRetriever(), FakeLLM(pieces=["a", "b"], fail_after=1))
        gen = spans()["generation"]
        assert gen.status.status_code == StatusCode.ERROR
        assert "exception" in [e.name for e in gen.events]

    async def test_no_sources_refusal_has_no_generation_span(self, spans):
        await run(FakeRetriever(passages=[]), FakeLLM())
        got = spans()
        assert "generation" not in got
        assert got["askdocs.answer"].attributes["askdocs.outcome"] == "refused"

    async def test_closed_stream_still_ends_every_span_as_cancelled(self, spans):
        gen = answer("q", FakeRetriever(), FakeLLM(pieces=["a", "b", "c"]), k=2, max_tokens=8)
        await gen.__anext__()                     # sources
        await gen.__anext__()                     # first token
        await gen.aclose()
        got = spans()
        assert {"askdocs.answer", "retrieval", "generation"} <= got.keys()
        assert got["askdocs.answer"].attributes["askdocs.outcome"] == "cancelled"

    async def test_span_context_does_not_leak_into_the_consumer(self):
        async for _ in answer("q", FakeRetriever(), FakeLLM(), k=2, max_tokens=8):
            assert not trace.get_current_span().get_span_context().is_valid

    async def test_parent_context_joins_the_callers_trace(self, spans):
        caller = trace.get_tracer("test").start_span("caller")
        await run(FakeRetriever(), FakeLLM(), parent=trace.set_span_in_context(caller))
        caller.end()
        root = spans()["askdocs.answer"]
        assert root.parent.span_id == caller.get_span_context().span_id


class TestCost:
    def test_cost_from_usage_and_prices(self):
        usage = {"prompt_tokens": 2_000_000, "completion_tokens": 500_000}
        assert cost_usd(usage, Prices(0.5, 2.0)) == 2.0
        assert cost_usd(usage, FREE) == 0.0
        assert cost_usd(None, Prices(1, 1)) is None

    async def test_done_carries_the_cost(self):
        events = await run(FakeRetriever(), FakeLLM(), prices=Prices(1_000_000, 0))
        assert events[-1][1]["cost_usd"] == 100.0       # 100 prompt tokens at $1 each


class TestStats:
    def test_percentile_is_an_observed_value(self):
        xs = list(range(1, 101))
        assert percentile(xs, 50) == 50 and percentile(xs, 95) == 95
        assert percentile([1, 2], 50) == 1 and percentile([1, 2], 95) == 2
        assert percentile([7.0], 95) == 7.0
        with pytest.raises(ValueError):
            percentile([], 50)
        assert summarize([]) == {"n": 0}

    def test_records_done_and_errors(self):
        st = Stats()
        st.record("done", {"refused": False, "cost_usd": 0.001,
                           "timings": {"retrieval_ms": 10.0, "total_ms": 500.0},
                           "usage": {"prompt_tokens": 2000, "completion_tokens": 30}})
        st.record("done", {"refused": True, "cost_usd": None,
                           "timings": {"retrieval_ms": 20.0, "total_ms": 30.0}, "usage": None})
        st.record("error", {"stage": "generation"})
        st.record("token", {"text": "x"})
        snap = st.snapshot()
        assert snap["answers"] == 2 and snap["refusals"] == 1
        assert snap["errors"] == {"generation": 1}
        assert snap["retrieval_ms"] == {"n": 2, "p50": 10.0, "p95": 20.0}
        assert snap["ttft_ms"] == {"n": 0}
        assert snap["prompt_tokens"]["n"] == 1 and snap["cost_usd"]["n"] == 1

    def test_window_keeps_only_the_latest(self):
        st = Stats(window=3)
        for ms in (1000.0, 1000.0, 1.0, 1.0, 1.0):
            st.record("done", {"refused": False, "timings": {"total_ms": ms}, "usage": None})
        assert st.snapshot()["total_ms"] == {"n": 3, "p50": 1.0, "p95": 1.0}

    def test_tracing_is_off_without_an_endpoint(self):
        assert setup_tracing(None, "askdocs") is None


async def test_stats_endpoint_and_traceparent_header(spans):
    app = create_app(Settings(), retriever=FakeRetriever(), llm=FakeLLM())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    trace_id = "0af7651916cd43dd8448eb211c80319c"
    async with app.router.lifespan_context(app), client:
        r = await client.post("/ask?stream=false", json={"question": "q"},
                              headers={"traceparent": f"00-{trace_id}-b7ad6b7169203331-01"})
        st = (await client.get("/stats")).json()
    assert r.json()["data"]["trace_id"] == trace_id
    assert format(spans()["askdocs.answer"].context.trace_id, "032x") == trace_id
    assert st["answers"] == 1 and st["total_ms"]["n"] == 1


async def test_answer_hangs_under_the_framework_server_span(spans):
    app = create_app(Settings(), retriever=FakeRetriever(), llm=FakeLLM())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    async with app.router.lifespan_context(app), client:
        await client.post("/ask", json={"question": "q"})          # streamed
    finished = EXPORTER.get_finished_spans()
    servers = [s for s in finished if s.kind == trace.SpanKind.SERVER]
    if not servers:
        pytest.skip("this FastAPI does not trace requests itself")
    # One trace: the answer hangs under the request (via fastapi.endpoint,
    # the span current in the handler), and the server span, open until
    # the last body chunk, covers the whole streamed answer.
    root, server = spans()["askdocs.answer"], servers[0]
    assert root.context.trace_id == server.context.trace_id
    assert server.start_time <= root.start_time and root.end_time <= server.end_time


def test_header_is_the_parent_without_a_server_span():
    from askdocs.app import parent_context

    class Req:
        headers = {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}
    ctx = parent_context(Req())
    assert format(trace.get_current_span(ctx).get_span_context().trace_id, "032x") == \
        "0af7651916cd43dd8448eb211c80319c"
