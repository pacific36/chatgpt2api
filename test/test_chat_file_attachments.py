from __future__ import annotations

import base64
import unittest
from unittest import mock

from services.openai_backend_api import OpenAIBackendAPI
from services.protocol.conversation import normalize_messages
from utils.helper import extract_file_from_message_content


def data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


PDF_BYTES = b"%PDF-1.4 fake"
PNG_BYTES = b"\x89PNG fake"


class ExtractFileTests(unittest.TestCase):
    def test_openai_file_part(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "file", "file": {"filename": "doc.pdf", "file_data": data_url(PDF_BYTES, "application/pdf")}},
        ]
        files = extract_file_from_message_content(content)
        self.assertEqual(files, [(PDF_BYTES, "application/pdf", "doc.pdf")])

    def test_input_file_part(self):
        content = [{"type": "input_file", "filename": "notes.txt", "file_data": data_url(b"abc", "text/plain")}]
        files = extract_file_from_message_content(content)
        self.assertEqual(files, [(b"abc", "text/plain", "notes.txt")])

    def test_ignores_invalid_parts(self):
        content = [
            {"type": "file", "file": {"filename": "x.pdf", "file_data": "https://example.com/x.pdf"}},
            {"type": "file"},
            "junk",
        ]
        self.assertEqual(extract_file_from_message_content(content), [])


class NormalizeMessagesTests(unittest.TestCase):
    def test_file_part_passthrough(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "看看这个文件"},
                {"type": "file", "file": {"filename": "doc.pdf", "file_data": data_url(PDF_BYTES, "application/pdf")}},
            ],
        }]
        normalized = normalize_messages(messages)
        self.assertEqual(len(normalized), 1)
        parts = normalized[0]["content"]
        self.assertEqual(parts[0], {"type": "text", "text": "看看这个文件"})
        self.assertEqual(parts[1], {"type": "file", "data": PDF_BYTES, "mime": "application/pdf", "name": "doc.pdf"})

    def test_text_only_stays_string(self):
        normalized = normalize_messages([{"role": "user", "content": "hi"}])
        self.assertEqual(normalized[0]["content"], "hi")


class ConversationMessageBuildTests(unittest.TestCase):
    def _backend(self) -> OpenAIBackendAPI:
        return OpenAIBackendAPI(access_token="token")

    def test_document_only_attachment(self):
        backend = self._backend()
        with mock.patch.object(
            backend,
            "_upload_file",
            return_value={"file_id": "file-doc1", "file_name": "doc.pdf", "file_size": len(PDF_BYTES), "mime_type": "application/pdf"},
        ) as upload_file:
            result = backend._api_messages_to_conversation_messages([{
                "role": "user",
                "content": [
                    {"type": "text", "text": "总结这个文件"},
                    {"type": "file", "data": PDF_BYTES, "mime": "application/pdf", "name": "doc.pdf"},
                ],
            }])
        upload_file.assert_called_once_with(PDF_BYTES, "doc.pdf", "application/pdf")
        message = result[0]
        self.assertEqual(message["content"], {"content_type": "text", "parts": ["总结这个文件"]})
        self.assertEqual(message["metadata"]["attachments"], [{
            "id": "file-doc1",
            "mimeType": "application/pdf",
            "name": "doc.pdf",
            "size": len(PDF_BYTES),
        }])

    def test_image_and_document_combined(self):
        backend = self._backend()
        with mock.patch.object(
            backend,
            "_upload_image",
            return_value={"file_id": "file-img1", "file_name": "image_1.png", "file_size": len(PNG_BYTES), "mime_type": "image/png", "width": 10, "height": 10},
        ), mock.patch.object(
            backend,
            "_upload_file",
            return_value={"file_id": "file-doc1", "file_name": "doc.pdf", "file_size": len(PDF_BYTES), "mime_type": "application/pdf"},
        ):
            result = backend._api_messages_to_conversation_messages([{
                "role": "user",
                "content": [
                    {"type": "text", "text": "对比图和文档"},
                    {"type": "image", "data": PNG_BYTES, "mime": "image/png"},
                    {"type": "file", "data": PDF_BYTES, "mime": "application/pdf", "name": "doc.pdf"},
                ],
            }])
        message = result[0]
        self.assertEqual(message["content"]["content_type"], "multimodal_text")
        self.assertEqual(message["content"]["parts"][0]["content_type"], "image_asset_pointer")
        self.assertEqual(message["content"]["parts"][1], "对比图和文档")
        attachment_ids = [item["id"] for item in message["metadata"]["attachments"]]
        self.assertEqual(attachment_ids, ["file-img1", "file-doc1"])

    def test_file_without_token_raises(self):
        backend = OpenAIBackendAPI(access_token="")
        with self.assertRaises(RuntimeError):
            backend._api_messages_to_conversation_messages([{
                "role": "user",
                "content": [{"type": "file", "data": PDF_BYTES, "mime": "application/pdf", "name": "doc.pdf"}],
            }])

    def test_plain_text_unchanged(self):
        backend = self._backend()
        result = backend._api_messages_to_conversation_messages([{"role": "user", "content": "hello"}])
        self.assertEqual(result[0]["content"], {"content_type": "text", "parts": ["hello"]})


if __name__ == "__main__":
    unittest.main()
