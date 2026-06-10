"""End-to-end: lazy sandbox download through the proxy HTTP API.

Usage: uv run python -m test.live_sandbox_e2e

Drives POST /v1/chat/completions so the model writes a file, then clicks the
resulting /sandbox-files?... proxy link and verifies the bytes stream through.
Nothing is stored locally — the click resolves upstream on demand.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
AUTH = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["auth-key"]
PROMPT = (
    "Run code now with the python tool. Execute these two lines and show me the real "
    "execution output (not a code block):\n"
    "with open('/mnt/data/e2e_demo.csv','w') as f: f.write('x,y\\n10,20\\n30,40\\n')\n"
    "import os; print('SIZE', os.path.getsize('/mnt/data/e2e_demo.csv'))\n"
    "Then give me the download link for /mnt/data/e2e_demo.csv."
)


def extract_links(text: str) -> list[str]:
    return re.findall(r"\]\((https?://[^)]+/sandbox-files\?[^)]+)\)", text)


def check(client: TestClient, content: str) -> bool:
    print("\nassistant content:\n", content)
    if "sandbox:" in content:
        print("FAIL: raw sandbox: link still present")
        return False
    links = extract_links(content)
    if not links:
        print("NOTE: no /sandbox-files? proxy link (model may not have executed this run)")
        return False
    rel = links[0].split("testserver", 1)[1] if "testserver" in links[0] else links[0]
    resp = client.get(rel)
    print(f"GET {rel[:80]}... -> {resp.status_code}, bytes={len(resp.content)}, sample={resp.content[:40]!r}")
    print("disposition:", resp.headers.get("content-disposition"))
    ok = resp.status_code == 200 and len(resp.content) > 0
    print("DOWNLOAD OK" if ok else "DOWNLOAD FAIL")
    return ok


def main() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {AUTH}"}

    print("=== non-stream ===")
    r = client.post("/v1/chat/completions", headers=headers, json={
        "model": "auto", "messages": [{"role": "user", "content": PROMPT}],
    }, timeout=300)
    print("status:", r.status_code)
    ns_ok = check(client, r.json()["choices"][0]["message"]["content"]) if r.status_code == 200 else False
    if r.status_code != 200:
        print(r.text[:400])

    print("\n=== stream ===")
    r = client.post("/v1/chat/completions", headers=headers, json={
        "model": "auto", "stream": True,
        "messages": [{"role": "user", "content": PROMPT + " (second run)"}],
    }, timeout=300)
    full = ""
    for line in r.text.splitlines():
        if line.startswith("data:") and "[DONE]" not in line:
            try:
                full += json.loads(line[5:].strip())["choices"][0]["delta"].get("content", "")
            except Exception:
                pass
    st_ok = check(client, full)

    print("\n=== tampered signature is rejected ===")
    bad = client.get("/sandbox-files", params={"cid": "x", "mid": "y", "p": "/mnt/data/x", "a": "ref", "s": "deadbeef"})
    print("tampered ->", bad.status_code, "(expect 403)")

    print("\nsummary:", {"non_stream": ns_ok, "stream": st_ok, "tamper_403": bad.status_code == 403})


if __name__ == "__main__":
    main()
