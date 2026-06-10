from __future__ import annotations

import unittest

from services.protocol import anthropic_v1_messages as messages


def chunk(text: str = "", finish_reason: str | None = None) -> dict:
    delta = {"content": text} if text else {}
    return {"choices": [{"delta": delta, "finish_reason": finish_reason}]}


def run_stream(chunks, model="auto", tools=None, stop_sequences=None, max_tokens=None) -> list[dict]:
    return list(messages.stream_events(iter(chunks), model, 10, tools, stop_sequences, max_tokens))


def events_of(events: list[dict], event_type: str) -> list[dict]:
    return [event for event in events if event.get("type") == event_type]


def streamed_text(events: list[dict]) -> str:
    return "".join(
        str(event["delta"].get("text") or "")
        for event in events_of(events, "content_block_delta")
        if event["delta"].get("type") == "text_delta"
    )


class StopSequenceHelpersTests(unittest.TestCase):
    def test_split_uses_earliest_match(self):
        text, matched = messages.split_stop_sequences("alpha STOP beta END gamma", ["END", "STOP"])
        self.assertEqual(text, "alpha ")
        self.assertEqual(matched, "STOP")

    def test_split_without_match(self):
        text, matched = messages.split_stop_sequences("hello", ["STOP"])
        self.assertEqual(text, "hello")
        self.assertEqual(matched, "")

    def test_holdback_partial_prefix(self):
        self.assertEqual(messages.stop_holdback("hello ST", ["STOP"]), 2)
        self.assertEqual(messages.stop_holdback("hello", ["STOP"]), 0)
        self.assertEqual(messages.stop_holdback("xS", ["STOP", "Sx"]), 1)


class MessageResponseTests(unittest.TestCase):
    def test_plain_response(self):
        response = messages.message_response("auto", "hello world", 10)
        self.assertEqual(response["stop_reason"], "end_turn")
        self.assertIsNone(response["stop_sequence"])
        self.assertEqual(response["content"], [{"type": "text", "text": "hello world"}])
        usage = response["usage"]
        self.assertEqual(usage["input_tokens"], 10)
        self.assertEqual(usage["cache_creation_input_tokens"], 0)
        self.assertEqual(usage["cache_read_input_tokens"], 0)
        self.assertGreater(usage["output_tokens"], 0)

    def test_stop_sequence_truncates(self):
        response = messages.message_response("auto", "hello STOP world", 10, stop_sequences=["STOP"])
        self.assertEqual(response["stop_reason"], "stop_sequence")
        self.assertEqual(response["stop_sequence"], "STOP")
        # content_blocks strips surrounding whitespace (pre-existing behavior)
        self.assertEqual(response["content"][0]["text"], "hello")

    def test_max_tokens_truncates(self):
        long_text = "word " * 200
        response = messages.message_response("auto", long_text, 10, max_tokens=5)
        self.assertEqual(response["stop_reason"], "max_tokens")
        self.assertEqual(response["usage"]["output_tokens"], 5)
        self.assertLess(len(response["content"][0]["text"]), len(long_text))

    def test_stop_sequence_disables_tool_parsing(self):
        text = "before STOP <tool_calls><tool_call><tool_name>x</tool_name><parameters>{}</parameters></tool_call></tool_calls>"
        tools = [{"name": "x", "description": "", "input_schema": {}}]
        response = messages.message_response("auto", text, 10, tools=tools, stop_sequences=["STOP"])
        self.assertEqual(response["stop_reason"], "stop_sequence")
        self.assertTrue(all(block["type"] == "text" for block in response["content"]))


