from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.adapters.base import BalanceResult
from app.adapters import generic_json, mock


AdapterFunc = Callable[[dict[str, Any]], Awaitable[BalanceResult]]

ADAPTERS: dict[str, AdapterFunc] = {
    "generic_json": generic_json.fetch_balance,
    "mock": mock.fetch_balance,
}


def get_adapter(name: str) -> AdapterFunc:
    return ADAPTERS.get(name, generic_json.fetch_balance)

