import json

import httpx
import pytest

from askdocs.config import Settings
from askdocs.llm import (
    DONE,
    ChatCompletionsLLM,
    Delta,
    Finish,
    LLMError,
    Usage,
    make_llm,
    parse_sse_line,
)


def chunk(text=None, usage=None, finish=None) -> str:
    choice = {"delta": {"content": text}} if text is not None else None
    if finish:
        choice = {**(choice or {"delta": {}}), "finish_reason": finish}
    body = {"choices": [choice] if choice else []}
    if usage:
        body["usage"] = usage
    return "data: " + json.dumps(body)


STREAM = "\n\n".join([chunk("Hel"), chunk("lo [1]"), chunk(finish="stop"),
                      chunk(usage={"prompt_tokens": 35, "completion_tokens": 4}),
                      "data: [DONE]"]) + "\n\n"


def llm(handler, **kw) -> ChatCompletionsLLM:
    opts = dict(temperature=0.0, connect_timeout_s=1, read_timeout_s=1, max_retries=2,
                retry_backoff_s=0.0)
    opts.update(kw)
    return ChatCompletionsLLM("http://llm/v1", "m", None,
                              transport=httpx.MockTransport(handler), **opts)


async def collect(model) -> list:
    return [ev async for ev in model.stream([{"role": "user", "content": "q"}], 32)]


def test_parse_sse_line():
    assert parse_sse_line(chunk("x")) == [Delta("x")]
    assert parse_sse_line(chunk("")) == []
    assert parse_sse_line(": keep-alive") == []
    assert parse_sse_line("data: [DONE]") is DONE
    usage = {"prompt_tokens": 1, "completion_tokens": 2}
    assert parse_sse_line(chunk(usage=usage)) == [Usage(1, 2)]
    # text, finish and usage in one chunk: nothing is dropped
    both = chunk("end", usage={"prompt_tokens": 1, "completion_tokens": 2}, finish="length")
    assert parse_sse_line(both) == [Delta("end"), Finish("length"), Usage(1, 2)]


@pytest.mark.parametrize("line", ["data: {not json", "data: []", "data: 3",
                                  'data: {"usage": {"prompt_tokens": null}}',
                                  'data: {"error": {"message": "out of memory"}}'])
def test_malformed_or_error_chunks_are_llm_errors(line):
    with pytest.raises(LLMError):
        parse_sse_line(line)


async def test_streams_deltas_then_usage_and_sends_request():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        seen["path"] = req.url.path
        return httpx.Response(200, text=STREAM)

    events = await collect(llm(handler))
    assert events == [Delta("Hel"), Delta("lo [1]"), Finish("stop"), Usage(35, 4)]
    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"]["stream"] is True and seen["body"]["max_tokens"] == 32
    assert seen["body"]["stream_options"] == {"include_usage": True}


async def test_retries_retryable_status_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, text=STREAM)

    assert len(await collect(llm(handler))) == 4
    assert len(calls) == 3


async def test_gives_up_after_max_retries():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503, text="busy")

    with pytest.raises(LLMError, match="503"):
        await collect(llm(handler, max_retries=1))
    assert len(calls) == 2


async def test_client_error_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, text="bad request")

    with pytest.raises(LLMError, match="400"):
        await collect(llm(handler))
    assert len(calls) == 1


async def test_connect_error_is_retried():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, text=STREAM)

    assert len(await collect(llm(handler))) == 4


async def test_no_retry_once_text_was_streamed():
    calls = []

    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (chunk("partial") + "\n\n").encode()
            raise httpx.RemoteProtocolError("peer closed")

    def handler(req):
        calls.append(1)
        return httpx.Response(200, stream=Broken())

    model = llm(handler)
    got = []
    with pytest.raises(LLMError, match="unreachable"):
        async for ev in model.stream([], 8):
            got.append(ev)
    assert got == [Delta("partial")] and len(calls) == 1


async def test_bearer_key_sent_only_when_configured():
    headers = []

    def handler(req):
        headers.append(req.headers.get("authorization"))
        return httpx.Response(200, text=STREAM)

    await collect(ChatCompletionsLLM("http://llm/v1", "m", "sk-test", temperature=0,
                                     connect_timeout_s=1, read_timeout_s=1, max_retries=0,
                                     retry_backoff_s=0, transport=httpx.MockTransport(handler)))
    await collect(llm(handler))
    assert headers == ["Bearer sk-test", None]


def test_provider_defaults_and_startup_checks():
    assert Settings().base_url() == "http://localhost:8081/v1"
    assert Settings(llm_provider="openai").base_url() == "https://api.openai.com/v1"
    with pytest.raises(ValueError, match="API_KEY"):
        Settings(llm_provider="openai").check()
    with pytest.raises(ValueError, match="MODEL"):
        Settings(llm_provider="openai", llm_api_key="k").check()
    Settings(llm_provider="openai", llm_api_key="k", llm_model="gpt-x").check()
    assert isinstance(make_llm(Settings()), ChatCompletionsLLM)


async def test_stream_cut_before_done_is_an_error():
    cut = chunk("partial") + "\n\n"           # no finish_reason, no [DONE]
    with pytest.raises(LLMError, match="ended before"):
        await collect(llm(lambda req: httpx.Response(200, text=cut)))


async def test_finish_without_done_is_complete():
    ok = chunk("all") + "\n\n" + chunk(finish="stop") + "\n\n"
    assert await collect(llm(lambda req: httpx.Response(200, text=ok))) == [
        Delta("all"), Finish("stop")]


async def test_error_chunk_with_http_200_is_an_error():
    body = 'data: {"error": {"message": "context overflow"}}\n\n'
    with pytest.raises(LLMError, match="upstream error"):
        await collect(llm(lambda req: httpx.Response(200, text=body)))


@pytest.mark.parametrize("exc", [httpx.WriteTimeout, httpx.PoolTimeout, httpx.ReadError])
async def test_other_transport_errors_retry_then_become_llm_errors(exc):
    calls = []

    def handler(req):
        calls.append(1)
        raise exc("boom")

    with pytest.raises(LLMError, match="unreachable"):
        await collect(llm(handler, max_retries=1))
    assert len(calls) == 2


async def test_retry_after_header_is_honoured(monkeypatch):
    waits = []

    async def fake_sleep(t):
        waits.append(t)

    monkeypatch.setattr("askdocs.llm.anyio.sleep", fake_sleep)
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, text="slow down")
        return httpx.Response(200, text=STREAM)

    await collect(llm(handler))
    assert waits == [3.0]


async def test_total_deadline_despite_keepalives():
    body = ": keep-alive\n\n" * 50

    model = llm(lambda req: httpx.Response(200, text=body), total_timeout_s=-1)
    with pytest.raises(LLMError, match="exceeded"):
        await collect(model)
