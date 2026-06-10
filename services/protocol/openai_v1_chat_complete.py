from __future__ import annotations

import time
import uuid
from typing import Any, Iterable, Iterator

from fastapi import HTTPException

from services.config import config
from services.protocol.chat_completion_cache import cache_key, chat_completion_cache, normalize_text_messages
from services.protocol.sandbox_files import (
    contains_sandbox_link,
    resolve_sandbox_links,
    rewrite_sandbox_links,
    scrub_sandbox_stream,
)
from services.protocol.conversation import (
    ConversationRequest,
    ImageOutput,
    TurnContext,
    collect_image_outputs,
    collect_text,
    count_message_image_tokens,
    count_message_text_tokens,
    count_text_tokens,
    encode_images,
    normalize_messages,
    stream_image_outputs_with_pool,
    stream_text_deltas,
    text_backend,
)


def _sandbox_rewriter(context: TurnContext, base_url: str | None):
    """Return the text post-processor for sandbox links: real download
    resolution when enabled, otherwise the dead-link note."""

    def rewrite(text: str) -> str:
        if not contains_sandbox_link(text):
            return text
        if config.sandbox_download_enabled:
            return resolve_sandbox_links(text, context.backend, context.conversation_id, context.message_id, base_url)
        return rewrite_sandbox_links(text)

    return rewrite
from utils.helper import build_chat_image_markdown_content, extract_chat_image, extract_chat_prompt, is_image_chat_request, parse_image_count
from utils.image_tokens import (
    chat_usage_from_image_usage,
    count_image_inputs_tokens,
    count_image_output_items_tokens,
    image_usage,
)

TOOL_UNAVAILABLE_SYSTEM_MESSAGE = (
    "This compatibility backend cannot execute local tools, shell commands, web searches, "
    "or file operations. Do not claim to have run tools or inspected external resources. "
    "If a user asks you to use a tool, say that tool execution is unavailable through this backend."
)


def completion_chunk(model: str, delta: dict[str, Any], finish_reason: str | None = None, completion_id: str = "", created: int | None = None) -> dict[str, Any]:
    return {
        "id": completion_id or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": created or int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def completion_response(
    model: str,
    content: str,
    created: int | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prompt_text_tokens = count_message_text_tokens(messages, model) if messages else 0
    prompt_image_tokens = count_message_image_tokens(messages, model) if messages else 0
    prompt_tokens = prompt_text_tokens + prompt_image_tokens
    completion_tokens = count_text_tokens(content, model) if messages else 0
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created or int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {
                "text_tokens": prompt_text_tokens,
                "image_tokens": prompt_image_tokens,
                "cached_tokens": 0,
            },
            "completion_tokens_details": {
                "text_tokens": completion_tokens,
                "image_tokens": 0,
                "reasoning_tokens": 0,
            },
        },
    }


def stream_text_chat_completion(backend, messages: list[dict[str, Any]], model: str, base_url: str | None = None) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    request = ConversationRequest(model=model, messages=messages)
    context = TurnContext()
    deltas = stream_text_deltas(backend, request, context)
    for delta_text in scrub_sandbox_stream(deltas, _sandbox_rewriter(context, base_url)):
        if not sent_role:
            sent_role = True
            yield completion_chunk(model, {"role": "assistant", "content": delta_text}, None, completion_id, created)
        else:
            yield completion_chunk(model, {"content": delta_text}, None, completion_id, created)
    if not sent_role:
        yield completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created)
    yield completion_chunk(model, {}, "stop", completion_id, created)


