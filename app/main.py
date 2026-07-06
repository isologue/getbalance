from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app.adapters import ADAPTERS, generic_json, get_adapter
from app.curl_parser import parse_curl_command, parse_login_curl_command
from app.login import is_auth_failure, login_site, preview_login_request


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    task = asyncio.create_task(_auto_refresh_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="GetBalance", version="0.4.0", lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on", "active"}


def _validate_json_object(raw: str, field_name: str) -> str:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail=f"{field_name} 必须是 JSON 对象")
    return raw or "{}"


def _normalize_site_payload(data: dict[str, Any]) -> dict[str, Any]:
    extra_headers = _validate_json_object(data.get("extra_headers", "{}") or "{}", "extra_headers")
    login_headers = _validate_json_object(data.get("login_headers", "{}") or "{}", "login_headers")

    try:
        scale = float(data.get("scale", 1) or 1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="scale 必须是数字") from exc

    name = str(data.get("name", "")).strip()
    base_url = str(data.get("base_url", "")).strip()
    balance_path = str(data.get("balance_path", "")).strip()
    if not name or not base_url or not balance_path:
        raise HTTPException(status_code=400, detail="name、base_url、balance_path 为必填项")

    return {
        "name": name,
        "base_url": base_url,
        "balance_endpoint": str(data.get("balance_endpoint") or "/").strip(),
        "method": str(data.get("method") or "GET").upper(),
        "adapter": str(data.get("adapter") or "generic_json"),
        "cookie": str(data.get("cookie") or ""),
        "authorization": str(data.get("authorization") or ""),
        "extra_headers": extra_headers,
        "request_body": str(data.get("request_body") or ""),
        "balance_path": balance_path,
        "currency_path": str(data.get("currency_path") or "").strip(),
        "default_currency": str(data.get("default_currency") or "USD").strip(),
        "scale": scale,
        "notes": str(data.get("notes") or ""),
        "is_active": _truthy(data.get("is_active", True)),
        "login_enabled": _truthy(data.get("login_enabled", False)),
        "login_url": str(data.get("login_url") or "").strip(),
        "login_method": str(data.get("login_method") or "api").strip(),
        "login_username": str(data.get("login_username") or ""),
        "login_password": str(data.get("login_password") or ""),
        "login_headers": login_headers,
        "login_body_template": str(data.get("login_body_template") or ""),
        "login_token_path": str(data.get("login_token_path") or "").strip(),
        "login_token_prefix": str(data.get("login_token_prefix") or "Bearer").strip(),
        "login_cookie_from_response": _truthy(data.get("login_cookie_from_response", False)),
        "auth_fail_status_codes": str(data.get("auth_fail_status_codes") or "401,403"),
        "auth_fail_keywords": str(
            data.get("auth_fail_keywords") or "unauthorized,token expired,login required,未登录,登录过期"
        ),
    }


def _normalize_preview_payload(data: dict[str, Any]) -> dict[str, Any]:
    extra_headers = _validate_json_object(data.get("extra_headers", "{}") or "{}", "extra_headers")
    base_url = str(data.get("base_url", "")).strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url 不能为空")
    return {
        "base_url": base_url,
        "balance_endpoint": str(data.get("balance_endpoint") or "/").strip(),
        "method": str(data.get("method") or "GET").upper(),
        "cookie": str(data.get("cookie") or ""),
        "authorization": str(data.get("authorization") or ""),
        "extra_headers": extra_headers,
        "request_body": str(data.get("request_body") or ""),
    }


def _normalize_login_preview_payload(data: dict[str, Any]) -> dict[str, Any]:
    login_headers = _validate_json_object(data.get("login_headers", "{}") or "{}", "login_headers")
    login_url = str(data.get("login_url", "")).strip()
    if not login_url:
        raise HTTPException(status_code=400, detail="login_url 不能为空")
    return {
        "login_url": login_url,
        "login_method": str(data.get("login_method") or "api").strip(),
        "login_username": str(data.get("login_username") or ""),
        "login_password": str(data.get("login_password") or ""),
        "login_headers": login_headers,
        "login_body_template": str(data.get("login_body_template") or ""),
    }


