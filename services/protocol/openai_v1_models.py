from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.account_service import account_service
from services.openai_backend_api import OpenAIBackendAPI
from utils.helper import CODEX_IMAGE_MODEL


def list_models() -> dict[str, Any]:
    result = OpenAIBackendAPI().list_models()
    data = result.get("data")
    if not isinstance(data, list):
        return result
    seen = {str(item.get("id") or "").strip() for item in data if isinstance(item, dict)}
    dynamic_models: set[str] = set()
    accounts = account_service.list_accounts()
    web_image_accounts = [
        account
        for account in accounts
        if isinstance(account, dict)
    ]
    codex_types = {
        normalized
        for account in accounts
        if isinstance(account, dict)
           and account_service._normalize_source_type(account.get("source_type")) == "codex"
           and (normalized := account_service._normalize_account_type(account.get("type")))
    }

    if web_image_accounts:
        dynamic_models.add("gpt-image-2")
    if codex_types & {"Plus", "Team", "Pro"}:
        dynamic_models.add(CODEX_IMAGE_MODEL)
    if "Plus" in codex_types:
        dynamic_models.add(f"plus-{CODEX_IMAGE_MODEL}")
    if "Team" in codex_types:
        dynamic_models.add(f"team-{CODEX_IMAGE_MODEL}")
    if "Pro" in codex_types:
        dynamic_models.add(f"pro-{CODEX_IMAGE_MODEL}")

    for model in sorted(dynamic_models):
        if model not in seen:
            data.append({
                "id": model,
                "object": "model",
                "created": 0,
                "owned_by": "chatgpt2api",
                "permission": [],
                "root": model,
                "parent": None,
            })
    return result


def to_anthropic_models(result: dict[str, Any]) -> dict[str, Any]:
    """Convert the OpenAI-style model list to the Anthropic GET /v1/models shape."""
    data = result.get("data")
    items = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    models = []
    for item in items:
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        created = item.get("created")
        created_at = (
            datetime.fromtimestamp(created, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            if isinstance(created, (int, float)) and created > 0
            else "1970-01-01T00:00:00Z"
        )
        models.append({
            "type": "model",
            "id": model_id,
            "display_name": model_id,
            "created_at": created_at,
        })
    return {
        "data": models,
        "first_id": models[0]["id"] if models else None,
        "last_id": models[-1]["id"] if models else None,
        "has_more": False,
    }
