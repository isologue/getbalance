from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.adapters.base import AdapterError, get_by_path


HUMAN_CHECK_KEYWORDS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "cloudflare",
    "cf-challenge",
    "turnstile",
    "slider",
    "verify",
    "人机验证",
    "安全验证",
    "验证码",
)


@dataclass
class LoginResult:
    status: str
    authorization: str | None = None
    cookie: str | None = None
    error: str = ""
    raw_response_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "authorization": self.authorization,
            "cookie": self.cookie,
            "error": self.error,
            "raw_response_preview": self.raw_response_preview,
        }


def _parse_json_object(raw: str, field_name: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} 必须是 JSON 对象")
    return {str(key): str(value) for key, value in parsed.items()}


def _render_template(template: str, username: str, password: str) -> str:
    return template.replace("{{username}}", username).replace("{{password}}", password)


def _build_login_request(site: dict[str, Any]) -> tuple[dict[str, str], bytes | None]:
    headers = _parse_json_object(site.get("login_headers", "{}"), "login_headers")
    body = _render_template(
        site.get("login_body_template", ""),
        site.get("login_username", ""),
        site.get("login_password", ""),
    )
    return headers, body.encode("utf-8") if body else None


def _contains_human_check(text: str) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in HUMAN_CHECK_KEYWORDS)


def _split_keywords(raw: str) -> list[str]:
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _split_status_codes(raw: str) -> set[int]:
    codes: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            codes.add(int(item))
        except ValueError:
            continue
    return codes or {401, 403}


def is_auth_failure(site: dict[str, Any], result: dict[str, Any]) -> bool:
    status_code = result.get("status_code")
    if status_code in _split_status_codes(site.get("auth_fail_status_codes", "401,403")):
        return True

    haystack = " ".join(
        str(value or "")
        for value in (
            result.get("error"),
            result.get("raw_response_preview"),
            result.get("status"),
        )
    ).lower()
    return any(keyword in haystack for keyword in _split_keywords(site.get("auth_fail_keywords", "")))


def _cookies_from_response(response: httpx.Response) -> str:
    cookie_pairs: list[str] = []
    for cookie in response.cookies.jar:
        cookie_pairs.append(f"{cookie.name}={cookie.value}")
    return "; ".join(cookie_pairs)


async def login_site(site: dict[str, Any]) -> LoginResult:
    if not site.get("login_enabled"):
        return LoginResult(status="login_failed", error="未启用自动登录")
    if site.get("login_method", "api") != "api":
        return LoginResult(status="login_failed", error="首版仅支持 API 登录")
    if not site.get("login_url"):
        return LoginResult(status="login_failed", error="login_url 不能为空")

    try:
        headers, body = _build_login_request(site)
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.post(
                site["login_url"],
                headers=headers,
                content=body,
            )

        preview = response.text[:5000]
        if _contains_human_check(preview):
            return LoginResult(
                status="need_manual_login",
                error="登录响应疑似包含人机验证，需要人工登录后重新粘贴 Cookie/Token",
                raw_response_preview=preview,
            )
        if response.status_code >= 400:
            return LoginResult(
                status="login_failed",
                error=f"登录失败: HTTP {response.status_code}",
                raw_response_preview=preview,
            )

        authorization: str | None = None
        token_path = site.get("login_token_path", "").strip()
        if token_path:
            try:
                token = get_by_path(response.json(), token_path)
            except (ValueError, AdapterError) as exc:
                return LoginResult(
                    status="login_failed",
                    error=f"无法提取登录 token: {exc}",
                    raw_response_preview=preview,
                )
            prefix = (site.get("login_token_prefix") or "").strip()
            authorization = f"{prefix} {token}".strip() if prefix else str(token)

        cookie: str | None = None
        if site.get("login_cookie_from_response"):
            cookie = _cookies_from_response(response)

        if not authorization and not cookie:
            return LoginResult(
                status="login_failed",
                error="登录成功但未提取到 Authorization 或 Cookie",
                raw_response_preview=preview,
            )

        return LoginResult(
            status="ok",
            authorization=authorization,
            cookie=cookie,
            raw_response_preview=preview,
        )
    except Exception as exc:
        return LoginResult(status="login_failed", error=str(exc))


async def preview_login_request(site: dict[str, Any]) -> tuple[Any, str, str]:
    if not site.get("login_url"):
        raise ValueError("login_url 不能为空")

    headers, body = _build_login_request(site)
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.post(site["login_url"], headers=headers, content=body)

    preview = response.text[:5000]
    if _contains_human_check(preview):
        raise ValueError("登录响应疑似包含人机验证，无法自动预览 JSON")
    response.raise_for_status()

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"登录响应不是合法 JSON: {exc}") from exc

    return payload, preview, _cookies_from_response(response)