async def _read_form_urlencoded(request: Request) -> dict[str, Any]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _set_form_checkbox_defaults(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    for field_name in ("is_active", "login_enabled", "login_cookie_from_response"):
        if field_name not in normalized:
            normalized[field_name] = "0"
    return normalized


def _attach_login_fields(site: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    result["login_status"] = site.get("login_status", "not_configured")
    result["last_login_error"] = site.get("last_login_error", "")
    result["last_login_at"] = site.get("last_login_at")
    return result


async def _run_site_adapter(site: dict[str, Any]) -> dict[str, Any]:
    adapter = get_adapter(site["adapter"])
    return (await adapter(site)).to_dict()


async def _login_and_retry(site: dict[str, Any], first_result: dict[str, Any]) -> dict[str, Any]:
    db.update_site_auth(
        site["id"],
        login_status="login_expired",
        last_login_error=first_result.get("error", "登录态失效"),
    )

    login_result = await login_site(site)
    if login_result.status != "ok":
        db.update_site_auth(
            site["id"],
            login_status=login_result.status,
            last_login_error=login_result.error,
        )
        return {
            "status": login_result.status,
            "balance": None,
            "currency": site.get("default_currency"),
            "error": login_result.error,
            "raw_response_preview": login_result.raw_response_preview,
            "status_code": None,
            "login_status": login_result.status,
            "last_login_error": login_result.error,
            "last_login_at": None,
        }

    db.update_site_auth(
        site["id"],
        authorization=login_result.authorization,
        cookie=login_result.cookie,
        login_status="ok",
        last_login_error="",
    )

    refreshed_site = db.get_site(site["id"])
    if not refreshed_site:
        raise HTTPException(status_code=404, detail="站点不存在")

    retry_result = await _run_site_adapter(refreshed_site)
    if retry_result.get("status") == "ok":
        retry_result["status"] = "login_refreshed"
        retry_result["error"] = ""
    retry_result["login_status"] = "ok"
    retry_result["last_login_error"] = ""
    retry_result["last_login_at"] = None
    return retry_result


async def _refresh_site(site_id: int) -> dict[str, Any]:
    site = db.get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")

    if not site["is_active"]:
        result = {
            "status": "error",
            "balance": None,
            "currency": site.get("default_currency"),
            "error": "站点已停用",
            "raw_response_preview": "",
            "status_code": None,
        }
        db.record_balance(site_id, result)
        return _attach_login_fields(site, result)

    result = await _run_site_adapter(site)
    if result.get("status") != "ok" and site.get("login_enabled") and is_auth_failure(site, result):
        result = await _login_and_retry(site, result)
        refreshed_site = db.get_site(site_id) or site
        result = _attach_login_fields(refreshed_site, result)
    else:
        result = _attach_login_fields(site, result)

    db.record_balance(site_id, result)
    return result


async def _refresh_all_sites_for_background() -> None:
    for site in db.list_sites():
        try:
            await _refresh_site(site["id"])
        except Exception:
            # ??????????????????
            continue


async def _auto_refresh_loop() -> None:
    while True:
        try:
            if db.auto_refresh_due():
                db.mark_auto_refresh_started()
                error = ""
                try:
                    await _refresh_all_sites_for_background()
                except Exception as exc:
                    error = str(exc)
                db.mark_auto_refresh_finished(error=error)
        except Exception:
            # ??????????????????????????
            with suppress(Exception):
                db.mark_auto_refresh_finished(error="background auto refresh crashed")
        await asyncio.sleep(5)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request, "sites": db.list_sites()})


@app.get("/sites", response_class=HTMLResponse)
def sites() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


@app.get("/sites/new", response_class=HTMLResponse)
def new_site(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "site_form.html",
        {"request": request, "site": None, "adapters": ADAPTERS.keys()},
    )


@app.post("/sites/new")
async def create_site_form(request: Request) -> RedirectResponse:
    form_data = _set_form_checkbox_defaults(await _read_form_urlencoded(request))
    payload = _normalize_site_payload(form_data)
    db.create_site(payload)
    return RedirectResponse("/", status_code=303)


@app.get("/sites/{site_id}/edit", response_class=HTMLResponse)
def edit_site(site_id: int, request: Request) -> HTMLResponse:
    site = db.get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    return templates.TemplateResponse(
        "site_form.html",
        {"request": request, "site": site, "adapters": ADAPTERS.keys()},
    )


@app.post("/sites/{site_id}/edit")
async def update_site_form(site_id: int, request: Request) -> RedirectResponse:
    if not db.get_site(site_id):
        raise HTTPException(status_code=404, detail="站点不存在")
    form_data = _set_form_checkbox_defaults(await _read_form_urlencoded(request))
    payload = _normalize_site_payload(form_data)
    db.update_site(site_id, payload)
    return RedirectResponse("/", status_code=303)


