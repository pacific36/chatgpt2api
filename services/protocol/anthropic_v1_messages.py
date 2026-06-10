from __future__ import annotations

import html
import json
import re
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from services.account_service import account_service
from services.openai_backend_api import OpenAIBackendAPI
from services.protocol.conversation import (
    count_message_tokens,
    count_text_tokens,
    encoding_for_model,
    normalize_messages,
)
from services.protocol.openai_v1_chat_complete import collect_chat_content, stream_text_chat_completion

XML_TOOL_RULE = """Tool output adapter: when calling tools, output ONLY this XML and no prose/markdown:
<tool_calls><tool_call><tool_name>TOOL_NAME</tool_name><parameters><PARAM><![CDATA[value]]></PARAM></parameters></tool_call></tool_calls>"""


@dataclass
class MessageRequest:
    backend: OpenAIBackendAPI
    messages: list[dict[str, Any]]
    model: str
    tools: Any = None
    stop_sequences: list[str] | None = None
    max_tokens: int | None = None


def parse_stop_sequences(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def parse_max_tokens(value: object) -> int | None:
    try:
        max_tokens = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return max_tokens if max_tokens > 0 else None


def split_stop_sequences(text: str, stop_sequences: list[str]) -> tuple[str, str]:
    """Truncate at the earliest stop sequence; return (text, matched_stop)."""
    best_index = -1
    matched = ""
    for stop in stop_sequences:
        index = text.find(stop)
        if index != -1 and (best_index == -1 or index < best_index):
            best_index, matched = index, stop
    if best_index == -1:
        return text, ""
    return text[:best_index], matched


def stop_holdback(text: str, stop_sequences: list[str]) -> int:
    """Chars to withhold from streaming because they may begin a stop sequence."""
    holdback = 0
    for stop in stop_sequences:
        for size in range(min(len(stop) - 1, len(text)), 0, -1):
            if text.endswith(stop[:size]):
                holdback = max(holdback, size)
                break
    return holdback


def truncate_to_max_tokens(text: str, model: str, max_tokens: int | None) -> tuple[str, bool]:
    if not max_tokens or not text:
        return text, False
    encoding = encoding_for_model(model)
    tokens = encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text, False
    return encoding.decode(tokens[:max_tokens]), True


def _tool_meta(tool: dict[str, object]) -> tuple[str, str, object]:
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    name = str(tool.get("name") or fn.get("name") or "").strip()
    desc = str(tool.get("description") or fn.get("description") or "").strip()
    schema = tool.get("input_schema") or tool.get("parameters") or fn.get("input_schema") or fn.get("parameters") or {}
    return name, desc, schema


def build_tool_prompt(tools: object) -> str:
    if not isinstance(tools, list):
        return ""
    blocks = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name, desc, schema = _tool_meta(tool)
        if name:
            blocks.append(f"Tool: {name}\nDescription: {desc}\nParameters: {json.dumps(schema, ensure_ascii=False)}")
    if not blocks:
        return ""
    return "Available tools:\n" + "\n".join(blocks) + """

Tool use rules:
- If the user asks to list/read/search files, inspect project state, run a command, or answer from local code, you MUST call a suitable tool first. Do not say you cannot access files.
- To call tools, output ONLY XML and no prose/markdown:
<tool_calls><tool_call><tool_name>TOOL_NAME</tool_name><parameters><PARAM><![CDATA[value]]></PARAM></parameters></tool_call></tool_calls>
- Put parameters under <parameters> using the exact schema names.
""".strip()


def merge_system(system: object, extra: str) -> object:
    system = compact_system(system)
    if _has_claude_code_system(system):
        extra = XML_TOOL_RULE
    if not extra:
        return system
    if isinstance(system, str) and system.strip():
        return f"{system.strip()}\n\n{extra}"
    if isinstance(system, list):
        return [*system, {"type": "text", "text": extra}]
    return extra


def _has_claude_code_system(system: object) -> bool:
    if isinstance(system, str):
        return "You are Claude Code" in system
    if isinstance(system, list):
        return any(isinstance(item, dict) and "You are Claude Code" in str(item.get("text") or "") for item in system)
    return False


def compact_system(system: object) -> object:
    if isinstance(system, str):
        return _compact_system_text(system)
    if isinstance(system, list):
        result = []
        for item in system:
            if isinstance(item, dict) and str(item.get("type") or "") == "text":
                copied = dict(item)
                copied["text"] = _compact_system_text(str(item.get("text") or ""))
                result.append(copied)
            else:
                result.append(item)
        return result
    return system


def _compact_system_text(text: str) -> str:
    return text or ""


def _compact_message_text(text: str) -> str:
    return text or ""


def preprocess_payload(payload: dict[str, object], text_mapper: Callable[[str], str] | None = None) -> dict[str, object]:
    payload["messages"] = preprocess_messages(payload.get("messages"), text_mapper)
    payload["system"] = merge_system(payload.get("system"), build_tool_prompt(payload.get("tools")))
    return payload


def message_request(body: dict[str, Any]) -> MessageRequest:
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        raise HTTPException(status_code=400, detail="messages: at least one message is required")
    payload = preprocess_payload(dict(body))
    return MessageRequest(
        backend=OpenAIBackendAPI(access_token=account_service.get_text_access_token()),
        messages=normalize_messages(payload.get("messages"), payload.get("system")),
        model=str(payload.get("model") or "auto").strip() or "auto",
        tools=payload.get("tools"),
        stop_sequences=parse_stop_sequences(payload.get("stop_sequences")),
        max_tokens=parse_max_tokens(payload.get("max_tokens")),
    )


def preprocess_messages(messages: object, text_mapper: Callable[[str], str] | None = None) -> object:
    if not isinstance(messages, list):
        return messages
    mapper = text_mapper or (lambda text: text)
    result = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        item = dict(message)
        content = item.get("content")
        if isinstance(content, str):
            item["content"] = _compact_message_text(mapper(content))
        elif isinstance(content, list):
            item["content"] = [_preprocess_block(block, mapper) for block in content]
        result.append(item)
    return result


def _preprocess_block(block: object, text_mapper: Callable[[str], str]) -> object:
    if not isinstance(block, dict):
        return block
    block_type = str(block.get("type") or "")
    if block_type == "text":
        item = dict(block)
        item["text"] = _compact_message_text(text_mapper(str(block.get("text") or "")))
        return item
    if block_type == "tool_use":
        return {"type": "text", "text": f"<tool_calls><tool_call><tool_name>{block.get('name') or ''}</tool_name><parameters>{json.dumps(block.get('input') or {}, ensure_ascii=False)}</parameters></tool_call></tool_calls>"}
    if block_type == "tool_result":
        return {"type": "text", "text": f"Tool result {block.get('tool_use_id') or ''}: {block.get('content') or ''}"}
    return block


def usage_payload(input_tokens: int, output_tokens: int) -> dict[str, object]:
    return {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": output_tokens,
    }


def message_response(
    model: str,
    text: str,
    input_tokens: int,
    tools: object = None,
    stop_sequences: list[str] | None = None,
    max_tokens: int | None = None,
) -> dict[str, object]:
    matched_stop = ""
    if stop_sequences:
        text, matched_stop = split_stop_sequences(text, stop_sequences)
    text, truncated = truncate_to_max_tokens(text, model, max_tokens)
    content, stop_reason = content_blocks(text, None if matched_stop else tools)
    if matched_stop:
        stop_reason = "stop_sequence"
    elif truncated:
        stop_reason = "max_tokens"
    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": matched_stop or None,
        "usage": usage_payload(input_tokens, count_text_tokens(text, model)),
    }


