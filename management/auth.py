# -*- coding: utf-8 -*-
"""Authentication helpers and routes for the management WebUI."""

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any

from aiohttp import web


DEFAULT_USERNAME = "bubble"
DEFAULT_PASSWORD = "bubble"
SESSION_COOKIE = "comfyui_bubble_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60


class WebUIAuth:
    def __init__(self, settings_path: Path):
        self.settings_path = settings_path
        self.sessions: dict[str, float] = {}

    def _hash_password(self, password: str, salt: str | None = None) -> tuple[str, str]:
        salt = salt or secrets.token_hex(16)
        digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return salt, digest

    def _default_settings(self) -> dict[str, Any]:
        salt, digest = self._hash_password(DEFAULT_PASSWORD)
        return {"username": DEFAULT_USERNAME, "salt": salt, "password_hash": digest}

    def load_settings(self) -> dict[str, Any]:
        settings = self._default_settings()
        try:
            if self.settings_path.exists():
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    settings.update(data)
        except Exception:
            settings = self._default_settings()
        changed = False
        if not str(settings.get("username") or "").strip():
            settings["username"] = DEFAULT_USERNAME
            changed = True
        if not settings.get("salt") or not settings.get("password_hash"):
            salt, digest = self._hash_password(DEFAULT_PASSWORD)
            settings["salt"] = salt
            settings["password_hash"] = digest
            changed = True
        if changed or not self.settings_path.exists():
            self.save_settings(settings)
        return settings

    def save_settings(self, settings: dict[str, Any]) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def public_status(self, request: web.Request) -> dict[str, Any]:
        settings = self.load_settings()
        return {
            "ok": True,
            "authenticated": self.is_authenticated(request),
            "username": str(settings.get("username") or DEFAULT_USERNAME),
        }

    def verify_password(self, username: str, password: str) -> bool:
        settings = self.load_settings()
        expected_user = str(settings.get("username") or "")
        salt = str(settings.get("salt") or "")
        expected_hash = str(settings.get("password_hash") or "")
        _, digest = self._hash_password(password, salt)
        return hmac.compare_digest(username, expected_user) and hmac.compare_digest(digest, expected_hash)

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = time.time() + SESSION_TTL_SECONDS
        return token

    def clear_session(self, token: str) -> None:
        if token:
            self.sessions.pop(token, None)

    def is_authenticated(self, request: web.Request) -> bool:
        token = request.cookies.get(SESSION_COOKIE, "")
        expires = self.sessions.get(token)
        if not token or not expires:
            return False
        if expires < time.time():
            self.sessions.pop(token, None)
            return False
        self.sessions[token] = time.time() + SESSION_TTL_SECONDS
        return True

    def update_credentials(self, username: str, password: str) -> None:
        username = username.strip()
        if not username:
            raise ValueError("用户名不能为空")
        if len(password) < 4:
            raise ValueError("密码至少 4 位")
        salt, digest = self._hash_password(password)
        self.save_settings({"username": username, "salt": salt, "password_hash": digest})
        self.sessions.clear()


def auth_middleware(auth: WebUIAuth):
    @web.middleware
    async def middleware(request: web.Request, handler):
        path = request.path
        public_paths = {"/", "/webui_logo.jpg", "/api/auth/status", "/api/auth/login"}
        if path in public_paths:
            return await handler(request)
        if auth.is_authenticated(request):
            return await handler(request)
        if path.startswith("/api/"):
            return web.json_response({"ok": False, "error": "未登录"}, status=401)
        raise web.HTTPFound("/")

    return middleware


def register_auth_routes(app: web.Application, auth: WebUIAuth) -> None:
    async def status_handler(request: web.Request) -> web.Response:
        return web.json_response(auth.public_status(request))

    async def login_handler(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            data = {}
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not auth.verify_password(username, password):
            return web.json_response({"ok": False, "error": "用户名或密码错误"}, status=401)
        token = auth.create_session()
        resp = web.json_response({"ok": True, "username": username})
        resp.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="Lax",
            path="/",
        )
        return resp

    async def logout_handler(request: web.Request) -> web.Response:
        auth.clear_session(request.cookies.get(SESSION_COOKIE, ""))
        resp = web.json_response({"ok": True})
        resp.del_cookie(SESSION_COOKIE, path="/")
        return resp

    async def change_handler(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            data = {}
        current_username = str(data.get("current_username") or "").strip()
        current_password = str(data.get("current_password") or "")
        new_username = str(data.get("new_username") or "").strip()
        new_password = str(data.get("new_password") or "")
        if not auth.verify_password(current_username, current_password):
            return web.json_response({"ok": False, "error": "当前用户名或密码错误"}, status=400)
        try:
            auth.update_credentials(new_username, new_password)
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        resp = web.json_response({"ok": True, "username": new_username})
        resp.del_cookie(SESSION_COOKIE, path="/")
        return resp

    app.router.add_get("/api/auth/status", status_handler)
    app.router.add_post("/api/auth/login", login_handler)
    app.router.add_post("/api/auth/logout", logout_handler)
    app.router.add_post("/api/auth/change", change_handler)
