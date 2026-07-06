from __future__ import annotations

from typing import Any

from app.adapters.base import BalanceResult


async def fetch_balance(site: dict[str, Any]) -> BalanceResult:
    """Adapter used for local smoke tests and first-run UI checks."""
    return BalanceResult(
        status="ok",
        balance=123.45 * float(site.get("scale") or 1),
        currency=site.get("default_currency") or "USD",
        raw_response_preview='{"balance": 123.45}',
    )

