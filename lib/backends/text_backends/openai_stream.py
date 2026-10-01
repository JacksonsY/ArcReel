"""流式读取 Chat Completions，向现有解析与校验层交付完整响应。"""

from typing import Any

from openai import AsyncOpenAI, BadRequestError
from openai.lib.streaming.chat import ChatCompletionStreamState
from openai.types.chat import ChatCompletion

from lib.i18n import _
from lib.infra.retry import NonRetryableError


async def create_streamed_completion(client: AsyncOpenAI, **kwargs: Any) -> ChatCompletion:
    """原生、TOOLS 与 JSON 降级共用同一流式传输；半截响应不自动重放。"""
    kwargs["stream"] = True
    kwargs["stream_options"] = {"include_usage": True}
    try:
        stream = await client.chat.completions.create(**kwargs)
    except BadRequestError as exc:
        if "stream_options" not in str(exc):
            raise
        # 有些兼容端点接受流式，却不接受用量扩展；仍保持流式，未知用量返回 None。
        kwargs.pop("stream_options")
        stream = await client.chat.completions.create(**kwargs)

    # 仅复用 SDK 的增量合并；schema 校验与 length 处理保留在原有调用层。
    state = ChatCompletionStreamState()
    seen_choices: set[int] = set()
    finished_choices: set[int] = set()
    usage = None
    try:
        async with stream:
            async for chunk in stream:
                for choice in chunk.choices:
                    seen_choices.add(choice.index)
                    if choice.finish_reason is not None:
                        finished_choices.add(choice.index)
                if chunk.usage is not None:
                    usage = chunk.usage
                state.handle_chunk(chunk)
    except Exception as exc:
        raise NonRetryableError(_("text_stream_incomplete")) from exc

    if not seen_choices or seen_choices != finished_choices:
        raise NonRetryableError(_("text_stream_incomplete"))
    completion = state.current_completion_snapshot
    completion.usage = usage
    return completion
