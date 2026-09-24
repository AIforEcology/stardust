import asyncio
import json

import anthropic
import anthropic._base_client
import openai
import openai._base_client
import pytest

from stardust_sdk import instrument


def _http_module(base_client):
    """The HTTP library a vendor SDK is built on: httpx, or httpx2 in newer SDKs (anthropic >= 1.0)."""
    return getattr(base_client, "httpx2", None) or base_client.httpx


AHTTP = _http_module(anthropic._base_client)
OHTTP = _http_module(openai._base_client)

ANTHROPIC_MESSAGE = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
    "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn", "stop_sequence": None,
    "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 300, "cache_creation_input_tokens": 0},
}


def sse(events, named=True):
    out = []
    for e in events:
        if e == "[DONE]":
            out.append("data: [DONE]\n\n")
        elif named:
            out.append(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n")
        else:
            out.append(f"data: {json.dumps(e)}\n\n")
    return "".join(out).encode()


ANTHROPIC_STREAM = sse([
    {"type": "message_start", "message": {**ANTHROPIC_MESSAGE, "content": [], "stop_reason": None,
                                          "usage": {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 300}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 42}},
    {"type": "message_stop"},
])


def handler_for(http, json_body=None, stream_body=None):
    def handler(request):
        body = json.loads(request.content)
        if body.get("stream"):
            return http.Response(200, content=stream_body, headers={"content-type": "text/event-stream"})
        return http.Response(200, json=json_body)
    return handler


def anthropic_client(async_=False):
    transport = AHTTP.MockTransport(handler_for(AHTTP, ANTHROPIC_MESSAGE, ANTHROPIC_STREAM))
    if async_:
        return anthropic.AsyncAnthropic(api_key="test", http_client=AHTTP.AsyncClient(transport=transport))
    return anthropic.Anthropic(api_key="test", http_client=AHTTP.Client(transport=transport))


ARGS = dict(model="claude-sonnet-5", max_tokens=10, messages=[{"role": "user", "content": "hi"}])


def test_anthropic_sync(stardust, core):
    client = instrument(anthropic_client(), stardust)
    msg = client.messages.create(**ARGS)
    assert isinstance(msg, anthropic.types.Message)  # the vendor object comes back untouched
    assert stardust.flush(2)
    [e] = core.events
    assert (e["provider"], e["model"], e["tokens_in"], e["tokens_out"], e["tokens_cached_in"]) == ("anthropic", "claude-sonnet-5", 400, 20, 300)


def test_anthropic_stream(stardust, core):
    client = instrument(anthropic_client(), stardust)
    with client.messages.create(**ARGS, stream=True) as stream:
        kinds = [event.type for event in stream]
    assert kinds[0] == "message_start" and kinds[-1] == "message_stop"
    assert stardust.flush(2)
    [e] = core.events
    assert (e["tokens_in"], e["tokens_out"], e["tokens_cached_in"]) == (400, 42, 300)


def test_anthropic_async_and_async_stream(stardust, core):
    client = instrument(anthropic_client(async_=True), stardust)

    async def go():
        await client.messages.create(**ARGS)
        stream = await client.messages.create(**ARGS, stream=True)
        async for _ in stream:
            pass

    asyncio.run(go())
    assert stardust.flush(2)
    assert [e["tokens_out"] for e in core.events] == [20, 42]


def test_instrument_is_idempotent(stardust, core):
    client = anthropic_client()
    instrument(instrument(client, stardust), stardust)
    client.messages.create(**ARGS)
    assert stardust.flush(2)
    assert len(core.events) == 1


def test_openai_chat_and_stream(stardust, core):
    chat = {
        "id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-4o-2024-08-06",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050, "prompt_tokens_details": {"cached_tokens": 600}},
    }
    chunk = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-4o-2024-08-06"}
    stream = sse([
        {**chunk, "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}]},
        {**chunk, "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        "[DONE]",
    ], named=False)
    transport = OHTTP.MockTransport(handler_for(OHTTP, chat, stream))
    client = instrument(openai.OpenAI(api_key="test", http_client=OHTTP.Client(transport=transport)), stardust, region="eastus")

    msgs = [{"role": "user", "content": "hi"}]
    client.chat.completions.create(model="gpt-4o", messages=msgs)
    for _ in client.chat.completions.create(model="gpt-4o", messages=msgs, stream=True, stream_options={"include_usage": True}):
        pass

    assert stardust.flush(2)
    first, second = core.events
    assert (first["tokens_in"], first["tokens_out"], first["tokens_cached_in"], first["region"]) == (1000, 50, 600, "eastus")
    assert (second["tokens_in"], second["tokens_out"]) == (10, 5)


def test_openai_stream_without_usage_records_nothing(stardust, core):
    chunk = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-4o",
             "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}]}
    transport = OHTTP.MockTransport(handler_for(OHTTP, None, sse([chunk, "[DONE]"], named=False)))
    client = instrument(openai.OpenAI(api_key="test", http_client=OHTTP.Client(transport=transport)), stardust)
    for _ in client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}], stream=True):
        pass
    assert stardust.flush(2)
    assert core.events == []


def test_gemini_sync_async_and_streams(stardust, core):
    import google.genai.types as gt

    def response(prompt, out):
        return gt.GenerateContentResponse(
            model_version="gemini-2.5-flash",
            usage_metadata=gt.GenerateContentResponseUsageMetadata(prompt_token_count=prompt, candidates_token_count=out),
        )

    class Models:
        def generate_content(self, model, contents):
            return response(10, 3)

        def generate_content_stream(self, model, contents):
            yield response(10, 1)
            yield response(10, 4)

    class AsyncModels:
        async def generate_content(self, model, contents):
            return response(20, 6)

        async def generate_content_stream(self, model, contents):
            async def chunks():
                yield response(20, 2)
                yield response(20, 9)
            return chunks()

    class Aio:
        models = AsyncModels()

    FakeClient = type("Client", (), {"__module__": "google.genai.client"})
    client = FakeClient()
    client.models, client.aio = Models(), Aio()
    instrument(client, stardust)

    client.models.generate_content(model="gemini-2.5-flash", contents="hi")
    list(client.models.generate_content_stream(model="gemini-2.5-flash", contents="hi"))

    async def go():
        await client.aio.models.generate_content(model="gemini-2.5-flash", contents="hi")
        async for _ in await client.aio.models.generate_content_stream(model="gemini-2.5-flash", contents="hi"):
            pass

    asyncio.run(go())
    assert stardust.flush(2)
    assert [(e["tokens_in"], e["tokens_out"]) for e in core.events] == [(10, 3), (10, 4), (20, 6), (20, 9)]


def test_metering_failure_never_breaks_the_call(stardust, core, monkeypatch):
    import sys

    inst = sys.modules["stardust_sdk.instrument"]

    def boom(*a, **k):
        raise RuntimeError("extractor bug")

    monkeypatch.setattr(inst, "from_anthropic", boom)
    client = instrument(anthropic_client(), stardust)
    assert client.messages.create(**ARGS).id == "msg_1"


def test_unknown_client_rejected(stardust):
    with pytest.raises(TypeError):
        instrument(object(), stardust)
