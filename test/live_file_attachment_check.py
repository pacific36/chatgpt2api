"""Live upstream check for chat file attachments.

Usage: uv run python -m test.live_file_attachment_check [txt|pdf|image|all]

Sends a tiny attachment with a unique marker to the real ChatGPT backend using
the first usable account in the local pool, and verifies the assistant can read
the marker back. Network + account quota required; not part of unittest runs.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from services.openai_backend_api import OpenAIBackendAPI
from services.protocol.conversation import conversation_events

MARKER = "PINEAPPLE-7426"


def build_pdf(text: str) -> bytes:
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj".encode() + body + b"endobj\n")
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref_at}\n%%EOF".encode())
    return out.getvalue()


def build_image(text: str) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (480, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 45), text, fill="black")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def load_token() -> str:
    accounts = json.loads((Path(__file__).resolve().parents[1] / "data" / "accounts.json").read_text(encoding="utf-8"))
    for account in accounts:
        if account.get("status") == "正常" and account.get("access_token"):
            return account["access_token"]
    raise SystemExit("no usable account in data/accounts.json")


def run_case(token: str, name: str, parts: list[dict]) -> bool:
    backend = OpenAIBackendAPI(access_token=token)
    messages = [{"role": "user", "content": parts}]
    text = ""
    try:
        for event in conversation_events(backend, messages=messages, model="auto"):
            if event.get("type") == "conversation.delta":
                text = event.get("text") or text
    except Exception as exc:
        print(f"[{name}] FAILED: {exc}")
        return False
    found = MARKER in text
    print(f"[{name}] marker found: {found}")
    print(f"[{name}] reply: {text[:300]}")
    return found


def main() -> None:
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    token = load_token()
    prompt = "附件文件里有一个暗号，请只回复这个暗号本身，不要解释。"
    results = {}
    if which in ("txt", "all"):
        data = f"这是一份测试文档。\n暗号是：{MARKER}\n请记住它。".encode("utf-8")
        results["txt"] = run_case(token, "txt", [
            {"type": "text", "text": prompt},
            {"type": "file", "data": data, "mime": "text/plain", "name": "secret.txt"},
        ])
    if which in ("pdf", "all"):
        results["pdf"] = run_case(token, "pdf", [
            {"type": "text", "text": prompt},
            {"type": "file", "data": build_pdf(f"THE SECRET CODE IS {MARKER}"), "mime": "application/pdf", "name": "secret.pdf"},
        ])
    if which in ("image", "all"):
        results["image"] = run_case(token, "image", [
            {"type": "text", "text": "图片里写了一个暗号，请只回复这个暗号本身。"},
            {"type": "image", "data": build_image(f"CODE: {MARKER}"), "mime": "image/png"},
        ])
    print("summary:", results)


if __name__ == "__main__":
    main()
