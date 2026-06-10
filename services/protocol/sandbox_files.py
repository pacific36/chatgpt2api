"""Sandbox link handling for the text chat link.

The upstream model can answer with markdown links that use the `sandbox:`
scheme (e.g. ``[download](sandbox:/mnt/data/report.txt)``). Those links only
resolve inside the ChatGPT web app — through this proxy they are dead links the
user cannot click, because the real download endpoint lives on chatgpt.com and
requires the account session.

When sandbox download is enabled we rewrite each link to a *lazy* proxy URL
(`/sandbox-files?...`). On click, the proxy re-resolves the file upstream using
the account that created the conversation and streams the bytes through —
nothing is stored locally. The link is signed (HMAC over its parameters) so the
endpoint only honours links this server minted. Its lifetime matches the
upstream conversation/account: valid while upstream is, dead once it isn't.

When disabled (or when we lack a conversation id / account ref) we fall back to
rewriting the dead link into a clear note instead.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable, Iterable, Iterator
from urllib.parse import unquote, urlencode

SANDBOX_TOKEN = "sandbox:"
SANDBOX_NOTE = "（⚠️ 该文件由模型代码沙箱生成，无法通过本服务下载）"
SANDBOX_DOWNLOAD_PATH = "/sandbox-files"

# [label](sandbox:/mnt/data/foo.txt)
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\(\s*sandbox:([^)]*)\)")
# bare sandbox:/mnt/data/foo.txt, optionally wrapped in backticks
_BARE_LINK = re.compile(r"`?sandbox:(/[^\s`)\]<>\"']+)`?")
_TERMINATOR = re.compile(r"[\s)\]`<>\"']")


def contains_sandbox_link(text: str) -> bool:
    return SANDBOX_TOKEN in (text or "")


def _file_label(path: str) -> str:
    cleaned = unquote(path.strip().strip("`")).rstrip("/")
    name = cleaned.rsplit("/", 1)[-1]
    return name or cleaned or "文件"


def rewrite_sandbox_links(text: str) -> str:
    """Replace dead sandbox links with an inline note. Markdown links keep their
    label; bare links fall back to the file name. Used when real resolution is
    disabled or unavailable."""
    if not contains_sandbox_link(text):
        return text

    def replace_markdown(match: re.Match[str]) -> str:
        label = match.group(1).strip() or _file_label(match.group(2))
        return f"{label}{SANDBOX_NOTE}"

    # Markdown form first so its parenthesised target is consumed before the
    # bare-link pass runs.
    text = _MARKDOWN_LINK.sub(replace_markdown, text)
    text = _BARE_LINK.sub(lambda match: f"{_file_label(match.group(1))}{SANDBOX_NOTE}", text)
    return text


def _clean_sandbox_path(raw: str) -> str:
    return unquote(raw.strip().strip("`").strip()).rstrip("/")


def _sign(secret: str, conversation_id: str, message_id: str, sandbox_path: str, account_ref: str) -> str:
    message = "\n".join([conversation_id, message_id, sandbox_path, account_ref]).encode("utf-8")
    return hmac.new(str(secret or "").encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]


def verify_sandbox_signature(
    secret: str,
    conversation_id: str,
    message_id: str,
    sandbox_path: str,
    account_ref: str,
    signature: str,
) -> bool:
    expected = _sign(secret, conversation_id, message_id, sandbox_path, account_ref)
    return hmac.compare_digest(expected, str(signature or ""))


def build_sandbox_url(
    secret: str,
    conversation_id: str,
    message_id: str,
    sandbox_path: str,
    account_ref: str,
    base_url: str | None = None,
) -> str:
    signature = _sign(secret, conversation_id, message_id, sandbox_path, account_ref)
    query = urlencode({
        "cid": conversation_id,
        "mid": message_id,
        "p": sandbox_path,
        "a": account_ref,
        "s": signature,
    })
    root = (base_url or "").rstrip("/")
    return f"{root}{SANDBOX_DOWNLOAD_PATH}?{query}"


class SandboxLinkError(Exception):
    """Signature verification failed — the link was not minted by this server."""


def _detail_message_id(backend: object, conversation_id: str) -> str:
    """Any message id in the conversation works for the resolver; pull one from
    the conversation detail when the link did not carry one."""
    detail = backend._get_conversation(conversation_id)  # type: ignore[attr-defined]
    for node in (detail.get("mapping") or {}).values():
        message = (node or {}).get("message") or {}
        if message.get("id"):
            return str(message["id"])
    raise RuntimeError("no message id available for sandbox resolution")


def fetch_sandbox_file(
    conversation_id: str,
    message_id: str,
    sandbox_path: str,
    account_ref: str,
    signature: str,
) -> tuple[bytes, str, str]:
    """Click-time handler: verify the link, find the owning account, resolve the
    file upstream and return (bytes, file_name, mime). Raises SandboxLinkError on
    a bad signature; raises for any upstream/account failure (→ dead link)."""
    from services.account_service import account_service
    from services.config import config
    from services.openai_backend_api import OpenAIBackendAPI

    if not verify_sandbox_signature(config.auth_key, conversation_id, message_id, sandbox_path, account_ref, signature):
        raise SandboxLinkError("invalid signature")
    token = account_service.get_token_by_account_ref(account_ref)
    if not token:
        raise RuntimeError("owning account is no longer available")
    backend = OpenAIBackendAPI(access_token=token)
    mid = message_id or _detail_message_id(backend, conversation_id)
    return backend.download_sandbox_file(conversation_id, mid, sandbox_path)


def resolve_sandbox_links(
    text: str,
    backend: object,
    conversation_id: str,
    message_id: str,
    base_url: str | None = None,
) -> str:
    """Rewrite each sandbox link to a lazy, signed proxy URL that resolves the
    file upstream on click. Falls back to the dead-link note when we lack the
    conversation id / owning account needed to build a working link."""
    if not contains_sandbox_link(text):
        return text

    from services.account_service import account_service
    from services.config import config

    account_ref = account_service.account_ref_for_token(getattr(backend, "access_token", "")) if backend else ""
    if not conversation_id or not account_ref:
        return rewrite_sandbox_links(text)
    secret = config.auth_key

    def proxy_url(sandbox_path: str) -> str:
        return build_sandbox_url(secret, conversation_id, message_id, _clean_sandbox_path(sandbox_path), account_ref, base_url)

    def replace_markdown(match: re.Match[str]) -> str:
        label = match.group(1).strip() or _file_label(match.group(2))
        return f"[{label}]({proxy_url(match.group(2))})"

    def replace_bare(match: re.Match[str]) -> str:
        return f"[{_file_label(match.group(1))}]({proxy_url(match.group(1))})"

    text = _MARKDOWN_LINK.sub(replace_markdown, text)
    text = _BARE_LINK.sub(replace_bare, text)
    return text


# A markdown link still being formed at the buffer tail: an open label
# (``[lab``), a just-closed label (``[lab]``), or an open target (``[lab](url``)
# with no closing ``)`` yet. Anchored at the last ``[`` in the buffer.
_LINK_IN_PROGRESS = re.compile(r"\[[^\]]*$|\[[^\]]*\]$|\[[^\]]*\]\([^)]*$")


def _safe_emit_len(buffer: str) -> int:
    """How many leading chars are safe to emit without splitting a link that may
    still be forming at the buffer tail."""
    earliest = len(buffer)

    # A markdown link in progress — hold from its opening '[' so the whole link
    # is rewritten atomically once it completes.
    bracket = buffer.rfind("[")
    if bracket != -1 and _LINK_IN_PROGRESS.match(buffer[bracket:]):
        earliest = min(earliest, bracket)

    # A bare 'sandbox:' token whose value has not been terminated yet.
    bare = buffer.rfind(SANDBOX_TOKEN)
    if bare != -1 and not _TERMINATOR.search(buffer[bare + len(SANDBOX_TOKEN):]):
        earliest = min(earliest, bare)

    # Trailing partial prefix of the 'sandbox:' token (e.g. ends with 'sandbo').
    for size in range(min(len(SANDBOX_TOKEN) - 1, len(buffer)), 0, -1):
        if buffer.endswith(SANDBOX_TOKEN[:size]):
            earliest = min(earliest, len(buffer) - size)
            break

    return earliest


def scrub_sandbox_stream(
    deltas: Iterable[str],
    rewriter: Callable[[str], str] = rewrite_sandbox_links,
) -> Iterator[str]:
    """Wrap a stream of text deltas, rewriting sandbox links as they complete.

    Buffers only the minimal tail that could still be part of a forming link, so
    ordinary text streams through with no added latency. ``rewriter`` is applied
    to each emitted segment — pass a real-resolution rewriter to turn dead links
    into working downloads, or the default note-rewriter to degrade gracefully.
    The rewriter only does work when a complete sandbox link is present in the
    segment, so non-link text incurs no extra cost.
    """
    buffer = ""
    for delta in deltas:
        if not delta:
            continue
        buffer += delta
        cut = _safe_emit_len(buffer)
        if cut > 0:
            chunk = rewriter(buffer[:cut])
            buffer = buffer[cut:]
            if chunk:
                yield chunk
    if buffer:
        chunk = rewriter(buffer)
        if chunk:
            yield chunk
