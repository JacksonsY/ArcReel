import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from lib.backends.text_backends.base import TextGenerationRequest, TextOutputTruncatedError
from lib.backends.text_backends.openai import OpenAITextBackend
from lib.infra.retry import NonRetryableError
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, request_json

_URL = "https://relay.example/v1/chat/completions"


class Person(BaseModel):
    name: str
    age: int


def _sse_response(text: str, *, tool: bool = False, finish: str | None = "stop", usage: bool = True):
    events = []
    for index, part in enumerate((text[: len(text) // 2], text[len(text) // 2 :])):
        delta = {"content": part}
        if tool:
            call = {"index": 0, "function": {"arguments": part}}
            if index == 0:
                call.update(id="call-1", type="function")
                call["function"]["name"] = "Person"
            delta = {"tool_calls": [call]}
        if index == 0:
            delta["role"] = "assistant"
        events.append({"index": 0, "delta": delta, "finish_reason": None})
    if finish:
        events.append({"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool and finish == "stop" else finish})
    chunks = [
        {"id": "chat-1", "object": "chat.completion.chunk", "created": 1, "model": "m", "choices": [event]}
        for event in events
    ]
    if usage:
        chunks.append(
            {
                "id": "chat-1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "m",
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )
    body = b"".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode() for chunk in chunks)
    if finish:
        body += b"data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def _schema_rejected():
    return httpx.Response(400, json={"error": {"message": "json_schema is not supported"}})


async def test_native_stream_preserves_text_usage_and_request_parameters():
    with capture_http() as router:
        route = router.post(_URL).mock(return_value=_sse_response('{"name":"张三","age":30}'))
        result = await OpenAITextBackend(api_key="test", base_url="https://relay.example/v1").generate(
            TextGenerationRequest(prompt="人物", response_schema=Person, max_output_tokens=1000)
        )
    sent = request_json(route.calls.last.request)
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert sent["max_tokens"] == 1000
    assert result.text == '{"name":"张三","age":30}'
    assert (result.input_tokens, result.output_tokens) == (10, 5)
    assert route.call_count == 1


@pytest.mark.parametrize("fallback", ["tools", "md_json", "dict"])
async def test_all_structured_fallback_requests_stream(fallback):
    text = '{"name":"张三","age":30}'
    responses = [_schema_rejected()]
    if fallback == "md_json":
        responses.extend(_sse_response(text) for _ in range(3))
    responses.append(_sse_response(text, tool=fallback == "tools"))
    schema = Person.model_json_schema() if fallback == "dict" else Person
    with capture_http() as router:
        route = router.post(_URL).mock(side_effect=responses)
        result = await OpenAITextBackend(api_key="test", base_url="https://relay.example/v1").generate(
            TextGenerationRequest(prompt="人物", response_schema=schema)
        )
    assert json.loads(result.text) == {"name": "张三", "age": 30}
    assert all(request_json(call.request)["stream"] is True for call in route.calls)
    assert route.call_count == len(responses)
    assert (result.input_tokens, result.output_tokens) == ((40, 20) if fallback == "md_json" else (10, 5))


async def test_streamed_validation_retry_and_native_usage_are_preserved():
    with capture_http() as router:
        route = router.post(_URL).mock(
            side_effect=[
                _sse_response("not JSON"),
                _sse_response('{"name":"张三","age":"invalid"}', tool=True),
                _sse_response('{"name":"张三","age":30}', tool=True),
            ]
        )
        result = await OpenAITextBackend(api_key="test", base_url="https://relay.example/v1").generate(
            TextGenerationRequest(prompt="人物", response_schema=Person)
        )
    assert json.loads(result.text)["age"] == 30
    assert route.call_count == 3
    assert all(request_json(call.request)["stream"] is True for call in route.calls)
    assert (result.input_tokens, result.output_tokens) == (30, 15)


@pytest.mark.parametrize("fallback", [False, True])
async def test_streamed_length_finish_stays_a_truncation_error(fallback):
    responses = [_schema_rejected()] if fallback else []
    responses.append(_sse_response('{"name":', tool=fallback, finish="length"))
    with capture_http() as router, bounded_poll_clock():
        route = router.post(_URL).mock(side_effect=responses)
        backend = OpenAITextBackend(api_key="test", base_url="https://relay.example/v1")
        with pytest.raises(TextOutputTruncatedError):
            await backend.generate(TextGenerationRequest(prompt="人物", response_schema=Person))
    assert route.call_count == len(responses)


@pytest.mark.parametrize("response_kind", ["partial", "empty", "non_stream"])
async def test_missing_finish_marker_is_not_accepted_or_replayed(response_kind):
    response = _sse_response("partial", finish=None)
    if response_kind == "empty":
        response = httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"data: [DONE]\n\n")
    elif response_kind == "non_stream":
        response = httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})
    with capture_http() as router, bounded_poll_clock():
        route = router.post(_URL).mock(return_value=response)
        backend = OpenAITextBackend(api_key="test", base_url="https://relay.example/v1")
        with pytest.raises(NonRetryableError):
            await backend.generate(TextGenerationRequest(prompt="hello"))
    assert route.call_count == 1


async def test_missing_usage_is_unknown():
    with capture_http() as router:
        router.post(_URL).mock(return_value=_sse_response("hello", usage=False))
        result = await OpenAITextBackend(api_key="test", base_url="https://relay.example/v1").generate(
            TextGenerationRequest(prompt="hello")
        )
    assert result.text == "hello"
    assert result.input_tokens is None
    assert result.output_tokens is None


async def test_usage_option_rejection_keeps_the_retry_streaming():
    with capture_http() as router:
        route = router.post(_URL).mock(
            side_effect=[
                httpx.Response(400, json={"error": {"message": "stream_options is not supported"}}),
                _sse_response("hello", usage=False),
            ]
        )
        result = await OpenAITextBackend(api_key="test", base_url="https://relay.example/v1").generate(
            TextGenerationRequest(prompt="hello")
        )
    assert result.text == "hello"
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert route.call_count == 2
    assert all(request_json(call.request)["stream"] is True for call in route.calls)
    assert "stream_options" not in request_json(route.calls.last.request)


class _BrokenStream(httpx.AsyncByteStream):
    def __init__(self, *, cancel=False):
        self.cancel = cancel
        self.closed = False

    async def __aiter__(self):
        body = _sse_response("部分回复", finish=None, usage=False).content
        # HTTP boundaries can split a UTF-8 character or an SSE event.
        for start in range(0, len(body), 7):
            yield body[start : start + 7]
        if self.cancel:
            raise asyncio.CancelledError()
        raise httpx.ReadError("connection lost")

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("fallback", [False, True])
async def test_broken_stream_is_closed_without_replaying(cancel, fallback):
    body = _BrokenStream(cancel=cancel)
    responses = [_sse_response("not JSON")] if fallback else []
    responses.append(httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body))
    with capture_http() as router, bounded_poll_clock():
        route = router.post(_URL).mock(side_effect=responses)
        backend = OpenAITextBackend(api_key="test", base_url="https://relay.example/v1")
        error = asyncio.CancelledError if cancel else NonRetryableError
        with pytest.raises(error):
            await backend.generate(TextGenerationRequest(prompt="hello", response_schema=Person if fallback else None))
    assert body.closed
    assert route.call_count == len(responses)
