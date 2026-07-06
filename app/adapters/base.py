from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BalanceResult:
    status: str
    balance: float | None = None
    currency: str | None = None
    error: str = ""
    raw_response_preview: str = ""
    status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "balance": self.balance,
            "currency": self.currency,
            "error": self.error,
            "raw_response_preview": self.raw_response_preview,
            "status_code": self.status_code,
        }


class AdapterError(Exception):
    pass


def get_by_path(data: Any, path: str) -> Any:
    """Read a value from dict/list data using dotted paths like data.balance or items.0.value."""
    if not path:
        return None

    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise AdapterError(f"字段路径不存在: {path}")
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise AdapterError(f"列表路径无效: {path}") from exc
        else:
            raise AdapterError(f"无法继续读取字段路径: {path}")
    return current


def to_float(value: Any, scale: float) -> float:
    if value is None:
        raise AdapterError("余额字段为空")
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.startswith("$"):
            cleaned = cleaned[1:].strip()
        value = cleaned
    try:
        return float(value) * scale
    except (TypeError, ValueError) as exc:
        raise AdapterError(f"余额不是数字: {value!r}") from exc

