from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import httpx

from app.adapters.base import AdapterError, BalanceResult, get_by_path, to_float


def _build_url(base_url: str, endpoint: str) -> str:
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))


def _parse_extra_headers(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"extra_headers 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AdapterError("extra_headers 必须是 JSON 对象")
    return {str(key): str(value) for key, value in parsed.items()}


async def fetch_json_payload(site: dict[str, Any]) -> tuple[Any, str]:
    url = _build_url(site["base_url"], site["balance_endpoint"])
    method = site.get("method", "GET").upper()
    headers = _parse_extra_headers(site.get("extra_headers", "{}"))
    request_body = site.get("request_body", "")

    if site.get("cookie"):
        headers["Cookie"] = site["cookie"]
    if site.get("authorization"):
        headers["Authorization"] = site["authorization"]

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            content=request_body.encode("utf-8") if request_body else None,
        )

    preview = response.text[:5000]
    response.raise_for_status()

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise AdapterError(f"响应不是合法 JSON: {exc}") from exc

    return payload, preview


async def fetch_balance(site: dict[str, Any]) -> BalanceResult:
    preview = ""
    status_code: int | None = None
    try:
        payload, preview = await fetch_json_payload(site)

        raw_balance = get_by_path(payload, site["balance_path"])
        balance = to_float(raw_balance, float(site.get("scale") or 1))

        currency = site.get("default_currency") or "USD"
        if site.get("currency_path"):
            raw_currency = get_by_path(payload, site["currency_path"])
            if raw_currency:
                currency = str(raw_currency)

        return BalanceResult(
            status="ok",
            balance=balance,
            currency=currency,
            raw_response_preview=preview,
            status_code=200,
        )
    except Exception as exc:
        response = getattr(exc, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            preview = getattr(response, "text", preview)[:5000]
        return BalanceResult(
            status="error",
            error=str(exc),
            raw_response_preview=preview,
            status_code=status_code,
        )

