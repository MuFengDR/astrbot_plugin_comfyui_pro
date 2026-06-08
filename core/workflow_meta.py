# -*- coding: utf-8 -*-
"""Workflow metadata and input/output rule helpers."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..workflow_engine import list_workflows_in_dir
from .paths import META_PATH, PLUGIN_DATA_DIR, WORKFLOWS_DIR

WORKFLOW_NAME_RE = re.compile(r"^[\u4e00-\u9fff\u3040-\u30ffA-Za-z0-9_.:-]+$")


def _ensure_workflows_dir() -> None:
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)


def _load_workflow_meta() -> Dict[str, Any]:
    """从 workflow_meta.json 读取，返回 filename -> {short, detailed} 格式。"""
    if not META_PATH.exists():
        return {}
    try:
        data = json.loads(META_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # 检查是否是旧格式（直接是 filename -> string）
            descriptions = data.get("descriptions", data)
            result = {}
            for k, v in descriptions.items():
                if isinstance(v, str):
                    # 旧格式，转为新格式
                    result[k] = {"short": v, "detailed": v}
                elif isinstance(v, dict):
                    result[k] = v
                else:
                    result[k] = {"short": "", "detailed": ""}
            return result
    except Exception:
        return {}
    return {}


def _load_workflow_text_slots() -> Dict[str, List[str]]:
    """
    从 workflow_meta.json 读取 filename -> 文本槽位说明列表（兼容旧元数据）。
    用于 list_workflows 时告知 LLM 每个 text 的用途，例如 ["正面提示词", "负面提示词"] 或 ["修改说明"]。
    """
    if not META_PATH.exists():
        return {}
    try:
        data = json.loads(META_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        raw = data.get("text_slots")
        if not isinstance(raw, dict):
            return {}
        return {k: v if isinstance(v, list) else [] for k, v in raw.items()}
    except Exception:
        return {}


def _load_workflow_params() -> Dict[str, Any]:
    if not META_PATH.exists():
        return {}
    try:
        data = json.loads(META_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        raw = data.get("workflow_params")
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _list_workflows_in_configured_dir(workflow_dir: Path) -> List[Dict[str, Any]]:
    return list_workflows_in_dir(workflow_dir, _load_workflow_params())


def _get_configured_workflow_info(workflow_dir: Path, filename: str, workflow_params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    for item in list_workflows_in_dir(workflow_dir, workflow_params or _load_workflow_params()):
        if item.get("filename") == filename:
            return item
    return None


def _workflow_availability_error(workflow: Dict[str, Any], workflows: Optional[List[Dict[str, Any]]] = None) -> str:
    params = workflow.get("params") if isinstance(workflow.get("params"), dict) else {}
    inspection = params.get("inspection") if isinstance(params.get("inspection"), dict) else {}
    if not inspection.get("ok", True):
        return str(inspection.get("error") or "工作流未通过 AstrBubble 节点扫描。")
    name = str(workflow.get("name") or params.get("name") or "").strip()
    if not name:
        return "缺少工作流调用名称。"
    if not WORKFLOW_NAME_RE.match(name):
        return "工作流调用名称不合法。只能使用中文、日文、英文、数字、下划线 _、中划线 -、英文句号 . 和冒号 :"
    return ""


def _workflow_is_available(workflow: Dict[str, Any], workflows: Optional[List[Dict[str, Any]]] = None) -> bool:
    return _workflow_availability_error(workflow, workflows) == ""


def _apply_input_rule(values: List[Any], rule: Dict[str, Any], label: str) -> tuple[bool, List[Any], str]:
    limit = rule.get("limit") if isinstance(rule, dict) else None
    mode = rule.get("mode") if isinstance(rule, dict) else "loose"
    if limit is None:
        return True, values, ""
    limit = max(0, int(limit))
    count = len(values)
    if mode == "strict" and count != limit:
        return False, values, f"{label}需要严格输入 {limit} 个，当前提供 {count} 个。"
    if mode != "strict" and count > limit:
        return True, values[:limit], ""
    return True, values, ""


def _slot_label(kind: str, index: object, explain: object = "") -> str:
    kind_text = {"text": "文本", "image": "图片", "video": "视频"}.get(str(kind), str(kind))
    suffix = f" {explain}" if str(explain or "").strip() else ""
    return f"[{kind_text}{index}]{suffix}"


def _apply_slot_input_rule(values: List[Any], slots: List[Dict[str, Any]], kind: str) -> tuple[bool, List[Any], str]:
    if not slots:
        return True, values, ""
    slots = sorted(slots, key=lambda slot: int(slot.get("index") or 0))
    provided_count = sum(1 for item in values if str(item or "").strip())
    if len(values) > len(slots):
        return False, values, f"{kind}输入最多 {len(slots)} 个，当前提供 {provided_count} 个。"
    messages: List[str] = []
    for idx, slot in enumerate(slots):
        raw = values[idx] if idx < len(values) else ""
        provided = str(raw or "").strip() != ""
        if not provided and not bool(slot.get("optional", False)):
            messages.append(f"缺少必填输入：{_slot_label(slot.get('kind'), slot.get('index'), slot.get('explain'))}。")
    if messages:
        return False, values, " ".join(messages)
    return True, values, ""


def _workflow_input_slots(params: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    slots = params.get("slots") if isinstance(params.get("slots"), list) else []
    return [
        slot
        for slot in slots
        if isinstance(slot, dict)
        and slot.get("direction") == "input"
        and slot.get("kind") == kind
        and not slot.get("hidden")
    ]


def _apply_workflow_input_rules(info: Dict[str, Any], texts: List[str], images: List[str], videos: List[str]) -> tuple[bool, List[str], List[str], List[str], str]:
    params = info.get("params") if isinstance(info.get("params"), dict) else {}
    inputs = params.get("inputs") if isinstance(params.get("inputs"), dict) else {}
    text_slots = _workflow_input_slots(params, "text")
    image_slots = _workflow_input_slots(params, "image")
    video_slots = _workflow_input_slots(params, "video")
    ok_texts, texts, msg_texts = (
        _apply_slot_input_rule(texts, text_slots, "文本")
        if text_slots
        else _apply_input_rule(texts, inputs.get("text", {}), "文本")
    )
    ok_images, images, msg_images = (
        _apply_slot_input_rule(images, image_slots, "图片")
        if image_slots
        else _apply_input_rule(images, inputs.get("image", {}), "图片")
    )
    ok_videos, videos, msg_videos = (
        _apply_slot_input_rule(videos, video_slots, "视频")
        if video_slots
        else _apply_input_rule(videos, inputs.get("video", {}), "视频")
    )
    messages = [m for m in (msg_texts, msg_images, msg_videos) if m]
    return ok_texts and ok_images and ok_videos, texts, images, videos, " ".join(messages)


def _workflow_input_mismatch_message(
    workflow_name: str,
    candidates: List[Dict[str, Any]],
    texts: Any,
    images: Any,
    videos: Any,
) -> str:
    details: List[str] = []
    text_values = list(texts or []) if not isinstance(texts, int) else [""] * texts
    image_values = list(images or []) if not isinstance(images, int) else [""] * images
    video_values = list(videos or []) if not isinstance(videos, int) else [""] * videos
    text_count = sum(1 for item in text_values if str(item or "").strip())
    image_count = sum(1 for item in image_values if str(item or "").strip())
    video_count = sum(1 for item in video_values if str(item or "").strip())
    for item in candidates:
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        inspection = params.get("inspection") if isinstance(params.get("inspection"), dict) else {}
        if not inspection.get("ok", True):
            details.append(f"- {inspection.get('error') or '工作流未通过 AstrBubble 节点扫描。'}")
            continue
        ok, _, _, _, msg = _apply_workflow_input_rules(
            item, list(text_values), list(image_values), list(video_values)
        )
        if ok:
            continue
        details.append(f"- {msg or '输入数量不符合该工作流设置。'}")
    suffix = ("\n" + "\n".join(details)) if details else ""
    return (
        f"工作流「{workflow_name}」存在，但入参数量不符合条件。"
        f"当前提供：文本{text_count}，图片{image_count}，视频{video_count}。"
        + suffix
    )


async def _load_workflow_descriptions(config: Any) -> Dict[str, str]:
    """工作流说明：优先从 workflow_meta.json 读取（管理页编辑），兼容旧配置 workflow_descriptions。"""
    meta = _load_workflow_meta()
    if meta:
        return meta
    raw = getattr(config, "workflow_descriptions", None) or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _save_workflow_meta(descriptions: Dict[str, Any]) -> None:
    """将 filename -> {short, detailed} 写入 workflow_meta.json，保留已有 text_slots 等字段。"""
    PLUGIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, Any] = {}
    if META_PATH.exists():
        try:
            data = json.loads(META_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                existing = dict(data)
        except Exception:
            pass
    existing["descriptions"] = descriptions
    META_PATH.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _get_workflow_dir() -> Path:
    """工作流目录：优先使用插件数据目录，若为空则回退到 sd_json（兼容旧路径）。"""
    _ensure_workflows_dir()
    if any(WORKFLOWS_DIR.glob("*.json")):
        return WORKFLOWS_DIR
    fallback = Path("sd_json")
    return fallback if fallback.exists() else WORKFLOWS_DIR


__all__ = [
    "_apply_input_rule",
    "_apply_workflow_input_rules",
    "_ensure_workflows_dir",
    "_get_configured_workflow_info",
    "_get_workflow_dir",
    "_list_workflows_in_configured_dir",
    "_load_workflow_descriptions",
    "_load_workflow_meta",
    "_load_workflow_params",
    "_load_workflow_text_slots",
    "_save_workflow_meta",
    "_workflow_availability_error",
    "_workflow_input_mismatch_message",
    "_workflow_is_available",
]
