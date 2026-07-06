from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.adapters.base import BalanceResult
from app.login import LoginResult


def build_client(tmp_path: Path) -> TestClient:
    os.environ["GETBALANCE_DB"] = str(tmp_path / "test.sqlite3")

    import app.db
    import app.main

    importlib.reload(app.db)
    importlib.reload(app.main)
    app.db.init_db()
    return TestClient(app.main.app)


def site_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Mock Site",
        "base_url": "https://mock.local",
        "balance_endpoint": "/balance",
        "method": "GET",
        "adapter": "mock",
        "cookie": "",
        "authorization": "",
        "extra_headers": "{}",
        "request_body": "",
        "balance_path": "balance",
        "currency_path": "",
        "default_currency": "USD",
        "scale": 1,
        "notes": "for test",
        "is_active": True,
        "login_enabled": False,
        "login_url": "",
        "login_method": "api",
        "login_username": "",
        "login_password": "",
        "login_headers": "{}",
        "login_body_template": "",
        "login_token_path": "",
        "login_token_prefix": "Bearer",
        "login_cookie_from_response": False,
        "auth_fail_status_codes": "401,403",
        "auth_fail_keywords": "unauthorized,token expired,login required,未登录,登录过期",
    }
    payload.update(overrides)
    return payload


def test_create_mock_site_and_refresh(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    create_resp = client.post("/api/sites", json=site_payload())
    assert create_resp.status_code == 200
    site_id = create_resp.json()["id"]

    refresh_resp = client.post(f"/api/sites/{site_id}/refresh")
    assert refresh_resp.status_code == 200
    payload = refresh_resp.json()
    assert payload["status"] == "ok"
    assert payload["balance"] == 123.45
    assert payload["currency"] == "USD"

    history_resp = client.get(f"/api/history/{site_id}")
    assert history_resp.status_code == 200
    history = history_resp.json()
    assert len(history) == 1
    assert history[0]["status"] == "ok"


def test_disabled_site_returns_error(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    create_resp = client.post(
        "/api/sites",
        json=site_payload(
            name="Disabled Site",
            adapter="generic_json",
            balance_path="not.exists",
            is_active=False,
        ),
    )
    site_id = create_resp.json()["id"]

    refresh_resp = client.post(f"/api/sites/{site_id}/refresh")
    assert refresh_resp.status_code == 200
    payload = refresh_resp.json()
    assert payload["status"] == "error"
    assert payload["error"] == "站点已停用"


def test_parse_curl_command_autofill(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    curl_command = r"""curl 'https://happycode.vip/api/v1/auth/me?timezone=Asia%2FShanghai' \
  -H 'accept: application/json, text/plain, */*' \
  -H 'accept-language: zh' \
  -H 'authorization: Bearer token-123' \
  -b 'foo=bar; hello=world' \
  -H 'referer: https://happycode.vip/profile' \
  -H 'user-agent: Mozilla/5.0'"""

    response = client.post("/api/tools/parse-curl", json={"command": curl_command})
    assert response.status_code == 200

    payload = response.json()
    assert payload["name"] == "happycode.vip"
    assert payload["base_url"] == "https://happycode.vip"
    assert payload["balance_endpoint"] == "/api/v1/auth/me?timezone=Asia%2FShanghai"
    assert payload["method"] == "GET"
    assert payload["authorization"] == "Bearer token-123"
    assert payload["cookie"] == "foo=bar; hello=world"
    assert '"accept"' in payload["extra_headers"]
    assert payload["request_body"] == ""


def test_parse_curl_post_body(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    curl_command = r"""curl 'https://demo.local/api/balance' \
  -H 'content-type: application/json' \
  --data-raw '{"user_id":1}'"""

    response = client.post("/api/tools/parse-curl", json={"command": curl_command})
    assert response.status_code == 200

    payload = response.json()
    assert payload["method"] == "POST"
    assert payload["request_body"] == '{"user_id":1}'


def test_parse_login_curl_autofill(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    curl_command = r"""curl 'https://sub.g-aisc.com/api/v1/auth/login' \
  -H 'accept: application/json, text/plain, */*' \
  -H 'accept-language: zh' \
  -H 'content-type: application/json' \
  -b '__stripe_mid=mid; __stripe_sid=sid' \
  -H 'origin: https://sub.g-aisc.com' \
  -H 'referer: https://sub.g-aisc.com/login' \
  --data-raw '{"email":"2087989335@qq.com","password":"testAbc"}'"""

    response = client.post("/api/tools/parse-login-curl", json={"command": curl_command})
    assert response.status_code == 200

    payload = response.json()
    assert payload["login_url"] == "https://sub.g-aisc.com/api/v1/auth/login"
    assert payload["login_method"] == "api"
    assert payload["login_username"] == "2087989335@qq.com"
    assert payload["login_password"] == "testAbc"
    assert payload["login_body_template"] == '{"email":"{{username}}","password":"{{password}}"}'
    assert '"content-type": "application/json"' in payload["login_headers"]
    assert '"Cookie": "__stripe_mid=mid; __stripe_sid=sid"' in payload["login_headers"]
    assert payload["login_token_prefix"] == "Bearer"


def test_preview_login_request_returns_json(tmp_path: Path, monkeypatch) -> None:
    client = build_client(tmp_path)

    import app.main

    async def fake_preview_login_request(site: dict[str, str]):
        assert site["login_url"] == "https://sub.g-aisc.com/api/v1/auth/login"
        assert site["login_username"] == "u@test.com"
        assert site["login_password"] == "p123"
        return (
            {"data": {"token": "fresh-token"}},
            '{"data":{"token":"fresh-token"}}',
            "sid=fresh",
        )

    monkeypatch.setattr(app.main, "preview_login_request", fake_preview_login_request)

    response = client.post(
        "/api/tools/preview-login-request",
        json={
            "login_url": "https://sub.g-aisc.com/api/v1/auth/login",
            "login_method": "api",
            "login_username": "u@test.com",
            "login_password": "p123",
            "login_headers": '{"content-type":"application/json"}',
            "login_body_template": '{"email":"{{username}}","password":"{{password}}"}',
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["payload"]["data"]["token"] == "fresh-token"
    assert payload["cookies"] == "sid=fresh"


def test_preview_request_returns_json(tmp_path: Path, monkeypatch) -> None:
    client = build_client(tmp_path)

    import app.adapters.generic_json as generic_json

    async def fake_fetch_json_payload(site: dict[str, str]):
        assert site["base_url"] == "https://happycode.vip"
        return (
            {"data": {"balance": 88.8, "currency": "USD"}},
            '{"data":{"balance":88.8,"currency":"USD"}}',
        )

    monkeypatch.setattr(generic_json, "fetch_json_payload", fake_fetch_json_payload)

    response = client.post(
        "/api/tools/preview-request",
        json={
            "base_url": "https://happycode.vip",
            "balance_endpoint": "/api/v1/auth/me",
            "method": "GET",
            "authorization": "Bearer token-123",
            "cookie": "foo=bar",
            "extra_headers": '{"accept":"application/json"}',
            "request_body": "",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["payload"]["data"]["balance"] == 88.8
    assert payload["payload"]["data"]["currency"] == "USD"
    assert "balance" in payload["raw_response_preview"]


def test_refresh_auto_login_token_success(tmp_path: Path, monkeypatch) -> None:
    client = build_client(tmp_path)

    import app.main

    calls = {"count": 0}

    async def fake_adapter(site: dict[str, Any]) -> BalanceResult:
        calls["count"] += 1
        if calls["count"] == 1:
            return BalanceResult(status="error", error="unauthorized", status_code=401)
        assert site["authorization"] == "Bearer fresh-token"
        return BalanceResult(status="ok", balance=42, currency="USD", status_code=200)

    async def fake_login(site: dict[str, Any]) -> LoginResult:
        assert site["login_enabled"]
        return LoginResult(status="ok", authorization="Bearer fresh-token")

    monkeypatch.setattr(app.main, "get_adapter", lambda _: fake_adapter)
    monkeypatch.setattr(app.main, "login_site", fake_login)

    create_resp = client.post(
        "/api/sites",
        json=site_payload(
            adapter="generic_json",
            login_enabled=True,
            login_url="https://mock.local/login",
            login_username="u",
            login_password="p",
        ),
    )
    site_id = create_resp.json()["id"]

    refresh_resp = client.post(f"/api/sites/{site_id}/refresh")
    assert refresh_resp.status_code == 200
    payload = refresh_resp.json()
    assert payload["status"] == "login_refreshed"
    assert payload["balance"] == 42
    assert payload["login_status"] == "ok"

    site = client.get(f"/api/sites/{site_id}").json()
    assert site["authorization"] == "Bearer fresh-token"


def test_refresh_auto_login_cookie_success(tmp_path: Path, monkeypatch) -> None:
    client = build_client(tmp_path)

    import app.main

    calls = {"count": 0}

    async def fake_adapter(site: dict[str, Any]) -> BalanceResult:
        calls["count"] += 1
        if calls["count"] == 1:
            return BalanceResult(status="error", error="401", status_code=401)
        assert site["cookie"] == "sid=fresh"
        return BalanceResult(status="ok", balance=10, currency="USD", status_code=200)

    async def fake_login(site: dict[str, Any]) -> LoginResult:
        return LoginResult(status="ok", cookie="sid=fresh")

    monkeypatch.setattr(app.main, "get_adapter", lambda _: fake_adapter)
    monkeypatch.setattr(app.main, "login_site", fake_login)

    create_resp = client.post(
        "/api/sites",
        json=site_payload(adapter="generic_json", login_enabled=True, login_cookie_from_response=True),
    )
    site_id = create_resp.json()["id"]

    payload = client.post(f"/api/sites/{site_id}/refresh").json()
    assert payload["status"] == "login_refreshed"
    assert payload["balance"] == 10
    site = client.get(f"/api/sites/{site_id}").json()
    assert site["cookie"] == "sid=fresh"


def test_refresh_auto_login_failed(tmp_path: Path, monkeypatch) -> None:
    client = build_client(tmp_path)

    import app.main

    async def fake_adapter(site: dict[str, Any]) -> BalanceResult:
        return BalanceResult(status="error", error="unauthorized", status_code=401)

    async def fake_login(site: dict[str, Any]) -> LoginResult:
        return LoginResult(status="login_failed", error="登录失败: HTTP 401")

    monkeypatch.setattr(app.main, "get_adapter", lambda _: fake_adapter)
    monkeypatch.setattr(app.main, "login_site", fake_login)

    create_resp = client.post("/api/sites", json=site_payload(adapter="generic_json", login_enabled=True))
    site_id = create_resp.json()["id"]

    payload = client.post(f"/api/sites/{site_id}/refresh").json()
    assert payload["status"] == "login_failed"
    assert payload["login_status"] == "login_failed"
    assert "401" in payload["error"]


def test_refresh_need_manual_login_on_human_check(tmp_path: Path, monkeypatch) -> None:
    client = build_client(tmp_path)

    import app.main

    async def fake_adapter(site: dict[str, Any]) -> BalanceResult:
        return BalanceResult(status="error", error="unauthorized", status_code=401)

    async def fake_login(site: dict[str, Any]) -> LoginResult:
        return LoginResult(status="need_manual_login", error="captcha required")

    monkeypatch.setattr(app.main, "get_adapter", lambda _: fake_adapter)
    monkeypatch.setattr(app.main, "login_site", fake_login)

    create_resp = client.post("/api/sites", json=site_payload(adapter="generic_json", login_enabled=True))
    site_id = create_resp.json()["id"]

    payload = client.post(f"/api/sites/{site_id}/refresh").json()
    assert payload["status"] == "need_manual_login"
    assert payload["login_status"] == "need_manual_login"
    assert "captcha" in payload["error"]