def content_blocks(text: str, tools: object = None) -> tuple[list[dict[str, object]], str]:
    calls = parse_tool_calls(text) if isinstance(tools, list) and tools else []
    text = strip_tool_markup(text)
    if calls:
        content = ([{"type": "text", "text": text}] if text else []) + [{"type": "tool_use", "id": f"toolu_{uuid.uuid4()}", "name": name, "input": args} for name, args in calls]
        return content, "tool_use"
    return [{"type": "text", "text": text}], "end_turn"


def strip_tool_markup(text: str) -> str:
    return re.sub(r"(?is)<tool_calls\b[^>]*>.*?</tool_calls>|<tool_call\b[^>]*>.*?</tool_call>|<function_call\b[^>]*>.*?</function_call>|<invoke\b[^>]*>.*?</invoke>", "", text or "").strip()


def streamable_text(text: str) -> str:
    text = text or ""
    match = re.search(r"(?is)<tool_calls\b|<tool_call\b|<function_call\b|<invoke\b", text)
    return text[:match.start()].rstrip() if match else text


def parse_tool_calls(text: str) -> list[tuple[str, dict[str, object]]]:
    text = re.sub(r"(?is)```.*?```", "", text or "").strip()
    blocks = re.findall(r"(?is)<tool_call\b[^>]*>(.*?)</tool_call>|<function_call\b[^>]*>(.*?)</function_call>|<invoke\b[^>]*>(.*?)</invoke>", text)
    result = []
    for block in (next((part for part in match if part), "") for match in blocks):
        name = xml_value(block, "tool_name") or xml_value(block, "name") or xml_value(block, "function")
        params = xml_value(block, "parameters") or xml_value(block, "input") or xml_value(block, "arguments") or "{}"
        if name:
            result.append((name, parse_tool_params(params)))
    return result