@app.post("/sites/{site_id}/delete")
def delete_site_form(site_id: int) -> RedirectResponse:
    db.delete_site(site_id)
    return RedirectResponse("/", status_code=303)


@app.get("/history/{site_id}", response_class=HTMLResponse)
def history_page(site_id: int, request: Request) -> HTMLResponse:
    site = db.get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    return templates.TemplateResponse(
        "history.html",
        {"request": request, "site": site, "history": db.list_history(site_id)},
    )


@app.get("/api/settings/auto-refresh")
def api_get_auto_refresh_settings() -> dict[str, Any]:
    return db.get_auto_refresh_settings()


@app.post("/api/settings/auto-refresh")
async def api_update_auto_refresh_settings(request: Request) -> dict[str, Any]:
    payload = await request.json()
    return db.update_auto_refresh_settings(
        enabled=_truthy(payload.get("enabled", True)),
        minutes=int(payload.get("minutes") or 5),
    )


@app.get("/api/sites")
def api_list_sites() -> list[dict[str, Any]]:
    return db.list_sites()


@app.post("/api/sites")
async def api_create_site(request: Request) -> dict[str, Any]:
    payload = _normalize_site_payload(await request.json())
    site_id = db.create_site(payload)
    return {"id": site_id, **payload}


@app.get("/api/sites/{site_id}")
def api_get_site(site_id: int) -> dict[str, Any]:
    site = db.get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    return site


@app.put("/api/sites/{site_id}")
async def api_update_site(site_id: int, request: Request) -> dict[str, Any]:
    if not db.get_site(site_id):
        raise HTTPException(status_code=404, detail="站点不存在")
    payload = _normalize_site_payload(await request.json())
    db.update_site(site_id, payload)
    return {"id": site_id, **payload}


@app.delete("/api/sites/{site_id}")
def api_delete_site(site_id: int) -> dict[str, Any]:
    db.delete_site(site_id)
    return {"ok": True}


@app.post("/api/sites/{site_id}/refresh")
async def api_refresh_site(site_id: int) -> JSONResponse:
    return JSONResponse(await _refresh_site(site_id))


@app.post("/api/sites/{site_id}/login")
async def api_login_site(site_id: int) -> JSONResponse:
    site = db.get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    login_result = await login_site(site)
    db.update_site_auth(
        site_id,
        authorization=login_result.authorization if login_result.status == "ok" else None,
        cookie=login_result.cookie if login_result.status == "ok" else None,
        login_status="ok" if login_result.status == "ok" else login_result.status,
        last_login_error="" if login_result.status == "ok" else login_result.error,
    )
    return JSONResponse(login_result.to_dict())


@app.post("/api/refresh-all")
async def api_refresh_all() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for site in db.list_sites():
        try:
            result = await _refresh_site(site["id"])
        except Exception as exc:
            result = {
                "status": "error",
                "balance": None,
                "currency": site.get("default_currency"),
                "error": str(exc),
                "raw_response_preview": "",
                "status_code": None,
                "login_status": site.get("login_status", "not_configured"),
                "last_login_error": site.get("last_login_error", ""),
                "last_login_at": site.get("last_login_at"),
            }
        results.append({"site_id": site["id"], "name": site["name"], **result})
    return results


@app.get("/api/history/{site_id}")
def api_history(site_id: int) -> list[dict[str, Any]]:
    if not db.get_site(site_id):
        raise HTTPException(status_code=404, detail="站点不存在")
    return db.list_history(site_id)


@app.post("/api/tools/parse-curl")
async def api_parse_curl(request: Request) -> dict[str, Any]:
    payload = await request.json()
    command = str(payload.get("command") or "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="command 不能为空")
    try:
        return parse_curl_command(command)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/tools/parse-login-curl")
async def api_parse_login_curl(request: Request) -> dict[str, Any]:
    payload = await request.json()
    command = str(payload.get("command") or "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="command 不能为空")
    try:
        return parse_login_curl_command(command)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/tools/preview-request")
async def api_preview_request(request: Request) -> dict[str, Any]:
    preview_site = _normalize_preview_payload(await request.json())
    try:
        payload, preview = await generic_json.fetch_json_payload(preview_site)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"payload": payload, "raw_response_preview": preview}


@app.post("/api/tools/preview-login-request")
async def api_preview_login_request(request: Request) -> dict[str, Any]:
    preview_site = _normalize_login_preview_payload(await request.json())
    try:
        payload, preview, cookies = await preview_login_request(preview_site)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "payload": payload,
        "raw_response_preview": preview,
        "cookies": cookies,
    }