class StreamEventsTests(unittest.TestCase):
    def test_basic_stream_shape(self):
        events = run_stream([chunk("hello "), chunk("world"), chunk(finish_reason="stop")])
        types = [event["type"] for event in events]
        self.assertEqual(types[0], "message_start")
        self.assertEqual(types[1], "ping")
        self.assertEqual(types[2], "content_block_start")
        self.assertEqual(types[-2], "message_delta")
        self.assertEqual(types[-1], "message_stop")
        self.assertEqual(streamed_text(events), "hello world")
        message_delta = events_of(events, "message_delta")[0]
        self.assertEqual(message_delta["delta"]["stop_reason"], "end_turn")
        self.assertIsNone(message_delta["delta"]["stop_sequence"])
        self.assertGreater(message_delta["usage"]["output_tokens"], 0)
        message_start = events_of(events, "message_start")[0]
        self.assertIn("cache_creation_input_tokens", message_start["message"]["usage"])
        message_stop = events_of(events, "message_stop")[0]
        self.assertEqual(set(message_stop.keys()), {"type"})

    def test_stream_without_finish_reason_still_closes(self):
        events = run_stream([chunk("hello")])
        types = [event["type"] for event in events]
        self.assertIn("message_delta", types)
        self.assertEqual(types[-1], "message_stop")
        self.assertEqual(events_of(events, "message_delta")[0]["delta"]["stop_reason"], "end_turn")

    def test_stream_stop_sequence_across_chunks(self):
        events = run_stream(
            [chunk("hello ST"), chunk("OP world"), chunk(finish_reason="stop")],
            stop_sequences=["STOP"],
        )
        self.assertEqual(streamed_text(events), "hello ")
        message_delta = events_of(events, "message_delta")[0]
        self.assertEqual(message_delta["delta"]["stop_reason"], "stop_sequence")
        self.assertEqual(message_delta["delta"]["stop_sequence"], "STOP")

    def test_stream_holdback_released_when_not_stop(self):
        events = run_stream(
            [chunk("hello ST"), chunk("ILL world"), chunk(finish_reason="stop")],
            stop_sequences=["STOP"],
        )
        self.assertEqual(streamed_text(events), "hello STILL world")
        self.assertEqual(events_of(events, "message_delta")[0]["delta"]["stop_reason"], "end_turn")

    def test_stream_max_tokens(self):
        events = run_stream(
            [chunk("one two three four five six seven eight"), chunk(finish_reason="stop")],
            max_tokens=3,
        )
        message_delta = events_of(events, "message_delta")[0]
        self.assertEqual(message_delta["delta"]["stop_reason"], "max_tokens")
        self.assertEqual(message_delta["usage"]["output_tokens"], 3)
        self.assertTrue("one two three four five six seven eight".startswith(streamed_text(events)))
        self.assertLess(len(streamed_text(events)), len("one two three four five six seven eight"))

    def test_stream_tool_use(self):
        tools = [{"name": "get_weather", "description": "d", "input_schema": {}}]
        text = (
            "checking<tool_calls><tool_call><tool_name>get_weather</tool_name>"
            "<parameters>{\"location\": \"Paris\"}</parameters></tool_call></tool_calls>"
        )
        events = run_stream([chunk(text), chunk(finish_reason="stop")], tools=tools)
        starts = events_of(events, "content_block_start")
        self.assertEqual(starts[0]["content_block"]["type"], "text")
        tool_starts = [event for event in starts if event["content_block"]["type"] == "tool_use"]
        self.assertEqual(len(tool_starts), 1)
        self.assertEqual(tool_starts[0]["content_block"]["name"], "get_weather")
        message_delta = events_of(events, "message_delta")[0]
        self.assertEqual(message_delta["delta"]["stop_reason"], "tool_use")
        indexes = [event["index"] for event in starts]
        self.assertEqual(indexes, sorted(indexes))

    def test_stream_closes_upstream_iterator(self):
        closed = []

        def upstream():
            try:
                yield chunk("hello STOP world")
                yield chunk(finish_reason="stop")
            finally:
                closed.append(True)

        events = list(messages.stream_events(upstream(), "auto", 10, None, ["STOP"], None))
        self.assertTrue(closed)
        self.assertEqual(events_of(events, "message_delta")[0]["delta"]["stop_reason"], "stop_sequence")


class CountTokensTests(unittest.TestCase):
    def test_count_tokens(self):
        result = messages.count_tokens({
            "model": "auto",
            "system": "be brief",
            "messages": [{"role": "user", "content": "hello"}],
        })
        self.assertGreater(result["input_tokens"], 0)

    def test_count_tokens_requires_messages(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException):
            messages.count_tokens({"model": "auto", "messages": []})


if __name__ == "__main__":
    unittest.main()