def xml_value(text: str, tag: str) -> str:
    match = re.search(rf"(?is)<{tag}\b[^>]*>(.*?)</{tag}>", text)
    if not match:
        return ""
    value = match.group(1).strip()
    cdata = re.fullmatch(r"(?is)<!\[CDATA\[(.*?)]]>", value)
    return html.unescape(cdata.group(1) if cdata else value).strip()


def parse_tool_params(raw: str) -> dict[str, object]:
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {m.group(1): parse_tool_value(m.group(2)) for m in re.finditer(r"(?is)<([\w.-]+)\b[^>]*>(.*?)</\1>", raw)}


def parse_tool_value(raw: str) -> object:
    value = xml_value(f"<x>{raw}</x>", "x")
    try:
        return json.loads(value)
    except Exception:
        return value


def stream_events(
    chunks: Iterable[dict[str, object]],
    model: str,
    input_tokens: int,
    tools: object = None,
    stop_sequences: list[str] | None = None,
    max_tokens: int | None = None,
) -> Iterator[dict[str, object]]:
    encoding = encoding_for_model(model)
    stops = stop_sequences or []
    current_text = ""
    streamed_text = ""
    streamed_tokens = 0
    tool_mode = isinstance(tools, list) and bool(tools)
    tool_started = False
    text_open = False
    matched_stop = ""
    max_reached = False

    def emit_text(delta_text: str) -> Iterator[dict[str, object]]:
        nonlocal streamed_text, streamed_tokens, text_open, max_reached
        if not delta_text or max_reached:
            return
        tokens = encoding.encode(delta_text)
        if max_tokens:
            remaining = max_tokens - streamed_tokens
            if remaining <= 0:
                max_reached = True
                return
            if len(tokens) >= remaining:
                max_reached = True
                delta_text = encoding.decode(tokens[:remaining])
                tokens = tokens[:remaining]
                if not delta_text:
                    streamed_tokens = max_tokens
                    return
        streamed_tokens += len(tokens)
        if not text_open:
            text_open = True
            yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
        streamed_text += delta_text
        yield {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": delta_text}}

    def finish(content: list[dict[str, object]], stop_reason: str) -> Iterator[dict[str, object]]:
        nonlocal text_open, streamed_tokens
        if text_open:
            yield {"type": "content_block_stop", "index": 0}
            text_open = False
        if stop_reason == "tool_use":
            start_index = 1 if streamed_text else 0
            blocks = [block for block in content if block.get("type") != "text"]
            for block in blocks:
                streamed_tokens += len(encoding.encode(json.dumps(block.get("input") or {}, ensure_ascii=False)))
            yield from _stream_buffered_blocks(blocks, start_index)
        yield {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": matched_stop or None},
            "usage": {"output_tokens": streamed_tokens},
        }
        yield {"type": "message_stop"}

    def finalize() -> Iterator[dict[str, object]]:
        nonlocal matched_stop
        text = current_text
        if stops and not tool_started and not matched_stop:
            text, matched_stop = split_stop_sequences(text, stops)
        content, stop_reason = content_blocks(text, None if matched_stop else tools)
        if content and content[0].get("type") == "text":
            full_text = str(content[0].get("text") or "")
            if full_text.startswith(streamed_text):
                yield from emit_text(full_text[len(streamed_text):])
        if matched_stop:
            stop_reason = "stop_sequence"
        elif max_reached:
            stop_reason = "max_tokens"
        yield from finish(content, stop_reason)

    yield {
        "type": "message_start",
        "message": {
            "id": f"msg_{uuid.uuid4().hex}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": usage_payload(input_tokens, 0),
        },
    }
    yield {"type": "ping"}
    if not tool_mode:
        text_open = True
        yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
    try:
        for chunk in chunks:
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            text_delta = delta.get("content", "") if isinstance(delta, dict) else ""
            if text_delta:
                current_text += text_delta
                if not tool_started:
                    visible_text = current_text if not tool_mode else streamable_text(current_text)
                    tool_started = tool_mode and visible_text != current_text
                    if stops:
                        visible_text, matched_stop = split_stop_sequences(visible_text, stops)
                        if not matched_stop and not tool_started:
                            visible_text = visible_text[: len(visible_text) - stop_holdback(visible_text, stops)]
                    if visible_text.startswith(streamed_text):
                        yield from emit_text(visible_text[len(streamed_text):])
                    if matched_stop or max_reached:
                        yield from finish([], "stop_sequence" if matched_stop else "max_tokens")
                        return
            if choice.get("finish_reason"):
                yield from finalize()
                return
        yield from finalize()
    finally:
        close = getattr(chunks, "close", None)
        if callable(close):
            close()


def _stream_buffered_blocks(content: list[dict[str, object]], start_index: int = 0) -> Iterator[dict[str, object]]:
    for offset, block in enumerate(content):
        index = start_index + offset
        if block["type"] == "tool_use":
            start = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block.get("input") or {}, ensure_ascii=False)}
        else:
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block.get("text") or ""}
        yield {"type": "content_block_start", "index": index, "content_block": start}
        yield {"type": "content_block_delta", "index": index, "delta": delta}
        yield {"type": "content_block_stop", "index": index}


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    request = message_request(body)
    base_url = str(body.get("base_url") or "") or None
    if body.get("stream"):
        return stream_events(
            stream_text_chat_completion(request.backend, request.messages, request.model, base_url),
            request.model,
            count_message_tokens(request.messages, request.model),
            request.tools,
            request.stop_sequences,
            request.max_tokens,
        )
    text = collect_chat_content(stream_text_chat_completion(request.backend, request.messages, request.model, base_url))
    return message_response(
        request.model,
        text,
        count_message_tokens(request.messages, request.model),
        request.tools,
        request.stop_sequences,
        request.max_tokens,
    )


def count_tokens(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        raise HTTPException(status_code=400, detail="messages: at least one message is required")
    payload = preprocess_payload(dict(body))
    messages = normalize_messages(payload.get("messages"), payload.get("system"))
    model = str(payload.get("model") or "auto").strip() or "auto"
    return {"input_tokens": count_message_tokens(messages, model)}
