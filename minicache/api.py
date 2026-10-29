"""Thin client for the local Anthropic-style /v1/messages endpoint.

The proxy strips cache telemetry, so the MODEL is used only to generate
plausible multi-turn tool traffic; cache numbers come from replaying the
journal through the simulator. If the API is unreachable, the harness falls
back to a deterministic scripted agent so the journal can still be produced
(and the fallback is flagged in every record).
"""

import json
import os
import urllib.request


class ApiResult:
    def __init__(self, ok: bool, text: str, error: str | None = None,
                 fallback: bool = False):
        self.ok = ok
        self.text = text
        self.error = error
        self.fallback = fallback


def call_messages(request: dict, cfg) -> ApiResult:
    base = os.environ.get("ANTHROPIC_BASE_URL", cfg.base_url).rstrip("/")
    token = os.environ.get(cfg.api_key_env, "")
    body = dict(request)  # wire requests are pure provider-visible objects
    url = base + "/messages"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "x-api-key": token,
            "Authorization": "Bearer " + token,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            raw = r.read().decode()
        # endpoint streams SSE; extract text blocks
        text = _extract_text(raw)
        if text is None:
            # maybe plain JSON
            try:
                d = json.loads(raw.split("data: [DONE]")[0].strip())
                text = "".join(
                    b.get("text", "") for b in d.get("content", [])
                    if b.get("type") == "text"
                ) or None
            except Exception:
                text = None
        if text is None:
            return ApiResult(False, "", "no text in response", fallback=False)
        return ApiResult(True, text)
    except Exception as e:  # noqa: BLE001 - proxy flakiness is expected
        return ApiResult(False, "", str(e), fallback=False)


def _extract_text(sse: str) -> str | None:
    parts = []
    saw_any = False
    for line in sse.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            d = json.loads(line[6:])
        except Exception:
            continue
        if d.get("type") == "content_block_delta":
            delta = d.get("delta", {})
            if delta.get("type") == "text_delta":
                parts.append(delta.get("text", ""))
                saw_any = True
    if not saw_any:
        return None
    return "".join(parts)
