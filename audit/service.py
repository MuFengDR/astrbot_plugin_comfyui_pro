# -*- coding: utf-8 -*-
"""Persistent content-audit service for generated images."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from astrbot.api import logger

from .models import (
    default_send_policy,
    new_record_id,
    normalize_fail_policy,
    normalize_send_policy,
    normalize_status,
    now_ts,
    public_record,
)
from .providers.baidu import BaiduImageAuditProvider
from .providers.placeholder import PlaceholderAuditProvider


IMAGE_PROVIDERS = {"placeholder", "baidu_icr"}
TEST_IMAGE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAfUlEQVR4nNXOMREAIADEsFL/"
    "dn9HBAPXKMjZRpnESZzESZzESZzESZzESZzESZzESZzESZzESZzESZzESZzESZzESZzE"
    "SZzESZzESZzESZzESZzESZzESZzESZzESZzESZzESZzESZzESZzESZzESZzESZzESZzE"
    "SZzESZy/A68uywwDXzN02MoAAAAASUVORK5CYII="
)


class ContentAuditService:
    def __init__(self, plugin_data_dir: Path):
        self.plugin_data_dir = Path(plugin_data_dir)
        self.audit_dir = self.plugin_data_dir / "media" / "audit"
        self.records_path = self.audit_dir / "audit_records.json"
        self.settings_path = self.audit_dir / "audit_settings.json"
        self.provider = PlaceholderAuditProvider()

    def _ensure_dir(self) -> None:
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def default_settings(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "provider": self.provider.name,
            "providers": {"image": self.provider.name, "video": "", "text": ""},
            "baidu_icr": {"api_key": "", "secret_key": ""},
            "fail_policy": "allow",
            "send_policy": default_send_policy(),
        }

    def _normalize_providers(self, value: Any, fallback: str = "") -> Dict[str, str]:
        image = fallback if fallback in IMAGE_PROVIDERS else self.provider.name
        if isinstance(value, dict):
            candidate = str(value.get("image") or image).strip()
            image = candidate if candidate in IMAGE_PROVIDERS else self.provider.name
        return {"image": image, "video": "", "text": ""}

    def _normalize_baidu_settings(self, value: Any, current: Dict[str, Any] | None = None) -> Dict[str, str]:
        current = current or {}
        data = value if isinstance(value, dict) else {}
        api_key = str(data.get("api_key") or current.get("api_key") or "").strip()
        secret_value = str(data.get("secret_key") or "").strip()
        secret_key = secret_value if secret_value and secret_value != "********" else str(current.get("secret_key") or "").strip()
        return {"api_key": api_key, "secret_key": secret_key}

    def _public_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(settings)
        raw_baidu = settings.get("baidu_icr") if isinstance(settings.get("baidu_icr"), dict) else {}
        baidu = dict(raw_baidu)
        baidu["secret_key"] = "********" if raw_baidu.get("secret_key") else ""
        baidu["configured"] = bool(raw_baidu.get("api_key") and raw_baidu.get("secret_key"))
        data["baidu_icr"] = baidu
        return data

    def load_settings(self) -> Dict[str, Any]:
        settings = self.default_settings()
        try:
            if self.settings_path.exists():
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    settings.update(data)
        except Exception as e:
            logger.warning("ComfyUI content audit settings read failed: %s", e)
        settings["enabled"] = bool(settings.get("enabled", True))
        legacy_provider = str(settings.get("provider") or self.provider.name).strip()
        settings["providers"] = self._normalize_providers(settings.get("providers"), legacy_provider)
        settings["provider"] = settings["providers"]["image"]
        settings["baidu_icr"] = self._normalize_baidu_settings(settings.get("baidu_icr"))
        settings["fail_policy"] = normalize_fail_policy(settings.get("fail_policy"), "allow")
        settings["send_policy"] = normalize_send_policy(settings.get("send_policy"))
        return settings

    def public_settings(self) -> Dict[str, Any]:
        return self._public_settings(self.load_settings())

    def save_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        current = self.load_settings()
        if isinstance(payload, dict):
            if "enabled" in payload:
                current["enabled"] = bool(payload.get("enabled"))
            if "fail_policy" in payload:
                current["fail_policy"] = normalize_fail_policy(payload.get("fail_policy"), "allow")
            if "providers" in payload:
                current["providers"] = self._normalize_providers(
                    payload.get("providers"),
                    current.get("providers", {}).get("image", ""),
                )
                current["provider"] = current["providers"]["image"]
            if "baidu_icr" in payload:
                current["baidu_icr"] = self._normalize_baidu_settings(
                    payload.get("baidu_icr"),
                    current.get("baidu_icr") or {},
                )
            if "send_policy" in payload:
                current["send_policy"] = normalize_send_policy(payload.get("send_policy"))
            current["providers"] = self._normalize_providers(current.get("providers"), current.get("provider", ""))
            current["provider"] = current["providers"]["image"]
        self._ensure_dir()
        self.settings_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._public_settings(current)

    def _image_provider_for_settings(self, settings: Dict[str, Any]) -> Any:
        provider_name = str((settings.get("providers") or {}).get("image") or settings.get("provider") or "placeholder")
        if provider_name == "baidu_icr":
            baidu = settings.get("baidu_icr") if isinstance(settings.get("baidu_icr"), dict) else {}
            return BaiduImageAuditProvider(
                self.plugin_data_dir,
                api_key=str(baidu.get("api_key") or ""),
                secret_key=str(baidu.get("secret_key") or ""),
            )
        return self.provider

    def _settings_with_payload(self, payload: Dict[str, Any] | None) -> Dict[str, Any]:
        current = self.load_settings()
        if not isinstance(payload, dict):
            return current
        merged = dict(current)
        if "enabled" in payload:
            merged["enabled"] = bool(payload.get("enabled"))
        if "providers" in payload:
            merged["providers"] = self._normalize_providers(
                payload.get("providers"),
                current.get("providers", {}).get("image", ""),
            )
            merged["provider"] = merged["providers"]["image"]
        if "baidu_icr" in payload:
            merged["baidu_icr"] = self._normalize_baidu_settings(
                payload.get("baidu_icr"),
                current.get("baidu_icr") or {},
            )
        merged["providers"] = self._normalize_providers(merged.get("providers"), merged.get("provider", ""))
        merged["provider"] = merged["providers"]["image"]
        merged["baidu_icr"] = self._normalize_baidu_settings(merged.get("baidu_icr"), current.get("baidu_icr") or {})
        merged["fail_policy"] = normalize_fail_policy(merged.get("fail_policy"), "allow")
        merged["send_policy"] = normalize_send_policy(merged.get("send_policy"))
        return merged

    async def test_image_provider(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        settings = self._settings_with_payload(payload)
        provider_name = str((settings.get("providers") or {}).get("image") or settings.get("provider") or "placeholder")
        if provider_name == "placeholder":
            return {
                "ok": True,
                "provider": "placeholder",
                "message": "占位审核器可用，但不会进行真实内容识别。",
                "result": {"status": "unknown", "reason": "placeholder"},
            }
        provider = self._image_provider_for_settings(settings)
        self._ensure_dir()
        test_path = self.audit_dir / "baidu_icr_test.png"
        try:
            test_path.write_bytes(base64.b64decode(TEST_IMAGE_PNG))
            result = await provider.audit_image(str(test_path), {"test": True})
            status = normalize_status(result.get("status"))
            ok = status in {"pass", "block"}
            message = "百度内容审核连接成功。" if ok else str(result.get("reason") or "百度内容审核测试失败。")
            response = {
                "ok": ok,
                "provider": getattr(provider, "name", provider_name),
                "message": message,
                "result": result,
            }
            if not ok:
                response["error"] = message
            return response
        except Exception as e:
            message = f"百度内容审核连接失败：{e}"
            return {
                "ok": False,
                "provider": getattr(provider, "name", provider_name),
                "message": message,
                "error": message,
                "result": {"status": "error", "reason": str(e)},
            }
        finally:
            try:
                test_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _read_records(self) -> List[Dict[str, Any]]:
        try:
            if not self.records_path.exists():
                return []
            data = json.loads(self.records_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("ComfyUI content audit records read failed: %s", e)
            return []

    def _write_records(self, records: Iterable[Dict[str, Any]]) -> None:
        self._ensure_dir()
        self.records_path.write_text(
            json.dumps(list(records), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _decision_for_status(self, status: str, settings: Dict[str, Any]) -> str:
        if status == "pass":
            return "allow"
        if status == "block":
            return "block"
        return normalize_fail_policy(settings.get("fail_policy"), "allow")

    async def audit_images_for_task(self, task: Dict[str, Any], images: List[str]) -> Dict[str, Any]:
        origin = str(task.get("origin") or "")
        if origin not in {"command", "llm_tool"} or not images:
            return {"allowed_images": list(images), "blocked": [], "records": []}

        settings = self.load_settings()
        if not settings.get("enabled", True):
            return {"allowed_images": list(images), "blocked": [], "records": []}
        image_provider = self._image_provider_for_settings(settings)

        records = self._read_records()
        allowed: List[str] = []
        blocked: List[str] = []
        made: List[Dict[str, Any]] = []
        for index, image_url in enumerate(images, 1):
            try:
                result = await image_provider.audit_image(image_url, {"task": task, "index": index})
            except Exception as e:
                result = {
                    "status": "error",
                    "categories": [],
                    "scores": {},
                    "reason": f"审核执行失败：{e}",
                    "provider": getattr(image_provider, "name", self.provider.name),
                    "raw": {},
                }
            status = normalize_status(result.get("status"))
            decision = self._decision_for_status(status, settings)
            record = {
                "id": new_record_id(),
                "task_id": str(task.get("task_id") or ""),
                "prompt_id": str(task.get("prompt_id") or ""),
                "origin": origin,
                "origin_label": task.get("origin_label") or origin,
                "session_label": task.get("session_label") or "",
                "session_key": task.get("session_key") or "",
                "workflow_name": task.get("workflow_name") or "",
                "workflow_file": task.get("workflow_file") or "",
                "port_name": task.get("port_name") or "",
                "image_url": image_url,
                "thumbnail": image_url,
                "status": status,
                "decision": decision,
                "sent": decision != "block",
                "reason": str(result.get("reason") or ""),
                "categories": result.get("categories") or [],
                "scores": result.get("scores") or {},
                "provider": result.get("provider") or getattr(image_provider, "name", self.provider.name),
                "manual": False,
                "created_at": now_ts(),
                "updated_at": now_ts(),
                "raw": result.get("raw") or {},
            }
            records.append(record)
            made.append(public_record(record))
            if decision == "block":
                blocked.append(image_url)
            else:
                allowed.append(image_url)
        self._write_records(records)
        return {"allowed_images": allowed, "blocked": blocked, "records": made}

    def list_records(self, filters: Dict[str, Any] | None = None) -> Dict[str, Any]:
        filters = filters or {}
        records = [public_record(item) for item in self._read_records()]
        status = str(filters.get("status") or "").strip()
        origin = str(filters.get("origin") or "").strip()
        workflow = str(filters.get("workflow") or "").strip()
        port = str(filters.get("port") or "").strip()
        if status:
            records = [r for r in records if str(r.get("status") or "") == status or str(r.get("decision") or "") == status]
        if origin:
            records = [r for r in records if str(r.get("origin") or "") == origin]
        if workflow:
            records = [r for r in records if str(r.get("workflow_name") or "") == workflow]
        if port:
            records = [r for r in records if str(r.get("port_name") or "") == port]
        records.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        try:
            limit = int(filters.get("limit") or 200)
        except Exception:
            limit = 200
        return {"ok": True, "records": records[: max(1, min(limit, 500))]}

    def stats(self) -> Dict[str, Any]:
        records = self._read_records()
        return {
            "ok": True,
            "stats": {
                "total": len(records),
                "unknown": sum(1 for r in records if r.get("status") == "unknown"),
                "pass": sum(1 for r in records if r.get("status") == "pass"),
                "block": sum(1 for r in records if r.get("decision") == "block"),
                "error": sum(1 for r in records if r.get("status") == "error"),
            },
        }

    def manual_review(self, record_id: str, decision: str, reason: str = "") -> Dict[str, Any]:
        decision = "block" if str(decision or "").strip() == "block" else "allow"
        records = self._read_records()
        for record in records:
            if str(record.get("id") or "") == str(record_id or ""):
                record["decision"] = decision
                record["status"] = "block" if decision == "block" else "pass"
                record["sent"] = bool(record.get("sent")) if decision == "allow" else False
                record["manual"] = True
                record["reason"] = reason or ("人工拦截" if decision == "block" else "人工通过")
                record["updated_at"] = now_ts()
                self._write_records(records)
                return {"ok": True, "record": public_record(record)}
        return {"ok": False, "error": "审核记录不存在。"}

    async def retry(self, record_id: str) -> Dict[str, Any]:
        records = self._read_records()
        for record in records:
            if str(record.get("id") or "") == str(record_id or ""):
                task = {
                    "task_id": record.get("task_id"),
                    "prompt_id": record.get("prompt_id"),
                    "origin": record.get("origin"),
                    "origin_label": record.get("origin_label"),
                    "session_label": record.get("session_label"),
                    "workflow_name": record.get("workflow_name"),
                    "workflow_file": record.get("workflow_file"),
                    "port_name": record.get("port_name"),
                }
                settings = self.load_settings()
                image_provider = self._image_provider_for_settings(settings)
                try:
                    result = await image_provider.audit_image(str(record.get("image_url") or ""), {"task": task, "retry": True})
                except Exception as e:
                    result = {
                        "status": "error",
                        "categories": [],
                        "scores": {},
                        "reason": f"审核执行失败：{e}",
                        "provider": getattr(image_provider, "name", self.provider.name),
                        "raw": {},
                    }
                status = normalize_status(result.get("status"))
                decision = self._decision_for_status(status, settings)
                record.update(
                    {
                        "status": status,
                        "decision": decision,
                        "reason": str(result.get("reason") or ""),
                        "categories": result.get("categories") or [],
                        "scores": result.get("scores") or {},
                        "provider": result.get("provider") or getattr(image_provider, "name", self.provider.name),
                        "manual": False,
                        "updated_at": now_ts(),
                        "raw": result.get("raw") or {},
                    }
                )
                self._write_records(records)
                return {"ok": True, "record": public_record(record)}
        return {"ok": False, "error": "审核记录不存在。"}

    def remove_task_records(self, task_id: str) -> None:
        task_id = str(task_id or "")
        if not task_id:
            return
        records = [r for r in self._read_records() if str(r.get("task_id") or "") != task_id]
        self._write_records(records)
