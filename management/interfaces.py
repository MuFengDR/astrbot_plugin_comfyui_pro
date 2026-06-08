# -*- coding: utf-8 -*-
"""ComfyUI interface configuration routes."""

import json
import re

from aiohttp import ClientSession, ClientTimeout, web

from ..workflow_engine import parse_workflow_filename
from .context import ManagementContext


def register_interface_routes(app: web.Application, ctx: ManagementContext) -> None:
    workflows_dir = ctx.workflows_dir
    meta_path = ctx.meta_path
    ports_config_path = ctx.ports_config_path
    active_port_state_path = ctx.active_port_state_path
    load_ports_func = ctx.load_ports_func
    save_ports_func = ctx.save_ports_func
    active_port_changed_func = ctx.active_port_changed_func

    def _load_workflow_params() -> dict[str, object]:
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("workflow_params"), dict):
                return data["workflow_params"]
        except Exception:
            pass
        return {}
    def _normalize_http(value: str) -> str:
        raw = str(value or "").strip().rstrip("/")
        if not raw:
            return ""
        if raw.startswith(("http://", "https://")):
            return raw
        return f"http://{raw}"

    def _normalize_workflow_names(value) -> list[str]:
        raw_items = value if isinstance(value, list) else []
        names: list[str] = []
        seen = set()
        for item in raw_items:
            name = str(item or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names

    def _normalize_port_entry(entry, idx: int) -> dict[str, object] | None:
        if not isinstance(entry, dict):
            return None
        name = str(entry.get("name") or "").strip()
        http = _normalize_http(entry.get("http") or "")
        workflows = _normalize_workflow_names(entry.get("workflows"))
        if not name and not http:
            return None
        if not name:
            name = f"port{idx}"
        if not http:
            return None
        return {"name": name, "http": http, "workflows": workflows}

    def _load_ports() -> list[dict[str, object]]:
        if load_ports_func:
            return list(load_ports_func() or [])
        try:
            if ports_config_path and ports_config_path.exists():
                data = json.loads(ports_config_path.read_text(encoding="utf-8"))
                raw_ports = data.get("ports") if isinstance(data, dict) else data
                if isinstance(raw_ports, list):
                    ports = []
                    for idx, item in enumerate(raw_ports, start=1):
                        port = _normalize_port_entry(item, idx)
                        if port:
                            ports.append(port)
                    return ports
        except Exception:
            pass
        return []

    def _save_ports(ports: list[dict[str, object]]) -> None:
        if save_ports_func:
            save_ports_func(ports)
            return
        if ports_config_path:
            ports_config_path.parent.mkdir(parents=True, exist_ok=True)
            ports_config_path.write_text(
                json.dumps({"ports": ports}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _ensure_active_port_valid(ports: list[dict[str, object]]) -> str:
        active = _read_active_port()
        names = [str(port.get("name") or "").strip() for port in ports]
        if active and active in names:
            return active
        next_active = names[0] if names else ""
        _write_active_port(next_active)
        return next_active

    def _read_active_port() -> str:
        try:
            if active_port_state_path and active_port_state_path.exists():
                data = json.loads(active_port_state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return str(data.get("name") or "").strip()
        except Exception:
            pass
        return ""

    def _write_active_port(name: str) -> None:
        if not active_port_state_path:
            return
        active_port_state_path.parent.mkdir(parents=True, exist_ok=True)
        active_port_state_path.write_text(
            json.dumps({"name": name}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _workflow_options() -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        seen = set()
        workflow_params = _load_workflow_params()
        if workflows_dir.exists():
            for f in sorted(workflows_dir.glob("*.json")):
                params = (
                    workflow_params.get(f.name, {})
                    if isinstance(workflow_params, dict)
                    else {}
                )
                name = (
                    (params.get("name") if isinstance(params, dict) else "")
                    or (parse_workflow_filename(f.name) or {}).get("name")
                    or f.stem
                )
                if name in seen:
                    continue
                seen.add(name)
                options.append({"name": name, "filename": f.name})
        return options

    async def ports_handler(_: web.Request) -> web.Response:
        ports = _load_ports()
        active = _ensure_active_port_valid(ports)
        return web.json_response(
            {
                "ok": True,
                "ports": ports,
                "active": active,
                "workflows": _workflow_options(),
            }
        )

    async def save_ports_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        raw_ports = body.get("ports") if isinstance(body, dict) else None
        if not isinstance(raw_ports, list):
            return web.json_response(
                {"ok": False, "error": "ports must be a list"}, status=400
            )
        ports: list[dict[str, object]] = []
        for idx, item in enumerate(raw_ports, start=1):
            port = _normalize_port_entry(item, idx)
            if port:
                ports.append(port)
        _save_ports(ports)
        active = _ensure_active_port_valid(ports)
        if active_port_changed_func:
            active_port_changed_func()
        return web.json_response({"ok": True, "ports": ports, "active": active})

    async def active_port_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        name = str(body.get("name") or "").strip() if isinstance(body, dict) else ""
        if not name:
            return web.json_response({"ok": False, "error": "missing name"}, status=400)
        ports = _load_ports()
        if not any(str(port.get("name") or "") == name for port in ports):
            return web.json_response({"ok": False, "error": "接口不存在"}, status=404)
        _write_active_port(name)
        if active_port_changed_func:
            active_port_changed_func()
        return web.json_response({"ok": True, "active": name})

    async def embed_check_handler(request: web.Request) -> web.Response:
        name = str(request.query.get("port_name") or "").strip()
        ports = _load_ports()
        port = next((p for p in ports if str(p.get("name") or "") == name), None)
        if not port:
            return web.json_response({"ok": False, "error": "接口不存在"}, status=404)
        url = _normalize_http(str(port.get("http") or ""))
        if not url:
            return web.json_response({"ok": False, "error": "接口地址为空"}, status=400)
        try:
            async with ClientSession(timeout=ClientTimeout(total=8)) as session:
                async with session.get(url) as resp:
                    x_frame = resp.headers.get("x-frame-options", "")
                    csp = resp.headers.get("content-security-policy", "")
                    status = resp.status
        except Exception as e:
            return web.json_response(
                {"ok": False, "embeddable": False, "url": url, "error": f"接口不可访问：{e}"},
                status=400,
            )

        origin = f"{request.scheme}://{request.host}"
        reasons: list[str] = []
        x_frame_lower = x_frame.lower()
        if x_frame_lower:
            if "deny" in x_frame_lower or "sameorigin" in x_frame_lower:
                reasons.append(f"X-Frame-Options: {x_frame}")
            elif "allow-from" in x_frame_lower and origin.lower() not in x_frame_lower:
                reasons.append(f"X-Frame-Options: {x_frame}")

        frame_ancestors = ""
        match = re.search(r"frame-ancestors\s+([^;]+)", csp, flags=re.IGNORECASE)
        if match:
            frame_ancestors = match.group(1).strip()
            ancestors_lower = frame_ancestors.lower()
            allowed = "*" in ancestors_lower or origin.lower() in ancestors_lower
            blocked_self_only = ancestors_lower in {"'self'", "self"} or "'none'" in ancestors_lower
            if blocked_self_only or not allowed:
                reasons.append(f"Content-Security-Policy frame-ancestors: {frame_ancestors}")

        embeddable = not reasons
        message = "接口可访问，未检测到禁止内嵌响应头。" if embeddable else "接口可访问，但浏览器可能会阻止内嵌。"
        return web.json_response(
            {
                "ok": True,
                "embeddable": embeddable,
                "url": url,
                "status": status,
                "x_frame_options": x_frame,
                "frame_ancestors": frame_ancestors,
                "message": message,
                "reasons": reasons,
            }
        )

    app.router.add_get("/api/ports", ports_handler)
    app.router.add_post("/api/ports", save_ports_handler)
    app.router.add_post("/api/active_port", active_port_handler)
    app.router.add_get("/api/comfyui/embed_check", embed_check_handler)
