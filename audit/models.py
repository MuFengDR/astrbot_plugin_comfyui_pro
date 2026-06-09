# -*- coding: utf-8 -*-
"""Lightweight content-audit model helpers."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict


AUDIT_STATUSES = {"pass", "block", "unknown", "error"}
AUDIT_FAIL_POLICIES = {"allow", "block"}
AUDIT_SEND_STATES = ("audit_disabled", "audit_error", "audit_hit", "audit_pass")
AUDIT_MEDIA_TYPES = ("text", "image", "video")
# 策略名将同步给 LLM，新增策略请保证名称含义清晰、准确。
AUDIT_SEND_METHODS = {
    "direct_send",
    "obfuscated_send",
    "dont_send",
}
LEGACY_AUDIT_SEND_METHODS = {
    "direct": "direct_send",
    "obfuscated": "obfuscated_send",
    "none": "dont_send",
}


def normalize_send_method(value: Any, media_type: str = "image") -> str:
    method = str(value or "").strip().lower()
    method = LEGACY_AUDIT_SEND_METHODS.get(method, method)
    if method == "obfuscated_send" and media_type != "image":
        return "direct_send"
    return method if method in AUDIT_SEND_METHODS else "direct_send"


def default_send_policy() -> Dict[str, Dict[str, str]]:
    direct = {media_type: "direct_send" for media_type in AUDIT_MEDIA_TYPES}
    return {
        "audit_disabled": dict(direct),
        "audit_error": dict(direct),
        "audit_hit": {"text": "direct_send", "image": "obfuscated_send", "video": "dont_send"},
        "audit_pass": dict(direct),
    }


def now_ts() -> float:
    return time.time()


def new_record_id() -> str:
    return f"audit_{uuid.uuid4().hex}"


def normalize_status(value: Any, default: str = "unknown") -> str:
    status = str(value or "").strip().lower()
    return status if status in AUDIT_STATUSES else default


def normalize_fail_policy(value: Any, default: str = "allow") -> str:
    policy = str(value or "").strip().lower()
    return policy if policy in AUDIT_FAIL_POLICIES else default


def normalize_send_policy(value: Any) -> Dict[str, Dict[str, str]]:
    policy = default_send_policy()
    if not isinstance(value, dict):
        return policy
    for state in AUDIT_SEND_STATES:
        source_row = value.get(state)
        if not isinstance(source_row, dict):
            continue
        for media_type in AUDIT_MEDIA_TYPES:
            policy[state][media_type] = normalize_send_method(source_row.get(media_type), media_type)
    return policy


def public_record(record: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(record)
    data.pop("raw", None)
    return data
