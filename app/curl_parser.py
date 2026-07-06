from __future__ import annotations

import json
import re
import shlex
from typing import Any
from urllib.parse import urlsplit


class CurlParseError(ValueError):
    pass


USERNAME_KEYS = ("email", "username", "account", "user", "login")
PASSWORD_KEYS = ("password", "passwd", "pass")


def _normalize_multiline_command(command: str) -> str:
    normalized = command.strip()
    normalized = re.sub(r"\\\r?\n", " ", normalized)
    normalized = re.sub(r"\^\r?\n", " ", normalized)
    normalized = re.sub(r"`\r?\n", " ", normalized)
    return normalized


def _parse_header(raw: str) -> tuple[str, str]:
    if ":" not in raw:
        raise CurlParseError(f"无法识别请求头: {raw}")
    name, value = raw.split(":", 1)
    return name.strip(), value.strip()


def _split_curl(command: str) -> list[str]:
    normalized = _normalize_multiline_command(command)
    try:
        tokens = shlex.split(normalized, posix=True)
    except ValueError as exc:
        raise CurlParseError(f"curl 命令解析失败: {exc}") from exc

    if not tokens:
        raise CurlParseError("curl 命令为空")
    executable = tokens[0].lower()
    if executable not in {"curl", "curl.exe"}:
        raise CurlParseError("当前仅支持粘贴 curl 命令")
    return tokens


def parse_curl_command(command: str) -> dict[str, str]:
    tokens = _split_curl(command)

    method = ""
    url = ""
    headers: dict[str, tuple[str, str]] = {}
    cookie_parts: list[str] = []
    data_parts: list[str] = []

    index = 1
    while index < len(tokens):
        token = tokens[index]

        if token in {"-H", "--header"}:
            if index + 1 >= len(tokens):
                raise CurlParseError("请求头参数缺少值")
            header_name, header_value = _parse_header(tokens[index + 1])
            headers[header_name.lower()] = (header_name, header_value)
            index += 2
            continue

        if token in {"-b", "--cookie"}:
            if index + 1 >= len(tokens):
                raise CurlParseError("Cookie 参数缺少值")
            cookie_parts.append(tokens[index + 1].strip())
            index += 2
            continue

        if token in {"-X", "--request"}:
            if index + 1 >= len(tokens):
                raise CurlParseError("请求方法参数缺少值")
            method = tokens[index + 1].strip().upper()
            index += 2
            continue

        if token in {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode"}:
            if index + 1 >= len(tokens):
                raise CurlParseError("请求体参数缺少值")
            data_parts.append(tokens[index + 1])
            index += 2
            continue

        if token.startswith("http://") or token.startswith("https://"):
            if not url:
                url = token
            index += 1
            continue

        index += 1

    if not url:
        raise CurlParseError("未识别到请求 URL")

    if not method:
        method = "POST" if data_parts else "GET"

    cookie = ""
    if "cookie" in headers:
        _, cookie = headers.pop("cookie")
    elif cookie_parts:
        cookie = "; ".join(part for part in cookie_parts if part)

    authorization = ""
    if "authorization" in headers:
        _, authorization = headers.pop("authorization")

    extra_headers = {original_name: value for original_name, value in headers.values()}

    parsed_url = urlsplit(url)
    if not parsed_url.scheme or not parsed_url.netloc:
        raise CurlParseError("URL 缺少 scheme 或 host")

    endpoint = parsed_url.path or "/"
    if parsed_url.query:
        endpoint = f"{endpoint}?{parsed_url.query}"

    request_body = "&".join(data_parts)

    return {
        "name": parsed_url.hostname or parsed_url.netloc,
        "base_url": f"{parsed_url.scheme}://{parsed_url.netloc}",
        "balance_endpoint": endpoint,
        "method": method,
        "authorization": authorization,
        "cookie": cookie,
        "extra_headers": json.dumps(extra_headers, ensure_ascii=False, indent=2),
        "request_body": request_body,
    }


def _replace_login_values(value: Any, found: dict[str, str]) -> Any:
    if isinstance(value, dict):
        replaced: dict[str, Any] = {}
        for key, item in value.items():
            lowered_key = str(key).lower()
            if lowered_key in USERNAME_KEYS and isinstance(item, str):
                found["username"] = item
                replaced[key] = "{{username}}"
            elif lowered_key in PASSWORD_KEYS and isinstance(item, str):
                found["password"] = item
                replaced[key] = "{{password}}"
            else:
                replaced[key] = _replace_login_values(item, found)
        return replaced
    if isinstance(value, list):
        return [_replace_login_values(item, found) for item in value]
    return value


def _infer_login_body_template(request_body: str) -> tuple[str, str, str]:
    if not request_body:
        return "", "", ""
    try:
        parsed = json.loads(request_body)
    except json.JSONDecodeError:
        return request_body, "", ""

    found: dict[str, str] = {"username": "", "password": ""}
    templated = _replace_login_values(parsed, found)
    return (
        json.dumps(templated, ensure_ascii=False, separators=(",", ":")),
        found["username"],
        found["password"],
    )


def parse_login_curl_command(command: str) -> dict[str, str | bool]:
    parsed = parse_curl_command(command)
    headers = json.loads(parsed["extra_headers"] or "{}")

    if parsed.get("cookie"):
        headers["Cookie"] = parsed["cookie"]
    if parsed.get("authorization"):
        headers["Authorization"] = parsed["authorization"]

    body_template, username, password = _infer_login_body_template(parsed.get("request_body", ""))

    return {
        "login_url": f"{parsed['base_url']}{parsed['balance_endpoint']}",
        "login_method": "api",
        "login_username": username,
        "login_password": password,
        "login_headers": json.dumps(headers, ensure_ascii=False, indent=2),
        "login_body_template": body_template,
        "login_token_path": "",
        "login_token_prefix": "Bearer",
        "login_cookie_from_response": False,
    }