def collect_chat_content(chunks: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for chunk in chunks:
        choices = chunk.get("choices")
        first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
        content = str(delta.get("content") or "")
        if content:
            parts.append(content)
    return "".join(parts)


def chat_messages_from_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        return [message for message in messages if isinstance(message, dict)]
    prompt = str(body.get("prompt") or "").strip()
    if prompt:
        return [{"role": "user", "content": prompt}]
    raise HTTPException(status_code=400, detail={"error": "messages or prompt is required"})


def chat_image_args(body: dict[str, Any]) -> tuple[str, str, int, list[tuple[bytes, str, str]]]:
    model = str(body.get("model") or "gpt-image-2").strip() or "gpt-image-2"
    prompt = extract_chat_prompt(body)
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required"})
    images = [
        (data, f"image_{idx}.png", mime)
        for idx, (data, mime) in enumerate(extract_chat_image(body), start=1)
    ]
    return model, prompt, parse_image_count(body.get("n")), images


def text_chat_parts(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    model = str(body.get("model") or "auto").strip() or "auto"
    messages = normalize_text_messages(normalize_messages(chat_messages_from_body(body)))
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        messages.insert(0, {"role": "system", "content": TOOL_UNAVAILABLE_SYSTEM_MESSAGE})
    return model, messages


def image_result_content(result: dict[str, Any]) -> str:
    data = result.get("data")
    if isinstance(data, list) and data:
        return build_chat_image_markdown_content(result)
    return str(result.get("message") or "Image generation completed.")


def image_chat_response(body: dict[str, Any]) -> dict[str, Any]:
    model, prompt, n, images = chat_image_args(body)
    result = collect_image_outputs(stream_image_outputs_with_pool(ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        response_format="b64_json",
        images=encode_images(images) or None,
    )))
    response = completion_response(model, image_result_content(result), int(result.get("created") or 0) or None)
    usage = image_usage(
        input_text_tokens=count_text_tokens(prompt, model),
        input_image_tokens=count_image_inputs_tokens(images, model),
        output_tokens=count_image_output_items_tokens(result.get("data")),
    )
    response["usage"] = chat_usage_from_image_usage(usage)
    return response


def image_chat_events(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    model, prompt, n, images = chat_image_args(body)
    image_outputs = stream_image_outputs_with_pool(ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        response_format="b64_json",
        images=encode_images(images) or None,
    ))
    yield from stream_image_chat_completion(image_outputs, model)


def stream_image_chat_completion(image_outputs: Iterable[ImageOutput], model: str) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    sent_text = ""
    for output in image_outputs:
        content = ""
        if output.kind == "progress":
            content = output.text
            sent_text += content
        elif output.kind == "result":
            content = build_chat_image_markdown_content({"data": output.data})
        elif output.kind == "message":
            content = output.text[len(sent_text):] if output.text.startswith(sent_text) else output.text
        if not content:
            continue
        if not sent_role:
            sent_role = True
            yield completion_chunk(model, {"role": "assistant", "content": content}, None, completion_id, created)
        else:
            yield completion_chunk(model, {"content": content}, None, completion_id, created)
    if not sent_role:
        yield completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created)
    yield completion_chunk(model, {}, "stop", completion_id, created)


def _collect_resolved_text(model: str, messages: list[dict[str, Any]], base_url: str | None) -> str:
    context = TurnContext()
    text = collect_text(text_backend(), ConversationRequest(model=model, messages=messages), context)
    return _sandbox_rewriter(context, base_url)(text)


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    base_url = str(body.get("base_url") or "") or None
    if body.get("stream"):
        if is_image_chat_request(body):
            return image_chat_events(body)
        model, messages = text_chat_parts(body)
        key = cache_key(body, messages, stream=True)
        return chat_completion_cache.get_or_compute_stream(
            key,
            lambda: stream_text_chat_completion(text_backend(), messages, model, base_url),
        )
    if is_image_chat_request(body):
        return image_chat_response(body)
    model, messages = text_chat_parts(body)
    key = cache_key(body, messages, stream=False)
    return chat_completion_cache.get_or_compute_response(
        key,
        lambda: completion_response(
            model,
            _collect_resolved_text(model, messages, base_url),
            messages=messages,
        ),
    )
