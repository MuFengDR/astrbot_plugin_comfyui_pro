# -*- coding: utf-8 -*-
"""Workflow management routes."""

import base64
import json
import re
import uuid
from pathlib import Path

from aiohttp import web

from ..workflow_engine import (
    apply_workflow_slot_edits_file,
    inspect_workflow_file,
    list_workflows_in_dir,
    parse_workflow_filename,
)
from .context import ManagementContext
from .utils import SAFE_FILENAME_RE, safe_basename as _safe_basename

WORKFLOW_NAME_RE = re.compile(r"^[\u4e00-\u9fff\u3040-\u30ffA-Za-z0-9_.:-]+$")


def register_workflow_routes(app: web.Application, ctx: ManagementContext) -> None:
    workflows_dir = ctx.workflows_dir
    output_media_dir = ctx.output_media_dir
    meta_path = ctx.meta_path
    load_meta = ctx.load_meta
    save_meta = ctx.save_meta
    def _load_workflow_params() -> dict[str, object]:
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("workflow_params"), dict):
                return data["workflow_params"]
        except Exception:
            pass
        return {}

    def _save_workflow_params(params: dict[str, object]) -> None:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, object] = {}
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    existing = dict(data)
            except Exception:
                pass
        existing["workflow_params"] = params
        meta_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _save_single_workflow_params(filename: str, params: dict[str, object]) -> None:
        all_params = _load_workflow_params()
        current = all_params.get(filename, {})
        if isinstance(current, dict) and current.get("name") and not params.get("name"):
            params["name"] = current.get("name")
        if isinstance(current, dict) and "allow_other_outputs" in current:
            params["allow_other_outputs"] = bool(current.get("allow_other_outputs"))
        all_params[filename] = params
        _save_workflow_params(all_params)

    def _normalize_workflow_payload(
        body: dict[str, object],
    ) -> tuple[str, dict[str, str], dict[str, object]]:
        filename = _safe_basename(body.get("filename") or "")
        if not filename or not filename.endswith(".json"):
            raise ValueError("invalid filename")
        if not (workflows_dir / filename).exists():
            raise FileNotFoundError("file not found in workflows")

        raw_description = body.get("description")
        if isinstance(raw_description, dict):
            short = str(raw_description.get("short") or "").strip()
            detailed = str(raw_description.get("detailed") or "").strip()
        else:
            short = str(raw_description or "").strip()
            detailed = ""

        params = body.get("params")
        if not isinstance(params, dict):
            raise TypeError("invalid params")
        slot_edits = body.get("slot_edits")
        if isinstance(slot_edits, list):
            apply_workflow_slot_edits_file(workflows_dir / filename, slot_edits)
        scanned = inspect_workflow_file(workflows_dir / filename, str(params.get("name") or "").strip())
        if params.get("name") and isinstance(scanned, dict):
            scanned["name"] = str(params.get("name") or "").strip()
        if isinstance(scanned, dict):
            scanned["allow_other_outputs"] = bool(params.get("allow_other_outputs", False))
        return filename, {"short": short, "detailed": detailed}, scanned

    def _save_workflow_payload(
        filename: str, description: dict[str, str], params: dict[str, object]
    ) -> None:
        meta = load_meta()
        current = meta.get(filename)
        if isinstance(current, dict):
            current["short"] = description.get("short", "")
            current["detailed"] = description.get("detailed", "")
            meta[filename] = current
        else:
            meta[filename] = {
                "short": description.get("short", ""),
                "detailed": description.get("detailed", "") or str(current or ""),
            }
        save_meta(meta)

        all_params = _load_workflow_params()
        all_params[filename] = params
        _save_workflow_params(all_params)

    def _validate_unique_workflow_names(
        updates: list[tuple[str, dict[str, str], dict[str, object]]]
    ) -> None:
        all_params = _load_workflow_params()
        merged = dict(all_params) if isinstance(all_params, dict) else {}
        for filename, _, params in updates:
            merged[filename] = params
        seen: dict[str, str] = {}
        if workflows_dir.exists():
            for f in sorted(workflows_dir.glob("*.json")):
                params = merged.get(f.name, {})
                name = ""
                if isinstance(params, dict):
                    name = str(params.get("name") or "").strip()
                if not name:
                    continue
                if not WORKFLOW_NAME_RE.match(name):
                    raise ValueError(
                        f"工作流调用名称「{name}」不合法。只能使用中文、日文、英文、数字、下划线 _、中划线 -、英文句号 . 和冒号 :"
                    )
                if name in seen and seen[name] != f.name:
                    raise ValueError(
                        f"工作流调用名称「{name}」重复：{seen[name]} 与 {f.name}"
                    )
                seen[name] = f.name

    async def list_handler(_: web.Request) -> web.Response:
        """List workflow JSON files and metadata."""
        meta = load_meta()
        workflow_params = _load_workflow_params()
        files = []
        if workflows_dir.exists():
            workflows = list_workflows_in_dir(workflows_dir, workflow_params)
            for item in sorted(workflows, key=lambda data: str(data.get("filename") or "")):
                name = str(item.get("filename") or "")
                params = item.get("params") if isinstance(item.get("params"), dict) else {}
                display_name = params.get("name") if isinstance(params, dict) else ""
                files.append(
                    {
                        "filename": name,
                        "name": display_name
                        or item.get("name")
                        or (parse_workflow_filename(name) or {}).get("name", name.removesuffix(".json")),
                        "description": meta.get(name, ""),
                        "params": params,
                        "slots": params.get("slots", []) if isinstance(params, dict) else [],
                        "inspection": params.get("inspection", {}) if isinstance(params, dict) else {},
                    }
                )
        return web.json_response({"files": files})

    async def upload_handler(request: web.Request) -> web.Response:
        """Upload a workflow JSON file."""
        reader = await request.multipart()
        field = None
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file":
                field = part
                break
        if field is None:
            return web.json_response(
                {"ok": False, "error": "missing field: file"}, status=400
            )
        filename = _safe_basename(field.filename or "workflow.json")
        if not filename.lower().endswith(".json"):
            return web.json_response(
                {"ok": False, "error": "only .json allowed"}, status=400
            )
        if not SAFE_FILENAME_RE.match(filename):
            return web.json_response(
                {"ok": False, "error": "invalid filename"}, status=400
            )
        workflows_dir.mkdir(parents=True, exist_ok=True)
        path = workflows_dir / filename
        tmp_path = workflows_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
        size = 0
        try:
            with open(tmp_path, "wb") as out:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    size += len(chunk)
                    out.write(chunk)
            params = inspect_workflow_file(tmp_path, Path(filename).stem)
            tmp_path.replace(path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        _save_single_workflow_params(filename, params)
        return web.json_response(
            {
                "ok": True,
                "filename": filename,
                "size": size,
                "params": params,
                "inspection": params.get("inspection", {}),
            }
        )

    async def description_handler(request: web.Request) -> web.Response:
        """Save short workflow description."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        filename = _safe_basename(body.get("filename") or "")
        description = (body.get("description") or "").strip()
        if not filename or not filename.endswith(".json"):
            return web.json_response(
                {"ok": False, "error": "invalid filename"}, status=400
            )
        if not (workflows_dir / filename).exists():
            return web.json_response(
                {"ok": False, "error": "file not found in workflows"}, status=404
            )
        meta = load_meta()
        current = meta.get(filename)
        if isinstance(current, dict):
            current["short"] = description
            meta[filename] = current
        else:
            meta[filename] = {"short": description, "detailed": str(current or "")}
        save_meta(meta)
        return web.json_response({"ok": True})

    async def workflow_params_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        filename = _safe_basename(body.get("filename") or "")
        params = body.get("params")
        if not filename or not filename.endswith(".json"):
            return web.json_response(
                {"ok": False, "error": "invalid filename"}, status=400
            )
        if not (workflows_dir / filename).exists():
            return web.json_response(
                {"ok": False, "error": "file not found in workflows"}, status=404
            )
        if not isinstance(params, dict):
            return web.json_response(
                {"ok": False, "error": "invalid params"}, status=400
            )
        params = inspect_workflow_file(workflows_dir / filename, str(params.get("name") or "").strip())
        params["allow_other_outputs"] = bool(body.get("params", {}).get("allow_other_outputs", False))
        try:
            _validate_unique_workflow_names([(filename, {"short": "", "detailed": ""}, params)])
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        all_params = _load_workflow_params()
        all_params[filename] = params
        _save_workflow_params(all_params)
        return web.json_response({"ok": True})

    async def workflow_slot_media_handler(request: web.Request) -> web.Response:
        try:
            reader = await request.multipart()
            kind = ""
            original_name = ""
            content_type = ""
            file_data = bytearray()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "kind":
                    kind = (await part.text()).strip().lower()
                elif part.name == "file":
                    original_name = _safe_basename(part.filename or "media")
                    content_type = part.headers.get("content-type", "")
                    while True:
                        chunk = await part.read_chunk()
                        if not chunk:
                            break
                        file_data.extend(chunk)
                else:
                    while await part.read_chunk():
                        pass
        except Exception as e:
            return web.json_response({"ok": False, "error": f"invalid form: {e}"}, status=400)
        if kind not in {"image", "video"}:
            return web.json_response({"ok": False, "error": "kind must be image or video"}, status=400)
        if not file_data:
            return web.json_response({"ok": False, "error": "missing field: file"}, status=400)
        if not original_name:
            original_name = "image.png" if kind == "image" else "video.mp4"
        suffix = Path(original_name).suffix.lower()
        if kind == "image":
            if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                return web.json_response({"ok": False, "error": "unsupported image file"}, status=400)
            content_type = content_type or "image/png"
            encoded = base64.b64encode(bytes(file_data)).decode("utf-8")
            return web.json_response(
                {
                    "ok": True,
                    "kind": "image",
                    "name": original_name,
                    "default": encoded,
                    "preview": f"data:{content_type};base64,{encoded}",
                    "size": len(file_data),
                }
            )
        if suffix not in {".mp4", ".webm", ".mov", ".avi", ".mkv"}:
            return web.json_response({"ok": False, "error": "unsupported video file"}, status=400)
        stem = Path(original_name).stem or "video"
        safe_stem = re.sub(r"[^a-zA-Z0-9_\-\+=\.\u4e00-\u9fff]+", "_", stem)[:60] or "video"
        output_media_dir.mkdir(parents=True, exist_ok=True)
        filename = f"workflow_default_{uuid.uuid4().hex}_{safe_stem}{suffix}"
        path = output_media_dir / filename
        with path.open("wb") as out:
            out.write(bytes(file_data))
        size = len(file_data)
        if not size:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return web.json_response({"ok": False, "error": "empty file"}, status=400)
        return web.json_response(
            {"ok": True, "kind": "video", "name": original_name, "default": filename, "size": size}
        )

    async def description_detailed_handler(request: web.Request) -> web.Response:
        """Save detailed workflow description."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        filename = _safe_basename(body.get("filename") or "")
        description = (body.get("description") or "").strip()
        if not filename or not filename.endswith(".json"):
            return web.json_response(
                {"ok": False, "error": "invalid filename"}, status=400
            )
        if not (workflows_dir / filename).exists():
            return web.json_response(
                {"ok": False, "error": "file not found in workflows"}, status=404
            )
        meta = load_meta()
        if filename not in meta:
            meta[filename] = {"short": "", "detailed": ""}
        meta[filename]["detailed"] = description
        save_meta(meta)
        return web.json_response({"ok": True})

    async def workflow_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        try:
            filename, description, params = _normalize_workflow_payload(body)
            _validate_unique_workflow_names([(filename, description, params)])
            _save_workflow_payload(filename, description, params)
        except FileNotFoundError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=404)
        except TypeError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        return web.json_response({"ok": True, "filename": filename})

    async def workflows_bulk_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            return web.json_response(
                {"ok": False, "error": "items must be a list"}, status=400
            )

        normalized: list[tuple[str, dict[str, str], dict[str, object]]] = []
        for item in items:
            if not isinstance(item, dict):
                return web.json_response(
                    {"ok": False, "error": "invalid workflow item"}, status=400
                )
            try:
                normalized.append(_normalize_workflow_payload(item))
            except FileNotFoundError as e:
                return web.json_response({"ok": False, "error": str(e)}, status=404)
            except (TypeError, ValueError) as e:
                return web.json_response({"ok": False, "error": str(e)}, status=400)

        try:
            _validate_unique_workflow_names(normalized)
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

        for filename, description, params in normalized:
            _save_workflow_payload(filename, description, params)
        return web.json_response({"ok": True, "saved": len(normalized)})

    async def rename_handler(request: web.Request) -> web.Response:
        """Rename a workflow file."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        old_name = _safe_basename(body.get("old_name") or "")
        new_name = _safe_basename(body.get("new_name") or "")
        if not old_name.endswith(".json") or not new_name.endswith(".json"):
            return web.json_response(
                {"ok": False, "error": "only .json allowed"}, status=400
            )
        if not SAFE_FILENAME_RE.match(new_name):
            return web.json_response(
                {"ok": False, "error": "invalid new filename"}, status=400
            )
        old_path = workflows_dir / old_name
        new_path = workflows_dir / new_name
        if not old_path.exists():
            return web.json_response(
                {"ok": False, "error": "file not found"}, status=404
            )
        if new_path.exists():
            return web.json_response(
                {"ok": False, "error": "target already exists"}, status=400
            )
        old_path.rename(new_path)
        meta = load_meta()
        if old_name in meta:
            meta[new_name] = meta.pop(old_name)
            save_meta(meta)
        workflow_params = _load_workflow_params()
        if old_name in workflow_params:
            workflow_params[new_name] = workflow_params.pop(old_name)
            _save_workflow_params(workflow_params)
        return web.json_response({"ok": True, "filename": new_name})

    async def delete_handler(request: web.Request) -> web.Response:
        """Delete a workflow file."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        filename = _safe_basename(body.get("filename") or "")
        if not filename.endswith(".json"):
            return web.json_response(
                {"ok": False, "error": "invalid filename"}, status=400
            )
        path = workflows_dir / filename
        if not path.exists():
            return web.json_response(
                {"ok": False, "error": "file not found"}, status=404
            )
        path.unlink()
        meta = load_meta()
        meta.pop(filename, None)
        save_meta(meta)
        workflow_params = _load_workflow_params()
        workflow_params.pop(filename, None)
        _save_workflow_params(workflow_params)
        return web.json_response({"ok": True})

    app.router.add_get("/api/list", list_handler)
    app.router.add_post("/api/upload", upload_handler)
    app.router.add_post("/api/description", description_handler)
    app.router.add_post("/api/description_detailed", description_detailed_handler)
    app.router.add_post("/api/workflow_params", workflow_params_handler)
    app.router.add_post("/api/workflow_slot_media", workflow_slot_media_handler)
    app.router.add_post("/api/workflow", workflow_handler)
    app.router.add_post("/api/workflows/bulk", workflows_bulk_handler)
    app.router.add_post("/api/rename", rename_handler)
    app.router.add_post("/api/delete", delete_handler)
